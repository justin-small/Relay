//! The module registry (#652): the list of modules compiled into the server, each switched on and off at run time.
//!
//! The registry is core. `relay-server` builds it from the list in its `modules.rs`; no crate other than the server names a
//! module crate. The switch lives in config.json at `modules.<id>.enabled`, exactly where Python keeps it.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex};

use axum::extract::{Request, State};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::Router;

use crate::config::Config;
use crate::error::{AppError, ErrorCode};
use crate::module::{Host, HealthState, Listener, Module, TAB_PAGES};
use serde_json::{json, Value};

/// A listener of a module that is on.
#[derive(Clone, Debug)]
pub struct ActiveListener {
    pub module: &'static str,
    pub module_name: &'static str,
    pub listener: Listener,
    /// The port from the module's settings.
    pub port: u16,
}

/// The sidebar headings, in order (`NAV_SECTIONS` in `app/modules/__init__.py`). A module whose section is not here is under "Modules".
pub const NAV_SECTIONS: [&str; 6] = ["Show", "Crew", "Screens", "Sound", "Building", "Modules"];

/// What the admin page needs from the modules that are on, for one signed-in user (Python's `admin_nav`, `admin_tabs`,
/// `admin_scripts` and `admin_styles`), as the JSON `admin.html` is rendered with.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct AdminView {
    /// One sidebar entry per module with a page the user may see, in `NAV_SECTIONS` order.
    pub nav: Vec<Value>,
    /// `{core page id: [tab, ...]}`.
    pub tabs: BTreeMap<String, Vec<Value>>,
    /// `<module id>/<file>` for each script of a module with a page the user may see.
    pub scripts: Vec<String>,
    pub styles: Vec<String>,
}

pub fn health_word(state: HealthState) -> &'static str {
    match state {
        HealthState::Ok => "ok",
        HealthState::Warn => "warn",
        HealthState::Error => "error",
        HealthState::Idle => "idle",
    }
}

pub struct Registry {
    modules: Vec<Arc<dyn Module>>,
    config: Arc<Config>,
    host: Host,
    running: Mutex<BTreeSet<&'static str>>,
}

/// An id is 2 to 32 lowercase letters, digits and underscores, starting with a letter (Python's `_ID_RE`).
pub fn valid_id(id: &str) -> bool {
    let b = id.as_bytes();
    (2..=32).contains(&b.len()) && b[0].is_ascii_lowercase() && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_')
}

impl Registry {
    pub fn new(config: Arc<Config>, host: Host) -> Self {
        host.default_store(&config);
        Self { modules: Vec::new(), config, host, running: Mutex::default() }
    }

    /// Add a module. Fails on a bad or repeated id, or a route prefix that is not a path.
    pub fn register(&mut self, module: Arc<dyn Module>) -> Result<(), AppError> {
        let m = module.manifest();
        if !valid_id(m.id) {
            return Err(AppError::new(ErrorCode::Internal).because(format!("module id {:?} is not valid", m.id)));
        }
        if self.modules.iter().any(|o| o.manifest().id == m.id) {
            return Err(AppError::new(ErrorCode::Internal).because(format!("module id {:?} is registered twice", m.id)));
        }
        if m.route_prefixes.iter().any(|p| !p.starts_with('/')) {
            return Err(AppError::new(ErrorCode::Internal).because(format!("module {:?} has a route prefix that is not a path", m.id)));
        }
        for field in module.secret_settings() {
            self.host.add_secret(m.settings_key, field);
        }
        module.attach(&self.host);
        self.modules.push(module);
        Ok(())
    }

    /// The modules, in the order they were registered.
    pub fn modules(&self) -> &[Arc<dyn Module>] {
        &self.modules
    }

    pub fn ids(&self) -> Vec<&'static str> {
        self.modules.iter().map(|m| m.manifest().id).collect()
    }

    pub fn enabled(&self, id: &str) -> bool {
        self.config.module_enabled(id)
    }

    pub fn running(&self, id: &str) -> bool {
        self.running.lock().map(|r| r.contains(id)).unwrap_or(false)
    }

    fn find(&self, id: &str) -> Option<&Arc<dyn Module>> {
        self.modules.iter().find(|m| m.manifest().id == id)
    }

    /// Switch a module on or off now, and remember the choice in config.json.
    pub async fn set_enabled(&self, id: &str, on: bool) -> Result<(), AppError> {
        let module = self.find(id).ok_or_else(|| AppError::new(ErrorCode::NotFound))?;
        self.config.set_module_enabled(id, on)?;
        if on {
            self.start_one(module).await
        } else {
            self.stop_one(module).await;
            Ok(())
        }
    }

    async fn start_one(&self, module: &Arc<dyn Module>) -> Result<(), AppError> {
        let m = module.manifest();
        if self.running(m.id) {
            return Ok(());
        }
        let settings = module.clean(&self.host.settings(m.settings_key).unwrap_or_else(|| module.defaults()));
        module.start(self.host.clone(), settings).await?;
        if let Ok(mut r) = self.running.lock() {
            r.insert(m.id);
        }
        tracing::info!(module = m.id, "module started");
        Ok(())
    }

    async fn stop_one(&self, module: &Arc<dyn Module>) {
        let id = module.manifest().id;
        let was = self.running.lock().map(|mut r| r.remove(id)).unwrap_or(false);
        if was {
            module.stop().await;
            tracing::info!(module = id, "module stopped");
        }
    }

    /// Start every module that is on. One that fails to start is logged and left stopped; the rest still start.
    pub async fn start_enabled(&self) {
        for module in &self.modules {
            if self.enabled(module.manifest().id) {
                if let Err(e) = self.start_one(module).await {
                    tracing::error!(module = module.manifest().id, "module did not start: {e}");
                }
            }
        }
    }

    /// Stop every running module, in reverse order of registration, and detach them all.
    pub async fn stop_all(&self) {
        for module in self.modules.iter().rev() {
            self.stop_one(module).await;
            module.detach(&self.host);
            self.host.withdraw_all(module.manifest().id);
        }
    }

    /// Every module's routes in one router. A route answers 404 while its module is off (Python's `require_enabled`).
    pub fn routes(self: &Arc<Self>) -> Router {
        let mut app = Router::new();
        for module in &self.modules {
            let id = module.manifest().id;
            let gated = module.routes().layer(middleware::from_fn_with_state((Arc::clone(self), id), require_enabled));
            app = app.merge(gated);
        }
        app
    }

    /// Each listener of the modules that are on, with the port from the module's settings (Python's `modules.listeners()`).
    pub fn listeners(&self) -> Vec<ActiveListener> {
        let mut out = Vec::new();
        for module in &self.modules {
            let m = module.manifest();
            if !self.enabled(m.id) {
                continue;
            }
            let settings = module.clean(&self.config.settings(m.settings_key).unwrap_or_else(|| module.defaults()));
            for listener in module.listeners() {
                let port = listener.port(&settings);
                out.push(ActiveListener { module: m.id, module_name: m.name, listener, port });
            }
        }
        out
    }

    /// Every path prefix a module serves only on a listener of its own, whether the module is on or not.
    pub fn listener_prefixes(&self) -> Vec<String> {
        self.modules.iter().flat_map(|m| m.listeners()).flat_map(|l| l.prefixes.iter().map(|p| (*p).to_owned())).collect()
    }

    /// The admin pages, scripts and styles of the modules that are on, for a user who holds the permissions `can` says yes to.
    /// A module with no page the user may see adds nothing, so a sidebar entry exists only for someone who may open it.
    pub fn admin_view(&self, can: &dyn Fn(&str) -> bool) -> AdminView {
        let mut view = AdminView::default();
        for module in &self.modules {
            let m = module.manifest();
            if !self.enabled(m.id) {
                continue;
            }
            let pages = module.admin_pages();
            let perm = |p: &crate::module::AdminPage| if p.permission.is_empty() { format!("{}.view", m.id) } else { p.permission.to_owned() };
            let allowed: Vec<_> = pages.iter().filter(|p| can(&perm(p))).collect();
            let page_json = |p: &crate::module::AdminPage| {
                json!({"id": p.id, "title": p.title, "icon": p.icon, "template": format!("{}/{}", m.id, p.template),
                       "module": m.name, "version": m.version, "guide": m.guide})
            };
            let entries: Vec<Value> = allowed.iter().filter(|p| p.tab_of.is_empty()).map(|p| page_json(p)).collect();
            for p in allowed.iter().filter(|p| TAB_PAGES.contains(&p.tab_of)) {
                view.tabs.entry(p.tab_of.to_owned()).or_default().push(json!({
                    "id": p.id, "title": p.title, "template": format!("{}/{}", m.id, p.template), "module": m.name,
                    "module_id": m.id, "version": m.version, "guide": m.guide}));
            }
            if !entries.is_empty() {
                let health = module.health();
                let section = if NAV_SECTIONS.contains(&m.section) { m.section } else { "Modules" };
                view.nav.push(json!({"id": m.id, "name": m.name, "section": section, "icon": entries[0]["icon"],
                    "health": health_word(health.state), "detail": health.detail, "pages": entries, "tabbed": module.admin_tabbed()}));
            }
            let sees_module = if pages.is_empty() { can(&format!("{}.view", m.id)) } else { !allowed.is_empty() };
            if sees_module {
                view.scripts.extend(module.admin_scripts().iter().map(|s| format!("{}/{s}", m.id)));
                view.styles.extend(module.admin_styles().iter().map(|s| format!("{}/{s}", m.id)));
            }
        }
        view.nav.sort_by_key(|e| NAV_SECTIONS.iter().position(|s| Some(*s) == e["section"].as_str()));
        view
    }

    /// Which modules are on, for a status read: `id -> (enabled, running)`.
    pub fn states(&self) -> BTreeMap<&'static str, (bool, bool)> {
        self.ids().into_iter().map(|id| (id, (self.enabled(id), self.running(id)))).collect()
    }
}

async fn require_enabled(State((registry, id)): State<(Arc<Registry>, &'static str)>, request: Request, next: Next) -> Response {
    if registry.enabled(id) {
        next.run(request).await
    } else {
        AppError::new(ErrorCode::NotFound).into_response()
    }
}
