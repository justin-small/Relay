//! config.json, read and written in place (#652). The file is the Python server's: same path, same keys, same sealed secrets,
//! so an install upgrades with no migration step and can go back.
//!
//! The rules that keep the two servers compatible:
//!
//! * The file is held as a [`serde_json::Value`] with key order kept (`preserve_order`), so a key this server does not know
//!   (a newer Python's, or a module that is not compiled in) is written back as it was, in the same place.
//! * A secret is `enc:v1:<kid>:<data>`; this layer neither opens nor changes one. Opening them is #656's job, with the
//!   machine key from the OS store (`app/secretbox.py`). A save writes the sealed text back untouched.
//! * A save is whole-file and atomic: a temp file beside it, owner-only, renamed over. A reader never sees half a file.
//! * `modules.<id>.enabled` is the on/off switch. A module with no switch is off here; the Python server writes every
//!   module's switch at startup (docs/modules.md, "Upgrades"), so an install that has run Python has them all.

use std::path::{Path, PathBuf};
use std::sync::Mutex;

use serde_json::{Map, Value};

use crate::error::{AppError, ErrorCode};

pub struct Config {
    path: PathBuf,
    data: Mutex<Value>,
}

impl Config {
    /// Read config.json. A missing file is an empty config (a fresh install); a file that is not a JSON object is an error
    /// the server stops on, rather than overwriting what the operator wrote.
    pub fn load(path: &Path) -> Result<Self, AppError> {
        let data = match std::fs::read_to_string(path) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(v) if v.is_object() => v,
                Ok(_) => return Err(AppError::new(ErrorCode::Internal).because("config.json is not a JSON object")),
                Err(e) => return Err(AppError::new(ErrorCode::Internal).because(format!("config.json is not valid JSON: {e}"))),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Value::Object(Map::new()),
            Err(e) => return Err(AppError::new(ErrorCode::Internal).because(format!("config.json could not be read: {e}"))),
        };
        Ok(Self { path: path.to_path_buf(), data: Mutex::new(data) })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    /// A copy of one top-level key.
    pub fn get(&self, key: &str) -> Option<Value> {
        self.data.lock().ok()?.get(key).cloned()
    }

    /// A module's settings (its `settings_key`), if any were saved.
    pub fn settings(&self, key: &str) -> Option<Value> {
        self.get(key).filter(Value::is_object)
    }

    pub fn module_enabled(&self, id: &str) -> bool {
        self.data
            .lock()
            .ok()
            .and_then(|d| d.get("modules")?.get(id)?.get("enabled")?.as_bool())
            .unwrap_or(false)
    }

    pub fn set_module_enabled(&self, id: &str, on: bool) -> Result<(), AppError> {
        self.update(|root| {
            let modules = root.entry("modules").or_insert_with(|| Value::Object(Map::new()));
            if !modules.is_object() {
                *modules = Value::Object(Map::new());
            }
            if let Some(m) = modules.as_object_mut() {
                let entry = m.entry(id.to_owned()).or_insert_with(|| Value::Object(Map::new()));
                if let Some(e) = entry.as_object_mut() {
                    e.insert("enabled".to_owned(), Value::Bool(on));
                }
            }
        })
    }

    /// Change the top-level object and save it. The change is kept in memory only if the save worked.
    pub fn update(&self, change: impl FnOnce(&mut Map<String, Value>)) -> Result<(), AppError> {
        let mut data = self.data.lock().map_err(|_| AppError::new(ErrorCode::Internal).because("config lock poisoned"))?;
        // Start from the file as it is now, not from what was read at start-up: `relay-config`'s `Settings` (#656) saves this same
        // file, and a change made from a stale copy would undo whatever it saved since.
        let mut next = std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .filter(Value::is_object)
            .unwrap_or_else(|| data.clone());
        if let Some(root) = next.as_object_mut() {
            change(root);
        }
        write_atomic(&self.path, &next)?;
        *data = next;
        Ok(())
    }
}

fn write_atomic(path: &Path, value: &Value) -> Result<(), AppError> {
    let fail = |what: &str, e: std::io::Error| AppError::new(ErrorCode::Internal).because(format!("config.json: {what}: {e}"));
    let text = serde_json::to_string_pretty(value).map_err(|e| AppError::new(ErrorCode::Internal).because(e))?;
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        std::fs::create_dir_all(dir).map_err(|e| fail("make the folder", e))?;
    }
    let tmp = path.with_extension("json.tmp");
    std::fs::write(&tmp, text).map_err(|e| fail("write", e))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600)).map_err(|e| fail("restrict", e))?;
    }
    std::fs::rename(&tmp, path).map_err(|e| fail("replace", e))
}
