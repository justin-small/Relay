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
use crate::module::{Host, Listener, Module};

/// A listener of a module that is on.
#[derive(Clone, Debug)]
pub struct ActiveListener {
    pub module: &'static str,
    pub module_name: &'static str,
    pub listener: Listener,
    /// The port from the module's settings.
    pub port: u16,
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
        module.attach(&self.host);
        self.modules.push(module);
        Ok(())
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
        let settings = module.clean(&self.config.settings(m.settings_key).unwrap_or_else(|| module.defaults()));
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
