//! The helper's settings file: where Relay is, who it is, and where ProPresenter's API listens.

use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Default, PartialEq, Serialize, Deserialize)]
pub struct Config {
    /// `ws://host:port` or `wss://host` of Relay.
    #[serde(default)]
    pub relay_url: String,
    #[serde(default)]
    pub helper_id: String,
    #[serde(default)]
    pub token: String,
    #[serde(default)]
    pub name: String,
    /// SHA-256 fingerprint of the certificate a `wss://` Relay must present; empty means the public roots decide.
    #[serde(default)]
    pub tls_pin: String,
    #[serde(default)]
    pub propresenter: ProPresenterConfig,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ProPresenterConfig {
    #[serde(default = "default_host")]
    pub api_host: String,
    /// The port in ProPresenter's Network settings; 0 means not set yet.
    #[serde(default)]
    pub api_port: u16,
}

fn default_host() -> String {
    "127.0.0.1".to_string()
}

impl Default for ProPresenterConfig {
    fn default() -> Self {
        ProPresenterConfig { api_host: default_host(), api_port: 0 }
    }
}

impl Config {
    pub fn load(path: &Path) -> Result<Config, String> {
        match std::fs::read_to_string(path) {
            Ok(text) => serde_json::from_str(&text).map_err(|e| format!("{} is not valid: {e}", path.display())),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(Config::default()),
            Err(e) => Err(format!("cannot read {}: {e}", path.display())),
        }
    }

    pub fn save(&self, path: &Path) -> Result<(), String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
        }
        let text = serde_json::to_string_pretty(self).map_err(|e| e.to_string())?;
        std::fs::write(path, text).map_err(|e| format!("cannot write {}: {e}", path.display()))?;
        restrict(path);
        Ok(())
    }
}

#[cfg(unix)]
fn restrict(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
}

#[cfg(not(unix))]
fn restrict(_path: &Path) {}

/// Where the settings live: the OS's per-user application data folder.
pub fn default_path() -> PathBuf {
    let base = if cfg!(windows) {
        std::env::var_os("APPDATA").map(PathBuf::from)
    } else if cfg!(target_os = "macos") {
        std::env::var_os("HOME").map(|h| PathBuf::from(h).join("Library").join("Application Support"))
    } else {
        std::env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .or_else(|| std::env::var_os("HOME").map(|h| PathBuf::from(h).join(".config")))
    };
    base.unwrap_or_else(|| PathBuf::from(".")).join("Relay Helper").join("helper.json")
}
