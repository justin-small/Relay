//! The shell's own few settings, in `shell.json` in the state folder (never config.json: that is the
//! backend's, and holds the API key).

use serde::{Deserialize, Serialize};

use crate::paths;

#[derive(Serialize, Deserialize, Default, Clone)]
pub struct Settings {
    /// The "Relay keeps running in the tray" explanation has been shown.
    #[serde(default)]
    pub tray_notice_shown: bool,
    /// Start at login was switched on once by first-run setup; after that only the user changes it.
    #[serde(default)]
    pub autostart_initialised: bool,
    /// Where updates are looked for: a full URL to a `latest.json`, or `owner/repo` on GitHub. Absent means
    /// the built-in default; `RELAY_UPDATE_URL` in the environment beats it (updater.rs).
    #[serde(default)]
    pub update_url: Option<String>,
}

pub fn load() -> Settings {
    std::fs::read(paths::shell_settings_file())
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .unwrap_or_default()
}

pub fn save(s: &Settings) {
    let file = paths::shell_settings_file();
    if let Some(dir) = file.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(json) = serde_json::to_vec_pretty(s) {
        let _ = std::fs::write(file, json);
    }
}
