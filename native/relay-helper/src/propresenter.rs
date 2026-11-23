//! The ProPresenter capability: put songs into ProPresenter and reach its network API from the computer it runs on.
//!
//! ProPresenter 22 keeps its library in a database, so a song reaches it by opening the file with the program (it
//! imports it); looks, timers, messages, props and playlists go through its network API, which only listens on
//! this computer's network. Relay cannot reach either from another machine, which is why this helper exists (#497).

use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use base64::Engine;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::capability::{CallFuture, Capability};
use crate::config::ProPresenterConfig;

/// Files ProPresenter opens as songs.
const EXTENSIONS: &[&str] = &["pro"];
const MAX_FILE: usize = 16 * 1024 * 1024;
const MAX_FILES: usize = 200;
const METHODS: &[&str] = &["GET", "POST", "PUT", "DELETE"];

/// Hands files to the program that opens them. A seam: tests record the paths instead of starting ProPresenter.
pub trait Opener: Send + Sync {
    fn open(&self, files: &[PathBuf]) -> Result<(), String>;
}

#[derive(Debug)]
pub struct SystemOpener;

impl Opener for SystemOpener {
    fn open(&self, files: &[PathBuf]) -> Result<(), String> {
        if cfg!(target_os = "macos") {
            let status = std::process::Command::new("open")
                .arg("-a")
                .arg("ProPresenter")
                .args(files)
                .status()
                .map_err(|e| format!("Could not start ProPresenter: {e}"))?;
            if !status.success() {
                return Err("ProPresenter would not open the files (is it installed?).".to_string());
            }
            Ok(())
        } else if cfg!(windows) {
            // `start` opens each file with the program Windows has for .pro: ProPresenter.
            for f in files {
                let status =
                    std::process::Command::new("cmd").args(["/C", "start", ""]).arg(f).status().map_err(|e| format!("Could not start ProPresenter: {e}"))?;
                if !status.success() {
                    return Err("ProPresenter would not open the file (is it installed?).".to_string());
                }
            }
            Ok(())
        } else {
            Err("Opening files in ProPresenter works on macOS and Windows only.".to_string())
        }
    }
}

pub struct ProPresenter {
    config: ProPresenterConfig,
    opener: Arc<dyn Opener>,
    scratch: PathBuf,
}

#[derive(Deserialize)]
struct FileArg {
    name: String,
    data_b64: String,
}

impl ProPresenter {
    pub fn new(config: ProPresenterConfig, opener: Arc<dyn Opener>, scratch: PathBuf) -> Self {
        ProPresenter { config, opener, scratch }
    }

    fn base(&self) -> Result<String, String> {
        if self.config.api_port == 0 {
            return Err("Set ProPresenter's network port in the helper's settings first.".to_string());
        }
        Ok(format!("http://{}:{}", self.config.api_host, self.config.api_port))
    }

    async fn status(&self) -> Result<Value, String> {
        let Ok(base) = self.base() else {
            return Ok(json!({"api_configured": false, "api_reachable": false}));
        };
        match http(&format!("{base}/version"), "GET", None).await {
            Ok((200, body)) => Ok(json!({"api_configured": true, "api_reachable": true, "version": body})),
            _ => Ok(json!({"api_configured": true, "api_reachable": false})),
        }
    }

    async fn api(&self, args: Value) -> Result<Value, String> {
        let method = args.get("method").and_then(Value::as_str).unwrap_or("GET").to_ascii_uppercase();
        if !METHODS.contains(&method.as_str()) {
            return Err(format!("The ProPresenter API call method must be one of {}.", METHODS.join(", ")));
        }
        let path = args.get("path").and_then(Value::as_str).unwrap_or("");
        check_api_path(path)?;
        let body = match args.get("body") {
            None | Some(Value::Null) => None,
            Some(v) => Some(serde_json::to_string(v).map_err(|e| e.to_string())?),
        };
        let (status, parsed) = http(&format!("{}{path}", self.base()?), &method, body).await?;
        Ok(json!({"status": status, "body": parsed}))
    }

    async fn open_files(&self, args: Value) -> Result<Value, String> {
        let files: Vec<FileArg> = serde_json::from_value(args.get("files").cloned().unwrap_or(Value::Null))
            .map_err(|_| "open_files needs a list of files, each with a name and data_b64.".to_string())?;
        if files.is_empty() {
            return Err("open_files needs at least one file.".to_string());
        }
        if files.len() > MAX_FILES {
            return Err(format!("At most {MAX_FILES} files at a time."));
        }
        let dir = self.scratch.join(uuid::Uuid::new_v4().to_string());
        tokio::fs::create_dir_all(&dir).await.map_err(|e| format!("Cannot make a folder for the files: {e}"))?;
        let mut paths = Vec::new();
        for f in &files {
            let name = safe_name(&f.name)?;
            let data = base64::engine::general_purpose::STANDARD.decode(f.data_b64.as_bytes()).map_err(|_| format!("'{}' is not valid base64.", f.name))?;
            if data.len() > MAX_FILE {
                return Err(format!("'{}' is larger than {} MB.", f.name, MAX_FILE / 1024 / 1024));
            }
            let path = dir.join(&name);
            tokio::fs::write(&path, &data).await.map_err(|e| format!("Cannot write '{name}': {e}"))?;
            paths.push(path);
        }
        let opener = Arc::clone(&self.opener);
        let to_open = paths.clone();
        tokio::task::spawn_blocking(move || opener.open(&to_open)).await.map_err(|e| e.to_string())??;
        // ProPresenter copies a song into its own library when it opens it; the file is not needed after that.
        let cleanup = dir.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_secs(600)).await;
            let _ = tokio::fs::remove_dir_all(cleanup).await;
        });
        Ok(json!({"opened": paths.len()}))
    }
}

/// A file name that is one plain `.pro` name: no folders, no dots at the start, an extension ProPresenter imports.
pub fn safe_name(name: &str) -> Result<String, String> {
    let bad = name.is_empty() || name.len() > 200 || name.starts_with('.') || name.contains(['/', '\\', '\0', ':']) || name.chars().any(char::is_control);
    if bad {
        return Err(format!("'{name}' is not a file name the helper will write."));
    }
    let ext = Path::new(name).extension().and_then(|e| e.to_str()).unwrap_or("").to_ascii_lowercase();
    if !EXTENSIONS.contains(&ext.as_str()) {
        return Err(format!("'{name}' is not a kind of file the helper opens in ProPresenter (.pro only)."));
    }
    Ok(name.to_string())
}

/// Only ProPresenter's versioned API, and only a plain path: the helper must not become a way to reach other
/// services on this computer.
pub fn check_api_path(path: &str) -> Result<(), String> {
    let ok = path.starts_with("/v1/")
        && !path.contains("..")
        && !path.contains("//")
        && !path.contains('#')
        && !path.contains('@')
        && path.chars().all(|c| c.is_ascii_graphic());
    if ok {
        Ok(())
    } else {
        Err("A ProPresenter API path must start with /v1/ and be a plain path.".to_string())
    }
}

async fn http(url: &str, method: &str, body: Option<String>) -> Result<(u16, Value), String> {
    let (url, method) = (url.to_string(), method.to_string());
    tokio::task::spawn_blocking(move || {
        let agent: ureq::Agent = ureq::Agent::config_builder().timeout_global(Some(Duration::from_secs(10))).http_status_as_error(false).build().into();
        let mut req = ureq::http::Request::builder().method(method.as_str()).uri(&url);
        if body.is_some() {
            req = req.header("Content-Type", "application/json");
        }
        let req = req.body(body.unwrap_or_default()).map_err(|_| "That is not a valid ProPresenter API call.".to_string())?;
        let mut resp = agent.run(req).map_err(|_| "ProPresenter's network API did not answer.".to_string())?;
        let status = resp.status().as_u16();
        let text = resp.body_mut().read_to_string().map_err(|_| "ProPresenter's answer could not be read.".to_string())?;
        let parsed = serde_json::from_str(&text).unwrap_or(Value::String(text));
        Ok((status, parsed))
    })
    .await
    .map_err(|e| e.to_string())?
}

impl Capability for ProPresenter {
    fn id(&self) -> &'static str {
        "propresenter"
    }
    fn version(&self) -> u32 {
        1
    }
    fn ops(&self) -> &'static [&'static str] {
        &["status", "open_files", "api"]
    }
    fn call<'a>(&'a self, op: &'a str, args: Value) -> CallFuture<'a> {
        Box::pin(async move {
            match op {
                "status" => self.status().await,
                "open_files" => self.open_files(args).await,
                "api" => self.api(args).await,
                _ => Err(format!("Unknown operation '{op}'.")),
            }
        })
    }
}

impl std::fmt::Debug for ProPresenter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProPresenter").field("scratch", &self.scratch).finish_non_exhaustive()
    }
}
