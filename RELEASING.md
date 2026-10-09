# Releases

Renamest is one Rust library package. Release tags supply its version; committed
package and lockfile versions stay at `0.0.0`. Push an annotated stable tag such
as `v0.1.0`, or a prerelease such as `v0.2.0-rc.1`. Build metadata is not accepted.

## Normal releases

1. Merge into `main` and let CI pass.
2. Tag the intended commit and push the tag:

   ```sh
   git tag -a v0.1.0 -m 'Release v0.1.0'
   git push origin v0.1.0
   ```

The workflow validates and stamps the version, runs CI, stages a complete GitHub
draft, publishes the crate, then publishes the immutable GitHub release.
Prereleases are published to crates.io and marked on GitHub without becoming the
stable latest release. Manual workflow runs cannot publish.

## First publication and credentials

Enable immutable releases and create the `crates-io` Actions environment without
required reviewers. The first publication needs a bootstrap API token because
the crate must exist before its trusted publisher can be configured.

Create a dedicated, short-lived crates.io token restricted to `renamest` with
`publish-new` permission. Store it as the `CARGO_REGISTRY_TOKEN` secret in the
`crates-io` environment using GitHub's UI or the hidden prompt:

```sh
gh secret set CARGO_REGISTRY_TOKEN --repo mevanlc/renamest --env crates-io
```

After the first publication, add the crates.io GitHub trusted publisher with
owner `mevanlc`, repository `renamest`, workflow `release.yml`, and environment
`crates-io`. Run Release on `main` in `verify-oidc` mode. This exchanges and
automatically revokes a temporary token without publishing. Once successful,
remove Renamest's bootstrap secret and revoke only its dedicated token.

The workflow selects the bootstrap secret when present and OIDC otherwise.
Already-published matching bytes require no upload or registry credentials.
Publishing credentials are supplied only to the publishing step.

## Validation

Release's manual `build` mode accepts a version without `v` and runs all checks,
retaining the verified crate as an Actions artifact.

```sh
cargo fmt --all --check
cargo clippy --locked --all-targets -- -D warnings
cargo clippy --locked --all-targets --all-features -- -D warnings
cargo nextest run --locked --all-targets
cargo nextest run --locked --all-targets --all-features
cargo test --doc --locked
cargo test --doc --locked --all-features
uv run scripts/test_release.py
actionlint
```

Use a disposable checkout for `uv run scripts/release.py stamp 0.1.0`: stamping
edits the manifest and the package's own lockfile entry, checking that dependency
resolution stays identical. All helper commands assume the repository root.

CI runs nextest and doctests on x64 and ARM64 Linux GNU, Linux musl, macOS, and
Windows, with default and all features. Android ARM64 is compile-checked; its
atomic operations remain unsupported. macOS uses deployment target 11.0.
The Rust toolchain, uv, Python, nextest, and workflow actions are pinned.

Crate verification builds the packaged source, runs its tests and doctests, and
compiles and runs an isolated downstream consumer using all three public APIs.
The upload job recreates the `.crate` and compares its SHA-256 with the verified
artifact before authenticating. Publication uses `--no-verify` only because CI
already compiled that exact package. After publication, run
`uv run scripts/release.py check-consumer --registry` in a checkout stamped to
the released version to test a fresh downstream dependency from crates.io.

## Assets and verification

Each release contains exactly three attached assets:

- `renamest-VERSION.crate`: the exact archive published to crates.io.
- `release-manifest.json`: source commit, Rust toolchain, checked targets, and hash.
- `SHA256SUMS`: hashes of the crate and manifest.

GitHub's generated source archives reflect the committed `0.0.0` manifest. Use
the attached `.crate` for the stamped package. There are no prebuilt executables
or platform-specific library downloads.

```sh
gh release download v0.1.0 --repo mevanlc/renamest
sha256sum --check SHA256SUMS
gh release verify v0.1.0 --repo mevanlc/renamest
```

On macOS, use `shasum -a 256 --check SHA256SUMS`. On Windows, compare hashes with
`Get-FileHash -Algorithm SHA256`. `gh release verify-asset` can also verify the
immutable release attestation for downloaded assets.

## Recovery and maintenance

Rerun failed jobs in the original Actions run. Verified crate artifacts are kept
for 30 days; the assembled release bundle is kept for 90 days. Existing assets
must match their hashes; only missing assets are uploaded. Unexpected assets or
different bytes stop publication without replacing anything.

If crate upload succeeded before a later failure, retries check its registry
checksum and skip matching uploads. After a Cargo upload timeout, the helper
polls for up to five minutes to reconcile indexing. Completed matching releases
are successful no-ops on retry.

Publish a new version to fix a library bug. Do not move published tags or replace
release assets. If a rebuild produces different bytes, reuse the original run's
artifacts. Update tool pins deliberately and validate with a complete manual
build; Dependabot proposes GitHub Actions updates monthly.

This setup is copied from tuisv and maintained independently. Its helpers support
one library package; workspace publishing would need a separate design.
