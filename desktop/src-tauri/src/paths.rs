//! Where the shell keeps its files. The state folder matches `default_home()` in app/paths.py, so the
//! backend and the shell agree even though the shell sets RELAY_HOME explicitly.

use std::path::PathBuf;

pub fn state_dir() -> PathBuf {
    if let Ok(v) = std::env::var("RELAY_HOME") {
        if !v.trim().is_empty() {
            return PathBuf::from(v);
        }
    }
    #[cfg(target_os = "windows")]
    let name = "Relay";
    #[cfg(target_os = "macos")]
    let name = "Relay";
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    let name = "relay";
    // data_dir: %APPDATA% (Roaming), ~/Library/Application Support, $XDG_DATA_HOME or ~/.local/share.
    dirs::data_dir().unwrap_or_else(|| PathBuf::from(".")).join(name)
}

pub fn logs_dir() -> PathBuf {
    state_dir().join("logs")
}

pub fn shell_settings_file() -> PathBuf {
    state_dir().join("shell.json")
}
