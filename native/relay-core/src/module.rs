//! The module trait (#652). Python's `Module` (`app/modules/__init__.py`, `docs/modules.md`) in Rust: a manifest, settings,
//! routes, listeners, `attach`/`detach` and `start`/`stop`, switched on and off at run time.
//!
//! What a module is allowed to do, and what the registry guarantees:
//!
//! * `attach` runs once at startup (even while the module is off): offer what the module provides by name
//!   (`Host::provide`), and nothing that runs. `detach` undoes it.
//! * `start` runs only while the module is on, and every background task it spawns ends when `stop` returns or the
//!   [`Shutdown`] is set. A module that is off carries no code path: its routes answer 404 (the registry wraps them), it
//!   adds nothing to a status, and `start` is never called.
//! * It reaches another module only through `Host::using`, which gives `None` while that module is off or has not
//!   provided the name. A module crate never depends on another module crate.

use std::any::Any;
use std::collections::BTreeMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};

use axum::Router;
use serde_json::Value;

use crate::error::AppError;
use crate::shutdown::Shutdown;

pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// What a module is. `id` is its crate's name without the `relay-module-` prefix and its key under config `"modules"`.
#[derive(Clone, Debug)]
pub struct Manifest {
    pub id: &'static str,
    pub name: &'static str,
    /// `major.minor.patch`; bumped when the module changes.
    pub version: &'static str,
    pub summary: &'static str,
    /// The config.json key holding the module's own settings (where Python keeps them, so an upgrade moves no data).
    pub settings_key: &'static str,
    /// A README anchor or a docs/ path, shown next to the settings.
    pub guide: &'static str,
    /// Other modules this one does more with when they are on too. It never imports them; it asks `Host::using`.
    pub uses: &'static [&'static str],
    /// Every route the module adds is under one of these.
    pub route_prefixes: &'static [&'static str],
    /// The id this module had before it was renamed: settings, switch and routes saved under it move on load (#432).
    pub renamed_from: &'static str,
    /// The admin sidebar heading it is listed under; empty is "Modules".
    pub section: &'static str,
}

/// A plain-HTTP port of the module's own (#205): only its routes under `prefixes` answer on it.
#[derive(Clone, Debug)]
pub struct Listener {
    pub name: &'static str,
    pub label: &'static str,
    pub port_setting: &'static str,
    pub default_port: u16,
    pub prefixes: &'static [&'static str],
    /// The traffic class whose network port it listens on (`relay-server`'s `netroute`): a core class (`"viewers"`, `"crew"`)
    /// or one the module declares. Unrouted, it listens on every interface as before. Python's default is `"viewers"`.
    pub traffic: &'static str,
}

impl Listener {
    /// The port to open: the module's setting `port_setting`, or `default_port` when it is missing or not a port.
    pub fn port(&self, settings: &Value) -> u16 {
        settings
            .get(self.port_setting)
            .and_then(Value::as_u64)
            .filter(|p| (1..=65535).contains(p))
            .and_then(|p| u16::try_from(p).ok())
            .unwrap_or(self.default_port)
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum HealthState {
    Ok,
    Warn,
    Error,
    Idle,
}

#[derive(Clone, Debug)]
pub struct Health {
    pub state: HealthState,
    /// A fixed sentence the module wrote, never an error's text.
    pub detail: &'static str,
}

impl Health {
    pub const fn new(state: HealthState, detail: &'static str) -> Self {
        Self { state, detail }
    }
    pub const fn idle() -> Self {
        Self::new(HealthState::Idle, "")
    }
}

type Provided = Arc<Mutex<BTreeMap<String, (String, Arc<dyn Any + Send + Sync>)>>>;

/// What core hands a module: where it asks for other modules' capabilities and offers its own, and the shutdown signal.
#[derive(Clone)]
pub struct Host {
    shared: Provided,
    pub shutdown: Shutdown,
}

impl Host {
    pub fn new(shutdown: Shutdown) -> Self {
        Self { shared: Arc::default(), shutdown }
    }

    /// Offer `value` under `name` (`"<area>.<what>"`). `owner` is the module's id; `withdraw_all` takes it back.
    pub fn provide<T: Any + Send + Sync>(&self, owner: &str, name: &str, value: Arc<T>) {
        if let Ok(mut map) = self.shared.lock() {
            map.insert(name.to_owned(), (owner.to_owned(), value));
        }
    }

    pub fn withdraw_all(&self, owner: &str) {
        if let Ok(mut map) = self.shared.lock() {
            map.retain(|_, (who, _)| who != owner);
        }
    }

    /// What another module provides under `name`, or `None` while it is off or offers something else. Ask at the moment
    /// of need, not in `attach`, so it does not matter which module attached first.
    pub fn using<T: Any + Send + Sync>(&self, name: &str) -> Option<Arc<T>> {
        let map = self.shared.lock().ok()?;
        let (_, value) = map.get(name)?;
        Arc::clone(value).downcast::<T>().ok()
    }
}

pub trait Module: Send + Sync + 'static {
    fn manifest(&self) -> &Manifest;

    /// The settings a fresh install has, under `settings_key`.
    fn defaults(&self) -> Value {
        Value::Object(serde_json::Map::new())
    }

    /// Settings fields holding a secret (sealed in config.json; stripped from what an API returns).
    fn secret_settings(&self) -> &'static [&'static str] {
        &[]
    }

    /// Clean what the operator saved: right types, in range, unknown keys dropped. Never fails; a bad value becomes the default.
    fn clean(&self, raw: &Value) -> Value {
        raw.clone()
    }

    /// The routes it adds. The registry mounts them behind the on/off switch.
    fn routes(&self) -> Router {
        Router::new()
    }

    fn listeners(&self) -> Vec<Listener> {
        Vec::new()
    }

    fn attach(&self, _host: &Host) {}

    fn detach(&self, _host: &Host) {}

    /// Begin work. Called only while the module is on. `settings` is its cleaned settings.
    fn start(&self, host: Host, settings: Value) -> BoxFuture<'_, Result<(), AppError>>;

    /// Undo everything `start` began, so switching off at run time leaves nothing running.
    fn stop(&self) -> BoxFuture<'_, ()>;

    fn health(&self) -> Health {
        Health::idle()
    }
}
