//! What the Python app behind the server calls on the audio bus while it still runs modules that use it (#657): captions read a lane,
//! talkback and the alert tones play into an output, and a module's network source or sender (NDI, Dante, AES67) is read and fed by
//! Python. The engine lives here; these routes are the whole of its door for Python (`app/audiobus_remote.py` is the caller).
//!
//! Loopback only and `X-Relay-Front: <token>` (the same secret `front.rs` uses): a client on the LAN gets a plain 404, as if no
//! such route existed. Audio travels as raw little-endian `f32`, interleaved by channel, so no sample is ever turned into text.

use std::pin::Pin;
use std::task::{Context, Poll};
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::{Path, Request, State};
use axum::http::header::CONTENT_TYPE;
use axum::http::{HeaderMap, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use futures_core::Stream;
use serde_json::{json, Value};
use subtle::ConstantTimeEq;
use tokio::sync::mpsc;

use crate::audio::Api;
use crate::forward::Conn;

const WAIT_MAX: Duration = Duration::from_secs(25);
const TAP_READ: usize = 4800;

/// The server's secret, and the one address a call may come from.
#[derive(Clone)]
pub struct Door {
    pub api: Api,
    pub token: String,
}

fn not_found() -> Response {
    StatusCode::NOT_FOUND.into_response()
}

fn loopback(conn: Option<&Conn>) -> bool {
    conn.is_some_and(|c| c.peer.ip().is_loopback() || matches!(c.peer.ip(), std::net::IpAddr::V6(v6) if v6.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback())))
}

fn allowed(door: &Door, headers: &HeaderMap, conn: Option<&Conn>) -> bool {
    let given = headers.get("x-relay-front").and_then(|v| v.to_str().ok()).unwrap_or("");
    !door.token.is_empty() && loopback(conn) && bool::from(given.as_bytes().ct_eq(door.token.as_bytes()))
}

macro_rules! door {
    ($door:expr, $headers:expr, $conn:expr) => {
        if !allowed(&$door, &$headers, $conn.as_ref().map(|c| &c.0)) {
            return not_found();
        }
    };
}

fn query(req: &Request) -> std::collections::HashMap<String, String> {
    serde_urlencoded::from_str(req.uri().query().unwrap_or("")).unwrap_or_default()
}

fn floats(body: &Bytes) -> Vec<f32> {
    body.as_chunks::<4>().0.iter().map(|b| f32::from_le_bytes(*b)).collect()
}

fn pcm(samples: &[f32]) -> Response {
    let mut out = Vec::with_capacity(samples.len() * 4);
    for s in samples {
        out.extend_from_slice(&s.to_le_bytes());
    }
    ([(CONTENT_TYPE, "application/octet-stream")], out).into_response()
}

async fn body_of(req: Request) -> Bytes {
    axum::body::to_bytes(req.into_body(), 16 << 20).await.unwrap_or_default()
}

fn number<T: std::str::FromStr>(q: &std::collections::HashMap<String, String>, key: &str, default: T) -> T {
    q.get(key).and_then(|v| v.parse().ok()).unwrap_or(default)
}

/// `GET config?since=N`: the setting the bus is running, once it differs from generation `N` (or after a wait), so a Python driver of a
/// network source or sender follows the bus without polling.
async fn config(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    door!(door, headers, conn);
    let since: Option<u64> = query(&req).get("since").and_then(|v| v.parse().ok());
    let mut rx = door.api.bus.subscribe();
    let now = *rx.borrow_and_update();
    if since == Some(now) {
        let _ = tokio::time::timeout(WAIT_MAX, rx.changed()).await;
    }
    let gen = *rx.borrow();
    let bus = &door.api.bus;
    Json(json!({"gen": gen, "running": bus.running(), "config": bus.config()})).into_response()
}

async fn status(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>) -> Response {
    door!(door, headers, conn);
    Json(door.api.bus.status()).into_response()
}

async fn external(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, Json(state): Json<Value>) -> Response {
    door!(door, headers, conn);
    door.api.bus.set_external(state);
    Json(json!({"ok": true})).into_response()
}

async fn reapply(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>) -> Response {
    door!(door, headers, conn);
    let problem = door.api.bus.reapply().await;
    Json(json!({"problem": problem})).into_response()
}

async fn push(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    door!(door, headers, conn);
    let q = query(&req);
    let (id, channels, rate) = (q.get("input").cloned().unwrap_or_default(), number(&q, "channels", 1usize), number(&q, "rate", 48_000u32));
    let data = floats(&body_of(req).await);
    Json(json!({"ok": channels > 0 && door.api.bus.push(&id, &data, channels, rate)})).into_response()
}

async fn pull(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    door!(door, headers, conn);
    let q = query(&req);
    match door.api.bus.pull(q.get("output").map(String::as_str).unwrap_or(""), number(&q, "frames", 480usize).min(48_000)) {
        Some(block) => pcm(&block),
        None => StatusCode::GONE.into_response(),
    }
}

async fn play(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    door!(door, headers, conn);
    let q = query(&req);
    let (key, rate) = (q.get("key").cloned().unwrap_or_default(), number(&q, "rate", 48_000u32));
    let reached = door.api.bus.play_to(&key, &floats(&body_of(req).await), rate);
    Json(json!({"reached": reached})).into_response()
}

async fn inject(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    door!(door, headers, conn);
    let q = query(&req);
    let channels: Vec<usize> = q.get("channels").map(|c| c.split(',').filter_map(|n| n.parse().ok()).collect()).unwrap_or_default();
    let output = q.get("output").cloned().unwrap_or_default();
    let rate = number(&q, "rate", 48_000u32);
    let samples = floats(&body_of(req).await);
    let done = door.api.bus.inject(&output, &channels, &samples, rate);
    Json(json!({"ok": done})).into_response()
}

async fn stop_injects(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    door!(door, headers, conn);
    let output = query(&req).get("output").cloned().unwrap_or_default();
    Json(json!({"ok": door.api.bus.stop_injects(&output)})).into_response()
}

async fn destinations(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, Json(body): Json<Value>) -> Response {
    door!(door, headers, conn);
    let bus = &door.api.bus;
    if let (Some(key), Some(label)) = (body["register"]["key"].as_str(), body["register"]["label"].as_str()) {
        bus.register_destination(key, label);
    }
    if let Some(key) = body["unregister"].as_str() {
        bus.unregister_destination(key);
    }
    let cfg = bus.config();
    bus.report_channels(&cfg).await;
    Json(json!({"ok": true})).into_response()
}

/// A channel of a tap on one lane: one block per message, each as a 4-byte little-endian length and then the samples.
struct Blocks(mpsc::Receiver<Bytes>);

impl Stream for Blocks {
    type Item = Result<Bytes, std::convert::Infallible>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.0.poll_recv(cx).map(|b| b.map(Ok))
    }
}

/// `GET tap/{lane}`: a stream of the lane's audio (mono, 48 kHz) until the caller hangs up. The engine's audio threads never wait for
/// it: a reader that falls behind loses frames, and the loss is counted in the status.
async fn tap(State(door): State<Door>, Path(lane): Path<usize>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>) -> Response {
    door!(door, headers, conn);
    if !(1..=32).contains(&lane) {
        return StatusCode::BAD_REQUEST.into_response();
    }
    let bus = door.api.bus.clone();
    let Some(id) = bus.open_tap(lane) else { return StatusCode::SERVICE_UNAVAILABLE.into_response() };
    let (tx, rx) = mpsc::channel::<Bytes>(64);
    let spawned = std::thread::Builder::new().name(format!("lane-{lane}-tap")).spawn(move || {
        while let Some(read) = bus.read_tap(id, TAP_READ, Duration::from_millis(100)) {
            if read.samples.is_empty() {
                if tx.is_closed() {
                    break;
                }
                continue;
            }
            let mut out = Vec::with_capacity(4 + read.samples.len() * 4);
            out.extend_from_slice(&((read.samples.len() * 4) as u32).to_le_bytes());
            for s in &read.samples {
                out.extend_from_slice(&s.to_le_bytes());
            }
            if tx.blocking_send(Bytes::from(out)).is_err() {
                break;
            }
        }
        bus.close_tap(id);
    });
    if spawned.is_err() {
        return StatusCode::SERVICE_UNAVAILABLE.into_response();
    }
    ([(CONTENT_TYPE, "application/octet-stream")], Body::from_stream(Blocks(rx))).into_response()
}

// -- metering ---------------------------------------------------------------------------------------------------------
async fn meter_public(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>) -> Response {
    door!(door, headers, conn);
    Json(json!({"public": door.api.metering.public(), "snapshots": door.api.metering.snapshots(),
                "choices": door.api.metering.choices()})).into_response()
}

async fn meter_source(State(door): State<Door>, headers: HeaderMap, conn: Option<axum::Extension<Conn>>, Json(body): Json<Value>) -> Response {
    door!(door, headers, conn);
    let m = &door.api.metering;
    if let (Some(id), Some(label)) = (body["register"]["id"].as_str(), body["register"]["label"].as_str()) {
        m.register_source(id, label).await;
    }
    if let Some(id) = body["unregister"].as_str() {
        m.unregister_source(id).await;
    }
    if let Some(id) = body["publish"]["id"].as_str() {
        m.publish(id, &body["publish"]["reading"]);
    }
    Json(json!({"ok": true})).into_response()
}

/// Every route this file answers. They are not under `/api/admin`: no browser and no API token ever reaches them.
pub fn routes(api: Api, token: &str) -> Router {
    Router::new()
        .route("/internal/audio/config", get(config))
        .route("/internal/audio/status", get(status))
        .route("/internal/audio/external", post(external))
        .route("/internal/audio/reapply", post(reapply))
        .route("/internal/audio/push", post(push))
        .route("/internal/audio/pull", get(pull))
        .route("/internal/audio/play", post(play))
        .route("/internal/audio/inject", post(inject))
        .route("/internal/audio/stop_injects", post(stop_injects))
        .route("/internal/audio/destinations", post(destinations))
        .route("/internal/audio/tap/{lane}", get(tap))
        .route("/internal/metering", get(meter_public).post(meter_source))
        .with_state(Door { api, token: token.to_owned() })
}
