#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.14"
# dependencies = []
# ///
"""Release tooling for one Cargo library package. See RELEASING.md."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.error
import urllib.request
from pathlib import Path

NUMBER = r"(?:0|[1-9][0-9]*)"
IDENTIFIER = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
VERSION = re.compile(
    rf"{NUMBER}\.{NUMBER}\.{NUMBER}(?:-{IDENTIFIER}(?:\.{IDENTIFIER})*)?"
)
TARGETS_FILE = Path(__file__).with_name("release-targets.json")


def run(*args, capture=False, cwd=None):
    return subprocess.run(
        args,
        check=True,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        cwd=cwd,
    ).stdout


def require(condition, message):
    if not condition:
        raise ValueError(message)


def version(value):
    require(VERSION.fullmatch(value) is not None, f"Invalid release version: {value!r}")
    return value


def tag_version(tag):
    require(tag.startswith("v"), "Release tags must start with v")
    return version(tag[1:])


def project(root=Path(".")):
    manifest = tomllib.loads((root / "Cargo.toml").read_text())
    package = manifest["package"]
    require(
        "workspace" not in manifest,
        "This exemplar supports a single package, not a workspace",
    )
    return package


def targets():
    return json.loads(TARGETS_FILE.read_text())


def target_triples():
    return list(dict.fromkeys(entry["target"] for entry in targets()))


def toolchain():
    return tomllib.loads(Path("rust-toolchain.toml").read_text())["toolchain"][
        "channel"
    ]


def source_commit():
    return run("git", "rev-parse", "HEAD", capture=True).strip()


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def stamp(release_version, root=Path(".")):
    version(release_version)
    package = project(root)
    require(package["version"] == "0.0.0", "Committed package version must be 0.0.0")
    manifest_path, lock_path = root / "Cargo.toml", root / "Cargo.lock"
    manifest_text, lock_text = manifest_path.read_text(), lock_path.read_text()
    old_manifest, old_lock = tomllib.loads(manifest_text), tomllib.loads(lock_text)
    entries = [
        p
        for p in old_lock["package"]
        if p["name"] == package["name"] and "source" not in p
    ]
    require(
        len(entries) == 1 and entries[0]["version"] == "0.0.0",
        "Expected one local 0.0.0 lockfile entry",
    )

    def replace_section(text, section, predicate):
        parts = re.split(r"(?m)(?=^\[\[?[^ \n])", text)
        count = 0
        for index, part in enumerate(parts):
            if part.startswith(section + "\n") and predicate(tomllib.loads(part)):
                parts[index], changed = re.subn(
                    r'(?m)^(version\s*=\s*)"0\.0\.0"(\s*(?:#.*)?)$',
                    lambda match: f'{match[1]}"{release_version}"{match[2]}',
                    part,
                )
                require(
                    changed == 1,
                    "Expected exactly one placeholder in the selected section",
                )
                count += 1
        require(count == 1, f"Expected exactly one {section} section")
        return "".join(parts)

    stamped_manifest = replace_section(manifest_text, "[package]", lambda _: True)
    stamped_lock = replace_section(
        lock_text,
        "[[package]]",
        lambda data: (
            data["package"][0]["name"] == package["name"]
            and "source" not in data["package"][0]
        ),
    )
    old_manifest["package"]["version"] = release_version
    entries[0]["version"] = release_version
    require(
        tomllib.loads(stamped_manifest) == old_manifest,
        "Stamping changed unrelated manifest fields",
    )
    require(
        tomllib.loads(stamped_lock) == old_lock,
        "Stamping changed dependency resolution",
    )
    # Validate both files before replacing either.
    manifest_path.write_text(stamped_manifest)
    lock_path.write_text(stamped_lock)


def expected_assets(package, release_version):
    return {f"{package}-{release_version}.crate"}


def bundle(directory=Path("dist")):
    package = project()
    release_version = version(package["version"])
    expected = expected_assets(package["name"], release_version)
    actual = {path.name for path in directory.iterdir()}
    require(
        actual == expected,
        f"Unexpected artifact set: missing {expected - actual}, extra {actual - expected}",
    )
    manifest = {
        "schema": 1,
        "package": package["name"],
        "version": release_version,
        "commit": source_commit(),
        "toolchain": toolchain(),
        "targets": target_triples(),
        "assets": {name: digest(directory / name) for name in sorted(expected)},
    }
    (directory / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n"
    )
    checksums = {
        **manifest["assets"],
        "release-manifest.json": digest(directory / "release-manifest.json"),
    }
    (directory / "SHA256SUMS").write_text(
        "".join(f"{sha}  {name}\n" for name, sha in sorted(checksums.items()))
    )
    return manifest


def verify_bundle(directory=Path("dist")):
    manifest = json.loads((directory / "release-manifest.json").read_text())
    package = project()
    require(manifest["schema"] == 1, "Unknown release manifest schema")
    require(
        manifest["package"] == package["name"]
        and manifest["version"] == package["version"],
        "Wrong package/version",
    )
    require(
        manifest["commit"] == source_commit(), "Artifacts belong to a different commit"
    )
    require(
        manifest["toolchain"] == toolchain(), "Artifacts used a different toolchain"
    )
    require(
        manifest["targets"] == target_triples(),
        "Wrong target matrix",
    )
    expected = expected_assets(package["name"], manifest["version"])
    require(set(manifest["assets"]) == expected, "Incomplete release manifest")
    require(
        {p.name for p in directory.iterdir()}
        == expected | {"release-manifest.json", "SHA256SUMS"},
        "Unexpected files",
    )
    for name, sha in manifest["assets"].items():
        require(digest(directory / name) == sha, f"Checksum mismatch: {name}")
    checksums = {
        **manifest["assets"],
        "release-manifest.json": digest(directory / "release-manifest.json"),
    }
    expected_sums = "".join(
        f"{sha}  {name}\n" for name, sha in sorted(checksums.items())
    )
    require(
        (directory / "SHA256SUMS").read_text() == expected_sums,
        "Checksum file mismatch",
    )
    return manifest


def json_request(url, token=None, missing_ok=False):
    headers = {
        "User-Agent": "renamest-release (https://github.com/mevanlc/renamest)",
        "Accept": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(
                urllib.request.Request(url, headers=headers), timeout=30
            ) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            error.close()
            if missing_ok and error.code == 404:
                return None
            if error.code not in (429, 500, 502, 503, 504) or attempt == 3:
                raise
            time.sleep(2**attempt)
    raise RuntimeError("Unreachable")


def repository():
    repo = os.environ["GITHUB_REPOSITORY"]
    require(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo), "Invalid repository"
    )
    return repo


def github(path, missing_ok=False):
    return json_request(
        f"https://api.github.com/repos/{repository()}/{path}",
        token=os.environ["GH_TOKEN"],
        missing_ok=missing_ok,
    )


def assert_publication():
    require(
        os.environ.get("GITHUB_ACTIONS") == "true",
        "Publication runs only in GitHub Actions",
    )
    require(os.environ.get("GITHUB_EVENT_NAME") == "push", "Manual runs cannot publish")
    tag = os.environ.get("GITHUB_REF_NAME", "")
    release_version = tag_version(tag)
    require(
        os.environ.get("GITHUB_REF") == f"refs/tags/{tag}",
        "Publication requires a tag push",
    )
    package = project()
    require(
        package["version"] == release_version, "Package version differs from the tag"
    )
    obj = github(f"git/ref/tags/{tag}")["object"]
    while obj["type"] == "tag":
        obj = github(f"git/tags/{obj['sha']}")["object"]
    require(
        obj["type"] == "commit" and obj["sha"] == source_commit(),
        "Release tag moved or checkout differs",
    )
    return tag


def release_state(tag):
    published = github(f"releases/tags/{tag}", missing_ok=True)
    if published is not None:
        return published
    # The by-tag REST endpoint finds published releases, not pending draft tags.
    page = 1
    while True:
        releases = github(f"releases?per_page=100&page={page}")
        for release in releases:
            if release["tag_name"] == tag:
                return release
        if len(releases) < 100:
            return None
        page += 1


def verify_remote_assets(release, directory, complete=False):
    local = {p.name: digest(p) for p in directory.iterdir()}
    remote = release["assets"]
    names = [asset["name"] for asset in remote]
    require(len(set(names)) == len(names), "Duplicate remote release assets")
    require(set(names) <= set(local), "Release contains unexpected assets")
    if complete:
        require(set(names) == set(local), "Published release is incomplete")
    for asset in remote:
        if asset.get("digest"):
            require(
                asset["digest"] == f"sha256:{local[asset['name']]}",
                f"Existing asset differs: {asset['name']}; preserve it and resume the original run",
            )
            continue
        # Older assets may not have GitHub's SHA-256 digest field.
        with tempfile.TemporaryDirectory() as temporary:
            run(
                "gh",
                "release",
                "download",
                release["tag_name"],
                "--repo",
                repository(),
                "--pattern",
                asset["name"],
                "--dir",
                temporary,
            )
            require(
                digest(Path(temporary) / asset["name"]) == local[asset["name"]],
                f"Existing asset differs: {asset['name']}; preserve it and resume the original run",
            )
    return set(local) - set(names)


def stage(directory=Path("dist")):
    manifest = verify_bundle(directory)
    tag = assert_publication()
    release = release_state(tag)
    if release is None:
        notes = (
            f"Add the library: cargo add {manifest['package']}@{manifest['version']}\n\n"
            "The attached .crate is the exact source archive published to crates.io. SHA256SUMS covers all downloadable assets; "
            "release-manifest.json records the source commit and build toolchain.\n"
        )
        with tempfile.TemporaryDirectory() as temporary:
            notes_path = Path(temporary) / "notes.md"
            notes_path.write_text(notes)
            args = [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                repository(),
                "--draft",
                "--verify-tag",
                "--title",
                tag,
                "--generate-notes",
                "--notes-file",
                str(notes_path),
            ]
            if "-" in manifest["version"]:
                args.append("--prerelease")
            run(*args)
        # Creation can succeed before the draft appears in GitHub's read APIs.
        # Retry reads only: never create another draft to recover visibility.
        for attempt in range(6):
            release = release_state(tag)
            if release is not None:
                break
            if attempt < 5:
                time.sleep(2**attempt)
        require(
            release is not None,
            "Created draft is not visible yet; rerun failed jobs to reconcile",
        )
    require(
        release["prerelease"] == ("-" in manifest["version"]),
        "Existing release has the wrong prerelease status",
    )
    missing = verify_remote_assets(release, directory, complete=not release["draft"])
    if missing:
        run(
            "gh",
            "release",
            "upload",
            tag,
            "--repo",
            repository(),
            *(str(directory / name) for name in sorted(missing)),
        )
    verify_remote_assets(release_state(tag), directory, complete=True)


def crate_checksum(package, release_version):
    data = json_request(
        f"https://crates.io/api/v1/crates/{package}/{release_version}", missing_ok=True
    )
    return data["version"]["checksum"] if data else None


def check_registry(manifest):
    expected = manifest["assets"][f"{manifest['package']}-{manifest['version']}.crate"]
    actual = crate_checksum(manifest["package"], manifest["version"])
    if actual is None:
        return False
    require(
        actual == expected, "crates.io already has different bytes for this version"
    )
    return True


def check_package(directory=Path("dist")):
    manifest = verify_bundle(directory)
    run("cargo", "package", "--locked", "--allow-dirty", "--no-verify")
    filename = f"{manifest['package']}-{manifest['version']}.crate"
    require(
        digest(Path("target/package") / filename) == manifest["assets"][filename],
        "Recreated crate differs from the verified CI package",
    )


def publish_crate(directory=Path("dist")):
    manifest = verify_bundle(directory)
    tag = assert_publication()
    release = release_state(tag)
    require(release is not None, "Stage the draft before publishing the crate")
    verify_remote_assets(release, directory, complete=True)
    if check_registry(manifest):
        print("Matching crate is already published")
        return
    # CI compiled the extracted crate; check-package reproduced its exact bytes.
    filename = f"{manifest['package']}-{manifest['version']}.crate"
    require(
        digest(Path("target/package") / filename) == manifest["assets"][filename],
        "Run check-package first",
    )
    result = subprocess.run(
        ["cargo", "publish", "--locked", "--allow-dirty", "--no-verify"], check=False
    )
    # Upload can succeed even if Cargo subsequently times out waiting for indexing.
    for attempt in range(30):
        if check_registry(manifest):
            print("Published crate checksum verified")
            return
        if attempt < 29:
            time.sleep(10)
    raise RuntimeError(
        f"Crate is not visible after publication (cargo exit {result.returncode}); rerun to reconcile"
    )


def finalize(directory=Path("dist")):
    manifest = verify_bundle(directory)
    tag = assert_publication()
    require(
        check_registry(manifest), "Crate must be published before the GitHub release"
    )
    release = release_state(tag)
    require(release is not None, "Missing staged release")
    verify_remote_assets(release, directory, complete=True)
    if release["draft"]:
        args = ["gh", "release", "edit", tag, "--repo", repository(), "--draft=false"]
        if "-" in manifest["version"]:
            args.extend(["--prerelease", "--latest=false"])
        run(*args)
    else:
        print("Matching GitHub release is already published")
    final = release_state(tag)
    require(
        not final["draft"] and final.get("immutable"),
        "Release must be published and immutable",
    )
    print(final["html_url"])


def check_consumer(registry=False):
    package = project()
    release_version = version(package["version"])
    library = package["name"].replace("-", "_")
    if registry:
        dependency = f'version = "={release_version}"'
    else:
        source = Path("target/package") / f"{package['name']}-{release_version}"
        require(
            (source / "Cargo.toml").is_file(),
            "Package the crate before testing its consumer",
        )
        dependency = f"path = {json.dumps(str(source.resolve()))}"

    Path("target").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="consumer-", dir="target") as temporary:
        root = Path(temporary).resolve()
        (root / "src").mkdir()
        (root / "data").mkdir()
        (root / "Cargo.toml").write_text(
            '[package]\nname = "release-consumer"\nversion = "0.0.0"\nedition = "2021"\n'
            f"\n[dependencies]\n{package['name']} = {{ {dependency} }}\n"
        )
        (root / "src/main.rs").write_text(
            """use std::io::{ErrorKind, Result};
use std::path::PathBuf;

fn main() -> Result<()> {
    let directory = PathBuf::from(std::env::args_os().nth(1).unwrap());
    let source = directory.join("source");
    let destination = directory.join("destination");
    std::fs::write(&source, b"original")?;
    let _supported = LIBRARY::rename_exclusive_is_atomic(&directory)?;
    let _atomic = LIBRARY::rename_exclusive_fallback(&source, &destination)?;
    assert!(!source.exists());
    assert_eq!(std::fs::read(&destination)?, b"original");
    std::fs::write(&source, b"new")?;
    assert_eq!(
        LIBRARY::rename_exclusive_fallback(&source, &destination).unwrap_err().kind(),
        ErrorKind::AlreadyExists
    );
    assert_eq!(std::fs::read(&source)?, b"new");
    assert_eq!(std::fs::read(&destination)?, b"original");
    let exclusive = directory.join("exclusive");
    match LIBRARY::rename_exclusive(&source, &exclusive) {
        Ok(()) => assert_eq!(std::fs::read(exclusive)?, b"new"),
        Err(error) if error.kind() == ErrorKind::Unsupported => assert!(source.exists()),
        Err(error) => return Err(error),
    }
    Ok(())
}
""".replace("LIBRARY", library)
        )
        run(
            "cargo",
            "run",
            "--quiet",
            "--manifest-path",
            str(root / "Cargo.toml"),
            "--",
            str(root / "data"),
        )


def copy_crate(destination=Path("dist")):
    package = project()
    source = Path("target/package") / f"{package['name']}-{package['version']}.crate"
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination / source.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for command in ("version", "tag", "stamp"):
        commands.add_parser(command).add_argument("value")
    for command in (
        "matrix",
        "toolchain",
        "bundle",
        "verify",
        "stage",
        "check-package",
        "publish-crate",
        "finalize",
        "copy-crate",
        "crate-published",
    ):
        commands.add_parser(command)
    consumer = commands.add_parser("check-consumer")
    consumer.add_argument("--registry", action="store_true")
    args = parser.parse_args()
    match args.command:
        case "version":
            print(version(args.value))
        case "tag":
            print(tag_version(args.value))
        case "stamp":
            stamp(args.value)
        case "matrix":
            print(json.dumps({"include": targets()}, separators=(",", ":")))
        case "toolchain":
            print(toolchain())
        case "bundle":
            bundle()
        case "verify":
            verify_bundle()
        case "stage":
            stage()
        case "check-package":
            check_package()
        case "publish-crate":
            publish_crate()
        case "finalize":
            finalize()
        case "check-consumer":
            check_consumer(registry=args.registry)
        case "copy-crate":
            copy_crate()
        case "crate-published":
            print(str(check_registry(verify_bundle())).lower())


if __name__ == "__main__":
    try:
        main()
    except (
        ValueError,
        KeyError,
        OSError,
        RuntimeError,
        subprocess.CalledProcessError,
    ) as error:
        sys.exit(f"release: {error}")
