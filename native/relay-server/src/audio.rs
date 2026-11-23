//! The audio bus, routes, channels and meters over HTTP (#657): `app/main.py`'s `/api/admin/audiobus*`, `/api/admin/metering*`,
//! `/api/admin/routes*` and `/api/admin/network/channels*`, on `relay-audio`. The rules and the fixed sentences are the Python
//! routes' own; each handler writes its audit entry once the change succeeded, as the Python route does.
//!
//! What only a Python module knows (the audio sources and outputs modules registered, how a module applies a route to its gear)
//! comes from the app behind the server through `pyapp.rs`, until each of those modules is ported.
//!
//! The server runs the engine only when asked (`--rust-audio`, `RELAY_RUST_AUDIO=1`): the app behind it must then be started with the
//! same switch, because two programs cannot both open the sound cards.

// axum's `Response` is the error half of every helper here (the answer to send instead): that is the idiom, and each is built once.
#![allow(clippy::result_large_err)]

use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path, Request, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use relay_audio::error::{channel_status, route_status, sentence};
use relay_audio::normalize::LANES;
use relay_audio::{AudioBus, Channels, Error, MeterStore, Metering, Routing, Store};
use serde_json::{json, Map, Value};

use crate::accounts::{detail, Caller, Shared};
use crate::forward::Conn;
use crate::pyapp::PyApp;

const BAD_REQUEST: &str = "That request is not valid.";
const NOT_ALLOWED: &str = "Not allowed";
const TEST_PLAYED: &str = "The test tone is playing.";
const TEST_UNPATCHED: &str = "Test tone is not routed to any output yet. Route it to an output channel below, then try again.";

/// config.json's `audio_bus` and `metering` blocks, through `relay_config::Settings`.
pub struct ConfigStore(pub Arc<relay_config::Settings>);

impl Store for ConfigStore {
    fn audio_bus(&self) -> Value {
        self.0.get().ok().and_then(|c| c.get("audio_bus").cloned()).unwrap_or_else(|| json!({"lanes": [], "outputs": [], "matrix": []}))
    }
    fn save_audio_bus(&self, cfg: &Value) -> Value {
        let clean = relay_audio::normalize::normalize(cfg);
        match self.0.save(&json!({"audio_bus": clean}), &["audio_bus"]) {
            Ok(saved) => saved.get("audio_bus").cloned().unwrap_or(clean),
            Err(e) => {
                tracing::error!("the audio bus setting was not saved: {e:?}");
                clean
            }
        }
    }
    fn targets(&self) -> Vec<String> {
        self.0.get().ok().and_then(|c| c.get("targets").and_then(Value::as_object).map(|t| t.keys().cloned().collect())).unwrap_or_default()
    }
}

impl MeterStore for ConfigStore {
    fn metering(&self) -> Value {
        self.0.get().ok().and_then(|c| c.get("metering").cloned()).unwrap_or_else(|| json!({"meters": []}))
    }
    fn save_metering(&self, cfg: &Value) -> Value {
        let clean = relay_audio::metering::normalize(cfg);
        match self.0.save(&json!({"metering": clean}), &["metering"]) {
            Ok(saved) => saved.get("metering").cloned().unwrap_or(clean),
            Err(e) => {
                tracing::error!("the metering setting was not saved: {e:?}");
                clean
            }
        }
    }
}

/// Everything the audio routes share.
pub struct AudioApi {
    pub acc: Shared,
    pub bus: Arc<AudioBus>,
    pub routing: Arc<Routing>,
    pub channels: Arc<Channels>,
    pub metering: Arc<Metering>,
    pub app: Option<Arc<PyApp>>,
}

pub type Api = Arc<AudioApi>;

fn error(status: u16, sentence: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"error": sentence}))).into_response()
}

fn down(what: &str) -> Response {
    error(503, &format!("The database is down, so {what} right now."))
}

/// A refusal or a database failure as the JSON answer; the sentence is always one of the fixed ones.
fn refused(e: Error, status: fn(&str) -> u16, down_what: &str) -> Response {
    match e {
        Error::Refused(key) => error(status(key), sentence(key)),
        Error::Unavailable(cause) => {
            tracing::warn!("audio: {cause}");
            down(down_what)
        }
    }
}

fn object(body: &Bytes) -> Result<Map<String, Value>, Response> {
    match serde_json::from_slice::<Value>(body) {
        Ok(Value::Object(m)) => Ok(m),
        _ => Err(detail(422, BAD_REQUEST)),
    }
}

async fn read(req: Request) -> Bytes {
    axum::body::to_bytes(req.into_body(), 1 << 20).await.unwrap_or_default()
}

/// Sign-in and permission first, as `Depends(require(...))` runs before a route's body is read.
async fn guard(api: &Api, conn: Option<axum::Extension<Conn>>, headers: &HeaderMap, perm: Option<&'static str>) -> Result<Caller, Response> {
    api.acc.caller(headers, conn.map(|c| c.0), perm).await
}

// ----------------------------------------------------------------------------------------------------------- channels
async fn channels_view(api: &Api) -> Result<Value, Error> {
    let mut chans: Vec<Value> = api.channels.listing("").await?.iter().map(|c| c.json()).collect();
    let routes = api.routing.listing("", None).await?;
    let names = |v: &Value, key: &str| -> Vec<Value> { v[key].as_array().into_iter().flatten().map(|s| s["name"].clone()).collect() };
    for c in &mut chans {
        let me = c["ref"].clone();
        let by_dest = routes.iter().find(|v| v["destination"] == me);
        c["fed_by"] = json!(by_dest.map(|v| names(v, "sources")).unwrap_or_default());
        c["asked"] = json!(by_dest.filter(|v| v["state"] == "differs").map(|v| names(v, "wanted")).unwrap_or_default());
        c["feeds"] = json!(routes.iter().filter(|r| r["sources"].as_array().is_some_and(|s| s.iter().any(|x| x["ref"] == me))).map(|r| r["name"].clone()).collect::<Vec<_>>());
    }
    let slim = |c: &relay_audio::channels::Channel| json!({"id": c.id, "ref": c.reference(), "shown": c.shown, "kind": c.kind, "providers": c.providers});
    let suggestions: Vec<Value> = api.channels.suggestions().await?.iter().map(|(a, b, why)| json!({"a": slim(a), "b": slim(b), "why": why})).collect();
    Ok(json!({"channels": chans, "suggestions": suggestions}))
}

async fn channel_answer(api: &Api, done: Result<(), Error>) -> Response {
    let out = match done {
        Ok(()) => channels_view(api).await,
        Err(e) => Err(e),
    };
    match out {
        Ok(v) => Json(v).into_response(),
        Err(e) => refused(e, channel_status, "channels cannot be changed"),
    }
}

async fn channels_get(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), Some("network.view")).await {
        return r;
    }
    match channels_view(&api).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => refused(e, channel_status, "channels cannot be read"),
    }
}

macro_rules! manage {
    ($api:expr, $conn:expr, $req:expr, $perm:expr) => {{
        let caller = match guard(&$api, $conn, $req.headers(), $perm).await {
            Ok(c) => c,
            Err(r) => return r,
        };
        let body = match object(&read($req).await) {
            Ok(b) => b,
            Err(r) => return r,
        };
        (caller, body)
    }};
}

fn ref_of(v: Option<&Value>) -> Option<i64> {
    relay_audio::channels::channel_id(v.unwrap_or(&Value::Null))
}

async fn channel_merge(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("network.manage"));
    let (Some(keep), Some(other)) = (ref_of(body.get("keep")), ref_of(body.get("other"))) else { return error(400, sentence("no_channel")) };
    let done = api.channels.merge(keep, other).await.map(|_| ());
    let out = channel_answer(&api, done).await;
    api.acc.record(&who, "network.channel_merge", "Joined two channels into one", "", Some(json!({"keep": relay_audio::channels::reference(keep), "other": relay_audio::channels::reference(other)}))).await;
    out
}

async fn channel_split(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("network.manage"));
    let Some(end) = body.get("end").and_then(Value::as_i64) else { return error(400, sentence("no_end")) };
    let done = api.channels.split(end).await.map(|_| ());
    let out = channel_answer(&api, done).await;
    api.acc.record(&who, "network.channel_split", "Split a channel apart", "", Some(json!({"end": end}))).await;
    out
}

async fn channel_decline(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("network.manage"));
    let (Some(a), Some(b)) = (ref_of(body.get("a")), ref_of(body.get("b"))) else { return error(400, sentence("no_channel")) };
    let done = api.channels.decline(a, b).await;
    let out = channel_answer(&api, done).await;
    api.acc
        .record(&who, "network.channel_decline", "Said two channels are not the same", "", Some(json!({"a": relay_audio::channels::reference(a), "b": relay_audio::channels::reference(b)})))
        .await;
    out
}

async fn channel_rename(State(api): State<Api>, Path(cid): Path<i64>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("network.manage"));
    let name = match body.get("name") {
        None => "",
        Some(Value::String(s)) if s.chars().count() <= relay_audio::channels::NAME_MAX => s.as_str(),
        _ => return error(400, &format!("A name is up to {} characters.", relay_audio::channels::NAME_MAX)),
    };
    let target = relay_audio::channels::reference(cid);
    if body.get("device") == Some(&Value::Bool(true)) {
        let done = api.channels.rename_on_device(cid, name).await;
        let ok = done.is_ok();
        let out = channel_answer(&api, done).await;
        if ok {
            api.acc.record(&who, "network.channel_rename_device", "Renamed a channel on the gear", &target, Some(json!({"name": name}))).await;
        }
        return out;
    }
    let done = api.channels.rename(cid, name).await;
    let ok = done.is_ok();
    let out = channel_answer(&api, done).await;
    if ok {
        api.acc.record(&who, "network.channel_rename", "Renamed a channel in Relay only", &target, Some(json!({"name": name}))).await;
    }
    out
}

async fn channel_note(State(api): State<Api>, Path(cid): Path<i64>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("network.manage"));
    let text = match body.get("text") {
        None => "",
        Some(Value::String(s)) if s.chars().count() <= relay_audio::channels::NOTE_MAX => s.as_str(),
        _ => return error(400, &format!("A note is up to {} characters.", relay_audio::channels::NOTE_MAX)),
    };
    let done = api.channels.set_note(cid, text).await;
    let out = channel_answer(&api, done).await;
    api.acc.record(&who, "network.channel_note", "Changed a note on a channel", &relay_audio::channels::reference(cid), None).await;
    out
}

async fn channel_forget(State(api): State<Api>, Path(cid): Path<i64>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), Some("network.manage")).await {
        Ok(c) => c,
        Err(r) => return r,
    };
    let done = api.channels.forget(cid).await;
    let out = channel_answer(&api, done).await;
    api.acc.record(&who, "network.channel_forget", "Forgot a channel", &relay_audio::channels::reference(cid), None).await;
    out
}

// -------------------------------------------------------------------------------------------------------------- routes
#[derive(serde::Deserialize, Default)]
struct RoutesQuery {
    #[serde(default)]
    provider: String,
    #[serde(default)]
    channel: String,
}

fn query_of(req: &Request) -> RoutesQuery {
    serde_urlencoded::from_str(req.uri().query().unwrap_or("")).unwrap_or_default()
}

async fn routes_view(api: &Api, provider: &str, feed: &str) -> Result<Value, Error> {
    let mut out = json!({"routes": api.routing.listing(provider, None).await?, "salvos": api.routing.salvos(None).await?});
    if !feed.is_empty() {
        out["feeds"] = json!(api.routing.feeds(&json!(feed)).await?);
        out["feeding"] = json!(api.routing.feeding(&json!(feed)).await?);
    }
    Ok(out)
}

fn route_down<T>(r: Result<T, Error>) -> Result<T, Response> {
    r.map_err(|e| refused(e, route_status, "routes cannot be changed"))
}

async fn routes_get(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), Some("network.view")).await {
        return r;
    }
    let q = query_of(&req);
    let (provider, feed): (String, String) = (q.provider.chars().take(32).collect(), q.channel.chars().take(60).collect());
    match route_down(routes_view(&api, &provider, &feed).await) {
        Ok(v) => Json(v).into_response(),
        Err(r) => r,
    }
}

/// 403 unless the user holds network.manage or the permission the destination's module names.
async fn may_route(api: &Api, who: &Caller, destination: &Value) -> Option<Response> {
    match api.routing.may_route(destination, &|p| who.user.can(p)).await {
        Ok(true) => None,
        Ok(false) => Some(error(403, NOT_ALLOWED)),
        Err(_) => Some(down("routes cannot be changed")),
    }
}

async fn route_set(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, None);
    let dest = body.get("destination").cloned().unwrap_or(Value::Null);
    if let Some(no) = may_route(&api, &who, &dest).await {
        return no;
    }
    let source = body.get("source").cloned().unwrap_or(Value::Null);
    let got = match route_down(api.routing.set(&dest, &source, body.get("gain_db").unwrap_or(&Value::Null), true).await) {
        Ok(g) => g,
        Err(r) => return r,
    };
    api.acc.record(&who, "routes.set", "Routed a source to a destination", "", Some(json!({"destination": dest, "source": source}))).await;
    Json(json!({"status": got["status"], "problems": got["problems"]})).into_response()
}

async fn route_clear(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, None);
    let dest = body.get("destination").cloned().unwrap_or(Value::Null);
    if let Some(no) = may_route(&api, &who, &dest).await {
        return no;
    }
    let got = match route_down(api.routing.clear(&dest, body.get("source").unwrap_or(&Value::Null), true).await) {
        Ok(g) => g,
        Err(r) => return r,
    };
    api.acc.record(&who, "routes.clear", "Took a destination off a source", "", Some(json!({"destination": dest}))).await;
    Json(json!({"status": got["status"], "problems": got["problems"]})).into_response()
}

async fn salvo_save(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("network.manage"));
    let Some(dests) = body.get("destinations").and_then(Value::as_array).filter(|d| d.len() <= 500) else { return error(400, "Choose the destinations to save.") };
    let sid = body.get("id").and_then(Value::as_i64);
    let scope = body.get("scope").and_then(Value::as_str).unwrap_or("");
    let name = body.get("name").and_then(Value::as_str).unwrap_or("");
    match route_down(api.routing.save_salvo(name, dests, scope, sid).await) {
        Ok(got) => {
            api.acc.record(&who, "routes.salvo_save", "Saved a route preset", got["name"].as_str().unwrap_or(""), None).await;
            Json(got).into_response()
        }
        Err(r) => r,
    }
}

async fn salvo_take(State(api): State<Api>, Path(sid): Path<i64>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), Some("network.manage")).await {
        Ok(c) => c,
        Err(r) => return r,
    };
    match route_down(api.routing.take_salvo(sid).await) {
        Ok(problems) => {
            api.acc.record(&who, "routes.salvo_take", "Took a route preset", &sid.to_string(), None).await;
            Json(json!({"problems": problems})).into_response()
        }
        Err(r) => r,
    }
}

async fn salvo_delete(State(api): State<Api>, Path(sid): Path<i64>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), Some("network.manage")).await {
        Ok(c) => c,
        Err(r) => return r,
    };
    match route_down(api.routing.delete_salvo(sid).await) {
        Ok(()) => {
            api.acc.record(&who, "routes.salvo_delete", "Deleted a route preset", &sid.to_string(), None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(r) => r,
    }
}

// ---------------------------------------------------------------------------------------------------------- audio bus
/// One second of a 1 kHz tone with a short fade at each end: the test tone (`app/tones.py`).
fn test_tone(seconds: f32, hz: f32) -> Vec<f32> {
    let rate = relay_audio::bus::LANE_RATE as f32;
    let n = (seconds * rate) as usize;
    let fade = (0.01 * rate) as usize;
    (0..n)
        .map(|i| {
            let edge = i.min(n - 1 - i);
            let env = if edge < fade { edge as f32 / fade as f32 } else { 1.0 };
            0.3 * env * (2.0 * std::f32::consts::PI * hz * i as f32 / rate).sin()
        })
        .collect()
}

async fn audiobus_get(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), Some("session.view")).await {
        return r;
    }
    let registry = match &api.app {
        Some(app) => app.audio_registry().await,
        None => json!({"sources": [{"id": "portaudio", "label": "Sound card"}], "outputs": [{"id": "portaudio", "label": "Sound card"}]}),
    };
    let mut page = api.bus.page().await;
    page["sources"] = registry["sources"].clone();
    page["outputs"] = registry["outputs"].clone();
    page["input_devices"] = json!(api.bus.devices(true));
    page["output_devices"] = json!(api.bus.devices(false));
    Json(page).into_response()
}

async fn audiobus_test(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), Some("audio.manage")).await {
        Ok(c) => c,
        Err(r) => return r,
    };
    let reached = api.bus.play_to("test", &test_tone(2.0, 1000.0), relay_audio::bus::LANE_RATE);
    api.acc.record(&who, "audio.bus", "Played the audio bus test tone", "", None).await;
    Json(json!({"reached": reached, "message": if reached > 0 { TEST_PLAYED } else { TEST_UNPATCHED }})).into_response()
}

async fn audiobus_setup(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("audio.manage"));
    let outputs = api.bus.devices(false);
    let width = move |label: &str| outputs.iter().find(|d| d["label"] == label).and_then(|d| d["channels"].as_i64());
    let done = api.bus.setup(body.get("input").unwrap_or(&Value::Null), body.get("output"), width).await;
    match done {
        Ok(v) => {
            api.acc.record(&who, "audio.bus", "Finished the first audio setup", "", None).await;
            Json(v).into_response()
        }
        Err(e) => refused(e, route_status, "the routes cannot be saved"),
    }
}

async fn audiobus_save(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("audio.manage"));
    match api.bus.save(&Value::Object(body)).await {
        Ok(v) => {
            let cfg = &v["config"];
            let n = |k: &str| cfg[k].as_array().map_or(0, Vec::len);
            api.acc.record(&who, "audio.bus", "Changed the audio bus", "", Some(json!({"lanes": n("lanes"), "outputs": n("outputs"), "crosspoints": n("matrix")}))).await;
            Json(v).into_response()
        }
        Err(e) => refused(e, route_status, "the crosspoints cannot be saved"),
    }
}

// ----------------------------------------------------------------------------------------------------------- metering
async fn metering_get(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), Some("session.view")).await {
        return r;
    }
    Json(json!({"config": api.metering.config(), "status": api.metering.snapshots(), "lanes": LANES})).into_response()
}

async fn metering_save(State(api): State<Api>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("audio.manage"));
    let out = api.metering.save(&Value::Object(body)).await;
    let meters: Vec<Value> = out["config"]["meters"].as_array().into_iter().flatten().map(|m| json!({"id": m["id"], "lane": m["lane"], "kind": m["kind"], "enabled": m["enabled"]})).collect();
    api.acc.record(&who, "audio.metering", "Changed the sound meters", "", Some(json!({"meters": meters}))).await;
    Json(out).into_response()
}

async fn metering_calibrate(State(api): State<Api>, Path(mid): Path<String>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let (who, body) = manage!(api, conn, req, Some("audio.manage"));
    if !api.metering.config()["meters"].as_array().is_some_and(|m| m.iter().any(|x| x["id"] == mid.as_str())) {
        return error(404, "There is no such meter.");
    }
    match api.metering.calibrate(&mid, body.get("reference_db").unwrap_or(&Value::Null)) {
        Ok(cal) => {
            api.acc.record(&who, "audio.metering", "Calibrated a sound meter", "", Some(json!({"meter": mid, "calibration_db": cal}))).await;
            Json(api.metering.save_calibration(&mid, cal)).into_response()
        }
        Err(reason) => error(409, relay_audio::metering::calibrate_sentence(reason)),
    }
}

/// Every route this file answers. Anything else on /api/admin is forwarded to the Python app.
pub fn routes(api: Api) -> Router {
    Router::new()
        .route("/api/admin/network/channels", get(channels_get))
        .route("/api/admin/network/channels/merge", post(channel_merge))
        .route("/api/admin/network/channels/split", post(channel_split))
        .route("/api/admin/network/channels/decline", post(channel_decline))
        .route("/api/admin/network/channels/{cid}/rename", post(channel_rename))
        .route("/api/admin/network/channels/{cid}/note", post(channel_note))
        .route("/api/admin/network/channels/{cid}", delete(channel_forget))
        .route("/api/admin/routes", get(routes_get).post(route_set))
        .route("/api/admin/routes/clear", post(route_clear))
        .route("/api/admin/routes/salvos", post(salvo_save))
        .route("/api/admin/routes/salvos/{sid}/take", post(salvo_take))
        .route("/api/admin/routes/salvos/{sid}", delete(salvo_delete))
        .route("/api/admin/audiobus", get(audiobus_get).post(audiobus_save))
        .route("/api/admin/audiobus/test", post(audiobus_test))
        .route("/api/admin/audiobus/setup", post(audiobus_setup))
        .route("/api/admin/metering", get(metering_get).post(metering_save))
        .route("/api/admin/metering/{mid}/calibrate", post(metering_calibrate))
        .with_state(api)
}

/// Start the audio bus and meters on `relay.db` and `config.json`, and return what the routes share. `None` (and the reason in the log)
/// when the database does not have the channels and routes tables yet: the app behind the server serves them instead.
pub async fn start(
    pool: relay_audio::Pool,
    acc: Shared,
    config_path: &std::path::Path,
    app: Option<Arc<PyApp>>,
    shutdown: relay_core::Shutdown,
) -> Option<Api> {
    if let Err(why) = relay_audio::check_tables(&pool).await {
        tracing::error!("the audio bus is served by the app behind this server, not by it: {why}");
        return None;
    }
    let store = Arc::new(ConfigStore(Arc::new(relay_config::Settings::new(config_path))));
    let channels = Channels::new(pool);
    let routing = Routing::new(channels.clone());
    let bus = AudioBus::new(relay_audio::Backing::Cpal, store.clone(), channels.clone(), routing.clone());
    let metering = Metering::new(bus.clone(), store);
    let gear = app.as_ref().map(crate::pyapp::gear);
    if let (Some(app), Some(gear)) = (&app, &gear) {
        channels.set_gear(Some(gear.clone()));
        crate::pyapp::refresh(app, &routing, gear).await;
    }
    bus.start().await;
    metering.start().await;
    bus.report_input_channels().await;
    if let (Some(app), Some(gear)) = (app.clone(), gear) {
        let (routing, shutdown) = (routing.clone(), shutdown.clone());
        tokio::spawn(async move {
            loop {
                tokio::select! {
                    () = shutdown.wait() => break,
                    () = tokio::time::sleep(std::time::Duration::from_secs(5)) => crate::pyapp::refresh(&app, &routing, &gear).await,
                }
            }
        });
    }
    {
        let bus = bus.clone();
        tokio::spawn(async move {
            shutdown.wait().await;
            metering_stop(&bus);
        });
    }
    Some(Arc::new(AudioApi { acc, bus, routing, channels, metering, app }))
}

fn metering_stop(bus: &Arc<AudioBus>) {
    bus.close();
}

