//! Digital signage (#204, #205; ported from `app/modules/signage` in #660): turn any TV with a browser into a signage screen.
//!
//! Operators upload images and videos, build playlists, and give each screen an address, `/signage/<slug>`, on a plain-HTTP port of its
//! own (8090 by default), so a smart TV needs no login and no certificate. Screens can be put in groups that share a playlist, and
//! players pick up every change live (a server-sent event stream) and keep playing what they have when Relay restarts.
//!
//! What stays in the app until the modules around it are ported (#661): the admin page and its script (the Rust module trait has no admin
//! pages yet), items from other modules' sources (ProPresenter slides, Planning Center media, YouTube: they are skipped), the takeover and
//! tag layers other modules provide, and offering each screen to the NDI module as an output.

// axum's `Response` is the error half of every route helper (the answer to send instead), and the store functions take the columns of one
// row: both are the shape of the Python they port, so the size and argument-count lints are not applied here.
#![allow(clippy::result_large_err, clippy::too_many_arguments, clippy::type_complexity)]

pub mod live;
pub mod media;
pub mod schedule;
pub mod store;
pub mod takeover;

mod routes;

use std::path::PathBuf;
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use relay_core::module::BoxFuture;
use relay_core::screens::{ScreensCap, DB, SCREENS};
use relay_core::{AppError, ErrorCode, Health, HealthState, Host, Listener, Manifest, Module, Permission};
use serde_json::{json, Value};

use crate::live::Live;
use crate::media::Media;
use crate::store::Store;

pub const DEFAULT_PORT: u16 = 8090;
const PRUNE_EVERY: Duration = Duration::from_secs(6 * 3600);

pub const MANIFEST: Manifest = Manifest {
    id: "signage",
    name: "Signage",
    version: "1.1.0",
    summary: "Plays your images and videos on any TV with a web browser, each screen at its own address.",
    settings_key: "signage",
    guide: "https://github.com/justin-small/relay#signage",
    uses: &[],
    route_prefixes: &["/signage", "/api/admin/signage"],
    renamed_from: "",
    section: "Screens",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission { id: "signage.view", label: "See signage screens, playlists and media", group: "Signage", min_role: "viewer" },
    Permission { id: "signage.manage", label: "Upload media and change signage screens, groups and playlists", group: "Signage", min_role: "admin" },
];

pub(crate) fn rand_hex(bytes: usize) -> String {
    use rand::Rng;
    let mut rng = rand::thread_rng();
    (0..bytes).map(|_| format!("{:02x}", rng.gen::<u8>())).collect()
}

pub(crate) fn timewin_iso(epoch: i64) -> String {
    relay_core::timewin::iso(epoch)
}

/// What the routes need once the module is on: the store, and the folder the player script is served from.
pub struct Service {
    pub store: Store,
    pub app_dir: PathBuf,
}

pub type Slot = Arc<Mutex<Option<Arc<Service>>>>;

pub struct Signage {
    host: OnceLock<Host>,
    slot: Slot,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
}

impl Default for Signage {
    fn default() -> Self {
        Self::new()
    }
}

impl Signage {
    pub fn new() -> Self {
        Self { host: OnceLock::new(), slot: Slot::default(), task: Mutex::new(None) }
    }

    /// The slot the routes read the service from (a test reads what `with_service` put there).
    pub fn slot(&self) -> &Slot {
        &self.slot
    }

    /// The service a test builds itself, in place of the one `start` makes.
    pub fn with_service(host: Host, service: Service) -> Self {
        let me = Self::new();
        let _ = me.host.set(host);
        if let Ok(mut s) = me.slot.lock() {
            *s = Some(Arc::new(service));
        }
        me
    }
}

fn clean_settings(raw: &Value) -> Value {
    let raw = raw.as_object().cloned().unwrap_or_default();
    let port = match raw.get("port") {
        Some(Value::Number(n)) => n.as_i64().unwrap_or(0),
        Some(Value::String(s)) => s.trim().parse().unwrap_or(0),
        _ => 0,
    };
    let port = if port == 0 { i64::from(DEFAULT_PORT) } else { port };
    let groups: Vec<i64> = match raw.get("alert_groups") {
        Some(Value::Array(a)) => {
            let mut g: Vec<i64> = a.iter().filter_map(|v| v.as_i64().filter(|n| *n > 0)).collect();
            g.sort_unstable();
            g.dedup();
            g.truncate(200);
            g
        }
        _ => vec![],
    };
    let alert = raw.get("alert_screens").and_then(Value::as_str).filter(|a| ["all", "groups", "none"].contains(a)).unwrap_or("all");
    let until = raw.get("override_until").and_then(Value::as_str).filter(|a| ["next_change", "cleared"].contains(a)).unwrap_or("next_change");
    json!({"port": if (1024..=65535).contains(&port) { port } else { i64::from(DEFAULT_PORT) }, "alert_screens": alert, "alert_groups": groups, "override_until": until})
}

impl Module for Signage {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn permissions(&self) -> &'static [Permission] {
        PERMISSIONS
    }

    fn defaults(&self) -> Value {
        json!({"port": DEFAULT_PORT, "override_until": "next_change", "alert_screens": "all", "alert_groups": []})
    }

    fn clean(&self, raw: &Value) -> Value {
        clean_settings(raw)
    }

    fn routes(&self) -> axum::Router {
        match self.host.get() {
            Some(host) => routes::router(host.clone(), Arc::clone(&self.slot)),
            None => axum::Router::new(),
        }
    }

    fn listeners(&self) -> Vec<Listener> {
        vec![Listener { name: "screens", label: "signage screens", port_setting: "port", default_port: DEFAULT_PORT, prefixes: &["/signage"], traffic: "viewers" }]
    }

    fn attach(&self, host: &Host) {
        let _ = self.host.set(host.clone());
    }

    fn start(&self, host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            let internal = |why: &'static str| AppError::new(ErrorCode::Internal).because(why);
            let pool = host.using::<sqlx::SqlitePool>(DB).ok_or_else(|| internal("the server gave signage no database"))?;
            let screens = host.using::<ScreensCap>(SCREENS).ok_or_else(|| internal("the server gave signage no screens"))?;
            let state = relay_core::paths::state_dir();
            let backups = std::env::var("RELAY_DB_BACKUPS").ok().filter(|v| !v.trim().is_empty()).map_or_else(|| state.join("backups"), PathBuf::from);
            let app_dir = host.using::<relay_core::screens::AppDir>(relay_core::screens::APP_DIR).map_or_else(|| PathBuf::from("app"), |a| a.0.clone());
            let store = Store::open((*pool).clone(), Arc::clone(&screens.0), Arc::new(Live::default()), host.clone(), Media::new(&state, &backups)).await.map_err(|_| internal("signage's tables could not be opened"))?;
            let service = Arc::new(Service { store, app_dir });
            if let Ok(mut s) = self.slot.lock() {
                *s = Some(Arc::clone(&service));
            }
            let shutdown = host.shutdown.clone();
            let handle = tokio::spawn(async move {
                loop {
                    housekeeping(&service).await;
                    tokio::select! {
                        () = shutdown.wait() => break,
                        () = tokio::time::sleep(PRUNE_EVERY) => {}
                    }
                }
            });
            if let Ok(mut t) = self.task.lock() {
                *t = Some(handle);
            }
            Ok(())
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            let task = self.task.lock().ok().and_then(|mut t| t.take());
            if let Some(t) = task {
                t.abort();
                let _ = t.await;
            }
        })
    }

    fn health(&self) -> Health {
        let Some(host) = self.host.get() else { return Health::idle() };
        let port = host.settings("signage").and_then(|s| s["port"].as_i64()).unwrap_or(i64::from(DEFAULT_PORT));
        let _ = port;
        Health::new(HealthState::Ok, "Screens are served on the signage port")
    }
}

/// Copy any media the backups folder lacks and prune copies nothing names; end takeovers and overrides that ran out.
async fn housekeeping(service: &Service) {
    let st = &service.store;
    let work = async {
        let rows = st.media_list().await?;
        let pairs: Vec<(String, String)> = rows.iter().map(|r| (r["sha256"].as_str().unwrap_or("").to_owned(), r["ext"].as_str().unwrap_or("").to_owned())).collect();
        st.media.mirror_missing(&pairs);
        st.media.prune_mirror(&pairs.iter().map(|p| p.0.clone()).collect::<Vec<_>>());
        st.sweep_overrides().await?;
        st.takeover_sweep().await
    };
    if let Err(e) = work.await {
        tracing::warn!("signage housekeeping did not finish: {e:?}");
    }
}

