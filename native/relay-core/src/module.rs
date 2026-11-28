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
use std::sync::atomic::{AtomicU16, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

use axum::Router;
use serde_json::Value;

use crate::future::BoxFuture;
use crate::error::AppError;
use crate::access::{Authorizer, Permission};
use crate::net::Net;
use crate::store::SettingsStore;
use crate::shutdown::Shutdown;


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

/// A page the module adds to the admin panel, shown only while the module is on (Python's `AdminPage`, #719).
#[derive(Clone, Copy, Debug)]
pub struct AdminPage {
    /// The `#hash` and `data-page` name; unique across Relay.
    pub id: &'static str,
    /// The nav label.
    pub title: &'static str,
    /// A template in the module's `templates/` folder, rendered as `<module id>/<template>`.
    pub template: &'static str,
    /// Inner SVG for the 16x16 nav icon; the module's sidebar entry uses its first page's.
    pub icon: &'static str,
    /// The permission needed to see the page; empty is `<module id>.view`.
    pub permission: &'static str,
    /// A core page this one is a tab of instead of a sidebar entry (`"network"` or `"signage"`, [`TAB_PAGES`]); empty is none.
    pub tab_of: &'static str,
}

/// The core pages that take a module's page as a tab.
pub const TAB_PAGES: &[&str] = &["network", "signage"];

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

/// Called by [`Host::announce`] with the event's argument (`""` when it has none).
pub type Hear = Arc<dyn Fn(&str) + Send + Sync>;

/// `(owner, topic, listener)`: who listens to what (Python's `modules.listen`).
type Heard = Arc<Mutex<Vec<(String, String, Hear)>>>;

/// What core hands a module: where it asks for other modules' capabilities and offers its own, and the shutdown signal.
/// The port the viewer pages are served on, known once the server has opened it. The server keeps one handle and sets it; the
/// [`Host`] it was given to reads it.
#[derive(Clone, Default)]
pub struct ViewerPort(Arc<AtomicU16>);

impl ViewerPort {
    pub fn set(&self, port: u16) {
        self.0.store(port, Ordering::Relaxed);
    }

    pub fn get(&self) -> u16 {
        self.0.load(Ordering::Relaxed)
    }
}

#[derive(Clone)]
pub struct Host {
    shared: Provided,
    heard: Heard,
    pub shutdown: Shutdown,
    net: Net,
    store: Arc<OnceLock<Arc<dyn SettingsStore>>>,
    authorizer: Arc<OnceLock<Arc<dyn Authorizer>>>,
    viewer_port: ViewerPort,
}

impl Host {
    pub fn new(shutdown: Shutdown) -> Self {
        Self { shared: Arc::default(), heard: Arc::default(), shutdown, net: Net::unrouted(), store: Arc::default(), authorizer: Arc::default(), viewer_port: ViewerPort::default() }
    }

    /// The port the viewer pages are served on right now (`serving.viewer_port`), 0 until the server has opened it.
    pub fn viewer_port(&self) -> u16 {
        self.viewer_port.get()
    }

    /// Read the viewer port from `port`, which the server sets.
    pub fn with_viewer_port(mut self, port: ViewerPort) -> Self {
        self.viewer_port = port;
        self
    }

    /// Open and seal module secrets with the server's store (built before the registry, so it is in place for `attach`).
    pub fn with_store(self, store: Arc<dyn SettingsStore>) -> Self {
        let _ = self.store.set(store);
        self
    }

    /// The store used when the server gave none: settings as stored on disk. Called by `Registry::new`.
    pub(crate) fn default_store(&self, config: &Arc<crate::config::Config>) {
        let _ = self.store.set(Arc::new(crate::store::PlainStore(Arc::clone(config))));
    }

    /// The server hands over its accounts once they exist; until then every gated route answers 401.
    pub fn set_authorizer(&self, authorizer: Arc<dyn Authorizer>) {
        let _ = self.authorizer.set(authorizer);
    }

    pub fn authorizer(&self) -> Option<Arc<dyn Authorizer>> {
        self.authorizer.get().cloned()
    }

    /// Write an audit entry for the caller of `request` (the accounts decide who that is). A request nobody signed in for writes nothing.
    pub async fn note(&self, request: &axum::http::request::Parts, action: &str, summary: &str, target: &str, detail: Option<Value>) {
        if let Some(a) = self.authorizer() {
            a.note(request, action, summary, target, detail).await;
        }
    }

    /// Write an audit entry for something the module did on its own, with nobody signed in behind it (`audit.record`).
    pub async fn record(&self, action: &str, summary: &str, target: &str) {
        if let Some(a) = self.authorizer() {
            a.record(action, summary, target).await;
        }
    }

    /// This module's settings, secrets opened. `None` when the install has none saved.
    pub fn settings(&self, settings_key: &str) -> Option<Value> {
        self.store.get()?.read(settings_key)
    }

    /// Save changes to the module's settings (secrets sealed on disk).
    pub fn save_settings(&self, settings_key: &str, changes: &Value) -> Result<(), AppError> {
        match self.store.get() {
            Some(store) => store.save(settings_key, changes),
            None => Err(AppError::new(crate::error::ErrorCode::Internal).because("no settings store")),
        }
    }

    /// Overwrite this module's whole settings object (secrets sealed on disk). `save_settings` merges, so a key could never be removed with it.
    pub fn replace_settings(&self, settings_key: &str, value: &Value) -> Result<(), AppError> {
        match self.store.get() {
            Some(store) => store.replace(settings_key, value),
            None => Err(AppError::new(crate::error::ErrorCode::Internal).because("no settings store")),
        }
    }

    pub(crate) fn add_secret(&self, settings_key: &str, field: &str) {
        if let Some(store) = self.store.get() {
            store.add_secret(settings_key, field);
        }
    }

    /// Use `net` for every module's outbound traffic (the server's router; `Host::new` has none, so classes are unrouted).
    pub fn with_net(mut self, net: Net) -> Self {
        self.net = net;
        self
    }

    /// Outbound connections and UDP sockets, routed by traffic class. A module opens no socket of its own.
    pub fn net(&self) -> &Net {
        &self.net
    }

    /// Offer `value` under `name` (`"<area>.<what>"`). `owner` is the module's id; `withdraw_all` takes it back.
    pub fn provide<T: Any + Send + Sync>(&self, owner: &str, name: &str, value: Arc<T>) {
        if let Ok(mut map) = self.shared.lock() {
            map.insert(name.to_owned(), (owner.to_owned(), value));
        }
    }

    /// Everything provided under a name that starts with `prefix`, as `(name without the prefix, value)`, for the ones of type `T`.
    pub fn using_all<T: Any + Send + Sync>(&self, prefix: &str) -> Vec<(String, Arc<T>)> {
        let Ok(map) = self.shared.lock() else { return Vec::new() };
        map.iter().filter_map(|(name, (_, v))| Some((name.strip_prefix(prefix)?.to_owned(), Arc::clone(v).downcast::<T>().ok()?))).collect()
    }

    /// Take back everything `owner` provided and every event it listens to.
    pub fn withdraw_all(&self, owner: &str) {
        if let Ok(mut map) = self.shared.lock() {
            map.retain(|_, (who, _)| who != owner);
        }
        if let Ok(mut heard) = self.heard.lock() {
            heard.retain(|(who, _, _)| who != owner);
        }
    }

    /// Be called when `announce(topic, ..)` is (`plan.changed`, `directory.changed`): a module hears another's events with no
    /// import and no matter which came on first. `withdraw_all(owner)` stops it. A listener runs on the announcer's thread and
    /// must not block; it spawns what takes time.
    pub fn listen(&self, owner: &str, topic: &str, listener: Hear) {
        if let Ok(mut heard) = self.heard.lock() {
            heard.push((owner.to_owned(), topic.to_owned(), listener));
        }
    }

    /// Tell everyone listening to `topic`. `arg` is the event's one argument (a provider id), `""` for none.
    pub fn announce(&self, topic: &str, arg: &str) {
        let hearers: Vec<Hear> = match self.heard.lock() {
            Ok(heard) => heard.iter().filter(|(_, t, _)| t == topic).map(|(_, _, f)| Arc::clone(f)).collect(),
            Err(_) => return,
        };
        for f in hearers {
            f(arg);
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

    /// The permissions this module adds (Python's `Module.permissions`); the server registers them with the role table.
    fn permissions(&self) -> &'static [Permission] {
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

    /// The pages it adds to the admin panel. The server lists them in the sidebar only while the module is on, and only to
    /// someone who holds the page's permission.
    fn admin_pages(&self) -> &'static [AdminPage] {
        &[]
    }

    /// Scripts in the module's `static/` the admin page loads for it.
    fn admin_scripts(&self) -> &'static [&'static str] {
        &[]
    }

    /// Stylesheets in the module's `static/` the admin page loads for it.
    fn admin_styles(&self) -> &'static [&'static str] {
        &[]
    }

    /// True: the module's pages are one sidebar entry and tabs across the top of each page, not a submenu.
    fn admin_tabbed(&self) -> bool {
        false
    }

    /// The names of the event feeds the module owns, answered on the viewer port's `/stream?stream=<name>` (Python's `Module.streams`).
    fn streams(&self) -> &'static [&'static str] {
        &[]
    }

    /// The feed `name` for this request, or `None` when the module has no such feed or refuses it (Python's `Module.stream`).
    fn stream(&self, _name: &str, _query: &BTreeMap<String, String>, _headers: &axum::http::HeaderMap) -> Option<axum::response::Response> {
        None
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
