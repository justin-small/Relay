//! Live captions over HTTP (#659): `app/main.py`'s `/api/admin/status`, `/api/admin/start`, `/api/admin/stop`,
//! `/api/admin/target/{t}`, `/api/targets`, `/api/admin/recordings*`, the caption feeds `/stream` and the admin status stream, on
//! `relay-captions`. The rules and the fixed sentences are the Python routes' own.
//!
//! The room's audio comes from a lane of the Rust audio bus (a tap read at 24 kHz), so captions in Rust need `--rust-audio`. Switch
//! them on with `relay-server --rust-captions` (or `RELAY_RUST_CAPTIONS=1`). What the Python modules add to the pages (the top-bar
//! pills, each module's health, the schedule) is asked of the app once a second; `stream.started` and `stream.stopped` are told to it.
//! Python's own scheduled starts still open Python's pipeline, so leave the schedule off in the app until the scheduler moves (#661).

// axum's `Response` is the error half of every helper here (the answer to send instead): that is the idiom, and each is built once.
#![allow(clippy::result_large_err)]

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, RwLock};
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Path, Request, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use relay_audio::AudioBus;
use relay_captions::capture::{Consumers, Pump, TARGET_RATE};
use relay_captions::clock;
use relay_captions::engine::{Captions, Capture, Outside, Parts, Settings};
use relay_captions::exporting::{build_exports, zip_bytes, Row};
use relay_captions::net::{Https, WebSockets};
use relay_captions::recorder::{read_lines, Recorder};
use relay_captions::session::AudioFeed;
use relay_captions::transcribe::Backends;
use relay_core::hub::Hub;
use relay_core::Net;
use serde_json::{json, Map, Value};

use crate::accounts::{detail, Shared};
use crate::forward::{Conn, Upstream};
use crate::pyapp::PyApp;
use crate::show;
use crate::status::Show;
use crate::streams::Streams;

type Ext = Option<axum::Extension<Conn>>;

/// Where Python's `blocklist.txt` and `recordings/` are, and config.json's saved settings, through `relay_config::Settings`.
pub struct ConfigSettings {
    config: Arc<relay_config::Settings>,
    state_dir: PathBuf,
}

impl ConfigSettings {
    pub fn new(config: Arc<relay_config::Settings>, state_dir: PathBuf) -> Self {
        Self { config, state_dir }
    }

    fn within(&self, p: &str) -> PathBuf {
        let p = PathBuf::from(p);
        if p.is_absolute() {
            p
        } else {
            self.state_dir.join(p)
        }
    }
}

fn env_path(name: &str) -> Option<String> {
    std::env::var(name).ok().filter(|v| !v.is_empty())
}

impl Settings for ConfigSettings {
    fn get(&self) -> Value {
        self.config.get().unwrap_or(Value::Null)
    }

    fn save(&self, patch: &Value) {
        let keys: Vec<&str> = patch.as_object().map(|o| o.keys().map(String::as_str).collect()).unwrap_or_default();
        if let Err(e) = self.config.save(patch, &[]) {
            tracing::error!("the settings {keys:?} were not saved: {e:?}");
        }
    }

    /// `RELAY_BLOCKLIST` wins over the config key, as in Python; a relative path is in the state folder.
    fn blocklist_path(&self) -> PathBuf {
        let cfg = self.get();
        let p = env_path("RELAY_BLOCKLIST").or_else(|| cfg.get("blocklist_file").and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_owned)).unwrap_or_else(|| "blocklist.txt".to_owned());
        self.within(&p)
    }

    /// `RELAY_RECORDINGS` wins over the config key.
    fn recordings_path(&self) -> PathBuf {
        let cfg = self.get();
        let p = env_path("RELAY_RECORDINGS").or_else(|| cfg.pointer("/recording/dir").and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_owned)).unwrap_or_else(|| "recordings".to_owned());
        self.within(&p)
    }
}

/// The bus's lanes, and the rate it runs them at.
const MAX_LANES: usize = 32;
const LANE_RATE: u32 = 48_000;

/// How often the tap is read, and the most it is asked for at once: 40 ms of 24 kHz audio.
const READ_WAIT: Duration = Duration::from_millis(50);
const READ_MAX: usize = 960;

struct Running {
    tap: u64,
    stop: Arc<AtomicBool>,
    thread: std::thread::JoinHandle<()>,
}

/// The audio capture on a lane of the Rust bus: a tap that reads mono 24 kHz, a pump that meters and gates it, and the consumers it
/// fans out to. The bus owns the device, so an unplugged input is the bus's to report, not a reconnect loop here.
pub struct TapCapture {
    bus: Arc<AudioBus>,
    consumers: Arc<Consumers>,
    pump: Arc<Mutex<Option<Pump>>>,
    running: Mutex<Option<Running>>,
    lane: Mutex<usize>,
}

impl TapCapture {
    pub fn new(bus: Arc<AudioBus>) -> Arc<Self> {
        Arc::new(Self { bus, consumers: Consumers::new(), pump: Arc::new(Mutex::new(None)), running: Mutex::new(None), lane: Mutex::new(0) })
    }
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

/// `"lane3"`, `"3"` or `3` as a lane number, 1 to 32.
fn lane_number(selector: &str) -> Option<usize> {
    let n: usize = selector.trim().to_lowercase().trim_start_matches("lane").trim().parse().ok()?;
    (1..=MAX_LANES).contains(&n).then_some(n)
}

impl Capture for TapCapture {
    fn start(&self, lane: &str, gate_silence: bool) -> Result<(), String> {
        self.stop();
        let n = lane_number(lane).unwrap_or(1);
        let Some(tap) = self.bus.open_tap_at(n, TARGET_RATE) else {
            return Err("The audio bus is not running, so there is no input to caption. Set up the audio bus first.".to_owned());
        };
        *lock(&self.pump) = Some(Pump::new(TARGET_RATE, gate_silence));
        *lock(&self.lane) = n;
        let stop = Arc::new(AtomicBool::new(false));
        let (bus, consumers, pump, flag) = (Arc::clone(&self.bus), Arc::clone(&self.consumers), Arc::clone(&self.pump), Arc::clone(&stop));
        let thread = std::thread::Builder::new()
            .name("captions-tap".to_owned())
            .spawn(move || {
                while !flag.load(Ordering::Relaxed) {
                    let Some(read) = bus.read_tap(tap, READ_MAX, READ_WAIT) else { break };
                    if read.samples.is_empty() {
                        continue;
                    }
                    let frames = match lock(&pump).as_mut() {
                        Some(p) => p.push(&read.samples, clock::wall()),
                        None => break,
                    };
                    for f in frames {
                        consumers.dispatch(&f);
                    }
                }
            })
            .map_err(|_| "The audio input could not be opened.".to_owned())?;
        *lock(&self.running) = Some(Running { tap, stop, thread });
        Ok(())
    }

    fn stop(&self) {
        if let Some(r) = lock(&self.running).take() {
            r.stop.store(true, Ordering::Relaxed);
            let _ = r.thread.join();
            self.bus.close_tap(r.tap);
        }
        *lock(&self.pump) = None;
    }

    fn status(&self) -> Value {
        let running = lock(&self.running).is_some();
        let lane = *lock(&self.lane);
        let mut out = json!({
            "running": running,
            "device": if running { json!(format!("Lane {lane}")) } else { Value::Null },
            "native_rate": LANE_RATE,
            "target_rate": TARGET_RATE,
            "channel": 0,
            "error": null,
            "notice": null,
            "reconnecting": false,
            "consumers": self.consumers.len(),
            "dropped_capture": 0,
        });
        let meter = lock(&self.pump).as_ref().map(|p| p.meter_status(clock::wall()));
        if let (Some(o), Some(Value::Object(m))) = (out.as_object_mut(), meter) {
            o.extend(m);
        } else if let Some(o) = out.as_object_mut() {
            for (k, v) in [("level", json!(0.0)), ("peak", json!(0.0)), ("peak_hold", json!(0.0)), ("rms_dbfs", json!(-60.0)), ("peak_dbfs", json!(-60.0)), ("clipping", json!(false)), ("clipped_samples", json!(0)), ("speaking", json!(false)), ("last_audio_ts", Value::Null)] {
                o.insert(k.to_owned(), v);
            }
        }
        out
    }

    fn add_consumer(&self) -> (u64, AudioFeed) {
        self.consumers.add()
    }

    fn remove_consumer(&self, id: u64) {
        self.consumers.remove(id);
    }
}

/// What the Python app's modules add to the pages (the top-bar pills, each module's health, the schedule, the stream-watching
/// modules), asked of the app now and then and kept, so a status never waits on it; and `stream.started` told to it.
pub struct AppOutside {
    app: Option<Arc<PyApp>>,
    cache: RwLock<Value>,
    /// The Rust modules, which add their own part (the Planning Center service feeds) over the app's.
    host: Option<relay_core::Host>,
}

impl AppOutside {
    pub fn new(app: Option<Arc<PyApp>>, host: Option<relay_core::Host>) -> Arc<Self> {
        Arc::new(Self { app, cache: RwLock::new(json!({})), host })
    }

    /// Read the app's answer once a second, until `shutdown`.
    pub fn watch(self: &Arc<Self>, shutdown: relay_core::Shutdown) {
        let Some(app) = self.app.clone() else { return };
        let me = Arc::clone(self);
        tokio::spawn(async move {
            loop {
                if let Ok(v) = app.get("/api/internal/captions/outside").await {
                    *me.cache.write().unwrap_or_else(|e| e.into_inner()) = v;
                }
                tokio::select! {
                    () = shutdown.wait() => break,
                    () = tokio::time::sleep(Duration::from_secs(1)) => {}
                }
            }
        });
    }

    fn part(&self, key: &str, empty: Value) -> Value {
        self.cache.read().unwrap_or_else(|e| e.into_inner()).get(key).cloned().unwrap_or(empty)
    }
}

impl Outside for AppOutside {
    fn schedule_status(&self) -> Value {
        self.part("schedule", json!({}))
    }
    fn streams(&self) -> Value {
        self.part("streams", json!([]))
    }
    fn pills(&self) -> Value {
        self.part("pills", json!([]))
    }
    fn module_health(&self) -> Value {
        self.part("module_health", json!({}))
    }
    fn module_status(&self) -> Map<String, Value> {
        let mut out = match self.part("module_status", json!({})) {
            Value::Object(m) => m,
            _ => Map::new(),
        };
        // The service feeds a panel can pick, from the Rust Planning Center while it is on (`plan.feed.offered`).
        if let Some(plan) = self.host.as_ref().and_then(|h| h.using::<relay_core::caps::PlanCap>(relay_core::caps::PLAN)) {
            out.insert("service".to_owned(), Value::Array((plan.offered)()));
        }
        // The ProPresenter feeds a panel can pick, from the Rust ProPresenter while it is on: the lyrics (if the operator has them on for panels) and the timers.
        if let Some(offers) = self.host.as_ref().and_then(|h| h.using::<relay_core::caps::ProOffers>(relay_core::caps::PRO_OFFERS)) {
            out.insert("propresenter".to_owned(), json!({"lyrics": (offers.lyrics)(), "timers": (offers.timers)()}));
        }
        out
    }
    fn announce(&self, event: &str) {
        // The Rust modules that follow Start and Stop hear it here; the app's are told below.
        if let Some(host) = &self.host {
            host.announce(event, "");
        }
        let (Some(app), Ok(handle)) = (self.app.clone(), tokio::runtime::Handle::try_current()) else { return };
        let event = event.to_owned();
        handle.spawn(async move {
            if let Err(e) = app.post("/api/internal/stream/announce", &json!({"event": event})).await {
                tracing::warn!("the app was not told {event}: {e}");
            }
        });
    }
}

/// What the status feed, the meter and the admin status stream read: the captions engine's, and the panels and alerts when those are
/// served here too.
pub struct CaptionsShow {
    pub captions: Arc<Captions>,
    pub show: Option<show::Api>,
}

impl Show for CaptionsShow {
    fn running(&self) -> bool {
        self.captions.stream_status()["running"].as_bool().unwrap_or(false)
    }
    fn started_at(&self) -> Option<f64> {
        self.captions.stream_status()["started_at"].as_f64()
    }
    fn audio(&self) -> Value {
        self.captions.status()["audio"].clone()
    }
    fn live_languages(&self) -> Vec<String> {
        self.captions.live_targets().iter().filter_map(|t| t["label"].as_str().map(str::to_owned)).collect()
    }
    fn engine_status(&self) -> Value {
        self.captions.status()
    }
    fn meter(&self) -> Value {
        self.captions.meter()
    }
    fn panels(&self) -> Value {
        self.show.as_ref().map_or_else(|| json!({"type": "panels", "panels": []}), show::panels_frame)
    }
    fn alerts(&self) -> Value {
        self.show.as_ref().map_or_else(|| json!({"type": "alerts"}), show::alerts_frame)
    }
    fn panels_online(&self) -> String {
        self.show.as_ref().map(|s| s.panels.online_signature().join(",")).unwrap_or_default()
    }
}

/// Everything the routes share.
pub struct CaptionsApi {
    pub acc: Shared,
    pub captions: Arc<Captions>,
    pub streams: Arc<Streams>,
    pub settings: Arc<ConfigSettings>,
}

pub type Api = Arc<CaptionsApi>;

/// What it takes to start: the accounts, the bus, the config, the app behind the server and the (optional) alerts and panels.
pub struct Start {
    pub acc: Shared,
    pub bus: Arc<AudioBus>,
    pub config: Arc<relay_config::Settings>,
    pub state_dir: PathBuf,
    pub app: Option<Arc<PyApp>>,
    pub upstream: Option<Arc<Upstream>>,
    pub show: Option<show::Api>,
    pub net: Net,
    pub hub: Arc<Hub>,
    pub shutdown: relay_core::Shutdown,
    /// The modules, whose own feeds `/stream` hands on while they are on.
    pub registry: Option<Arc<relay_core::Registry>>,
    /// The modules' host: the viewers' payload carries what its Rust modules offer, and a service type followed or dropped pushes it.
    pub host: Option<relay_core::Host>,
}

pub fn start(s: Start) -> Api {
    let settings = Arc::new(ConfigSettings::new(Arc::clone(&s.config), s.state_dir));
    let outside = AppOutside::new(s.app, s.host.clone());
    outside.watch(s.shutdown.clone());
    let captions = Captions::new(Parts {
        hub: Arc::clone(&s.hub),
        settings: settings.clone(),
        capture: TapCapture::new(s.bus),
        chat: Arc::new(Https::new(s.net.clone())),
        connect: Arc::new(WebSockets::new(s.net)),
        outside: outside.clone(),
        backends: Backends::builtin(),
        recorder: Recorder::new(clock::wall, clock::relay_zone),
    });
    // A service type followed or dropped in Planning Center reaches open panel pickers at once (`feed.on_offered_change`).
    if let Some(host) = &s.host {
        let engine = Arc::downgrade(&captions);
        host.listen("core", relay_core::caps::OFFERED_CHANGED, Arc::new(move |_| {
            if let Some(engine) = engine.upgrade() {
                engine.push_status();
            }
        }));
    }
    let host = s.host.clone();
    let hub = Arc::clone(&s.hub);
    let api = assemble(Assemble { acc: s.acc, captions, hub: s.hub, config: s.config, settings, show: s.show, upstream: s.upstream, shutdown: s.shutdown, registry: s.registry });
    // What a module that watches the stream (OBS) reports to and reads from: the stream status and the lines the hub commits.
    if let Some(host) = host {
        host.provide("core", relay_core::caps::HUB, Arc::new(relay_core::caps::HubCap(hub)));
        host.provide("core", relay_core::caps::STREAMS, Arc::new(relay_core::caps::StreamsCap(Arc::new(crate::modcaps::StreamBoardForModules(Arc::clone(&api.streams.status))))));
    }
    api
}

/// The engine, ready to serve: what [`start`] builds around the real audio, and what a test builds around its own.
pub struct Assemble {
    pub acc: Shared,
    pub captions: Arc<Captions>,
    pub hub: Arc<Hub>,
    pub config: Arc<relay_config::Settings>,
    pub settings: Arc<ConfigSettings>,
    pub show: Option<show::Api>,
    pub upstream: Option<Arc<Upstream>>,
    pub shutdown: relay_core::Shutdown,
    pub registry: Option<Arc<relay_core::Registry>>,
}

pub fn assemble(a: Assemble) -> Api {
    clock::set_zone(a.settings.get().get("timezone").and_then(Value::as_str).unwrap_or(""));
    let captions = a.captions;
    captions.attach();
    let view: Arc<dyn Show> = Arc::new(CaptionsShow { captions: Arc::clone(&captions), show: a.show.clone() });
    let acc = Arc::clone(&a.acc);
    let streams = Streams::new(a.hub, view, a.config, a.shutdown.clone()).with_upstream(a.upstream).guarded(move |headers, conn| {
        let acc = Arc::clone(&acc);
        async move { acc.caller(&headers, conn, Some("session.view")).await.map(|_| ()) }
    });
    if let Some(api) = a.show {
        // The alert feed is the alerts' own: this server's `/stream` hands it on.
        streams.provide("alert", Arc::new(move |_, _| Some(show::sender_stream(&api))));
    }
    if let Some(registry) = a.registry {
        for name in registry.stream_names() {
            let registry = Arc::clone(&registry);
            streams.provide(name, Arc::new(move |q, headers| registry.stream(name, &q.params.iter().map(|(k, v)| (k.clone(), v.clone())).collect(), headers)));
        }
    }
    follow(&captions, &a.settings, a.shutdown);
    Arc::new(CaptionsApi { acc: a.acc, captions, streams: Arc::new(streams), settings: a.settings })
}

/// What the operator changes in settings while captions run, followed within a second (Python's config route did it in the route):
/// the blocklist file, the zone, a change that needs a new session, the stream's captions switch, the translation provider.
fn follow(captions: &Arc<Captions>, settings: &Arc<ConfigSettings>, shutdown: relay_core::Shutdown) {
    let (captions, settings) = (Arc::clone(captions), Arc::clone(settings));
    tokio::spawn(async move {
        let signature = |cfg: &Value| {
            let pick = |keys: &[&str]| keys.iter().map(|k| cfg.get(*k).cloned().unwrap_or(Value::Null)).collect::<Vec<_>>();
            (pick(&["audio_lane", "source_language", "realtime"]), cfg.get("stream_captions").cloned(), pick(&["ai", "openai_api_key", "vercel_api_key"]))
        };
        let (mut seen, mut mtime) = (signature(&settings.get()), relay_captions::redact::file_mtime(&settings.blocklist_path()));
        loop {
            tokio::select! {
                () = shutdown.wait() => break,
                () = tokio::time::sleep(Duration::from_secs(1)) => {}
            }
            let cfg = settings.get();
            clock::set_zone(cfg.get("timezone").and_then(Value::as_str).unwrap_or(""));
            let now = relay_captions::redact::file_mtime(&settings.blocklist_path());
            if now != mtime {
                mtime = now;
                captions.hub().set_blocklist(&relay_captions::redact::read_blocklist_file(&settings.blocklist_path()));
                captions.push_status();
            }
            let sig = signature(&cfg);
            if sig == seen {
                continue;
            }
            let (restart, parts, ai) = (sig.0 != seen.0, sig.1 != seen.1, sig.2 != seen.2);
            seen = sig;
            if parts {
                captions.apply_parts().await;
            }
            if restart {
                captions.restart().await;
            } else if ai {
                captions.apply_ai().await;
            }
        }
        captions.shutdown().await;
    });
}

/// The routes: the viewers' `/api/targets` and the caption feeds, and the operator's status, start, stop, targets and recordings.
pub fn routes(api: Api) -> Router {
    let feeds = crate::streams::routes(Arc::clone(&api.streams));
    Router::new()
        .route("/api/targets", get(api_targets))
        .route("/api/admin/status", get(admin_status))
        .route("/api/admin/start", post(admin_start))
        .route("/api/admin/stop", post(admin_stop))
        .route("/api/admin/target/{target}", post(admin_target))
        .route("/api/admin/recordings", get(admin_recordings))
        .route("/api/admin/recordings/{run_id}", get(admin_recording).delete(admin_recording_delete))
        .route("/api/admin/recordings/{run_id}/export.zip", get(admin_recording_zip))
        .route("/api/admin/recordings/{run_id}/file/{name}", get(admin_recording_file))
        .with_state(api)
        .merge(feeds)
}

async fn guard(api: &Api, conn: Ext, headers: &HeaderMap, perm: &'static str) -> Result<crate::accounts::Caller, Response> {
    api.acc.caller(headers, conn.map(|c| c.0), Some(perm)).await
}

/// First paint and resync for the viewer picker; the live set is pushed over the caption feed.
async fn api_targets(State(api): State<Api>) -> Response {
    Json(api.captions.api_targets()).into_response()
}

async fn admin_status(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "session.view").await {
        return r;
    }
    Json(api.captions.status()).into_response()
}

async fn admin_start(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "session.control").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    match api.captions.start().await {
        Ok(status) => {
            api.acc.record(&who, "session.start", "Started captions", "", None).await;
            Json(status).into_response()
        }
        Err(refused) => {
            // The refusal last: the status carries its own `error` (the sticky last error), which must not mask why this press failed.
            let mut body = api.captions.status();
            body["error"] = json!(relay_captions::engine::start_message(refused.key).map_or(refused.message, str::to_owned));
            (StatusCode::BAD_REQUEST, Json(body)).into_response()
        }
    }
}

async fn admin_stop(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "session.control").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let status = api.captions.stop().await;
    api.acc.record(&who, "session.stop", "Stopped captions", "", None).await;
    Json(status).into_response()
}

async fn admin_target(State(api): State<Api>, Path(target): Path<String>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "targets.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let raw = axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default();
    let Ok(payload @ Value::Object(_)) = serde_json::from_slice::<Value>(&raw) else { return detail(422, "That request is not valid.") };
    let enabled = match payload.get("enabled") {
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) | None => false,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Object(o)) => !o.is_empty(),
    };
    match api.captions.set_target(&target.to_uppercase(), enabled).await {
        Ok(status) => {
            api.acc.record(&who, "session.target", "Changed a target language", "", None).await;
            Json(status).into_response()
        }
        Err(_) => detail(404, "unknown target"),
    }
}

// ---------------------------------------------------------------- recordings

fn run_or_404(api: &Api, run_id: &str) -> Result<PathBuf, Response> {
    api.captions.recorder().lock().unwrap_or_else(|e| e.into_inner()).run_dir(run_id).ok_or_else(|| detail(404, "unknown run"))
}

fn download(body: Vec<u8>, filename: &str, media: &str) -> Response {
    let mut resp = Response::new(Body::from(body));
    let h = resp.headers_mut();
    if let Ok(v) = HeaderValue::from_str(media) {
        h.insert(header::CONTENT_TYPE, v);
    }
    if let Ok(v) = HeaderValue::from_str(&format!("attachment; filename=\"{filename}\"")) {
        h.insert(header::CONTENT_DISPOSITION, v);
    }
    resp
}

fn meta_of(api: &Api, run_id: &str) -> Value {
    api.captions.recorder().lock().unwrap_or_else(|e| e.into_inner()).list_run(run_id).unwrap_or_else(|| json!({"run_id": run_id}))
}

fn rows_of(dir: &std::path::Path) -> Vec<Row> {
    read_lines(dir).iter().map(Row::from_json).collect()
}

async fn admin_recordings(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "recordings.view").await {
        return r;
    }
    let cfg = api.settings.get();
    let runs = api.captions.recorder().lock().unwrap_or_else(|e| e.into_inner()).list_runs();
    Json(json!({
        "enabled": cfg.pointer("/recording/enabled").cloned().unwrap_or(json!(false)),
        "keep_runs": cfg.pointer("/recording/keep_runs").cloned().unwrap_or(json!(20)),
        "dir": api.settings.recordings_path().display().to_string(),
        "runs": runs,
    }))
    .into_response()
}

/// One run's metadata plus the files a download would produce, with line counts: enough for the panel to show what is in there
/// before fetching several megabytes of transcript.
async fn admin_recording(State(api): State<Api>, Path(run_id): Path<String>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "recordings.view").await {
        return r;
    }
    let dir = match run_or_404(&api, &run_id) {
        Ok(d) => d,
        Err(r) => return r,
    };
    let meta = meta_of(&api, &run_id);
    let built = tokio::task::spawn_blocking({
        let meta = meta.clone();
        move || build_exports(&meta, &rows_of(&dir), clock::relay_zone)
    })
    .await
    .unwrap_or_default();
    let files: Vec<Value> = built.iter().map(|(name, text)| json!({"name": name, "lines": text.matches('\n').count(), "bytes": text.len()})).collect();
    Json(json!({"run": meta, "files": files})).into_response()
}

async fn admin_recording_zip(State(api): State<Api>, Path(run_id): Path<String>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "recordings.view").await {
        return r;
    }
    let dir = match run_or_404(&api, &run_id) {
        Ok(d) => d,
        Err(r) => return r,
    };
    let meta = meta_of(&api, &run_id);
    let body = tokio::task::spawn_blocking(move || {
        let raw = std::fs::read_to_string(dir.join("lines.jsonl")).unwrap_or_default();
        zip_bytes(&meta, &rows_of(&dir), &raw, clock::relay_zone, clock::wall())
    })
    .await
    .unwrap_or_default();
    download(body, &format!("relay-{run_id}.zip"), "application/zip")
}

/// One derived file. `name` is matched against the set this run actually produces; it is never joined onto a path.
async fn admin_recording_file(State(api): State<Api>, Path((run_id, name)): Path<(String, String)>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "recordings.view").await {
        return r;
    }
    let dir = match run_or_404(&api, &run_id) {
        Ok(d) => d,
        Err(r) => return r,
    };
    let meta = meta_of(&api, &run_id);
    let mut files = tokio::task::spawn_blocking(move || build_exports(&meta, &rows_of(&dir), clock::relay_zone)).await.unwrap_or_default();
    let Some(text) = files.remove(&name) else { return detail(404, "unknown file") };
    let media = if name.ends_with(".jsonl") { "application/x-ndjson" } else { "text/plain" };
    download(text.into_bytes(), &format!("{run_id}-{name}"), &format!("{media}; charset=utf-8"))
}

async fn admin_recording_delete(State(api): State<Api>, Path(run_id): Path<String>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "recordings.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    if let Err(r) = run_or_404(&api, &run_id) {
        return r;
    }
    let (deleted, runs) = {
        let rec = api.captions.recorder().lock().unwrap_or_else(|e| e.into_inner());
        (rec.delete_run(&run_id), rec.list_runs())
    };
    if !deleted {
        return detail(409, "that run is still being recorded");
    }
    api.acc.record(&who, "recordings.delete", "Deleted a recording", "", None).await;
    Json(json!({"deleted": run_id, "runs": runs})).into_response()
}
