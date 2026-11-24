//! The NDI module's state (`service.py`): the sources found, kept on the list when they go quiet, and the scan loop that keeps them current.
//!
//! A source seen in the last `GONE_AFTER` scans is online; one not seen stays on the list, offline, so a camera that is switched off is still
//! there when it comes back. `version` goes up on every change; the page long-polls it.

use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};

use relay_core::lanes::{LanesCap, LANES};
use relay_core::routes::{Routes, RoutesCap, ROUTES};
use relay_core::screens::{PageOutputs, NDI_OUTPUTS};
use relay_core::{AppError, ErrorCode, Health, HealthState, Host};
use serde_json::{json, Value};

use crate::discovery::{self, Found};
use crate::settings::{self, split_server};
use crate::analysis::{self, Analyzer};
use crate::pool::{Changes, Lease, Pool, LOW};
use crate::bus::{Driver, Guard};
use crate::outputs::{self, Maker, Outputs};
use crate::testpatterns::{self, Tests};
use crate::router::{self, Deps, Router};
use crate::multiview::{Clock, Env, Multiviews};
use crate::viewer::{Limits, Viewers};
use crate::watch::{Ctx, Watch};
use relay_ndi::watch::Kind;
use crate::NOT_RUNNING;

pub const GONE_AFTER: u32 = 2;
pub const MAX_FOUND_VIA_SDK: usize = discovery::MAX_FOUND;

/// What the NDI runtime says when it is missing (the Python module's sentence).
pub const NO_RUNTIME: &str = "The NDI runtime is not installed. Install it from ndi.video, then restart Relay.";

/// An analysis the admin page has open: its receiver stays held while the page keeps asking.
struct Open {
    _lease: Lease,
    expires: f64,
}

pub const ANALYSIS_HOLD_S: f64 = 30.0;

#[derive(Clone, Debug)]
struct Seen {
    found: Found,
    missed: u32,
    width: u32,
    height: u32,
    fps: f64,
    audio_only: bool,
}

pub struct Service {
    host: Host,
    settings: Arc<Mutex<Value>>,
    seen: Mutex<BTreeMap<String, Seen>>,
    servers: Mutex<Vec<Value>>,
    pub changes: Arc<Changes>,
    pub pool: Arc<Pool>,
    pub analysis: Arc<Analyzer>,
    open: Mutex<HashMap<String, Open>>,
    health: Mutex<HashMap<String, String>>,
    pub viewers: Viewers,
    pub multiviews: Multiviews,
    pub watch: Watch,
    pub pages: Outputs,
    pub listeners: crate::listen::Listeners,
    pub tests: Tests,
    maker: Arc<dyn Maker>,
    driver: Mutex<Option<Arc<Driver>>>,
    driver_task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    router: Mutex<Option<Router>>,
    running: AtomicBool,
    scanning: AtomicBool,
    last_scan: Mutex<f64>,
    via_server: AtomicU64,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    health_task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    error: Mutex<String>,
}

fn now_secs() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

impl Service {
    pub fn new(host: Host, settings: Value) -> Self {
        Self::with_pool(host, settings, None)
    }

    /// A service whose receivers are opened by `pool` (a test gives its own); `None` uses the NDI runtime.
    pub fn with_pool(host: Host, settings: Value, pool: Option<Arc<Pool>>) -> Self {
        Self::with_parts(host, settings, pool, Arc::new(outputs::Real))
    }

    /// A service whose page outputs are drawn and sent by `maker` (a test gives its own).
    pub fn with_parts(host: Host, settings: Value, pool: Option<Arc<Pool>>, maker: Arc<dyn Maker>) -> Self {
        let changes = Arc::new(Changes::default());
        let analysis = Arc::new(Analyzer::new(relay_core::paths::state_dir().join("ndi").join("analysis")));
        let pool = pool.unwrap_or_else(|| Pool::new(Arc::clone(&changes), Some(Arc::clone(&analysis))));
        let clean = settings::clean_settings(&settings);
        let shared = Arc::new(Mutex::new(clean.clone()));
        let limits: Arc<dyn Fn() -> Limits + Send + Sync> = Arc::new({
            let shared = Arc::clone(&shared);
            move || Limits::from_settings(&shared.lock().map(|s| s.clone()).unwrap_or_default())
        });
        let viewers = Viewers::new(Arc::clone(&pool), Arc::clone(&limits));
        let multiviews = Multiviews::new(Env {
            pool: Arc::clone(&pool),
            analysis: Arc::clone(&analysis),
            limits,
            settings: Arc::new({
                let shared = Arc::clone(&shared);
                move || shared.lock().map(|s| s.clone()).unwrap_or_default()
            }),
            clock: Arc::new({
                let host = host.clone();
                move || local_clock(&host)
            }),
        });
        let kept = Arc::clone(&maker);
        let tests = Tests::new(testpatterns::Deps {
            maker: Arc::clone(&maker),
            settings: Arc::new({
                let shared = Arc::clone(&shared);
                move || shared.lock().map(|s| s.clone()).unwrap_or_default()
            }),
            folder: Arc::new(|| relay_core::paths::state_dir().join("ndi")),
            clock: Arc::new({
                let host = host.clone();
                move || local_clock(&host)
            }),
            notify: Arc::new({
                let changes = Arc::clone(&changes);
                move || changes.bump()
            }),
        });
        let pages = Outputs::new(outputs::Deps {
            maker,
            settings: Arc::new({
                let shared = Arc::clone(&shared);
                move || shared.lock().map(|s| s.clone()).unwrap_or_default()
            }),
            config: Arc::new({
                let host = host.clone();
                move |key| host.settings(key)
            }),
            providers: Arc::new({
                let host = host.clone();
                move || host.using_all::<PageOutputs>(NDI_OUTPUTS).into_iter().flat_map(|(_, p)| (p.0)()).collect()
            }),
            base: Arc::new({
                let host = host.clone();
                move || format!("http://127.0.0.1:{}", if host.viewer_port() == 0 { 80 } else { host.viewer_port() })
            }),
            notify: Arc::new({
                let changes = Arc::clone(&changes);
                move || changes.bump()
            }),
        });
        Self {
            host,
            settings: shared,
            changes,
            pool,
            analysis,
            open: Mutex::default(),
            health: Mutex::default(),
            viewers,
            multiviews,
            watch: Watch::new(),
            pages,
            listeners: crate::listen::Listeners::default(),
            tests,
            maker: kept,
            driver: Mutex::default(),
            driver_task: Mutex::default(),
            router: Mutex::default(),
            seen: Mutex::default(),
            servers: Mutex::default(),
            running: AtomicBool::new(false),
            scanning: AtomicBool::new(false),
            last_scan: Mutex::new(0.0),
            via_server: AtomicU64::new(0),
            task: Mutex::default(),
            health_task: Mutex::default(),
            error: Mutex::default(),
        }
    }

    pub fn settings(&self) -> Value {
        self.settings.lock().map(|s| s.clone()).unwrap_or(Value::Null)
    }

    /// Write the SDK config and point the SDK at it, then load the runtime; the scan loop begins when it loaded.
    pub fn start(self: &std::sync::Arc<Self>) -> Result<(), AppError> {
        let folder = relay_core::paths::state_dir().join("ndi");
        if std::fs::create_dir_all(&folder).is_ok() {
            let cfg = settings::sdk_config(&self.settings());
            let _ = std::fs::write(folder.join("ndi-config.v1.json"), serde_json::to_vec_pretty(&cfg).unwrap_or_default());
            if std::env::var_os("NDI_CONFIG_DIR").is_none() {
                // SAFETY: set once at module start, before any thread reads it through the NDI runtime, which is not loaded yet.
                unsafe { std::env::set_var("NDI_CONFIG_DIR", &folder) };
            }
        }
        if let Err(why) = relay_ndi::api() {
            tracing::info!("NDI runtime load failed: {why}");
            if let Ok(mut e) = self.error.lock() {
                *e = NO_RUNTIME.to_owned();
            }
            return Err(AppError::new(ErrorCode::Unavailable).because("the NDI runtime is not installed"));
        }
        self.running.store(true, Ordering::SeqCst);
        self.start_router();
        self.start_bus();
        let pages = Arc::clone(self);
        tokio::spawn(async move { pages.pages.sync().await });
        let t = Arc::clone(self);
        tokio::task::spawn_blocking(move || t.tests.sync());
        let me = std::sync::Arc::clone(self);
        let shutdown = self.host.shutdown.clone();
        let handle = tokio::spawn(async move {
            loop {
                me.scan().await;
                let wait = me.settings()["interval"].as_u64().unwrap_or(15);
                tokio::select! {
                    () = shutdown.wait() => break,
                    () = tokio::time::sleep(std::time::Duration::from_secs(wait)) => {}
                }
            }
        });
        let me = std::sync::Arc::clone(self);
        let shutdown = self.host.shutdown.clone();
        let health = tokio::spawn(async move {
            loop {
                tokio::select! {
                    () = shutdown.wait() => break,
                    () = tokio::time::sleep(std::time::Duration::from_secs(2)) => {}
                }
                let me2 = std::sync::Arc::clone(&me);
                let _ = tokio::task::spawn_blocking(move || me2.health_tick()).await;
            }
        });
        if let Ok(mut t) = self.task.lock() {
            *t = Some(handle);
        }
        if let Ok(mut t) = self.health_task.lock() {
            *t = Some(health);
        }
        Ok(())
    }

    pub async fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
        let task = self.task.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = task {
            t.abort();
            let _ = t.await;
        }
        self.stop_router();
        self.stop_bus().await;
        let health = self.health_task.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = health {
            t.abort();
            let _ = t.await;
        }
        let (viewers, pool) = (&self.viewers, Arc::clone(&self.pool));
        let watch_pool = Arc::clone(&self.pool);
        let _ = &watch_pool;
        self.pages.close().await;
        self.tests.close();
        self.watch.stop();
        self.close_analyses();
        self.multiviews.close_all();
        viewers.close_all();
        let _ = tokio::task::spawn_blocking(move || pool.close_all()).await;
        self.analysis.clear();
        if let Ok(mut s) = self.seen.lock() {
            s.clear();
        }
        if let Ok(mut s) = self.servers.lock() {
            s.clear();
        }
        self.bump();
    }

    /// Whether `source` is on the list and online.
    pub fn known(&self, source: &str) -> bool {
        self.seen.lock().is_ok_and(|s| s.get(source).is_some_and(|r| r.missed < GONE_AFTER))
    }

    // ------------------------------------------------------------------------------------- analysis (#300)
    /// Start (or keep) analysing a source: a receiver is held for it, and its scopes and meters are computed while the page keeps asking.
    /// Returns the analysis key, or `None` when it cannot be received.
    pub fn analysis_open(&self, source: &str, low: bool) -> Option<String> {
        let key = if low { format!("{source}{LOW}") } else { source.to_owned() };
        let until = analysis::now() + ANALYSIS_HOLD_S;
        let mut open = self.open.lock().ok()?;
        if let Some(cur) = open.get_mut(&key) {
            cur.expires = until;
            return Some(key);
        }
        let lease = self.pool.acquire(source, low, Arc::new(|_| {}), None)?;
        open.insert(key.clone(), Open { _lease: lease, expires: until });
        drop(open);
        self.analysis.watch(&key, true);
        Some(key)
    }

    pub fn analysis_is_open(&self, key: &str) -> bool {
        self.open.lock().is_ok_and(|o| o.contains_key(key))
    }

    pub fn analysis_close(&self, key: &str) {
        let gone = self.open.lock().ok().and_then(|mut o| o.remove(key));
        if gone.is_some() {
            self.analysis.watch(key, false);
        }
    }

    pub fn close_analyses(&self) {
        let keys: Vec<String> = self.open.lock().map(|o| o.keys().cloned().collect()).unwrap_or_default();
        for k in keys {
            self.analysis_close(&k);
        }
    }

    pub fn analysis_snapshot(&self, key: &str, want_scopes: bool) -> Value {
        let source = key.strip_suffix(LOW).unwrap_or(key);
        let rec = self.sources().into_iter().find(|s| s["name"] == source).unwrap_or(Value::Null);
        let mut out = self.analysis.snapshot(key, source, &rec, want_scopes);
        out["source"] = json!(source);
        out["low"] = json!(key.ends_with(LOW));
        out
    }

    /// Close analyses the page stopped asking for, and tell the page when a source's health changed.
    pub fn health_tick(&self) {
        let at = analysis::now();
        self.watch.tick(at, self);
        let expired: Vec<String> = self.open.lock().map(|o| o.iter().filter(|(_, v)| v.expires < at).map(|(k, _)| k.clone()).collect()).unwrap_or_default();
        for k in expired {
            self.analysis_close(&k);
        }
        let seen: HashMap<String, String> = self.sources().into_iter().map(|s| (s["name"].as_str().unwrap_or("").to_owned(), s["health"].as_str().unwrap_or("").to_owned())).collect();
        let changed = self.health.lock().is_ok_and(|mut h| {
            let differs = *h != seen;
            if differs {
                *h = seen;
            }
            differs
        });
        if changed {
            self.bump();
        }
    }

    /// Sources the booth plainly relies on: those with a camera number and those in a multiview box.
    fn auto_names(&self) -> Vec<String> {
        let s = self.settings();
        let mut names: Vec<String> = s["tally"].as_object().map(|t| t.keys().cloned().collect()).unwrap_or_default();
        for mv in s["multiviews"].as_array().into_iter().flatten() {
            for b in mv["boxes"].as_array().into_iter().flatten() {
                if let Some(src) = b["source"].as_str().filter(|x| b["kind"] == "source" && !x.is_empty()) {
                    if !names.iter().any(|n| n == src) {
                        names.push(src.to_owned());
                    }
                }
            }
        }
        names
    }

    /// The Schedules page's schedules, for the watch card's "Only during" list: `[{"ref", "name"}]`.
    fn named_schedules(&self) -> Value {
        let all = self.host.settings("schedules").filter(Value::is_array).unwrap_or_else(|| json!([]));
        json!(all.as_array().into_iter().flatten().filter(|s| s["enabled"] == true).map(|s| json!({"ref": format!("schedule:relay:{}", s["id"].as_str().unwrap_or("")), "name": s["name"].as_str().filter(|n| !n.is_empty()).map_or_else(|| relay_core::schedules::summary(s), str::to_owned)})).collect::<Vec<_>>())
    }

    /// The source's saved label, or `None` when it is gone from the list (offline too long) or was never seen.
    fn offline_label(&self, source: &str) -> Option<String> {
        let seen = self.seen.lock().ok()?;
        match seen.get(source) {
            Some(r) if r.missed < GONE_AFTER => None,
            Some(r) => Some(if r.found.source.is_empty() { source.to_owned() } else { r.found.source.clone() }),
            None => Some(source.to_owned()),
        }
    }

    // ------------------------------------------------------------------------------------------ core routes (#544)
    fn routes(&self) -> Option<Arc<dyn Routes>> {
        self.host.using::<RoutesCap>(ROUTES).map(|c| Arc::clone(&c.0))
    }

    pub fn router(&self) -> Option<Router> {
        self.router.lock().ok().and_then(|r| r.clone())
    }

    /// Save changes to the settings: merged into what is saved, made safe, and written.
    pub fn save_settings(&self, changes: &Value) {
        let mut cur = self.settings();
        if let (Some(o), Some(c)) = (cur.as_object_mut(), changes.as_object()) {
            o.extend(c.clone());
        }
        let clean = settings::clean_settings(&cur);
        if let Ok(mut s) = self.settings.lock() {
            *s = clean.clone();
        }
        if self.host.save_settings("ndi", &clean).is_err() {
            tracing::warn!("the NDI settings could not be saved");
        }
    }

    /// Every source seen online (a source) and each of Relay's own routed outputs (a destination): the video channels NDI has now.
    pub fn gear_list(&self) -> Vec<Value> {
        let mut found: Vec<Value> = self
            .sources()
            .iter()
            .filter(|s| s["online"] == true)
            .map(|s| json!({"key": format!("src:{}", s["name"].as_str().unwrap_or("")), "kind": "video", "direction": "source",
                            "gear_name": if s["source"].as_str().is_some_and(|x| !x.is_empty()) { s["source"].clone() } else { s["name"].clone() }}))
            .collect();
        found.extend(self.tests.names().into_iter().chain(self.pages.names()).map(|n| json!({"key": format!("out:{n}"), "kind": "video", "direction": "destination", "gear_name": n})));
        found.extend(self.settings()["router_outputs"].as_array().into_iter().flatten().map(|r| {
            json!({"key": format!("route:{}", r["id"].as_str().unwrap_or("")), "kind": "video", "direction": "destination", "gear_name": router::ndi_name(r["name"].as_str().unwrap_or(""))})
        }));
        found
    }

    /// Tell core the video channels NDI has changed (#542).
    pub async fn report_channels(&self) {
        if let Some(routes) = self.routes() {
            routes.report(router::KEY, self.gear_list()).await;
        }
    }

    fn start_router(self: &Arc<Self>) {
        let Some(routes) = self.routes() else {
            tracing::info!("NDI routing is not available: the server has no core routes (start it with --rust-audio)");
            return;
        };
        let (weak, w2, w3, w4, w5) = (Arc::downgrade(self), Arc::downgrade(self), Arc::downgrade(self), Arc::downgrade(self), Arc::downgrade(self));
        let router = Router::new(Deps {
            routes: Arc::clone(&routes),
            backend: Arc::new(router::Runtime),
            settings: Arc::new(move || weak.upgrade().map_or(Value::Null, |s| s.settings())),
            save: Arc::new(move |c| {
                if let Some(s) = w2.upgrade() {
                    s.save_settings(&c);
                }
            }),
            notify: Arc::new(move || {
                if let Some(s) = w3.upgrade() {
                    s.bump();
                }
            }),
            running: Arc::new(move || w4.upgrade().is_some_and(|s| s.running())),
            report: Arc::new(move || {
                let w = w5.clone();
                Box::pin(async move {
                    if let Some(s) = w.upgrade() {
                        s.report_channels().await;
                    }
                })
            }),
        });
        routes.register(router::KEY, router.provider());
        if let Ok(mut r) = self.router.lock() {
            *r = Some(router.clone());
        }
        tokio::spawn(async move { router.start().await });
    }

    /// Join the audio bus: NDI sound in and out of the 32 lanes. Needs the server's bus (`--rust-audio`).
    fn start_bus(self: &Arc<Self>) {
        let Some(bus) = self.host.using::<LanesCap>(LANES).map(|c| Arc::clone(&c.0)) else {
            tracing::info!("NDI sound is not on the audio bus: the server does not run the bus (start it with --rust-audio)");
            return;
        };
        let pool = Arc::clone(&self.pool);
        let listen = Arc::new(move |source: &str, cb: crate::pool::OnAudio| -> Option<Guard> {
            pool.acquire(source, true, Arc::new(|_| {}), Some(cb)).map(|l| Box::new(l) as Guard)
        });
        let driver = Driver::new(bus, Arc::clone(&self.maker), listen);
        if let Ok(mut d) = self.driver.lock() {
            *d = Some(Arc::clone(&driver));
        }
        let shutdown = self.host.shutdown.clone();
        let handle = tokio::spawn(async move {
            loop {
                let d = Arc::clone(&driver);
                let _ = tokio::task::spawn_blocking(move || d.reconcile()).await;
                tokio::select! {
                    () = shutdown.wait() => break,
                    () = tokio::time::sleep(std::time::Duration::from_millis(500)) => {}
                }
            }
        });
        if let Ok(mut t) = self.driver_task.lock() {
            *t = Some(handle);
        }
    }

    async fn stop_bus(&self) {
        let task = self.driver_task.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = task {
            t.abort();
            let _ = t.await;
        }
        let driver = self.driver.lock().ok().and_then(|mut d| d.take());
        if let Some(d) = driver {
            let _ = tokio::task::spawn_blocking(move || d.stop()).await;
        }
    }

    fn stop_router(&self) {
        let router = self.router.lock().ok().and_then(|mut r| r.take());
        if let Some(r) = router {
            r.stop();
            if let Some(routes) = self.routes() {
                routes.withdraw(router::KEY);
            }
        }
    }

    /// The snapshot with the router's routes and presets.
    pub async fn snapshot_full(&self) -> Value {
        let mut out = self.snapshot();
        out["router"] = match self.router() {
            Some(r) => r.snapshot().await,
            None => json!({"routes": [], "salvos": []}),
        };
        out["inputs"] = json!(self.inputs());
        out
    }

    /// What a routed output can be pointed at: every source seen.
    pub fn inputs(&self) -> Vec<Value> {
        self.sources()
            .iter()
            .map(|s| {
                let label = if s["source"].as_str().is_some_and(|x| !x.is_empty()) { format!("{} ({})", s["machine"].as_str().unwrap_or(""), s["source"].as_str().unwrap_or("")) } else { s["name"].as_str().unwrap_or("").to_owned() };
                json!({"name": s["name"], "label": label, "online": s["online"]})
            })
            .collect()
    }

    /// The NDI names of every source Relay is sending now (page outputs and test patterns), sorted.
    fn senders(&self) -> Vec<String> {
        let mut v = self.pages.names();
        v.extend(self.tests.names());
        v.sort();
        v
    }

    pub fn running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    fn bump(&self) {
        self.changes.bump();
    }

    /// Return when the version differs from `since`, or after `timeout`.
    pub async fn wait_change(&self, since: u64, timeout: std::time::Duration) {
        self.changes.wait(since, timeout).await;
    }

    pub async fn scan(&self) {
        let s = self.settings();
        self.scanning.store(true, Ordering::SeqCst);
        self.bump();
        let strings = |k: &str| -> Vec<String> { s[k].as_array().map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect()).unwrap_or_default() };
        let (extra, groups, servers) = (strings("extra_ips"), strings("groups"), strings("servers"));
        let mdns = s["mdns"].as_bool().unwrap_or(true);
        let mut found = if mdns { discovery::visible(discovery::scan(self.host.net(), 2.5, &extra).await, &groups) } else { Vec::new() };
        let checked = self.check_servers(&servers).await;
        let reported = if !servers.is_empty() || !mdns { tokio::task::spawn_blocking(find_via_sdk).await.unwrap_or_default() } else { Vec::new() };
        self.scanning.store(false, Ordering::SeqCst);
        self.via_server.store(reported.len() as u64, Ordering::SeqCst);
        let have: Vec<String> = found.iter().map(|f| f.name.clone()).collect();
        found.extend(reported.into_iter().filter(|r| !have.contains(&r.name)));
        self.merge(found);
        if let Ok(mut l) = self.last_scan.lock() {
            *l = now_secs();
        }
        if let Ok(mut sv) = self.servers.lock() {
            *sv = checked;
        }
        self.report_channels().await;
        self.bump();
    }

    /// Is each discovery server answering on its TCP port?
    async fn check_servers(&self, servers: &[String]) -> Vec<Value> {
        let mut out = Vec::new();
        for sv in servers {
            let (host, port) = split_server(sv);
            let (ok, err) = match self.host.net().connect_tcp(discovery::TRAFFIC, &host, port, std::time::Duration::from_millis(1500)).await {
                Ok(_) => (true, String::new()),
                Err(e) if e.to_string().len() < 160 && e.to_string() != "timed out" => (false, e.to_string()),
                Err(_) => (false, "Nothing answered on that address.".to_owned()),
            };
            out.push(json!({"server": sv, "reachable": ok, "error": err}));
        }
        out
    }

    fn merge(&self, found: Vec<Found>) {
        let Ok(mut seen) = self.seen.lock() else { return };
        let names: Vec<String> = found.iter().map(|f| f.name.clone()).collect();
        for f in found {
            let old = seen.get(&f.name).cloned();
            seen.insert(f.name.clone(), Seen { missed: 0, width: old.as_ref().map_or(0, |o| o.width), height: old.as_ref().map_or(0, |o| o.height), fps: old.as_ref().map_or(0.0, |o| o.fps), audio_only: old.is_some_and(|o| o.audio_only), found: f });
        }
        for (n, rec) in seen.iter_mut() {
            if !names.contains(n) {
                rec.missed += 1;
            }
        }
    }

    /// Every source seen, offline ones too, ordered by machine then source name.
    pub fn sources(&self) -> Vec<Value> {
        let bridge: Vec<Value> = self.settings()["bridge"].as_array().cloned().unwrap_or_default();
        let _ = bridge;
        let users = self.pool.counts();
        let Ok(seen) = self.seen.lock() else { return Vec::new() };
        let mut out: Vec<Value> = seen
            .values()
            .map(|r| {
                let f = &r.found;
                let info = self.pool.info(&f.name);
                let health = self.analysis.health(&f.name);
                let (width, height, fps) = info.map_or((r.width, r.height, r.fps), |i| (i.width, i.height, i.fps));
                json!({"name": f.name, "machine": f.machine, "source": f.source, "host": f.host, "address": f.address, "port": f.port, "groups": f.groups,
                       "converter": f.converter.as_ref().map(|c| json!({"name": c.name, "source": c.source, "source_ip": c.source_ip})),
                       "width": width, "height": height, "fps": fps, "audio_only": r.audio_only || self.analysis.audio_only(&f.name), "online": r.missed < GONE_AFTER,
                       "listeners": users.get(&f.name).copied().unwrap_or(0), "via_bridge": Value::Null, "audio_channels": self.analysis.audio_channels(&f.name), "health": health.0, "health_why": health.1})
            })
            .collect();
        out.sort_by_key(|v| (v["machine"].as_str().unwrap_or("").to_lowercase(), v["source"].as_str().unwrap_or("").to_lowercase()));
        out
    }

    pub fn snapshot(&self) -> Value {
        let available = relay_ndi::api().is_ok();
        let error = self.error.lock().map(|e| e.clone()).unwrap_or_default();
        json!({"version": self.changes.version(), "available": available, "error": if error.is_empty() && !available { NO_RUNTIME.to_owned() } else { error },
               "running": self.running(), "scanning": self.scanning.load(Ordering::SeqCst), "last_scan": self.last_scan.lock().map(|l| *l).unwrap_or(0.0),
               "sources": self.sources(), "settings": self.settings(), "servers": self.servers.lock().map(|s| s.clone()).unwrap_or_default(),
               "via_server": self.via_server.load(Ordering::SeqCst), "watch": self.watch.snapshot(&self.settings()["watch"], &self.auto_names()),
               "schedules": self.named_schedules(), "outputs": self.senders(), "pages": self.pages.status(), "tests": self.tests.status(), "pages_error": crate::render::available()})
    }

    pub fn health(&self) -> Health {
        if !self.running() {
            return NOT_RUNNING;
        }
        let src = self.sources();
        let down = self.servers.lock().map(|s| s.iter().any(|x| x["reachable"] == false)).unwrap_or(false);
        if down {
            return Health::new(HealthState::Warn, "A discovery server is not answering");
        }
        if src.is_empty() {
            return Health::new(HealthState::Ok, "No sources found yet");
        }
        Health::new(HealthState::Ok, "Sources found")
    }
}

/// Sources the NDI runtime reports (it talks to the discovery servers), in the same shape as mDNS finds.
fn find_via_sdk() -> Vec<Found> {
    let Ok(got) = relay_ndi::find::Finder::scan(3.0) else { return Vec::new() };
    got.into_iter()
        .take(MAX_FOUND_VIA_SDK)
        .map(|g| {
            let (machine, source) = discovery::split_instance(&g.name);
            Found { name: g.name.chars().take(160).collect(), machine, source, host: String::new(), address: g.address.chars().take(45).collect(), port: 0, groups: vec![], converter: None }
        })
        .collect()
}

/// The wall clock in the Relay time zone.
fn local_clock(host: &Host) -> Clock {
    let tz = relay_core::schedules::clean_zone(host.settings("timezone").as_ref().and_then(Value::as_str).unwrap_or("")).to_owned();
    let secs = now_secs() as i64;
    let (_, minute) = relay_core::schedules::local_of_utc(secs.div_euclid(60), &tz);
    Clock { secs_of_day: minute * 60 + secs.rem_euclid(60) }
}

impl Ctx for Service {
    fn settings(&self) -> Value {
        Service::settings(self)["watch"].clone()
    }

    fn auto(&self) -> Vec<String> {
        self.auto_names()
    }

    fn problems(&self, source: &str, sound: bool) -> Vec<(Kind, String)> {
        if let Some(label) = self.offline_label(source) {
            return vec![(Kind::Offline, format!("{label} is offline"))];
        }
        let label = self.seen.lock().ok().and_then(|s| s.get(source).map(|r| if r.found.source.is_empty() { source.to_owned() } else { r.found.source.clone() })).unwrap_or_else(|| source.to_owned());
        let key = format!("{source}{LOW}");
        if self.analysis.get(&key).is_some_and(|t| !t.watch.load(Ordering::Relaxed)) {
            self.analysis.watch(&key, true);
        }
        let mut out = Vec::new();
        let (state, why) = self.analysis.health(&key);
        if state == "bad" {
            out.push((Kind::Frames, format!("{label}: {why}")));
        }
        if sound {
            let alarms = self.analysis.audio_alarms(&key);
            if alarms.contains(&"SILENCE") {
                out.push((Kind::Silence, format!("{label} has gone silent")));
            }
            if alarms.contains(&"CLIP") {
                out.push((Kind::Clipping, format!("{label} is clipping")));
            }
        }
        out
    }

    fn acquire(&self, source: &str) -> Option<Lease> {
        let lease = self.pool.acquire(source, true, Arc::new(|_| {}), None)?;
        self.analysis.watch(&format!("{source}{LOW}"), true);
        Some(lease)
    }

    fn in_service(&self, schedule: &str) -> bool {
        let all = self.host.settings("schedules").filter(Value::is_array).unwrap_or_else(|| json!([]));
        let tz = relay_core::schedules::clean_zone(self.host.settings("timezone").as_ref().and_then(Value::as_str).unwrap_or("")).to_owned();
        let (date, minute) = relay_core::schedules::local_of_utc((now_secs() as i64).div_euclid(60), &tz);
        relay_core::schedules::find(&all, schedule).is_some_and(|s| s["enabled"] == true && relay_core::schedules::is_open_local(s, date, minute, &tz))
    }

    fn changed(&self) {
        self.bump();
    }
}
