//! Where Relay keeps what changes (#652): the same folder the Python server uses, found the same way, so an install upgrades
//! in place. One folder holds config.json, relay.db, blocklist.txt, certs/, backups/, recordings/ and logs.
//!
//! Which folder, first match wins (`app/paths.py`):
//!   1. `RELAY_HOME`
//!   2. the folder holding `RELAY_CONFIG` (tests, the container, the service install)
//!   3. the working directory, when it already has a config.json (an existing source-checkout setup)
//!   4. the per-user default for the OS

use std::path::{Path, PathBuf};

/// The per-user folder for this OS (not created here).
pub fn default_home() -> PathBuf {
    if cfg!(windows) {
        let base = non_empty("APPDATA").map(PathBuf::from).or_else(|| home().map(|h| h.join("AppData").join("Roaming")));
        base.unwrap_or_default().join("Relay")
    } else if cfg!(target_os = "macos") {
        home().unwrap_or_default().join("Library").join("Application Support").join("Relay")
    } else {
        let base = non_empty("XDG_DATA_HOME").map(PathBuf::from).or_else(|| home().map(|h| h.join(".local").join("share")));
        base.unwrap_or_default().join("relay")
    }
}

fn non_empty(name: &str) -> Option<String> {
    std::env::var(name).ok().map(|v| v.trim().to_owned()).filter(|v| !v.is_empty())
}

fn home() -> Option<PathBuf> {
    non_empty(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)
}

/// config.json's path, resolved from the given environment values (so a test needs no process-wide state).
pub fn resolve_config_path(relay_config: Option<&str>, relay_home: Option<&str>, cwd: &Path, default_home: &Path) -> PathBuf {
    if let Some(p) = relay_config.map(str::trim).filter(|p| !p.is_empty()) {
        return PathBuf::from(p);
    }
    if let Some(h) = relay_home.map(str::trim).filter(|h| !h.is_empty()) {
        return Path::new(h).join("config.json");
    }
    let here = cwd.join("config.json");
    if here.exists() {
        return here;
    }
    default_home.join("config.json")
}

pub fn config_path() -> PathBuf {
    let cwd = std::env::current_dir().unwrap_or_default();
    resolve_config_path(non_empty("RELAY_CONFIG").as_deref(), non_empty("RELAY_HOME").as_deref(), &cwd, &default_home())
}

/// The folder everything mutable lives in: wherever config.json is.
pub fn state_dir() -> PathBuf {
    config_path().parent().map(Path::to_path_buf).unwrap_or_default()
}
