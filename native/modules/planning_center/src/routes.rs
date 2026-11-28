//! Planning Center's routes (`routes.py`): the admin page's connection and LIVE view, the rundown, the signage sync, the Service feed and the
//! panels' Next / Previous / Take control / Release presses. Mounted by the registry, so every one answers 404 while the module is off.

// axum's `Response` is the error half of every helper (the answer to send instead): the shape of the Python it ports.
#![allow(clippy::result_large_err)]

use std::collections::BTreeMap;
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::extract::{Path, RawQuery, Request, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use bytes::Bytes;
use http_body_util::BodyExt;
use relay_core::caps::{ChannelsCap, DirectoryCap, CHANNELS, DIRECTORY};
use relay_core::gate::gate;
use relay_core::Host;
use serde_json::{json, Value};

use crate::control::{self, Control};
use crate::feed::{frame, ServiceFeed};
use crate::follower::{now, Follower};
use crate::plan::{self, s};
use crate::rundown::{self, Cx};
use crate::settings::signage_fields;
use crate::signagesync::PlanSync;
use crate::KEY;

pub const RUNDOWN_TICK_S: f64 = 1.0;
pub const RUNDOWN_RESEND_S: f64 = 5.0;
pub const HEARTBEAT: Duration = Duration::from_secs(15);

#[derive(Clone)]
pub struct Ctx {
    pub host: Host,
    pub follower: Arc<Follower>,
    pub feed: Arc<ServiceFeed>,
    pub control: Arc<Control>,
    pub sync: Arc<PlanSync>,
    pub make_client: crate::signagesync::MakeClient,
}

fn error(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"error": text}))).into_response()
}

fn detail(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"detail": text}))).into_response()
}

async fn read(body: Body, limit: usize) -> Result<Vec<u8>, Response> {
    let mut out = Vec::new();
    let mut body = body;
    while let Some(frame) = body.frame().await {
        let Ok(frame) = frame else { return Err(detail(400, "Bad request")) };
        if let Some(d) = frame.data_ref() {
            out.extend_from_slice(d);
            if out.len() > limit {
                return Err(detail(413, "request too large"));
            }
        }
    }
    Ok(out)
}

async fn object(body: Body) -> Result<Value, Response> {
    match serde_json::from_slice::<Value>(&read(body, 1 << 20).await?) {
        Ok(v @ Value::Object(_)) => Ok(v),
        _ => Err(detail(422, "That request is not valid.")),
    }
}

/// An audit entry as Python's gate wrote for a module route that noted nothing itself.
async fn audit(c: &Ctx, parts: &axum::http::request::Parts) {
    let (method, path) = (parts.method.as_str(), parts.uri.path());
    c.host.note(parts, "api.change", &format!("{method} {path}"), "", Some(json!({"method": method, "path": path}))).await;
}

/// What the browser may see of the settings (`Module.public`): never the ID or the secret.
pub fn public(settings: &Value) -> Value {
    json!({
        "connected": !s(settings, "app_id").is_empty() && !s(settings, "secret").is_empty(),
        "service_types": settings["service_types"],
        "show_notes": settings["show_notes"] == Value::Bool(true),
        "rundown_screen": settings["rundown"]["screen"] == Value::Bool(true),
    })
}

fn body_of(c: &Ctx) -> Value {
    json!({"planning_center": public(&c.follower.settings())})
}

fn store(c: &Ctx, settings: &Value) -> Result<(), Response> {
    c.host.replace_settings(KEY, settings).map_err(|_| error(503, "The settings could not be saved right now. Try again in a moment."))
}

fn with_cx<R>(host: &Host, run: impl FnOnce(&Cx) -> R) -> R {
    let dir = host.using::<DirectoryCap>(DIRECTORY);
    let chans = host.using::<ChannelsCap>(CHANNELS);
    run(&Cx { directory: dir.as_ref().map(|d| d.0.as_ref()), channels: chans.as_ref().map(|d| d.0.as_ref()) })
}

// -------------------------------------------------------------------------------------------------------------- connection
async fn save_token(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let mut saved = c.follower.settings();
    let pick = |key: &str| Some(s(&payload, key).trim().to_owned()).filter(|t| !t.is_empty()).unwrap_or_else(|| s(&saved, key));
    let (app_id, secret) = (pick("app_id"), pick("secret"));
    if app_id.is_empty() || secret.is_empty() {
        return error(400, "Enter both the application ID and the secret.");
    }
    let changed = (&app_id, &secret) != (&s(&saved, "app_id"), &s(&saved, "secret"));
    saved["app_id"] = json!(app_id);
    saved["secret"] = json!(secret);
    if changed {
        saved["service_types"] = json!([]);
        saved["plan_overrides"] = json!({});
    }
    if let Err(r) = store(&c, &saved) {
        return r;
    }
    tracing::info!("Planning Center token {}", if changed { "saved" } else { "unchanged" });
    audit(&c, &parts).await;
    c.follower.poke().await;
    Json(body_of(&c)).into_response()
}

async fn test(State(c): State<Ctx>) -> Response {
    let Some(client) = (c.sync_client())(&c.follower.settings()) else { return error(400, "Save an application ID and secret first.") };
    match client.call(crate::client::Op::ServiceTypes).await {
        Ok(types) => {
            let mut out = body_of(&c);
            out["service_types"] = types;
            Json(out).into_response()
        }
        Err(e) => {
            tracing::warn!("Planning Center test: {}", e.detail());
            error(e.route_status(), e.operator_message())
        }
    }
}

async fn service_types(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let mut saved = c.follower.settings();
    if s(&saved, "app_id").is_empty() || s(&saved, "secret").is_empty() {
        return error(400, "Connect to Planning Center first.");
    }
    let Some(ids) = payload["ids"].as_array() else { return error(400, "ids must be a list.") };
    saved["service_types"] = Value::Array(ids.clone());
    // Cleaned on the way back in: only numeric ids, once each.
    let cleaned = crate::settings::clean_stored(&saved);
    if let Err(r) = store(&c, &cleaned) {
        return r;
    }
    audit(&c, &parts).await;
    c.follower.poke().await;
    Json(body_of(&c)).into_response()
}

async fn notes(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let Some(show) = payload["show"].as_bool() else { return error(400, "show must be true or false.") };
    let mut saved = c.follower.settings();
    saved["show_notes"] = json!(show);
    if let Err(r) = store(&c, &saved) {
        return r;
    }
    tracing::info!("Planning Center item notes on panels: {}", if show { "shown" } else { "hidden" });
    audit(&c, &parts).await;
    c.feed.refresh();
    Json(body_of(&c)).into_response()
}

async fn disconnect(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    if let Err(r) = store(&c, &crate::settings::clean_stored(&json!({}))) {
        return r;
    }
    tracing::info!("Planning Center disconnected");
    audit(&c, &parts).await;
    c.follower.poke().await;
    Json(body_of(&c)).into_response()
}

// ------------------------------------------------------------------------------------------------------------------- LIVE
async fn live(State(c): State<Ctx>) -> Response {
    let mut snap = c.follower.snapshot(None);
    snap["actions"] = json!(c.control.recent());
    Json(snap).into_response()
}

async fn pick_plan(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let mut saved = c.follower.settings();
    let st = s(&payload, "service_type");
    if !saved["service_types"].as_array().is_some_and(|a| a.iter().any(|v| v == st.as_str())) {
        return error(400, "Relay is not following that service type.");
    }
    let plan_id = s(&payload, "plan").trim().to_owned();
    if !plan_id.is_empty() && !plan_id.bytes().all(|b| b.is_ascii_digit()) {
        return error(400, "plan must be a Planning Center plan id.");
    }
    if let Some(o) = saved["plan_overrides"].as_object_mut() {
        o.remove(&st);
    }
    if !plan_id.is_empty() {
        let day = c.follower.track(&st).and_then(|t| t.day).unwrap_or_else(|| c.follower.zone().day_of(now()));
        saved["plan_overrides"][st.as_str()] = json!({"plan": plan_id, "day": plan::iso_day(day)});
    }
    if let Err(r) = store(&c, &saved) {
        return r;
    }
    tracing::info!("Planning Center, service type {st}: operator {}", if plan_id.is_empty() { "cleared the plan pick".to_owned() } else { format!("picked plan {plan_id} for today") });
    audit(&c, &parts).await;
    c.follower.poke().await;
    Json(c.follower.snapshot(None)).into_response()
}

async fn live_action(State(c): State<Ctx>, Path(action): Path<String>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    if !control::ACTIONS.contains(&action.as_str()) {
        return detail(404, "unknown action");
    }
    let st = s(&payload, "type");
    match c.control.press_admin(&st, &action, "Admin page").await {
        Ok(_) => {
            audit(&c, &parts).await;
            Json(json!({"ok": true, "rundown": rundown_now(&c, &st, "")})).into_response()
        }
        Err(r) => (StatusCode::from_u16(r.status()).unwrap_or(StatusCode::BAD_GATEWAY), Json(json!({"ok": false, "error": r.0, "message": control::admin_words(r.0)}))).into_response(),
    }
}

// ----------------------------------------------------------------------------------------------------------------- rundown
fn rundown_track(c: &Ctx, st: &str) -> Option<crate::follower::Track> {
    if !st.is_empty() {
        return c.follower.track(st);
    }
    let with_plan: Vec<_> = c.follower.tracks().into_iter().filter(|t| t.plan.is_some()).collect();
    let at = now();
    let running = with_plan.iter().position(|t| plan::running(t.live.as_ref(), at));
    with_plan.into_iter().nth(running.unwrap_or(0))
}

/// The rundown as it stands; a changed mic draw is saved, so it holds across restarts.
pub fn rundown_now(c: &Ctx, st: &str, want: &str) -> Value {
    let at = now();
    let Some(tr) = rundown_track(c, st) else {
        return json!({"type": "rundown", "service_type": if st.is_empty() { Value::Null } else { json!(st) }, "now": at, "plan": null, "items": [], "times": [],
                      "time": null, "header": null, "people": [], "mics": [], "notes": null, "live": false, "controller": null});
    };
    let settings = c.follower.settings();
    let (msg, draws) = rundown::build(&tr, &settings, at, &c.follower.zone(), want, &mut rundown::rng());
    if draws != settings["rundown"]["draws"] {
        let mut next = settings.clone();
        next["rundown"]["draws"] = draws;
        let _ = c.host.replace_settings(KEY, &next);
    }
    msg
}

fn link_flag(cx: &Cx, reference: &str) -> Value {
    match cx.channels {
        None => json!({"name_only": reference.is_empty(), "channel_missing": false}),
        Some(ch) => {
            let got = ch.by_ref(reference);
            json!({"name_only": got.is_none(), "channel_missing": got.as_ref().is_some_and(|g| g.missing)})
        }
    }
}

/// The settings for the admin page. Each mic mapping and channel assignment says whether Planning Center no longer lists its position
/// ("missing") or it was never matched to one ("unmatched"), so neither is silently ignored (#525).
fn settings_view(c: &Ctx) -> Value {
    with_cx(&c.host, |cx| {
        let rs = c.follower.settings()["rundown"].clone();
        let flagged = |e: &Value| {
            let got = rundown::describe(cx, &s(e, "position_ref"), &s(e, "position"));
            let mut o = e.clone();
            o["missing"] = json!(got.missing);
            o["unmatched"] = json!(got.unmatched);
            o
        };
        let mut out = rs.clone();
        if let Some(o) = out.as_object_mut() {
            o.remove("draws");
        }
        out["mics"] = Value::Array(
            rs["mics"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|m| {
                    let mut v = flagged(m);
                    let flags: serde_json::Map<String, Value> = m["mics"].as_array().into_iter().flatten().map(|x| (plan::text(x), link_flag(cx, &s(&m["mic_refs"], &plan::text(x))))).collect();
                    v["mic_flags"] = Value::Object(flags);
                    v
                })
                .collect(),
        );
        out["channels"] = Value::Array(
            rs["channels"]
                .as_array()
                .into_iter()
                .flatten()
                .map(|ch| {
                    let mut v = ch.clone();
                    v["positions"] = Value::Array(ch["positions"].as_array().into_iter().flatten().map(flagged).collect());
                    let flag = link_flag(cx, &s(ch, "channel_ref"));
                    v["name_only"] = flag["name_only"].clone();
                    v["channel_missing"] = flag["channel_missing"].clone();
                    v["also"] = Value::Array(
                        ch["also_refs"]
                            .as_array()
                            .into_iter()
                            .flatten()
                            .map(|r| {
                                let got = cx.channels.and_then(|x| x.by_ref(&plan::text(r)));
                                json!({"ref": r, "name": got.as_ref().map(|g| g.shown.clone()).unwrap_or_default(), "missing": got.as_ref().is_none_or(|g| g.missing)})
                            })
                            .collect(),
                    );
                    v
                })
                .collect(),
        );
        let got = rundown::describe(cx, &s(&rs, "leader_position_ref"), &s(&rs, "leader_position"));
        out["leader_missing"] = json!(got.missing);
        out["leader_unmatched"] = json!(got.unmatched);
        out
    })
}

fn four_digits(t: &str) -> String {
    if t.len() == 4 && t.bytes().all(|b| b.is_ascii_digit()) { t.to_owned() } else { String::new() }
}

async fn rundown_get(State(c): State<Ctx>, RawQuery(raw): RawQuery) -> Response {
    let q: BTreeMap<String, String> = url::form_urlencoded::parse(raw.unwrap_or_default().as_bytes()).into_owned().collect();
    let followed = c.follower.followed();
    let st = q.get("type").filter(|t| followed.contains(t)).cloned().unwrap_or_default();
    let want = four_digits(q.get("time").map_or("", String::as_str));
    let tracks = c.follower.tracks();
    let (catalog, source) = with_cx(&c.host, |cx| {
        let out: Vec<Value> = cx.channels.map_or_else(Vec::new, |ch| {
            ch.audio().into_iter().filter(|x| !x.missing && x.direction == "source").map(|x| json!({"id": x.reference, "ref": x.reference, "number": x.number.unwrap_or_default(), "name": x.shown})).collect()
        });
        let src = if out.is_empty() { "" } else { "Relay channels" };
        (out, src)
    });
    let all_positions: Vec<Value> = with_cx(&c.host, |cx| cx.directory.map_or_else(Vec::new, |d| d.positions().into_iter().filter(|p| !p.missing).map(|p| json!({"ref": p.reference, "name": p.name, "team": p.team})).collect()));
    Json(json!({
        "rundown": rundown_now(&c, &st, &want), "settings": settings_view(&c), "positions": rundown::positions(&tracks), "all_positions": all_positions,
        "type_positions": rundown::type_positions(&tracks), "catalog": catalog, "catalog_source": source,
        "service_types": followed.iter().map(|id| json!({"id": id, "name": c.follower.label(id)})).collect::<Vec<_>>(),
        "actions": c.control.recent(),
    }))
    .into_response()
}

async fn rundown_settings(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let mut saved = c.follower.settings();
    let mut rs = saved["rundown"].clone();
    for k in ["leader_note", "leader_position", "sermon_note"] {
        if let Some(v) = payload.get(k) {
            let Some(t) = v.as_str() else { return error(400, &format!("{k} must be text.")) };
            rs[k] = json!(t);
        }
    }
    if let Some(v) = payload.get("screen") {
        let Some(b) = v.as_bool() else { return error(400, "screen must be true or false.") };
        rs["screen"] = json!(b);
    }
    if let Some(v) = payload.get("leader_position_ref") {
        // Picked from the list (#521): "" picks none. A ref is kept only when the directory lists it.
        let r = v.as_str().unwrap_or("");
        let listed = with_cx(&c.host, |cx| cx.directory.is_some_and(|d| d.knows(r)));
        let kind_ok = relay_core::caps::parse_item(r).is_some_and(|(k, p, _)| k == "position" && p == relay_core::caps::PCO);
        if !r.is_empty() && !(listed && kind_ok) {
            return error(400, "Pick a position Planning Center lists.");
        }
        rs["leader_position_ref"] = json!(r);
        rs["leader_position"] = json!(if r.is_empty() { String::new() } else { with_cx(&c.host, |cx| rundown::describe(cx, r, "").name) });
    } else if payload.get("leader_position").is_some() {
        rs["leader_position_ref"] = json!(""); // a typed name is matched to a position on the next refresh
    }
    if let Some(v) = payload.get("mics") {
        if !v.is_array() {
            return error(400, "mics must be a list.");
        }
        rs["mics"] = v.clone();
    }
    if let Some(v) = payload.get("channels") {
        if !v.is_array() {
            return error(400, "channels must be a list.");
        }
        rs["channels"] = rundown::clean_channels(v);
    }
    let cleaned = rundown::clean_rundown(&rs);
    rs["mics"] = cleaned["mics"].clone();
    rs["channels"] = cleaned["channels"].clone();
    with_cx(&c.host, |cx| rundown::refresh_saved(&mut rs, cx));
    saved["rundown"] = rs.clone();
    if let Err(r) = store(&c, &saved) {
        return r;
    }
    let screen = if rs["screen"] == Value::Bool(true) { "on" } else { "off" };
    tracing::info!("Planning Center rundown settings saved (screen {screen})");
    audit(&c, &parts).await;
    Json(json!({"settings": settings_view(&c)})).into_response()
}

async fn rundown_redraw(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let (st, want) = (s(&payload, "type"), s(&payload, "time"));
    let st = if c.follower.followed().contains(&st) { st } else { String::new() };
    let want = four_digits(&want);
    let msg = rundown_now(&c, &st, &want);
    if msg["plan"].is_null() || msg["time"].is_null() {
        return error(409, "There is no plan and service time to draw for.");
    }
    let mut saved = c.follower.settings();
    let (team, position) = (s(&payload, "team"), s(&payload, "position"));
    let prefix = format!("{}:{}:", s(&msg["plan"], "id"), s(&msg["time"], "id"));
    let only = format!("{prefix}{team}|{position}");
    if let Some(d) = saved["rundown"]["draws"].as_object_mut() {
        d.retain(|k, _| !(k.starts_with(&prefix) && (position.is_empty() || *k == only)));
    }
    if let Err(r) = store(&c, &saved) {
        return r;
    }
    tracing::info!("Planning Center mics redrawn for {}", if position.is_empty() { "every position" } else { &position });
    audit(&c, &parts).await;
    Json(json!({"rundown": rundown_now(&c, &st, &want)})).into_response()
}

// ------------------------------------------------------------------------------------------------------------------ signage
fn signage_body(c: &Ctx) -> Value {
    let mut out = c.sync.state();
    out["service_types"] = Value::Array(c.follower.settings()["service_types"].as_array().into_iter().flatten().map(|id| json!({"id": id, "name": c.follower.label(id.as_str().unwrap_or(""))})).collect());
    out
}

async fn signage_get(State(c): State<Ctx>) -> Response {
    Json(signage_body(&c)).into_response()
}

async fn signage_settings(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let (fields, problem) = signage_fields(&payload);
    if !problem.is_empty() {
        return error(400, problem);
    }
    let mut saved = c.follower.settings();
    for (k, v) in fields {
        saved["signage"][k.as_str()] = v;
    }
    if let Err(r) = store(&c, &saved) {
        return r;
    }
    audit(&c, &parts).await;
    c.sync.look_again(); // a changed choice is looked at again at once
    c.sync.sync(false).await;
    Json(signage_body(&c)).into_response()
}

async fn signage_sync(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    if c.follower.settings()["signage"]["enabled"] != Value::Bool(true) {
        return error(409, "Switch on plan files for signage first.");
    }
    audit(&c, &parts).await;
    c.sync.sync(true).await;
    Json(signage_body(&c)).into_response()
}

// ------------------------------------------------------------------------------------------------------------------- panels
async fn panel_press(State(c): State<Ctx>, Path((panel_id, action)): Path<(String, String)>, req: Request) -> Response {
    if !control::ACTIONS.contains(&action.as_str()) {
        return detail(404, "unknown action");
    }
    if !c.control.knows_panel(&panel_id) {
        return detail(404, "unknown panel");
    }
    let raw = match read(req.into_body(), control::BODY_MAX_BYTES).await {
        Ok(r) => r,
        Err(r) => return r,
    };
    let body: Value = if raw.is_empty() { json!({}) } else {
        match serde_json::from_slice(&raw) {
            Ok(v) => v,
            Err(_) => return detail(400, "body must be JSON"),
        }
    };
    if !body.is_object() {
        return detail(400, "body must be a JSON object");
    }
    match c.control.press(&panel_id, &action, &body).await {
        Ok(state) => Json(json!({"ok": true, "service": state})).into_response(),
        Err(r) => (StatusCode::from_u16(r.status()).unwrap_or(StatusCode::BAD_GATEWAY), Json(json!({"ok": false, "error": r.0}))).into_response(),
    }
}

// ------------------------------------------------------------------------------------------------------------------ streams
fn sse(body: Body) -> Response {
    let mut r = Response::new(body);
    let h = r.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache, no-transform"));
    h.insert("x-accel-buffering", HeaderValue::from_static("no"));
    r
}

struct Watch {
    c: Ctx,
    st: String,
    id: u64,
    rx: tokio::sync::mpsc::Receiver<Bytes>,
}

impl Drop for Watch {
    fn drop(&mut self) {
        self.c.feed.unsubscribe(&self.st, self.id);
    }
}

/// Today's plan and LIVE state for one followed service type, with a tick every heartbeat.
pub fn service_stream(c: &Ctx, query: &BTreeMap<String, String>) -> Response {
    let st = query.get("type").map_or("", |t| t.trim()).to_owned();
    // Only a followed service type: the feed is no way to make Relay ask Planning Center about anything else.
    if !c.feed.offered().iter().any(|o| o["service_type"] == st.as_str()) {
        return detail(404, "not a followed service type");
    }
    let (id, rx) = c.feed.subscribe(&st);
    let watch = Watch { c: c.clone(), st, id, rx };
    let stream = futures_util::stream::unfold(watch, |mut w| async move {
        let chunk = match tokio::time::timeout(HEARTBEAT, w.rx.recv()).await {
            Ok(Some(f)) => f,
            Ok(None) => return None,
            Err(_) => frame(&w.c.feed.tick_payload(&w.st, None)),
        };
        Some((Ok::<_, std::convert::Infallible>(chunk), w))
    });
    sse(Body::from_stream(stream))
}

/// A request that came in through the remote access tunnel (Cloudflare marks every one it forwards).
pub fn via_tunnel(headers: &HeaderMap) -> bool {
    ["cf-ray", "cf-connecting-ip", "cf-visitor"].iter().any(|h| headers.contains_key(*h))
}

pub fn rundown_stream(c: &Ctx, query: &BTreeMap<String, String>, headers: &HeaderMap) -> Response {
    if c.follower.settings()["rundown"]["screen"] != Value::Bool(true) {
        return detail(403, "the rundown is not switched on for /screen");
    }
    if via_tunnel(headers) {
        return detail(403, "the rundown is shown on this network only");
    }
    let st = query.get("type").map_or("", |t| t.trim()).to_owned();
    if !st.is_empty() && !c.follower.followed().contains(&st) {
        return detail(404, "not a followed service type");
    }
    let want = four_digits(query.get("time").map_or("", |t| t.trim()));
    let stream = futures_util::stream::unfold((c.clone(), st, want, String::new(), 0.0_f64, true), |(c, st, want, mut last, mut sent_at, first)| async move {
        loop {
            if !first_or_wait(first).await {
                return None;
            }
            let msg = rundown_now(&c, &st, &want);
            let sig = rundown::signature(&msg);
            let at = msg["now"].as_f64().unwrap_or(0.0);
            if sig != last || at - sent_at >= RUNDOWN_RESEND_S {
                last = sig;
                sent_at = at;
                let chunk = Bytes::from(format!("data: {msg}\n\n"));
                return Some((Ok::<_, std::convert::Infallible>(chunk), (c, st, want, last, sent_at, false)));
            }
            tokio::time::sleep(Duration::from_secs_f64(RUNDOWN_TICK_S)).await;
        }
    });
    sse(Body::from_stream(stream))
}

/// The first pass runs at once; later ones wait a tick (always continues).
async fn first_or_wait(first: bool) -> bool {
    if !first {
        tokio::time::sleep(Duration::from_secs_f64(RUNDOWN_TICK_S)).await;
    }
    true
}

pub fn router(c: Ctx) -> Router {
    let h = c.host.clone();
    let view = |r| gate(&h, "planning_center.view", r);
    let control = |r| gate(&h, "planning_center.control", r);
    let manage = |r| gate(&h, "planning_center.manage", r);
    let base = "/api/admin/planning-center";
    Router::new()
        .route(base, manage(post(save_token).delete(disconnect)))
        .route(&format!("{base}/test"), manage(post(test)))
        .route(&format!("{base}/service-types/refresh"), manage(post(test)))
        .route(&format!("{base}/service-types"), manage(post(service_types)))
        .route(&format!("{base}/notes"), manage(post(notes)))
        .route(&format!("{base}/live"), view(get(live)))
        .route(&format!("{base}/plan"), control(post(pick_plan)))
        .route(&format!("{base}/rundown"), view(get(rundown_get)))
        .route(&format!("{base}/rundown/settings"), manage(post(rundown_settings)))
        .route(&format!("{base}/rundown/redraw"), control(post(rundown_redraw)))
        .route(&format!("{base}/live/{{action}}"), control(post(live_action)))
        .route(&format!("{base}/signage"), view(get(signage_get)))
        .route(&format!("{base}/signage/settings"), manage(post(signage_settings)))
        .route(&format!("{base}/signage/sync"), manage(post(signage_sync)))
        .route("/api/panels/{panel_id}/service/{action}", post(panel_press))
        .with_state(c)
}

impl Ctx {
    fn sync_client(&self) -> crate::signagesync::MakeClient {
        Arc::clone(&self.make_client)
    }
}
