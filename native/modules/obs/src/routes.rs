//! OBS's routes (`routes.py`): the admin page's, and the scene screen's (no sign-in: the link's per-computer key is its whole authority). Mounted by the
//! registry, so every one answers 404 while the module is off.

#![allow(clippy::result_large_err)]

use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use axum::extract::{Path, RawQuery, Request, State};
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use relay_core::access::Peer;
use relay_core::gate::gate;
use relay_core::Host;
use serde_json::{json, Value};

use crate::machine::Machine;
use crate::proto::{Connection, ObsError, EVENTS};
use crate::service::Service;
use crate::settings::{clean_settings, new_id, new_key, passwords, MAX_MACHINES};
use crate::web::{error, object, query_param};
use crate::KEY;

#[derive(Clone)]
pub struct Ctx {
    pub host: Host,
    pub service: Arc<Service>,
    pub guard: Arc<Guard>,
}

/// The scene screen's limits: how many presses and how many wrong keys one client address may make in a window.
pub struct Guard {
    pub window: Duration,
    pub max_presses: usize,
    pub max_bad_keys: usize,
    presses: Mutex<HashMap<IpAddr, Vec<Instant>>>,
    bad: Mutex<HashMap<IpAddr, Vec<Instant>>>,
}

impl Default for Guard {
    fn default() -> Self {
        Self { window: Duration::from_secs(10), max_presses: 20, max_bad_keys: 8, presses: Mutex::default(), bad: Mutex::default() }
    }
}

impl Guard {
    fn limited(&self, who: IpAddr) -> bool {
        let now = Instant::now();
        let mut t = self.presses.lock().unwrap_or_else(PoisonError::into_inner);
        if t.len() > 2000 {
            t.retain(|_, v| v.last().is_some_and(|l| now.duration_since(*l) <= self.window));
        }
        let mut recent: Vec<Instant> = t.get(&who).into_iter().flatten().copied().filter(|a| now.duration_since(*a) < self.window).collect();
        if recent.len() >= self.max_presses {
            t.insert(who, recent);
            return true;
        }
        recent.push(now);
        t.insert(who, recent);
        false
    }

    fn locked(&self, who: IpAddr) -> bool {
        let t = self.bad.lock().unwrap_or_else(PoisonError::into_inner);
        let hits = t.get(&who).map_or(&[][..], Vec::as_slice);
        hits.len() >= self.max_bad_keys && hits.last().is_some_and(|l| l.elapsed() < self.window)
    }

    fn bad_key(&self, who: IpAddr) {
        let now = Instant::now();
        let mut t = self.bad.lock().unwrap_or_else(PoisonError::into_inner);
        let mut hits: Vec<Instant> = t.get(&who).into_iter().flatten().copied().filter(|a| now.duration_since(*a) < self.window).collect();
        let keep = self.max_bad_keys.saturating_sub(1).min(hits.len());
        hits.drain(..hits.len() - keep);
        hits.push(now);
        t.insert(who, hits);
    }
}

fn who(req: &axum::http::request::Parts) -> IpAddr {
    req.extensions.get::<Peer>().map_or(IpAddr::from([0, 0, 0, 0]), |p| p.0)
}

/// An OBS refusal as the admin page shows it: 502 and the sentence.
fn obs_error(e: &ObsError) -> Response {
    error(502, &e.0)
}

fn machine_or_404(c: &Ctx, payload: &Value) -> Result<Arc<Machine>, Response> {
    payload["machine"].as_str().and_then(|id| c.service.machine(id)).ok_or_else(|| error(404, "There is no such OBS computer."))
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// Connect once, apart from the held connection, and say what OBS reports.
pub async fn test_machine(host: &Host, cfg: &Value, password: &str) -> Value {
    let name = cfg["name"].as_str().unwrap_or("OBS");
    let port = cfg["port"].as_u64().and_then(|p| u16::try_from(p).ok()).unwrap_or(crate::settings::DEFAULT_PORT);
    let conn = match Connection::open(host.net(), cfg["host"].as_str().unwrap_or(""), port, password, None, EVENTS).await {
        Ok(c) => c,
        Err(e) => return json!({"ok": false, "detail": format!("{name}: {e}")}),
    };
    let got = async { Ok::<_, ObsError>((conn.request("GetVersion", None).await?, conn.request("GetSceneList", None).await?)) }.await;
    conn.close().await;
    match got {
        Ok((v, scenes)) => {
            let n = scenes["scenes"].as_array().map_or(0, Vec::len);
            let short = |k: &str| clip(v[k].as_str().unwrap_or(""), 20);
            json!({"ok": true, "detail": format!("{name}: connected to OBS {} (obs-websocket {}), {n} scene{}.", short("obsVersion"), short("obsWebSocketVersion"), if n == 1 { "" } else { "s" })})
        }
        Err(e) => json!({"ok": false, "detail": format!("{name}: {e}")}),
    }
}

async fn admin_state(State(c): State<Ctx>) -> Response {
    Json(c.service.snapshot()).into_response()
}

/// Replace the list of OBS computers. A blank password keeps the saved one; `clear_password` removes it. The scene-screen key and its switch are changed
/// only by the screen route.
async fn admin_settings(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let Some(sent) = payload["machines"].as_array() else { return error(400, "Send the list of OBS computers.") };
    if sent.len() > MAX_MACHINES {
        return error(400, &format!("Relay follows up to {MAX_MACHINES} OBS computers."));
    }
    let cur = c.service.settings();
    let saved: Vec<Value> = cur["machines"].as_array().cloned().unwrap_or_default();
    let pw = passwords(&cur["passwords"]);
    let mut machines: Vec<Value> = Vec::new();
    for m in sent.iter().filter(|m| m.is_object()) {
        let mut m = m.clone();
        let known = m["id"].as_str().and_then(|id| saved.iter().find(|s| s["id"] == json!(id))).cloned();
        if known.is_none() {
            m["id"] = json!(new_id()); // a new computer: its id now, so its password follows it
        }
        if let Some(h) = m["host"].as_str().filter(|h| !h.trim().is_empty()) {
            if clean_settings(&json!({"machines": [{"host": h}]}))["machines"][0]["host"] == json!("") {
                return error(400, "That is not an address: use the OBS computer's IP address, like 192.168.1.60.");
            }
        }
        let old = known.unwrap_or_else(|| json!({}));
        m["key"] = old.get("key").cloned().unwrap_or_else(|| json!(""));
        m["screen_on"] = old.get("screen_on").cloned().unwrap_or(json!(false));
        machines.push(m);
    }
    let new = clean_settings(&json!({"machines": machines}));
    let mut keep = serde_json::Map::new();
    for m in new["machines"].as_array().into_iter().flatten() {
        let id = m["id"].as_str().unwrap_or("");
        let sent = machines.iter().find(|s| s["id"] == json!(id)).cloned().unwrap_or_else(|| json!({}));
        if let Some(p) = sent["password"].as_str().filter(|p| !p.is_empty()) {
            keep.insert(id.to_owned(), json!(clip(p, 200)));
        } else if sent["clear_password"] != Value::Bool(true) {
            if let Some(p) = pw.get(id) {
                keep.insert(id.to_owned(), p.clone());
            }
        }
    }
    let new = clean_settings(&json!({"machines": new["machines"], "passwords": Value::Object(keep)}));
    if c.host.replace_settings(KEY, &new).is_err() {
        return error(500, "The settings could not be saved.");
    }
    c.service.restart().await;
    let names: Vec<&str> = new["machines"].as_array().into_iter().flatten().filter_map(|m| m["name"].as_str()).collect();
    c.host.note(&parts, "obs.settings", "Changed the OBS computers", &if names.is_empty() { "(none)".to_owned() } else { names.join(", ") }, None).await;
    Json(c.service.snapshot()).into_response()
}

async fn admin_test(State(c): State<Ctx>, req: Request) -> Response {
    let payload = match object(req.into_body()).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let Some(m) = payload["machine"].as_str().and_then(|id| c.service.machine(id)) else { return Json(json!({"ok": false, "detail": "Save this OBS computer first."})).into_response() };
    let settings = c.service.settings();
    let cfg = settings["machines"].as_array().into_iter().flatten().find(|x| x["id"] == json!(m.id())).cloned().unwrap_or_else(|| json!({}));
    let pw = passwords(&settings["passwords"]).get(m.id()).and_then(Value::as_str).unwrap_or("").to_owned();
    Json(test_machine(&c.host, &cfg, &pw).await).into_response()
}

async fn admin_scene(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let Some(m) = payload["machine"].as_str().and_then(|id| c.service.machine(id)) else { return error(404, "There is no such OBS computer.") };
    let Some(scene) = payload["scene"].as_str() else { return error(400, "Choose a scene.") };
    if let Err(e) = m.set_scene(scene).await {
        return obs_error(&e);
    }
    c.host.note(&parts, "obs.scene", &format!("OBS ({}): switched to {}", m.cfg.name, clip(scene, 60)), m.id(), None).await;
    Json(json!({"ok": true})).into_response()
}

/// Start or stop the stream, start, stop, pause or resume the recording, or start, stop or save the replay buffer. Sent once and never retried: a dropped
/// connection may already have done it.
async fn admin_output(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let m = match machine_or_404(&c, &payload) {
        Ok(m) => m,
        Err(r) => return r,
    };
    let (Some(which), Some(action)) = (payload["output"].as_str(), payload["action"].as_str()) else { return error(400, "Choose an output and what to do.") };
    if let Err(e) = m.output(which, action).await {
        return obs_error(&e);
    }
    let words = match which {
        "stream" => "the stream",
        "record" => "the recording",
        _ => "the replay buffer",
    };
    let did = match action {
        "start" => "Started",
        "stop" => "Stopped",
        "pause" => "Paused",
        "resume" => "Resumed",
        _ => "Saved",
    };
    c.host.note(&parts, &format!("obs.{which}"), &format!("OBS ({}): {did} {words}", m.cfg.name), m.id(), None).await;
    Json(json!({"ok": true})).into_response()
}

/// Studio mode: switch it on or off, put a scene in preview, or take the preview to program.
async fn admin_studio(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let m = match machine_or_404(&c, &payload) {
        Ok(m) => m,
        Err(r) => return r,
    };
    let (action, scene) = (payload["action"].as_str().unwrap_or(""), payload["scene"].as_str());
    let (done, note) = match (action, scene) {
        ("on" | "off", _) => (m.set_studio(action == "on").await, format!("Turned studio mode {action}")),
        ("preview", Some(scene)) => (m.set_preview(scene).await, format!("Put {} in preview", clip(scene, 60))),
        ("take", _) => {
            let was = m.preview_scene();
            (m.take().await, format!("Took {} to program", if was.is_empty() { "the preview".to_owned() } else { clip(&was, 60) }))
        }
        _ => return error(400, "Choose on, off, preview or take."),
    };
    if let Err(e) = done {
        return obs_error(&e);
    }
    c.host.note(&parts, "obs.studio", &format!("OBS ({}): {note}", m.cfg.name), m.id(), None).await;
    Json(json!({"ok": true})).into_response()
}

async fn admin_mute(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let m = match machine_or_404(&c, &payload) {
        Ok(m) => m,
        Err(r) => return r,
    };
    let (Some(name), Some(muted)) = (payload["input"].as_str(), payload["muted"].as_bool()) else { return error(400, "Choose an input and whether it is muted.") };
    if let Err(e) = m.set_mute(name, muted).await {
        return obs_error(&e);
    }
    c.host.note(&parts, "obs.mute", &format!("OBS ({}): {} {}", m.cfg.name, if muted { "Muted" } else { "Unmuted" }, clip(name, 60)), m.id(), None).await;
    Json(json!({"ok": true})).into_response()
}

fn no_store(mut r: Response) -> Response {
    r.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    r
}

/// The watched inputs' meters and mutes, read a few times a second by the admin page.
async fn admin_audio(State(c): State<Ctx>, Path(id): Path<String>) -> Response {
    match c.service.machine(&id) {
        Some(m) => no_store(Json(json!({"audio": m.audio()})).into_response()),
        None => error(404, "There is no such OBS computer."),
    }
}

/// The program scene as a small JPEG, while that computer's preview is switched on. Never on the viewer port's pages: only this signed-in route serves it.
async fn admin_preview(State(c): State<Ctx>, Path(id): Path<String>) -> Response {
    let Some(m) = c.service.machine(&id).filter(|m| m.cfg.preview) else { return error(404, "The preview is off.") };
    match m.screenshot().await {
        Ok(img) => {
            let mut r = Response::new(axum::body::Body::from(img));
            r.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("image/jpeg"));
            no_store(r)
        }
        Err(e) => obs_error(&e),
    }
}

fn link(parts: &axum::http::request::Parts, mid: &str, key: &str) -> String {
    let host = parts.headers.get(header::HOST).and_then(|h| h.to_str().ok()).unwrap_or("");
    let scheme = parts.headers.get("x-forwarded-proto").and_then(|h| h.to_str().ok()).filter(|s| matches!(*s, "http" | "https")).unwrap_or("https");
    format!("{scheme}://{host}/obs/screen?obs={mid}&key={key}")
}

/// Switch a computer's scene screen on or off, or rotate its key (the old link stops at once). Answers with the link: the only place the key is ever sent.
async fn admin_screen_put(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let mut cur = c.service.settings();
    let Some(at) = cur["machines"].as_array().into_iter().flatten().position(|m| m["id"] == json!(id)) else { return error(404, "There is no such OBS computer.") };
    let (on, rotate) = (payload["on"] == Value::Bool(true), payload["rotate"] == Value::Bool(true));
    let has_key = cur["machines"][at]["key"].as_str().is_some_and(|k| !k.is_empty());
    if on && (!has_key || rotate) {
        cur["machines"][at]["key"] = json!(new_key());
    } else if rotate {
        return error(400, "Turn the screen on first.");
    }
    cur["machines"][at]["screen_on"] = json!(on);
    if c.host.replace_settings(KEY, &cur).is_err() {
        return error(500, "The settings could not be saved.");
    }
    let key = cur["machines"][at]["key"].as_str().unwrap_or("").to_owned();
    if let Some(live) = c.service.machine(&id) {
        live.set_screen(on, &key);
    }
    let name = cur["machines"][at]["name"].as_str().unwrap_or("").to_owned();
    c.host.note(&parts, "obs.screen", &format!("{} the OBS scene screen for {name}{}", if on { "Turned on" } else { "Turned off" }, if rotate { " with a new key" } else { "" }), &id, None).await;
    Json(json!({"on": on, "link": if on { link(&parts, &id, &key) } else { String::new() }})).into_response()
}

async fn admin_screen_get(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let cur = c.service.settings();
    let Some(m) = cur["machines"].as_array().into_iter().flatten().find(|m| m["id"] == json!(id)) else { return error(404, "There is no such OBS computer.") };
    let on = m["screen_on"] == Value::Bool(true);
    Json(json!({"on": on, "link": if on { link(&parts, &id, m["key"].as_str().unwrap_or("")) } else { String::new() }})).into_response()
}

// ====================================================================================================== the scene screen
// A computer's scenes on a browser screen: plain HTTP with no sign-in, so a tablet in the booth needs nothing but its link. The link's per-computer key is
// its whole authority: it can switch that computer's scenes and nothing else.

/// The machine when the key is right, else the answer to send. Wrong keys are counted, never logged.
fn checked(c: &Ctx, who: IpAddr, id: &str, key: &str) -> Result<Arc<Machine>, Response> {
    if c.guard.locked(who) {
        return Err((StatusCode::TOO_MANY_REQUESTS, Json(json!({"error": "slow"}))).into_response());
    }
    c.service.screen_machine(id, key).ok_or_else(|| {
        c.guard.bad_key(who);
        (StatusCode::NOT_FOUND, Json(json!({"error": "refused"}))).into_response()
    })
}

fn escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// JSON safe to put inside a `<script>`: no `<`, `>` or `&` survives, so nothing can close the tag.
fn script_json(v: &Value) -> String {
    v.to_string().replace('<', "\\u003c").replace('>', "\\u003e").replace('&', "\\u0026")
}

const SCREEN_HEAD: &str = "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n<meta name=\"referrer\" content=\"no-referrer\">\n<link rel=\"stylesheet\" href=\"/static/tokens.css\"><title>";
const SCREEN_BODY: &str = ": scenes</title>\n<link rel=\"stylesheet\" href=\"/static/modules/obs/screen.css\">\n</head>\n<body>\n<header id=\"obsHead\"><span id=\"obsName\"></span> <span id=\"obsLive\" class=\"badge\" hidden>LIVE</span>\n  <span id=\"obsRec\" class=\"badge rec\" hidden>REC</span> <span id=\"obsOff\" class=\"badge off\" hidden>OBS not connected</span></header>\n<main id=\"obsScenes\"></main>\n<div id=\"obsToast\" role=\"status\" aria-live=\"polite\" hidden></div>\n<script id=\"obsData\" type=\"application/json\">";
const SCREEN_TAIL: &str = "</script>\n<script src=\"/static/relay-ui.js\"></script>\n<script src=\"/static/modules/obs/screen.js\"></script>\n</body>\n</html>\n";

/// `/obs/screen?obs=<computer>&key=<key>`: that computer's scenes as big buttons. The same plain 404 whether the computer does not exist, its screen is off or
/// the key is wrong.
async fn screen_page(State(c): State<Ctx>, RawQuery(q): RawQuery, req: Request) -> Response {
    let peer = who(&req.into_parts().0);
    let m = match checked(&c, peer, &query_param(q.as_deref(), "obs"), &query_param(q.as_deref(), "key")) {
        Ok(m) => m,
        Err(r) if r.status() == StatusCode::TOO_MANY_REQUESTS => return r,
        Err(_) => return (StatusCode::NOT_FOUND, "Not found").into_response(),
    };
    let data = script_json(&json!({"machine": {"id": m.id(), "name": m.cfg.name}, "key": m.screen().1}));
    let mut r = Response::new(axum::body::Body::from(format!("{SCREEN_HEAD}{}{SCREEN_BODY}{data}{SCREEN_TAIL}", escape(&m.cfg.name))));
    let h = r.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/html; charset=utf-8"));
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    h.insert(header::REFERRER_POLICY, HeaderValue::from_static("no-referrer"));
    r
}

async fn screen_state(State(c): State<Ctx>, Path(id): Path<String>, RawQuery(q): RawQuery, req: Request) -> Response {
    let peer = who(&req.into_parts().0);
    let m = match checked(&c, peer, &id, &query_param(q.as_deref(), "key")) {
        Ok(m) => m,
        Err(r) => return r,
    };
    no_store(Json(json!({"connected": m.is_connected(), "scenes": m.scenes(), "program": m.program(), "streaming": m.streaming(), "recording": m.recording()})).into_response())
}

/// One switch from a screen. Answers `{"ok": true}` or `{"error": "unreachable" | "refused" | "slow"}`: a short code, never OBS's own words. Logged only
/// after the key checked out.
async fn screen_scene(State(c): State<Ctx>, Path(id): Path<String>, RawQuery(q): RawQuery, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let peer = who(&parts);
    let m = match checked(&c, peer, &id, &query_param(q.as_deref(), "key")) {
        Ok(m) => m,
        Err(r) => return r,
    };
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let refused = || (StatusCode::NOT_FOUND, Json(json!({"error": "refused"}))).into_response();
    let Some(scene) = payload["scene"].as_str().filter(|s| m.scenes().iter().any(|x| x == s)) else { return refused() };
    if c.guard.limited(peer) {
        return (StatusCode::TOO_MANY_REQUESTS, Json(json!({"error": "slow"}))).into_response();
    }
    if m.set_scene(scene).await.is_err() {
        return (StatusCode::BAD_GATEWAY, Json(json!({"error": "unreachable"}))).into_response();
    }
    c.host.record("obs.scene", &format!("OBS ({}): switched to {} (from the scene screen)", m.cfg.name, clip(scene, 60)), m.id()).await;
    Json(json!({"ok": true})).into_response()
}

pub fn router(c: Ctx) -> Router {
    let h = c.host.clone();
    let view = |r| gate(&h, "obs.view", r);
    let control = |r| gate(&h, "obs.control", r);
    let manage = |r| gate(&h, "obs.manage", r);
    let base = "/api/admin/obs";
    Router::new()
        .route(base, view(get(admin_state)))
        .route(&format!("{base}/settings"), manage(put(admin_settings)))
        .route(&format!("{base}/test"), manage(post(admin_test)))
        .route(&format!("{base}/scene"), control(post(admin_scene)))
        .route(&format!("{base}/output"), control(post(admin_output)))
        .route(&format!("{base}/studio"), control(post(admin_studio)))
        .route(&format!("{base}/mute"), control(post(admin_mute)))
        .route(&format!("{base}/audio/{{id}}"), view(get(admin_audio)))
        .route(&format!("{base}/preview/{{id}}"), view(get(admin_preview)))
        .route(&format!("{base}/screen/{{id}}"), manage(put(admin_screen_put).get(admin_screen_get)))
        .route("/obs/screen", get(screen_page))
        .route("/obs/_/state/{id}", get(screen_state))
        .route("/obs/_/scene/{id}", post(screen_scene))
        .with_state(c)
}
