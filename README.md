# renamest

A Rust library for renaming files without overwriting an existing destination,
atomically where the platform supports it.

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

The library provides:

- `rename_exclusive(from, to)`: renames atomically without overwriting an
  existing destination, or returns `std::io::ErrorKind::Unsupported` when the
  platform cannot perform the operation.
- `rename_exclusive_is_atomic(path)`: checks for platform and file system
  support. This is advisory; the rename operation can still fail.
- `rename_exclusive_fallback(from, to)`: attempts the atomic operation, then
  falls back to checking the destination before renaming if atomic support is
  unavailable. Returns `true` for the atomic operation and `false` for the
  fallback. The fallback has a race between checking and renaming and may
  overwrite a destination created during that interval.

## platform support

### Linux

On Linux, Renamest calls the kernel's `renameat2` system call through `libc`
with `RENAME_NOREPLACE`. It does not link to glibc's `renameat2` wrapper or
compile a musl shim, so building the crate no longer requires a C compiler or a
build-time probe for that wrapper.

If the running kernel or file system does not support the operation, Renamest
reports `std::io::ErrorKind::Unsupported`. Enabling the `always-fallback`
feature disables the Linux atomic implementation: `rename_exclusive` reports
`Unsupported`, while `rename_exclusive_fallback` uses its non-atomic path and
returns `false`. The legacy `always-supported` feature is still accepted but
has no effect.

### Apple platforms

On macOS, iOS, watchOS, and tvOS, `rename_exclusive` uses `renamex_np` with
`RENAME_EXCL`. Unsupported-operation errors are reported as
`std::io::ErrorKind::Unsupported`; other errors retain their original meaning.

`rename_exclusive_is_atomic(path)` resolves the path's containing volume,
queries that volume at its mount root, rejects incomplete capability responses,
and returns `true` only when `VOL_CAP_INT_RENAME_EXCL` is both valid and set.

### Windows and other platforms

On Windows, `rename_exclusive` calls `MoveFileExW` with no flags, and
`rename_exclusive_is_atomic` always returns `true` as an advisory result.

On other platforms, `rename_exclusive` returns `Unsupported` and
`rename_exclusive_is_atomic` returns `false`. The non-atomic fallback remains
available. This includes Android, which CI compile-checks without running it.

## building

Renamest requires Rust 1.98 or newer and uses the Rust 2021 edition.

```console
cargo build
```

CI tests Linux (GNU and musl), macOS, and Windows on x64 and ARM64 with default
and all features, and compile-checks Android ARM64. See the
[release guide](https://github.com/mevanlc/renamest/blob/main/RELEASING.md) for
tag-driven versioning and publication.

## license

Renamest is available under either the [Apache License 2.0](LICENSE-APACHE) or
the [MIT license](LICENSE-MIT), at your option.
