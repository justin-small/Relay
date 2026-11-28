//! OBS Studio (#250; ported from `app/modules/obs` in #668): scene, streaming and recording state, output statistics, scene switching (also through
//! studio mode, a tablet screen and slide tags), start and stop of the stream, recording and replay buffer, audio meters and mute, a preview on the admin
//! page, health alerts, tally, and closed captions into the stream (#140), over obs-websocket 5. A module (#157): switched on and off at run time; while off
//! it contacts OBS never and adds no route, page or stream entry.
//!
//! One held connection per OBS computer (a stream PC and a record PC, say) does all of it, and each publishes its own entry in the shared stream status
//! that the status view shows. Relay only ever talks out to the address the operator typed, through the `obs` traffic class's network port.

// axum's `Response` is the error half of route helpers: the shape of the Python it ports.
#![allow(clippy::result_large_err)]

pub const KEY: &str = "obs";

pub mod machine;
pub mod proto;
pub mod routes;
pub mod service;
pub mod settings;
pub mod web;

use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use axum::Router;
use relay_core::caps::{TagAction, STREAM_STARTED, STREAM_STOPPED, TAG_ACTION};
use relay_core::future::BoxFuture;
use relay_core::{AdminPage, AppError, Health, HealthState, Host, Manifest, Module, Permission};
use serde_json::Value;

use crate::machine::Timing;
use crate::routes::{Ctx, Guard};
use crate::service::Service;

pub const MANIFEST: Manifest = Manifest {
    id: "obs",
    name: "OBS Studio",
    version: "1.1.0",
    summary: "Shows whether OBS is streaming and recording and how healthy it is, switches scenes, and sends captions into the stream.",
    settings_key: KEY,
    guide: "https://github.com/justin-small/relay#obs-studio",
    uses: &[],
    route_prefixes: &["/api/admin/obs", "/obs"],
    renamed_from: "",
    section: "Show",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission { id: "obs.view", label: "See OBS's scene, outputs and preview", group: "OBS Studio", min_role: "viewer" },
    Permission { id: "obs.control", label: "Switch OBS scenes, start and stop the stream and recording, and mute audio", group: "OBS Studio", min_role: "operator" },
    Permission { id: "obs.manage", label: "Set up OBS computers, captions and scene screens", group: "OBS Studio", min_role: "admin" },
];

const PAGES: &[AdminPage] = &[AdminPage {
    id: "obs",
    title: "OBS Studio",
    template: "obs.html",
    icon: r##"<circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="1.5"/>"##,
    permission: "",
    tab_of: "",
}];

pub struct Obs {
    ctx: OnceLock<Ctx>,
    timing: Mutex<Timing>,
}

impl Default for Obs {
    fn default() -> Self {
        Self::new()
    }
}

impl Obs {
    pub fn new() -> Self {
        Self { ctx: OnceLock::new(), timing: Mutex::new(Timing::default()) }
    }

    /// With shorter waits (a test makes the poll and the backoff milliseconds, not seconds).
    #[must_use]
    pub fn with_timing(self, timing: Timing) -> Self {
        *self.timing.lock().unwrap_or_else(PoisonError::into_inner) = timing;
        self
    }

    /// The service and the scene screen's limits, once attached.
    pub fn ctx(&self) -> Option<&Ctx> {
        self.ctx.get()
    }
}

impl Module for Obs {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn defaults(&self) -> Value {
        settings::default_settings()
    }

    fn secret_settings(&self) -> &'static [&'static str] {
        &["passwords"]
    }

    fn permissions(&self) -> &'static [Permission] {
        PERMISSIONS
    }

    fn clean(&self, raw: &Value) -> Value {
        settings::clean_settings(raw)
    }

    fn admin_pages(&self) -> &'static [AdminPage] {
        PAGES
    }

    fn admin_scripts(&self) -> &'static [&'static str] {
        &["obs.js"]
    }

    fn admin_styles(&self) -> &'static [&'static str] {
        &["obs.css"]
    }

    fn routes(&self) -> Router {
        match self.ctx.get() {
            Some(c) => routes::router(c.clone()),
            None => Router::new(),
        }
    }

    fn attach(&self, host: &Host) {
        let timing = self.timing.lock().unwrap_or_else(PoisonError::into_inner).clone();
        let _ = self.ctx.set(Ctx { host: host.clone(), service: Service::new(host.clone(), timing), guard: Arc::new(Guard::default()) });
    }

    fn detach(&self, host: &Host) {
        host.withdraw_all(MANIFEST.id);
    }

    fn start(&self, host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            let Some(c) = self.ctx.get() else { return Ok(()) };
            // A slide-note tag `obs=<scene>`: every connected computer that has the scene switches to it.
            let service = Arc::clone(&c.service);
            host.provide(
                MANIFEST.id,
                &format!("{TAG_ACTION}obs"),
                Arc::new(TagAction(Box::new(move |value: Option<String>| {
                    let service = Arc::clone(&service);
                    Box::pin(async move {
                        let name = value.unwrap_or_default();
                        let name = name.trim();
                        if name.is_empty() {
                            return Err("Name the scene after obs=, like [relay:obs=Message].".to_owned());
                        }
                        service.switch_scene(name, "a ProPresenter slide tag").await.map(|()| format!("OBS switched to {name}."))
                    })
                }))),
            );
            // Relay's Start and Stop: each computer begins, and later ends, what it is set to begin with them.
            let service = Arc::clone(&c.service);
            host.listen(MANIFEST.id, STREAM_STARTED, Arc::new(move |_: &str| service.stream_started()));
            let service = Arc::clone(&c.service);
            host.listen(MANIFEST.id, STREAM_STOPPED, Arc::new(move |_: &str| service.stream_stopped()));
            c.service.start().await;
            Ok(())
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            if let Some(c) = self.ctx.get() {
                c.service.stop().await; // also clears each computer's stream status and tally
                c.host.withdraw_all(MANIFEST.id);
            }
        })
    }

    fn health(&self) -> Health {
        let Some(c) = self.ctx.get() else { return Health::idle() };
        let (level, detail) = c.service.health();
        Health::new(
            match level {
                "error" => HealthState::Error,
                "warn" => HealthState::Warn,
                "ok" => HealthState::Ok,
                _ => HealthState::Idle,
            },
            detail,
        )
    }
}
