//! The routes this slice serves (`routes.py`): the sources list and a scan. Gated by `ndi.view` and `ndi.manage` like Python's.

use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Path, Query, Request, State};
use axum::http::header;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use relay_core::access::gate;
use relay_core::Host;
use bytes::Bytes;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tokio_stream::wrappers::ReceiverStream;

use crate::pool::LOW;
use crate::viewer::{part, BOUNDARY, IDLE_WAIT};

use crate::{Service, Slot};

fn svc(slot: &Slot) -> Result<Arc<Service>, Response> {
    slot.lock().ok().and_then(|s| s.clone()).ok_or_else(|| (StatusCode::SERVICE_UNAVAILABLE, Json(json!({"detail": "NDI is not available right now"}))).into_response())
}

async fn snapshot(State(slot): State<Slot>) -> Response {
    match svc(&slot) {
        Ok(s) => Json(s.snapshot_full().await).into_response(),
        Err(r) => r,
    }
}

async fn scan(State(slot): State<Slot>) -> Response {
    match svc(&slot) {
        Ok(s) => {
            if !s.running() {
                return (StatusCode::CONFLICT, Json(json!({"detail": "NDI is not running."}))).into_response();
            }
            s.scan().await;
            Json(s.snapshot_full().await).into_response()
        }
        Err(r) => r,
    }
}

fn error(status: StatusCode, text: &str) -> Response {
    (status, [(header::CACHE_CONTROL, "no-store")], Json(json!({"error": text}))).into_response()
}

/// The source as MJPEG: each new JPEG as a part, until the source goes quiet or the browser leaves.
async fn stream(State(slot): State<Slot>, Path(source): Path<String>) -> Response {
    let Ok(s) = svc(&slot) else { return error(StatusCode::NOT_FOUND, "That NDI source is not available.") };
    if !s.running() || !s.known(&source) {
        return error(StatusCode::NOT_FOUND, "That NDI source is not available.");
    }
    let opened = {
        let s = Arc::clone(&s);
        let source = source.clone();
        tokio::task::spawn_blocking(move || s.viewers.open(&source)).await.ok().flatten()
    };
    let Some(feed) = opened else {
        return error(StatusCode::SERVICE_UNAVAILABLE, "Too many sources are being watched, or this one cannot be received. Try again in a moment.");
    };
    let s2 = Arc::clone(&s);
    let f2 = Arc::clone(&feed);
    mjpeg(feed, move || {
        drop(tokio::task::spawn_blocking(move || s2.viewers.close(&f2)));
    })
}

/// The multipart body for one browser: each new JPEG as a part, until the source goes quiet or the browser leaves; then `close` runs.
fn mjpeg(feed: Arc<crate::viewer::Feed>, close: impl FnOnce() + Send + 'static) -> Response {
    let (tx, rx) = mpsc::channel::<Result<Bytes, std::convert::Infallible>>(2);
    tokio::spawn(async move {
        let mut seq = 0;
        while let Some((n, jpeg)) = feed.next(seq, IDLE_WAIT).await {
            seq = n;
            if tx.send(Ok(part(&jpeg))).await.is_err() {
                break;
            }
        }
        close();
    });
    (
        [(header::CONTENT_TYPE, format!("multipart/x-mixed-replace; boundary={BOUNDARY}")), (header::CACHE_CONTROL, "no-store".to_owned()), (header::HeaderName::from_static("x-accel-buffering"), "no".to_owned())],
        Body::from_stream(ReceiverStream::new(rx)),
    )
        .into_response()
}

/// A multiview composed once however many browsers watch it, as MJPEG.
async fn multiview_stream(State(slot): State<Slot>, Path(mid): Path<String>) -> Response {
    let Ok(s) = svc(&slot) else { return error(StatusCode::NOT_FOUND, "That multiview is not available.") };
    let opened = if s.running() {
        let (s2, mid2) = (Arc::clone(&s), mid.clone());
        tokio::task::spawn_blocking(move || s2.multiviews.open(&mid2)).await.ok().flatten()
    } else {
        None
    };
    let Some(comp) = opened else { return error(StatusCode::NOT_FOUND, "That multiview is not available.") };
    let (s2, c2) = (Arc::clone(&s), Arc::clone(&comp));
    mjpeg(Arc::clone(&comp.feed), move || {
        drop(tokio::task::spawn_blocking(move || s2.multiviews.close(&c2)));
    })
}

fn bad(text: &str, status: StatusCode) -> Response {
    (status, Json(json!({"detail": text}))).into_response()
}

fn akey(source: &str, low: bool) -> String {
    if low {
        format!("{source}{LOW}")
    } else {
        source.to_owned()
    }
}

fn online(s: &Service, source: &Value) -> Option<String> {
    let name = source.as_str()?;
    s.known(name).then(|| name.to_owned())
}

/// The audio peak in dBFS (null when there is none), each channel's peak and RMS, the loudness, whether the source is audio only, and the
/// frame size: for the viewer's meters and caption.
async fn level(State(slot): State<Slot>, Path(source): Path<String>) -> Response {
    let Ok(s) = svc(&slot) else { return error(StatusCode::NOT_FOUND, "That NDI source is not available.") };
    let Some(src) = s.sources().into_iter().find(|x| x["name"] == source && x["online"] == true) else {
        return error(StatusCode::NOT_FOUND, "That NDI source is not available.");
    };
    let mut out = json!({"db": s.analysis.audio_level(&source), "width": src["width"], "height": src["height"], "fps": src["fps"]});
    if let (Some(o), Some(extra)) = (out.as_object_mut(), s.analysis.audio_state(&source).as_object()) {
        o.extend(extra.clone());
    }
    ([(header::CACHE_CONTROL, "no-store")], Json(out)).into_response()
}

#[derive(serde::Deserialize)]
struct AnalysisQuery {
    source: String,
    #[serde(default)]
    low: bool,
    scopes: Option<bool>,
}

async fn analysis_open(State(slot): State<Slot>, Json(payload): Json<Value>) -> Response {
    let low = match payload.get("low") {
        None => false,
        Some(Value::Bool(b)) => *b,
        Some(_) => return bad("Low bandwidth is on or off.", StatusCode::BAD_REQUEST),
    };
    let Ok(s) = svc(&slot) else { return bad("That NDI source is not available.", StatusCode::NOT_FOUND) };
    let Some(source) = online(&s, &payload["source"]).filter(|_| s.running()) else { return bad("That NDI source is not available.", StatusCode::NOT_FOUND) };
    let opened = {
        let s = Arc::clone(&s);
        let source = source.clone();
        tokio::task::spawn_blocking(move || s.analysis_open(&source, low)).await.ok().flatten()
    };
    match opened {
        Some(key) => Json(s.analysis_snapshot(&key, true)).into_response(),
        None => bad("That source cannot be received.", StatusCode::CONFLICT),
    }
}

/// The newest numbers. Asking also keeps the analysis open. `scopes=false` leaves out the picture scopes.
async fn analysis_read(State(slot): State<Slot>, Query(q): Query<AnalysisQuery>) -> Response {
    let Ok(s) = svc(&slot) else { return bad("That analysis is not open.", StatusCode::NOT_FOUND) };
    let key = akey(&q.source, q.low);
    if !s.analysis_is_open(&key) {
        return bad("That analysis is not open.", StatusCode::NOT_FOUND);
    }
    let s2 = Arc::clone(&s);
    let (source, low, want) = (q.source.clone(), q.low, q.scopes.unwrap_or(true));
    let snap = tokio::task::spawn_blocking(move || {
        s2.analysis_open(&source, low);
        s2.analysis_snapshot(&akey(&source, low), want)
    })
    .await;
    match snap {
        Ok(v) => ([(header::CACHE_CONTROL, "no-store")], Json(v)).into_response(),
        Err(_) => bad("That analysis is not open.", StatusCode::NOT_FOUND),
    }
}

async fn analysis_close(State(slot): State<Slot>, Query(q): Query<AnalysisQuery>) -> Response {
    if let Ok(s) = svc(&slot) {
        s.analysis_close(&akey(&q.source, q.low));
    }
    Json(json!({"ok": true})).into_response()
}

fn key_of(payload: &Value) -> String {
    akey(payload["source"].as_str().unwrap_or(""), payload["low"].as_bool().unwrap_or(false))
}

/// Start the loudness and clip counts again.
async fn analysis_reset(State(slot): State<Slot>, Json(payload): Json<Value>) -> Response {
    match svc(&slot) {
        Ok(s) if s.analysis.reset_audio(&key_of(&payload)) => Json(json!({"ok": true})).into_response(),
        _ => bad("That analysis is not open.", StatusCode::NOT_FOUND),
    }
}

/// Record per-frame timing to a CSV for `seconds` (1 to 3600). Needs the analysis open.
async fn analysis_record(State((host, slot)): State<(Host, Slot)>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let Ok(bytes) = axum::body::to_bytes(body, 64 * 1024).await else { return bad("That request is not valid.", StatusCode::BAD_REQUEST) };
    let payload: Value = serde_json::from_slice(&bytes).unwrap_or(Value::Null);
    let seconds = payload.get("seconds").cloned().unwrap_or(json!(60));
    let Some(seconds) = seconds.as_i64().filter(|n| (1..=3600).contains(n) && !seconds.is_boolean()) else {
        return bad("A recording is a whole number of seconds from 1 to 3600.", StatusCode::BAD_REQUEST);
    };
    let Ok(s) = svc(&slot) else { return bad("That NDI source is not available.", StatusCode::NOT_FOUND) };
    if online(&s, &payload["source"]).is_none() {
        return bad("That NDI source is not available.", StatusCode::NOT_FOUND);
    }
    let Some(rec) = s.analysis.record(&key_of(&payload), seconds) else { return bad("Open the analysis first.", StatusCode::CONFLICT) };
    host.note(&parts, "ndi.analysis", "Recorded NDI frame timing", payload["source"].as_str().unwrap_or(""), None).await;
    Json(rec.status()).into_response()
}

async fn analysis_record_stop(State(slot): State<Slot>, Json(payload): Json<Value>) -> Response {
    if let Ok(s) = svc(&slot) {
        s.analysis.stop_recording(&key_of(&payload));
    }
    Json(json!({"ok": true})).into_response()
}

/// The newest recording for the source, as a CSV to send to whoever runs the network.
async fn analysis_download(State(slot): State<Slot>, Query(q): Query<AnalysisQuery>) -> Response {
    let rec = svc(&slot).ok().and_then(|s| s.analysis.last_recording(&akey(&q.source, q.low)));
    let Some(rec) = rec else { return bad("There is no recording for that source yet.", StatusCode::NOT_FOUND) };
    let Ok(data) = tokio::fs::read(&rec.path).await else { return bad("There is no recording for that source yet.", StatusCode::NOT_FOUND) };
    let file = rec.path.file_name().and_then(|n| n.to_str()).unwrap_or("recording.csv").to_owned();
    ([(header::CONTENT_TYPE, "text/csv".to_owned()), (header::CACHE_CONTROL, "no-store".to_owned()), (header::CONTENT_DISPOSITION, format!("attachment; filename=\"{file}\""))], data).into_response()
}

// ------------------------------------------------------------------------------------------------ router (#298)
type Ctx = (Host, Slot);

/// The request's parts and its JSON body (empty and unreadable bodies are `null`).
async fn parts_and_payload(request: Request) -> (axum::http::request::Parts, Value) {
    let (parts, body) = request.into_parts();
    let payload = match axum::body::to_bytes(body, 64 * 1024).await {
        Ok(b) => serde_json::from_slice(&b).unwrap_or(Value::Null),
        Err(_) => Value::Null,
    };
    (parts, payload)
}

/// A router change that failed answers 409 with the sentence; one that worked is audited and answers the new snapshot.
async fn router_done(host: &Host, parts: &axum::http::request::Parts, s: &Service, what: &str, err: &str) -> Response {
    if !err.is_empty() {
        return bad(err, StatusCode::CONFLICT);
    }
    host.note(parts, "ndi.router", what, "", None).await;
    Json(s.snapshot_full().await).into_response()
}

fn router_of(slot: &Slot) -> Result<(Arc<Service>, crate::router::Router), Response> {
    let s = svc(slot)?;
    match s.router() {
        Some(r) => Ok((s, r)),
        None => Err(bad("NDI routing is not available right now.", StatusCode::SERVICE_UNAVAILABLE)),
    }
}

async fn router_add(State((host, slot)): State<Ctx>, request: Request) -> Response {
    let (parts, p) = parts_and_payload(request).await;
    let (s, r) = match router_of(&slot) {
        Ok(x) => x,
        Err(e) => return e,
    };
    let err = r.add_output(p["name"].as_str().unwrap_or("")).await.err().unwrap_or_default();
    router_done(&host, &parts, &s, "Added a routed NDI output", &err).await
}

async fn router_remove(State((host, slot)): State<Ctx>, Path(oid): Path<String>, request: Request) -> Response {
    let (parts, _) = parts_and_payload(request).await;
    let (s, r) = match router_of(&slot) {
        Ok(x) => x,
        Err(e) => return e,
    };
    let err = if r.remove_output(&oid).await { "" } else { "That output does not exist." };
    router_done(&host, &parts, &s, "Removed a routed NDI output", err).await
}

async fn router_route(State((host, slot)): State<Ctx>, Path(oid): Path<String>, request: Request) -> Response {
    let (parts, p) = parts_and_payload(request).await;
    let (s, r) = match router_of(&slot) {
        Ok(x) => x,
        Err(e) => return e,
    };
    let src = match p.get("source") {
        None => "",
        Some(Value::String(t)) => t.as_str(),
        Some(_) => return bad("A source is a name, or empty for none.", StatusCode::BAD_REQUEST),
    };
    let err = r.route(&oid, src).await;
    router_done(&host, &parts, &s, "Routed an NDI output", &err).await
}

async fn router_audio(State((host, slot)): State<Ctx>, Path(oid): Path<String>, request: Request) -> Response {
    let (parts, p) = parts_and_payload(request).await;
    let (s, r) = match router_of(&slot) {
        Ok(x) => x,
        Err(e) => return e,
    };
    let src = match p.get("source") {
        None => "",
        Some(Value::String(t)) => t.as_str(),
        Some(_) => return bad("A source is a name, or empty for the picture's own sound.", StatusCode::BAD_REQUEST),
    };
    let err = r.set_audio(&oid, src).await;
    router_done(&host, &parts, &s, "Set the sound of an NDI output", &err).await
}

async fn salvo_save(State((host, slot)): State<Ctx>, request: Request) -> Response {
    let (parts, p) = parts_and_payload(request).await;
    let (s, r) = match router_of(&slot) {
        Ok(x) => x,
        Err(e) => return e,
    };
    let err = r.save_salvo(p["name"].as_str().unwrap_or(""), p["id"].as_str().unwrap_or("")).await.err().unwrap_or_default();
    router_done(&host, &parts, &s, "Saved an NDI preset", &err).await
}

async fn salvo_delete(State((host, slot)): State<Ctx>, Path(sid): Path<String>, request: Request) -> Response {
    let (parts, _) = parts_and_payload(request).await;
    let (s, r) = match router_of(&slot) {
        Ok(x) => x,
        Err(e) => return e,
    };
    let err = if r.delete_salvo(&sid).await { "" } else { "That preset does not exist." };
    router_done(&host, &parts, &s, "Deleted an NDI preset", err).await
}

async fn salvo_take(State((host, slot)): State<Ctx>, Path(sid): Path<String>, request: Request) -> Response {
    let (parts, _) = parts_and_payload(request).await;
    let (s, r) = match router_of(&slot) {
        Ok(x) => x,
        Err(e) => return e,
    };
    let err = r.take(&sid).await;
    router_done(&host, &parts, &s, "Took an NDI preset", &err).await
}

/// Which page outputs are on, and their size and frame rate.
async fn admin_outputs(State((host, slot)): State<Ctx>, request: Request) -> Response {
    let (parts, p) = parts_and_payload(request).await;
    let Ok(s) = svc(&slot) else { return bad("NDI is not available right now", StatusCode::SERVICE_UNAVAILABLE) };
    let cur = s.settings();
    let max = crate::outputs::MAX_ON;
    let too_many = || bad(&format!("Switch on at most {max} outputs."), StatusCode::BAD_REQUEST);
    let on: Vec<String> = match p.get("on") {
        None => cur["out_on"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_owned)).collect(),
        Some(Value::Array(a)) if a.iter().all(Value::is_string) => a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect(),
        Some(_) => return too_many(),
    };
    let mut uniq = on.clone();
    uniq.sort();
    uniq.dedup();
    if uniq.len() > max {
        return too_many();
    }
    let known: Vec<String> = s.pages.defs().iter().filter_map(|d| d["id"].as_str().map(str::to_owned)).collect();
    if on.iter().any(|v| !known.contains(v)) {
        return bad("One of those outputs does not exist.", StatusCode::BAD_REQUEST);
    }
    let size = match p.get("size") {
        None => cur["out_size"].clone(),
        Some(v) if v.as_str().is_some_and(|x| crate::outputs::SIZES.iter().any(|(n, _, _)| *n == x)) => v.clone(),
        Some(_) => return bad("The size is 1280x720 or 1920x1080.", StatusCode::BAD_REQUEST),
    };
    let fps = match p.get("fps") {
        None => cur["out_fps"].clone(),
        Some(v) if v.as_i64().is_some_and(|f| (5..=30).contains(&f)) && v.is_i64() => v.clone(),
        Some(_) => return bad("Frames per second is a whole number from 5 to 30.", StatusCode::BAD_REQUEST),
    };
    s.save_settings(&json!({"out_on": on, "out_size": size, "out_fps": fps}));
    host.note(&parts, "ndi.outputs", "Changed which pages Relay sends as NDI", "", None).await;
    if s.running() {
        s.pages.sync().await;
    }
    Json(s.snapshot_full().await).into_response()
}

const TESTS_BAD_NAME: &str = "Give each test source its own name (letters, numbers, spaces), and use a size from the list.";

/// The test sources (a list; one without an id is new) and which are on.
async fn admin_tests(State((host, slot)): State<Ctx>, request: Request) -> Response {
    let (parts, p) = parts_and_payload(request).await;
    let Ok(s) = svc(&slot) else { return bad("NDI is not available right now", StatusCode::SERVICE_UNAVAILABLE) };
    let cur = s.settings();
    let raw = p.get("tests").cloned().unwrap_or_else(|| cur["tests"].clone());
    let Some(list) = raw.as_array().filter(|a| a.iter().all(Value::is_object)) else { return bad("The test sources are a list.", StatusCode::BAD_REQUEST) };
    let max = crate::settings::MAX_TESTS;
    if list.len() > max {
        return bad(&format!("Keep at most {max} test sources."), StatusCode::BAD_REQUEST);
    }
    let tests: Vec<Value> = list
        .iter()
        .map(|t| {
            let mut t = t.clone();
            if !t["id"].as_str().is_some_and(|i| !i.is_empty()) {
                t["id"] = json!(crate::settings::new_id());
            }
            t
        })
        .collect();
    let clean = crate::settings::clean_tests(Some(&json!(tests)));
    let clean_list = clean.as_array().cloned().unwrap_or_default();
    if clean_list.len() != tests.len() {
        return bad(TESTS_BAD_NAME, StatusCode::BAD_REQUEST);
    }
    let ids: Vec<&str> = clean_list.iter().filter_map(|t| t["id"].as_str()).collect();
    let max_on = crate::settings::MAX_TEST_ON;
    let on: Vec<String> = match p.get("on") {
        None => cur["tests_on"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_owned)).collect(),
        Some(Value::Array(a)) if a.iter().all(Value::is_string) => a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect(),
        Some(_) => return bad(&format!("Switch on at most {max_on} test sources at once."), StatusCode::BAD_REQUEST),
    };
    let mut uniq = on.clone();
    uniq.sort();
    uniq.dedup();
    if on.iter().any(|v| !ids.contains(&v.as_str())) || uniq.len() > max_on {
        return bad(&format!("Switch on at most {max_on} test sources at once."), StatusCode::BAD_REQUEST);
    }
    let taken: Vec<String> = s.pages.defs().iter().map(|d| crate::router::ndi_name(d["name"].as_str().unwrap_or("")).to_lowercase()).collect();
    if clean_list.iter().any(|t| taken.contains(&crate::testpatterns::test_ndi_name(t).to_lowercase())) {
        return bad("A page output already has that name.", StatusCode::BAD_REQUEST);
    }
    for gone in cur["tests"].as_array().into_iter().flatten().filter_map(|t| t["id"].as_str()).filter(|i| !ids.contains(i)) {
        s.tests.forget(gone);
    }
    s.save_settings(&json!({"tests": clean, "tests_on": on}));
    host.note(&parts, "ndi.tests", "Changed the NDI test patterns", "", None).await;
    if s.running() {
        let s2 = Arc::clone(&s);
        let _ = tokio::task::spawn_blocking(move || s2.tests.sync()).await;
    }
    Json(s.snapshot_full().await).into_response()
}

/// The picture a test source of the "Your own picture" kind shows: the PNG is the request body.
async fn admin_test_image(State((host, slot)): State<Ctx>, Path(tid): Path<String>, request: Request) -> Response {
    let (parts, body) = request.into_parts();
    let Ok(s) = svc(&slot) else { return bad("NDI is not available right now", StatusCode::SERVICE_UNAVAILABLE) };
    if !s.settings()["tests"].as_array().into_iter().flatten().any(|t| t["id"] == tid.as_str()) {
        return bad("That test source does not exist.", StatusCode::NOT_FOUND);
    }
    let Ok(raw) = axum::body::to_bytes(body, crate::testpatterns::MAX_IMAGE + 1024).await else { return bad("That picture is over 5 MB.", StatusCode::BAD_REQUEST) };
    let raw = raw.to_vec();
    let why = {
        let raw = raw.clone();
        tokio::task::spawn_blocking(move || crate::testpatterns::check_image(&raw)).await.unwrap_or("That PNG could not be read.")
    };
    if !why.is_empty() {
        return bad(why, StatusCode::BAD_REQUEST);
    }
    let path = s.tests.image_path(&tid);
    if let Some(dir) = path.parent() {
        let _ = tokio::fs::create_dir_all(dir).await;
    }
    if tokio::fs::write(&path, &raw).await.is_err() {
        return bad("The picture could not be saved.", StatusCode::INTERNAL_SERVER_ERROR);
    }
    host.note(&parts, "ndi.tests", "Uploaded a picture for an NDI test pattern", &tid, None).await;
    if s.running() {
        // Draw it again: the running source loaded the old picture when it started.
        let (s2, t2) = (Arc::clone(&s), tid.clone());
        let _ = tokio::task::spawn_blocking(move || s2.tests.restart(&t2)).await;
    }
    Json(s.snapshot_full().await).into_response()
}

#[derive(serde::Deserialize)]
struct ListenQuery {
    #[serde(default)]
    ch: String,
}

/// The source's sound as endless 16-bit stereo PCM (little endian) for a browser to play; `ch=1,2` picks channels (counted from 1; none means
/// all). The sample rate is in the `X-Rate` header and the source's channel count in `X-Channels`.
async fn listen(State(slot): State<Slot>, Path(source): Path<String>, Query(q): Query<ListenQuery>) -> Response {
    let Ok(s) = svc(&slot) else { return error(StatusCode::NOT_FOUND, "That NDI source is not available.") };
    if !s.running() || !s.known(&source) {
        return error(StatusCode::NOT_FOUND, "That NDI source is not available.");
    }
    let Some(pick) = crate::listen::parse_channels(&q.ch) else { return error(StatusCode::BAD_REQUEST, "Channels are numbers from 1, like 1,2.") };
    if !s.listeners.take() {
        return error(StatusCode::SERVICE_UNAVAILABLE, &format!("At most {} people can listen at once. Try again later.", crate::listen::MAX_LISTENERS));
    }
    let (pool, name) = (Arc::clone(&s.pool), source.clone());
    let stream = tokio::task::spawn_blocking(move || crate::listen::Stream::open(&pool, &name, pick)).await.ok().flatten();
    let first = match &stream {
        Some(st) => st.next(crate::listen::FIRST_WAIT).await,
        None => None,
    };
    let (Some(stream), Some(first)) = (stream, first) else {
        s.listeners.give();
        return error(StatusCode::NOT_FOUND, "That source is not sending any sound.");
    };
    let (rate, channels) = (first.0, first.1);
    let (tx, rx) = mpsc::channel::<Result<Bytes, std::convert::Infallible>>(4);
    let s2 = Arc::clone(&s);
    tokio::spawn(async move {
        let mut next = Some(first);
        while let Some((_, _, data)) = next.take() {
            if tx.send(Ok(Bytes::from(data))).await.is_err() {
                break;
            }
            next = stream.next(crate::listen::IDLE_WAIT).await;
        }
        let st = Arc::clone(&stream);
        drop(tokio::task::spawn_blocking(move || st.close()));
        s2.listeners.give();
    });
    (
        [
            (header::CONTENT_TYPE, "application/octet-stream".to_owned()),
            (header::CACHE_CONTROL, "no-store".to_owned()),
            (header::HeaderName::from_static("x-rate"), rate.to_string()),
            (header::HeaderName::from_static("x-channels"), channels.to_string()),
            (header::HeaderName::from_static("x-accel-buffering"), "no".to_owned()),
        ],
        Body::from_stream(ReceiverStream::new(rx)),
    )
        .into_response()
}

pub fn router(host: Host, slot: Slot) -> Router {
    Router::new()
        .route("/api/admin/ndi", gate(&host, "ndi.view", get(snapshot)))
        .route("/api/admin/ndi/scan", gate(&host, "ndi.manage", post(scan)))
        .route("/ndi/stream/{*source}", gate(&host, "ndi.view", get(stream)))
        .route("/ndi/multiview/{mid}/stream", gate(&host, "ndi.view", get(multiview_stream)))
        .route("/ndi/listen/{*source}", gate(&host, "ndi.view", get(listen)))
        .route("/ndi/level/{*source}", gate(&host, "ndi.view", get(level)))
        .route("/api/admin/ndi/analysis/open", gate(&host, "ndi.view", post(analysis_open)))
        .route("/api/admin/ndi/analysis", gate(&host, "ndi.view", get(analysis_read).delete(analysis_close)))
        .route("/api/admin/ndi/analysis/reset", gate(&host, "ndi.view", post(analysis_reset)))
        .route("/api/admin/ndi/analysis/record/stop", gate(&host, "ndi.manage", post(analysis_record_stop)))
        .route("/api/admin/ndi/analysis/recording", gate(&host, "ndi.view", get(analysis_download)))
        .with_state(slot.clone())
        .merge(
            Router::new()
                .route("/api/admin/ndi/outputs", gate(&host, "ndi.manage", axum::routing::put(admin_outputs)))
                .route("/api/admin/ndi/tests", gate(&host, "ndi.manage", axum::routing::put(admin_tests)))
                .route("/api/admin/ndi/tests/{tid}/image", gate(&host, "ndi.manage", axum::routing::put(admin_test_image)))
                .route("/api/admin/ndi/router/outputs", gate(&host, "ndi.manage", post(router_add)))
                .route("/api/admin/ndi/router/outputs/{oid}", gate(&host, "ndi.manage", axum::routing::delete(router_remove)))
                .route("/api/admin/ndi/router/outputs/{oid}/source", gate(&host, "ndi.manage", axum::routing::put(router_route)))
                .route("/api/admin/ndi/router/outputs/{oid}/audio", gate(&host, "ndi.manage", axum::routing::put(router_audio)))
                .route("/api/admin/ndi/router/salvos", gate(&host, "ndi.manage", post(salvo_save)))
                .route("/api/admin/ndi/router/salvos/{sid}", gate(&host, "ndi.manage", axum::routing::delete(salvo_delete)))
                .route("/api/admin/ndi/router/salvos/{sid}/take", gate(&host, "ndi.manage", post(salvo_take)))
                .with_state((host.clone(), slot.clone())),
        )
        .merge(Router::new().route("/api/admin/ndi/analysis/record", gate(&host, "ndi.manage", post(analysis_record))).with_state((host.clone(), slot)))
}
