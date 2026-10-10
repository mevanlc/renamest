use std::io::{ErrorKind, Result};
use std::path::Path;

fn native_available(root: &Path) -> Result<bool> {
    let source = root.join("probe-source");
    let destination = root.join("probe-destination");
    std::fs::write(&source, b"probe")?;
    match crate::rename_exclusive(&source, &destination) {
        Ok(()) => {
            std::fs::remove_file(destination)?;
            Ok(true)
        }
        Err(error) if error.kind() == ErrorKind::Unsupported => {
            assert_eq!(std::fs::read(&source)?, b"probe");
            assert!(!destination.exists());
            std::fs::remove_file(source)?;
            Ok(false)
        }
        Err(error) => Err(error),
    }
}

#[test]
fn native_support_matches_the_test_environment() -> Result<()> {
    let root = tempfile::tempdir()?;
    let available = native_available(root.path())?;
    if let Ok(expected) = std::env::var("RENAMEST_EXPECT_NATIVE") {
        assert_eq!(available, expected == "1");
        assert_eq!(crate::rename_exclusive_is_atomic(root.path())?, available);
    }
    Ok(())
}

#[cfg(any(
    all(target_os = "linux", not(feature = "always-fallback")),
    target_vendor = "apple",
    target_os = "windows",
    target_os = "freebsd"
))]
mod atomic {
    use super::native_available;
    use std::io::{ErrorKind, Result};
    use std::path::{Component, Path, PathBuf};

    struct CurrentDirectory {
        previous: PathBuf,
    }

    impl CurrentDirectory {
        fn set<T: AsRef<Path>>(to: T) -> Result<Self> {
            let previous = std::env::current_dir()?;
            std::env::set_current_dir(to)?;
            Ok(Self { previous })
        }
    }

    impl Drop for CurrentDirectory {
        fn drop(&mut self) {
            std::env::set_current_dir(&self.previous).unwrap();
        }
    }

    fn is_exists_error<T>(result: Result<T>) -> bool {
        if let Err(e) = result {
            e.kind() == ErrorKind::AlreadyExists
        } else {
            false
        }
    }

    fn parent_join(path: &Path) -> PathBuf {
        let mut parent = PathBuf::new();
        parent.push(Component::ParentDir);
        parent.push(path);
        parent
    }

    #[test]
    fn rename_exclusive_abs() -> Result<()> {
        let dir = tempfile::tempdir()?;
        if !native_available(dir.path())? {
            return Ok(());
        }

        let path_a = dir.path().join("a");
        let path_b = dir.path().join("b");
        let path_c = dir.path().join("c");

        std::fs::write(&path_a, "a")?;
        std::fs::create_dir(&path_b)?;

        // Rename a file to a non-existent path.
        crate::rename_exclusive(&path_a, &path_c)?;
        assert!(!path_a.try_exists()?);
        assert!(path_c.try_exists()?);
        assert_eq!(std::fs::read_to_string(&path_c)?, "a");

        // Rename a directory to a non-existent path.
        crate::rename_exclusive(&path_b, &path_a)?;
        assert!(!path_b.try_exists()?);
        assert!(path_a.try_exists()?);
        assert!(std::fs::metadata(&path_a)?.is_dir());

        // Rename a file to an existing directory.
        assert!(is_exists_error(crate::rename_exclusive(&path_c, &path_a)));
        assert!(path_c.try_exists()?);

        // Rename a directory to an existing file.
        assert!(is_exists_error(crate::rename_exclusive(&path_a, &path_c)));
        assert!(path_a.try_exists()?);

        Ok(())
    }

    #[test]
    fn rename_exclusive_rel() -> Result<()> {
        let dir = tempfile::tempdir()?;
        if !native_available(dir.path())? {
            return Ok(());
        }
        let _curr = CurrentDirectory::set(dir.path())?;

        let path_a = PathBuf::from("a");
        let path_b = PathBuf::from("b");
        let path_c = PathBuf::from("c");

        std::fs::write(&path_a, "a")?;
        std::fs::write(&path_b, "b")?;
        std::fs::create_dir(&path_c)?;

        // Rename a file to a non-existent path inside a directory.
        let path_c_b = path_c.join(&path_b);
        crate::rename_exclusive(&path_a, &path_c_b)?;
        assert!(!path_a.try_exists()?);
        assert!(path_c_b.try_exists()?);
        assert_eq!(std::fs::read_to_string(&path_c_b)?, "a");

        // Rename a directory to a non-existent path.
        crate::rename_exclusive(&path_c, &path_a)?;
        assert!(!path_c.try_exists()?);
        assert!(path_a.try_exists()?);
        assert!(std::fs::metadata(&path_a)?.is_dir());

        let _curr = CurrentDirectory::set(&path_a)?;

        let path_up_b = parent_join(&path_b);

        // Rename a file to an existing file in the parent directory.
        assert!(is_exists_error(crate::rename_exclusive(
            &path_b, &path_up_b
        )));
        assert!(path_b.try_exists()?);

        // Rename a file in a parent directory to a non-existent path.
        crate::rename_exclusive(&path_up_b, &path_a)?;
        assert!(!path_up_b.try_exists()?);
        assert!(path_a.try_exists()?);
        assert_eq!(std::fs::read_to_string(&path_a)?, "b");

        // Rename a file to a non-existent path in the parent directory.
        crate::rename_exclusive(&path_b, &path_up_b)?;
        assert!(!path_b.try_exists()?);
        assert!(path_up_b.try_exists()?);
        assert_eq!(std::fs::read_to_string(&path_up_b)?, "a");

        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn symlinks_are_moved_and_dangling_destinations_are_not_replaced() -> Result<()> {
        let root = tempfile::tempdir()?;
        if !native_available(root.path())? {
            return Ok(());
        }
        let source = root.path().join("source");
        let destination = root.path().join("destination");
        std::os::unix::fs::symlink("missing-source-target", &source)?;
        std::os::unix::fs::symlink("missing-destination-target", &destination)?;
        assert!(is_exists_error(crate::rename_exclusive(
            &source,
            &destination
        )));
        assert_eq!(
            std::fs::read_link(&source)?,
            Path::new("missing-source-target")
        );
        assert_eq!(
            std::fs::read_link(&destination)?,
            Path::new("missing-destination-target")
        );
        std::fs::remove_file(&destination)?;
        crate::rename_exclusive(&source, &destination)?;
        assert!(std::fs::symlink_metadata(&source).is_err());
        assert_eq!(
            std::fs::read_link(&destination)?,
            Path::new("missing-source-target")
        );
        Ok(())
    }
}

#[test]
fn rename_exclusive_is_atomic() -> Result<()> {
    if crate::rename_exclusive_is_atomic(std::env::current_dir()?)? {
        println!("rename_exclusive is supported");
    } else {
        println!("rename_exclusive is not supported");
    }

    Ok(())
}

#[test]
fn fallback_renames_files_and_directories() -> Result<()> {
    let directory = tempfile::tempdir()?;
    let native = native_available(directory.path())?;
    let source = directory.path().join("source");
    let destination = directory.path().join("destination");
    std::fs::write(&source, "contents")?;
    let atomic = crate::rename_exclusive_fallback(&source, &destination)?;
    assert_eq!(std::fs::read_to_string(&destination)?, "contents");
    assert!(!source.exists());

    std::fs::create_dir(&source)?;
    std::fs::write(source.join("child"), "child contents")?;
    let moved_directory = directory.path().join("moved-directory");
    assert_eq!(
        crate::rename_exclusive_fallback(&source, &moved_directory)?,
        atomic
    );
    assert!(!source.exists());
    assert_eq!(
        std::fs::read_to_string(moved_directory.join("child"))?,
        "child contents"
    );
    assert_eq!(atomic, native);
    Ok(())
}

#[test]
fn fallback_preserves_existing_destination() -> Result<()> {
    let directory = tempfile::tempdir()?;
    let source = directory.path().join("source");
    let destination = directory.path().join("destination");
    std::fs::write(&source, "source contents")?;
    std::fs::write(&destination, "destination contents")?;
    assert_eq!(
        crate::rename_exclusive_fallback(&source, &destination)
            .unwrap_err()
            .kind(),
        ErrorKind::AlreadyExists
    );
    assert_eq!(std::fs::read_to_string(&source)?, "source contents");
    assert_eq!(
        std::fs::read_to_string(&destination)?,
        "destination contents"
    );
    Ok(())
}

#[cfg(not(any(
    all(target_os = "linux", not(feature = "always-fallback")),
    target_vendor = "apple",
    target_os = "windows",
    target_os = "freebsd"
)))]
#[test]
fn atomic_rename_is_unsupported() -> Result<()> {
    let directory = tempfile::tempdir()?;
    let source = directory.path().join("source");
    let destination = directory.path().join("destination");
    std::fs::write(&source, "contents")?;
    assert!(!crate::rename_exclusive_is_atomic(directory.path())?);
    assert_eq!(
        crate::rename_exclusive(&source, &destination)
            .unwrap_err()
            .kind(),
        ErrorKind::Unsupported
    );
    assert_eq!(std::fs::read_to_string(&source)?, "contents");
    assert!(!destination.exists());
    Ok(())
}
