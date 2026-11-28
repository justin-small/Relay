//! What the server hands modules through `Host` (#662): their settings with secrets opened and sealed, and the accounts that
//! decide who may call a gated route.

use std::sync::Arc;

use axum::http::request::Parts;
use axum::response::Response;
use relay_core::future::BoxFuture;
use relay_core::{AppError, Authorizer, SettingsStore};
use serde_json::{json, Value};

use crate::accounts::Shared;
use crate::forward::Conn;

/// Settings read and saved through `relay_config::Settings`, which opens a secret on the way in and seals it on the way out.
pub struct SealedStore(pub Arc<relay_config::Settings>);

impl SettingsStore for SealedStore {
    fn read(&self, settings_key: &str) -> Option<Value> {
        self.0.get().ok()?.get(settings_key).cloned()
    }

    fn save(&self, settings_key: &str, changes: &Value) -> Result<(), AppError> {
        self.0.save(&json!({ settings_key: changes }), &[]).map(|_| ())
    }

    fn replace(&self, settings_key: &str, value: &Value) -> Result<(), AppError> {
        self.0.save(&json!({ settings_key: value }), &[settings_key]).map(|_| ())
    }

    fn add_secret(&self, settings_key: &str, field: &str) {
        self.0.add_secret(settings_key, field);
    }
}

/// The accounts, as the module routes' guard.
pub struct AccountsAuthorizer(pub Shared);

impl Authorizer for AccountsAuthorizer {
    fn require<'a>(&'a self, request: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async move { self.0.require(&request.headers, request.extensions.get::<Conn>().cloned(), permission).await })
    }

    fn note<'a>(&'a self, request: &'a Parts, action: &'a str, summary: &'a str, target: &'a str, detail: Option<Value>) -> BoxFuture<'a, ()> {
        Box::pin(async move { self.write(request, action, summary, target, detail).await })
    }
}

impl AccountsAuthorizer {
    async fn write(&self, request: &Parts, action: &str, summary: &str, target: &str, detail: Option<Value>) {
        if let Ok(caller) = self.0.caller(&request.headers, request.extensions.get::<Conn>().cloned(), None).await {
            self.0.record(&caller, action, summary, target, detail).await;
        }
    }
}

/// Register every module's permissions with the role table, so the Users page lists them and `can` knows them.
pub fn register_permissions(modules: &[Arc<dyn relay_core::Module>]) -> Result<(), String> {
    for module in modules {
        let perms: Vec<relay_auth::perms::Permission> = module
            .permissions()
            .iter()
            .map(|p| relay_auth::perms::Permission {
                id: p.id.into(),
                label: p.label.into(),
                group: p.group.into(),
                min_role: p.min_role.into(),
            })
            .collect();
        relay_auth::perms::register(&perms).map_err(|e| format!("module {}: {e}", module.manifest().id))?;
    }
    Ok(())
}
