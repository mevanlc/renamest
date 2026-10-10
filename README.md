# renamest

A cross-platform Rust library for atomic no-overwrite renaming of filesystem entries.

- `rename_exclusive(from, to)` atomically† renames a filesystem entry without
  overwriting an existing destination. Returns `std::io::ErrorKind::Unsupported`
  when the platform or filesystem cannot perform the operation.
- `rename_exclusive_fallback(from, to)` attempts the same operation as
  `rename_exclusive`. Only on `Unsupported`‡ does it fall back to a non-atomic,
  best-effort attempt to rename without overwriting.
- `rename_exclusive_is_atomic(path)` probes whether the platform and filesystem
  support `rename_exclusive` at that path. This is advisory; the rename operation
  can still fail.

† On Windows, renamest uses `MoveFileExW` with no flags to rename without
overwriting. However, [Microsoft's documentation](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw)
does not guarantee that this no-overwrite operation is atomic.

‡ Renamest does not support moving filesystem entries across separate
filesystems. Neither rename function falls back to copying and deleting, which
would not be an atomic rename.

Renamest is based on [Mike Clark's Renamore fork](https://github.com/mevanlc/renamore),
originally derived from [Renamore by Indiana Kernick](https://github.com/indianakernick/renamore).

## usage

Add the library with `cargo add renamest`, or add `renamest = "0.1"` to your
dependencies.

```rust,no_run
fn main() -> std::io::Result<()> {
    renamest::rename_exclusive("old.txt", "new.txt")
}
```

On success, `rename_exclusive_fallback` returns `true` for the native operation
and `false` for the fallback. The fallback checks the destination before calling
`std::fs::rename`. This check and rename are not atomic together, so the fallback
may overwrite a destination created during that interval. Errors other than
`Unsupported`, including an existing destination detected by the native
operation, are returned without falling back.

## platform support

### Linux and Android

On Linux and Android, Renamest calls the kernel's `renameat2` system call through
`libc` with `RENAME_NOREPLACE`. It does not link to glibc's `renameat2` wrapper or
compile a musl shim, so building the crate no longer requires a C compiler or a
build-time probe for that wrapper.

If the running kernel or file system does not support the operation, Renamest
reports `std::io::ErrorKind::Unsupported`. Enabling the `always-fallback`
feature disables the Linux and Android atomic implementation:
`rename_exclusive` reports `Unsupported`, while `rename_exclusive_fallback` uses
its non-atomic path and returns `false`. The legacy `always-supported` feature
is still accepted but has no effect.

### Apple platforms

On macOS, iOS, watchOS, and tvOS, `rename_exclusive` uses `renamex_np` with
`RENAME_EXCL`. Unsupported-operation errors are reported as
`std::io::ErrorKind::Unsupported`; other errors retain their original meaning.

`rename_exclusive_is_atomic(path)` resolves the path's containing volume,
queries that volume at its mount root, rejects incomplete capability responses,
and returns `true` only when `VOL_CAP_INT_RENAME_EXCL` is both valid and set.

### FreeBSD

On FreeBSD, `rename_exclusive` dynamically resolves `renameat2` and uses
`AT_RENAME_NOREPLACE`. Older libc or kernel versions and unsupported filesystems
return `std::io::ErrorKind::Unsupported`; the library does not require the new
symbol at load time. Existing destinations, including dangling symbolic links,
are never replaced by this operation.

`rename_exclusive_is_atomic(path)` checks API and kernel availability without
changing files and recognizes UFS, ZFS, tmpfs, and msdosfs. It returns `false`
for unknown filesystems. This is only an advisory check: the actual rename
determines support. `rename_exclusive_fallback` retains its existing non-atomic
fallback on unsupported systems.

### Windows and other platforms

On Windows, `rename_exclusive` calls `MoveFileExW` with no flags, and
`rename_exclusive_is_atomic` always returns `true` as an advisory result.

On other platforms, `rename_exclusive` returns `Unsupported` and
`rename_exclusive_is_atomic` returns `false`. The non-atomic fallback remains
available.

## building

Renamest requires Rust 1.98 or newer and uses the Rust 2021 edition.

```console
cargo build
```

CI tests Linux (GNU and musl), macOS, and Windows on x64 and ARM64, plus FreeBSD
15.0 and 15.1 on x64, with default and all features. Android ARM64 is
compile-checked. See the
[release guide](https://github.com/mevanlc/renamest/blob/main/RELEASING.md) for
tag-driven versioning and publication.

## license

Renamest is available under either the [Apache License 2.0](LICENSE-APACHE) or
the [MIT license](LICENSE-MIT), at your option.
