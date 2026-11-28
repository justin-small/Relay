//! Dante (#257; ported from `app/modules/dante` in #674): finds the Dante devices on the network (stage boxes, Dante Virtual Soundcard, Dante Via),
//! lists their transmit channels, checks that Relay can reach each one, reads their clocks, subscriptions and live levels (from each device's multicast
//! heartbeat, or by asking a hardware box to push its meters), watches for what goes wrong, and lets a permitted operator change a receive channel's
//! subscription, lock channels Relay must never change, undo the last change and save, compare and apply routing presets. A module (#157): switched on and
//! off at run time; while off it sends nothing, listens to nothing and adds no route, page or stream entry.
//!
//! Everything it sends leaves through the `dante` traffic class's network port (`Host::net()`): multicast DNS questions, the control-port check (UDP), the
//! devices' heartbeat (multicast 224.0.0.233, port 8708) and the meter requests to a device's port 8800 (the device then pushes to our UDP port 8751).
//! Renaming a device and the rest of what Dante Controller does stay there.

// axum's `Response` is the error half of route helpers: the shape of the Python it ports.
#![allow(clippy::result_large_err)]

pub const KEY: &str = "dante";

pub mod arc;
pub mod clock;
pub mod heartbeat;
pub mod inputs;
pub mod inventory;
pub mod mdns;
pub mod metering;
pub mod monitor;
pub mod routes;
pub mod routing;
pub mod service;
pub mod walk;
pub mod web;

use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use axum::Router;
use relay_core::caps::{DeviceList, GearFeed, InputLabels, NoSoundSteps, DANTE_ROUTING, GEAR_DANTE, INPUT_LABELS, NOSOUND_DANTE};
use relay_core::future::BoxFuture;
use relay_core::{AdminPage, AppError, Health, HealthState, Host, Manifest, Module, Permission};
use serde_json::Value;
use sqlx::SqlitePool;

use crate::routes::Ctx;
use crate::service::{Service, Tuning};

pub const MANIFEST: Manifest = Manifest {
    id: "dante",
    name: "Dante",
    version: "1.0.0",
    summary: "Finds the Dante devices on the network (stage boxes, Dante Virtual Soundcard, Dante Via), lists their transmit channels, checks that Relay can reach each one and changes which channel each receive channel listens to, with confirmation, undo and saved presets.",
    settings_key: KEY,
    guide: "https://github.com/justin-small/relay#dante",
    uses: &[],
    route_prefixes: &["/api/admin/dante"],
    renamed_from: "",
    section: "Sound",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission { id: "dante.view", label: "See the Dante devices Relay found and whether they answer", group: "Dante", min_role: "viewer" },
    Permission { id: "dante.control", label: "Change which Dante channel each receive channel listens to, and undo it", group: "Dante", min_role: "operator" },
    Permission { id: "dante.manage", label: "Look for Dante devices again, star channels, lock receive channels and save routing presets", group: "Dante", min_role: "admin" },
];

const PAGES: &[AdminPage] = &[AdminPage {
    id: "dante",
    title: "Dante",
    template: "dante.html",
    icon: r##"<rect x="2" y="3" width="12" height="4" rx="1"/><rect x="2" y="9" width="12" height="4" rx="1"/><path d="M5 5h.01M5 11h.01"/>"##,
    permission: "",
    tab_of: "",
}];

/// The tables Python's migrations made, applied the first time the module starts under the namespace `dante` (the same rows `db.migrate` writes).
const MIGRATIONS: [(&str, &str); 2] = [
    ("0001_monitor", include_str!("../../../../app/modules/dante/migrations/0001_monitor.sql")),
    ("0002_routing", include_str!("../../../../app/modules/dante/migrations/0002_routing.sql")),
];

pub struct Dante {
    ctx: OnceLock<Ctx>,
    tuning: Mutex<Tuning>,
}

impl Default for Dante {
    fn default() -> Self {
        Self::new()
    }
}

impl Dante {
    pub fn new() -> Self {
        Self { ctx: OnceLock::new(), tuning: Mutex::new(Tuning::default()) }
    }

    /// With other waits and ports (a test turns the scan, the probe and the listening ports to what a loopback device answers on).
    #[must_use]
    pub fn with_tuning(self, tuning: Tuning) -> Self {
        *self.tuning.lock().unwrap_or_else(PoisonError::into_inner) = tuning;
        self
    }

    /// The service, once attached.
    pub fn service(&self) -> Option<&Arc<Service>> {
        self.ctx.get().map(|c| &c.service)
    }
}

/// Make the module's tables and remember that it did (`schema_migrations`, as `db.migrate("dante", ...)` does).
pub async fn migrate(pool: &SqlitePool) -> Result<(), sqlx::Error> {
    sqlx::query("create table if not exists schema_migrations (namespace text not null, version text not null, applied_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), primary key (namespace, version))")
        .execute(pool)
        .await?;
    let mut tx = pool.begin().await?;
    let done: Vec<String> = sqlx::query_scalar("select version from schema_migrations where namespace = 'dante'").fetch_all(&mut *tx).await?;
    for (version, sql) in MIGRATIONS {
        if done.iter().any(|d| d == version) {
            continue;
        }
        sqlx::raw_sql(sql).execute(&mut *tx).await?;
        sqlx::query("insert into schema_migrations (namespace, version) values ('dante', ?)").bind(version).execute(&mut *tx).await?;
    }
    tx.commit().await
}

impl Module for Dante {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn permissions(&self) -> &'static [Permission] {
        PERMISSIONS
    }

    /// The module keeps nothing in config.json: everything it remembers is in the database.
    fn clean(&self, _raw: &Value) -> Value {
        serde_json::json!({})
    }

    fn admin_pages(&self) -> &'static [AdminPage] {
        PAGES
    }

    fn admin_scripts(&self) -> &'static [&'static str] {
        &["dante.js"]
    }

    fn routes(&self) -> Router {
        match self.ctx.get() {
            Some(c) => routes::router(c.clone()),
            None => Router::new(),
        }
    }

    fn attach(&self, host: &Host) {
        let tuning = self.tuning.lock().unwrap_or_else(PoisonError::into_inner).clone();
        let _ = self.ctx.set(Ctx { host: host.clone(), service: Service::new(host.clone(), tuning) });
    }

    fn detach(&self, host: &Host) {
        host.withdraw_all(MANIFEST.id);
    }

    fn start(&self, host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            let Some(c) = self.ctx.get() else { return Ok(()) };
            // History, stars, locks and presets live in relay.db. Without it they stay in memory (history) or are not kept, and the rest still works.
            match host.using::<SqlitePool>(relay_core::screens::DB) {
                Some(pool) => match migrate(&pool).await {
                    Ok(()) => c.service.monitor.use_database(Some((*pool).clone())),
                    Err(_) => {
                        tracing::warn!("dante: its tables could not be made, so history stays in memory");
                        c.service.monitor.use_database(None);
                    }
                },
                None => c.service.monitor.use_database(None),
            }
            self.offer(&host, &c.service);
            c.service.start();
            Ok(())
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            if let Some(c) = self.ctx.get() {
                c.service.stop().await;
                c.host.withdraw_all(MANIFEST.id);
            }
        })
    }

    fn health(&self) -> Health {
        let Some(c) = self.ctx.get() else { return Health::idle() };
        let s = c.service.snapshot();
        let n = |k: &str| s[k].as_u64().unwrap_or(0);
        if !s["error"].as_str().unwrap_or("").is_empty() {
            return Health::new(HealthState::Error, "The Dante network port is not available");
        }
        if s["devices"].as_array().is_none_or(Vec::is_empty) {
            return Health::new(HealthState::Warn, "Looking: no Dante devices found yet");
        }
        if s["problems"].as_array().is_some_and(|p| !p.is_empty()) {
            return Health::new(HealthState::Error, "A Dante subscription, clock or device has a problem");
        }
        if n("unreachable") > 0 {
            return Health::new(HealthState::Warn, "Some devices did not answer on their control port");
        }
        Health::new(HealthState::Ok, "Dante devices found")
    }
}

impl Dante {
    /// What the module offers the rest of Relay while it is on: the gear for the network map, the input picker's labels, the devices read-only for the
    /// signal map, and the Dante steps of the "No sound?" walk.
    fn offer(&self, host: &Host, s: &Arc<Service>) {
        let feed = Arc::clone(s);
        host.provide(MANIFEST.id, GEAR_DANTE, Arc::new(GearFeed(Box::new(move || feed.gear_list()))));
        let labels = Arc::clone(s);
        host.provide(MANIFEST.id, INPUT_LABELS, Arc::new(InputLabels(Box::new(move |inputs: &[Value]| inputs::labels(inputs, &labels.devices_copy(), &labels.local_addresses())))));
        let list = Arc::clone(s);
        host.provide(MANIFEST.id, DANTE_ROUTING, Arc::new(DeviceList(Box::new(move || list.devices_copy()))));
        let walk = Arc::clone(s);
        host.provide(MANIFEST.id, NOSOUND_DANTE, Arc::new(NoSoundSteps { order: 10, steps: Box::new(move |audio: &Value| walk::steps(audio, &walk.devices_copy(), &walk.local_addresses())) }));
    }
}
