# a Renamore fork

This is a fork of Renamore, a Rust library for renaming files without
overwriting an existing destination, atomically where the platform supports it.
See the [upstream repository](https://github.com/indianakernick/renamore) for
full documentation.

## about this fork

This fork keeps the upstream API and adds:

- a direct Linux `renameat2` syscall without build-time C or linker probing;
- corrected Apple volume-capability detection and unsupported-error handling;
  and
- a declared minimum supported Rust version of 1.98.

Everything else behaves like upstream Renamore unless noted below.

### Linux without build-time probing

On Linux, Renamore calls the kernel's `renameat2` system call through `libc`
with `RENAME_NOREPLACE`. It does not link to glibc's `renameat2` wrapper or
compile a musl shim, so building the crate no longer requires a C compiler or a
build-time probe for that wrapper.

If the running kernel or file system does not support the operation, Renamore
reports `std::io::ErrorKind::Unsupported`. Enabling the `always-fallback`
feature disables the Linux atomic implementation: `rename_exclusive` reports
`Unsupported`, while `rename_exclusive_fallback` uses its non-atomic path and
returns `false`. The legacy `always-supported` feature is still accepted but
has no effect.

### Apple volume-capability detection

On macOS, iOS, watchOS, and tvOS, `rename_exclusive` uses `renamex_np` with
`RENAME_EXCL`. Unsupported-operation errors are reported as
`std::io::ErrorKind::Unsupported`; other errors retain their original meaning.

`rename_exclusive_is_atomic(path)` resolves the path's containing volume,
queries that volume at its mount root, rejects incomplete capability responses,
and returns `true` only when `VOL_CAP_INT_RENAME_EXCL` is both valid and set.

## building

Renamore requires Rust 1.98 or newer and uses the Rust 2021 edition.

```console
cargo build
```

## license

Renamore is available under either the [Apache License 2.0](LICENSE-APACHE) or
the [MIT license](LICENSE-MIT), at your option.
