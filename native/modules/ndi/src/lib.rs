//! NDI (#294; ported from `app/modules/ndi` in #658): finds NDI sources on the network and shares one receiver per source with everything
//! in Relay that uses NDI, with the frames never entering Python.
//!
//! This first slice has the settings (every key and limit of the Python module), discovery (mDNS and the SDK's finder), the NDI runtime
//! and the sources list. The receiver pool, viewer, multiviews, analysis, router, test patterns, outputs and the bus input and output
//! follow in the next slices; until they do the Python module serves them.

#![allow(clippy::result_large_err)]

pub mod listen;
pub mod viewer;
pub mod watch;
pub mod font;
pub mod render;
pub mod testpatterns;
pub mod discovery;
pub mod analysis;
pub mod bus;
pub mod multiview;
pub mod outputs;
pub mod pool;
pub mod router;
pub mod service;
pub mod settings;

mod routes;

use std::sync::{Arc, Mutex, OnceLock};

use relay_core::future::BoxFuture;
use relay_core::{AppError, Health, HealthState, Host, Manifest, Module, Permission};
use serde_json::Value;

use crate::service::Service;

pub const MANIFEST: Manifest = Manifest {
    id: "ndi",
    name: "NDI",
    version: "1.0.0",
    summary: "Finds NDI video sources on the network (cameras, converters, software) and shares one receiver per source with everything in Relay that uses NDI.",
    settings_key: "ndi",
    guide: "/docs/ndi",
    uses: &["blackmagic", "obs", "tally_arbiter"],
    route_prefixes: &["/api/admin/ndi", "/ndi"],
    renamed_from: "",
    section: "Show",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission { id: "ndi.view", label: "See the NDI sources found on the network", group: "NDI", min_role: "viewer" },
    Permission { id: "ndi.manage", label: "Scan for NDI sources and set the extra addresses and groups to search", group: "NDI", min_role: "admin" },
];

pub type Slot = Arc<Mutex<Option<Arc<Service>>>>;

/// Until the rest of the module is ported, the Rust NDI runs only when asked (`RELAY_RUST_NDI=1`): otherwise the Python module keeps
/// answering `/api/admin/ndi` and owns the NDI runtime, and two programs must not both hold it.
pub const SWITCH: &str = "RELAY_RUST_NDI";

pub struct Ndi {
    host: OnceLock<Host>,
    slot: Slot,
    live: bool,
}

impl Default for Ndi {
    fn default() -> Self {
        Self::new()
    }
}

impl Ndi {
    pub fn new() -> Self {
        Self::switched(std::env::var(SWITCH).is_ok_and(|v| v != "0" && !v.eq_ignore_ascii_case("false") && !v.is_empty()))
    }

    /// A module that serves its routes and starts only when `live`.
    pub fn switched(live: bool) -> Self {
        Self { host: OnceLock::new(), slot: Slot::default(), live }
    }

    /// The slot the routes read the service from.
    pub fn slot(&self) -> &Slot {
        &self.slot
    }

    /// A module whose service a test built.
    pub fn with_service(host: Host, service: Service) -> Self {
        let me = Self::switched(true);
        let _ = me.host.set(host);
        if let Ok(mut s) = me.slot.lock() {
            *s = Some(Arc::new(service));
        }
        me
    }
}

impl Module for Ndi {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn permissions(&self) -> &'static [Permission] {
        PERMISSIONS
    }

    fn defaults(&self) -> Value {
        settings::defaults()
    }

    fn clean(&self, raw: &Value) -> Value {
        settings::clean_settings(raw)
    }

    fn routes(&self) -> axum::Router {
        match self.host.get() {
            Some(host) if self.live => routes::router(host.clone(), Arc::clone(&self.slot)),
            _ => axum::Router::new(),
        }
    }

    fn attach(&self, host: &Host) {
        let _ = self.host.set(host.clone());
    }

    fn start(&self, host: Host, settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            if !self.live {
                return Ok(());
            }
            let service = Arc::new(Service::new(host, settings));
            let started = service.start();
            if let Ok(mut s) = self.slot.lock() {
                *s = Some(Arc::clone(&service));
            }
            started
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            let service = self.slot.lock().ok().and_then(|mut s| s.take());
            if let Some(service) = service {
                service.stop().await;
            }
        })
    }

    fn health(&self) -> Health {
        let Some(service) = self.slot.lock().ok().and_then(|s| s.clone()) else { return Health::idle() };
        service.health()
    }
}

/// Nothing to say about a state that is not running.
pub(crate) const NOT_RUNNING: Health = Health::new(HealthState::Warn, "Off");
