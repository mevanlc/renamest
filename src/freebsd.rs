use std::ffi::{CStr, CString};
use std::io::{Error, ErrorKind, Result};
use std::os::unix::ffi::OsStrExt;
use std::path::Path;
use std::sync::OnceLock;

type RenameAt2 = unsafe extern "C" fn(
    libc::c_int,
    *const libc::c_char,
    libc::c_int,
    *const libc::c_char,
    libc::c_uint,
) -> libc::c_int;

fn renameat2() -> Option<RenameAt2> {
    static FUNCTION: OnceLock<Option<RenameAt2>> = OnceLock::new();
    *FUNCTION.get_or_init(|| {
        // Do not link directly: older FreeBSD libc versions lack this symbol.
        // RTLD_DEFAULT symbols remain loaded for the lifetime of the process.
        let address = unsafe { libc::dlsym(libc::RTLD_DEFAULT, c"renameat2".as_ptr()) };
        if address.is_null() {
            None
        } else {
            // The signature above matches FreeBSD's renameat2 declaration.
            Some(unsafe { std::mem::transmute::<*mut libc::c_void, RenameAt2>(address) })
        }
    })
}

fn unsupported_or(error: Error) -> Error {
    if error
        .raw_os_error()
        .is_some_and(|code| [libc::ENOSYS, libc::ENOTSUP, libc::EOPNOTSUPP].contains(&code))
    {
        Error::from(ErrorKind::Unsupported)
    } else {
        error
    }
}

pub fn rename_exclusive(from: &Path, to: &Path) -> Result<()> {
    rename_with(renameat2(), from, to)
}

fn rename_with(function: Option<RenameAt2>, from: &Path, to: &Path) -> Result<()> {
    let function = function.ok_or_else(|| Error::from(ErrorKind::Unsupported))?;
    let from = CString::new(from.as_os_str().as_bytes())?;
    let to = CString::new(to.as_os_str().as_bytes())?;
    // Both paths are NUL-terminated and live throughout the call.
    let result = unsafe {
        function(
            libc::AT_FDCWD,
            from.as_ptr(),
            libc::AT_FDCWD,
            to.as_ptr(),
            libc::AT_RENAME_NOREPLACE as libc::c_uint,
        )
    };
    if result == -1 {
        Err(unsupported_or(Error::last_os_error()))
    } else {
        Ok(())
    }
}

pub fn rename_exclusive_is_atomic(path: &Path) -> Result<bool> {
    let Some(function) = renameat2() else {
        return Ok(false);
    };
    // A relative path with an invalid directory descriptor cannot name an
    // object. EBADF confirms kernel support without changing the filesystem.
    let result = unsafe {
        function(
            -1,
            c"renamore-probe".as_ptr(),
            -1,
            c"renamore-probe".as_ptr(),
            libc::AT_RENAME_NOREPLACE as libc::c_uint,
        )
    };
    if result != -1 || Error::last_os_error().raw_os_error() != Some(libc::EBADF) {
        return Ok(false);
    }

    let path = CString::new(path.as_os_str().as_bytes())?;
    let mut stat = std::mem::MaybeUninit::<libc::statfs>::uninit();
    // statfs initializes the structure on success, including f_fstypename.
    if unsafe { libc::statfs(path.as_ptr(), stat.as_mut_ptr()) } == -1 {
        return Err(Error::last_os_error());
    }
    let stat = unsafe { stat.assume_init() };
    let name = unsafe { CStr::from_ptr(stat.f_fstypename.as_ptr()) };
    Ok(matches!(
        name.to_bytes(),
        b"ufs" | b"zfs" | b"tmpfs" | b"msdosfs"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn missing_api_leaves_the_source_untouched() {
        let root = tempfile::tempdir().unwrap();
        let source = root.path().join("source");
        let destination = root.path().join("destination");
        std::fs::write(&source, b"source").unwrap();
        let error = rename_with(None, &source, &destination).unwrap_err();
        assert_eq!(error.kind(), ErrorKind::Unsupported);
        assert_eq!(std::fs::read(source).unwrap(), b"source");
        assert!(!destination.exists());
    }

    #[test]
    fn unsupported_errors_preserve_other_os_errors() {
        for code in [libc::ENOSYS, libc::ENOTSUP, libc::EOPNOTSUPP] {
            assert_eq!(
                unsupported_or(Error::from_raw_os_error(code)).kind(),
                ErrorKind::Unsupported
            );
        }
        for code in [libc::EEXIST, libc::EACCES, libc::EXDEV, libc::EINVAL] {
            assert_eq!(
                unsupported_or(Error::from_raw_os_error(code)).raw_os_error(),
                Some(code)
            );
        }
    }
}
