//! A module's settings and secrets (#662): read with the secrets opened, saved with them sealed.
//!
//! `Config` (core) leaves a secret sealed (`enc:v1:...`), because opening one needs the machine key, which is `relay-config`'s.
//! A module therefore never reads `Config` directly: it asks `Host::settings` and saves with `Host::save_settings`, and the
//! server's store opens and seals the fields the module named in `secret_settings`. [`PlainStore`] is the fallback with no
//! key (tests, and a server that has not handed its store over): it reads and writes the file as it is.

use std::sync::Arc;

use serde_json::Value;

use crate::config::Config;
use crate::error::AppError;

pub trait SettingsStore: Send + Sync + 'static {
    /// The object at `settings_key` in config.json with its secrets opened, or `None` when there is none.
    fn read(&self, settings_key: &str) -> Option<Value>;

    /// Merge `changes` into the object at `settings_key`; a key named in `replace` is overwritten, not merged. Secrets are sealed.
    fn save(&self, settings_key: &str, changes: &Value) -> Result<(), AppError>;

    /// Overwrite the whole object at `settings_key` with `value` (what a module that removes a key needs: `save` only merges).
    fn replace(&self, settings_key: &str, value: &Value) -> Result<(), AppError>;

    /// `field` inside `settings_key` is a secret: sealed on disk, opened by `read`.
    fn add_secret(&self, settings_key: &str, field: &str);
}

/// Settings read and written as stored.
pub struct PlainStore(pub Arc<Config>);

impl SettingsStore for PlainStore {
    fn read(&self, settings_key: &str) -> Option<Value> {
        self.0.settings(settings_key)
    }

    fn save(&self, settings_key: &str, changes: &Value) -> Result<(), AppError> {
        let Some(changes) = changes.as_object() else { return Ok(()) };
        self.0.update(|root| {
            let slot = root.entry(settings_key.to_owned()).or_insert_with(|| Value::Object(serde_json::Map::new()));
            if let Some(object) = slot.as_object_mut() {
                for (k, v) in changes {
                    object.insert(k.clone(), v.clone());
                }
            }
        })
    }

    fn replace(&self, settings_key: &str, value: &Value) -> Result<(), AppError> {
        self.0.update(|root| {
            root.insert(settings_key.to_owned(), value.clone());
        })
    }

    fn add_secret(&self, _settings_key: &str, _field: &str) {}
}
