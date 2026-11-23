//! Owner-only files and folders (`app/paths.py` `make_private_dir` and `restrict`): 0700 for a folder and 0600 for a file on
//! Unix, an owner-only ACL on Windows (the Python server's `icacls` call, so the two servers leave the same permissions).

use std::path::Path;

/// Create `path` (and its parents), owner-only when it did not exist.
pub fn make_private_dir(path: &Path) -> std::io::Result<()> {
    let existed = path.exists();
    std::fs::create_dir_all(path)?;
    if !existed {
        restrict(path);
    }
    Ok(())
}

/// Best effort: a folder or file this process cannot change is left as it is (the caller decides whether that is fatal).
pub fn restrict(path: &Path) {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = if path.is_dir() { 0o700 } else { 0o600 };
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode));
    }
    #[cfg(windows)]
    {
        let Ok(user) = std::env::var("USERNAME") else { return };
        if user.is_empty() {
            return;
        }
        let grant = if path.is_dir() { format!("{user}:(OI)(CI)F") } else { format!("{user}:F") };
        let _ = std::process::Command::new("icacls").arg(path).args(["/inheritance:r", "/grant:r", &grant]).output();
    }
    #[cfg(not(any(unix, windows)))]
    let _ = path;
}

/// Write `text` to `path` through a private temp file renamed over it, so a reader never sees half a file.
pub fn write_private(path: &Path, tmp: &Path, text: &str) -> std::io::Result<()> {
    use std::io::Write;
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let result = (|| {
        let mut file = options.open(tmp)?;
        file.write_all(text.as_bytes())?;
        file.sync_all()?;
        drop(file);
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(tmp, std::fs::Permissions::from_mode(0o600))?;
        }
        std::fs::rename(tmp, path)
    })();
    if result.is_err() {
        let _ = std::fs::remove_file(tmp);
    }
    result
}
