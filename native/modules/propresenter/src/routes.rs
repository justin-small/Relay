//! ProPresenter's routes (`routes.py`): the admin pages' connection and switches, the stage message, the lyrics and timers streams. Mounted by
//! the registry, so every one answers 404 while the module is off. A page that asks for something ProPresenter did not do answers a fixed sentence
//! chosen by the error's kind, never the exception's own text.

#![allow(clippy::result_large_err)]

use std::sync::Arc;
use std::time::Duration;

use axum::extract::{Request, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use relay_core::caps::OFFERED_CHANGED;
use relay_core::gate::gate;
use relay_core::timers::timer_fields;
use relay_core::Host;
use serde_json::{json, Value};

use crate::client::ProError;
use crate::discover;
use crate::control::Control;
use crate::feeds::{frame, LyricsFeed, TimersFeed};
use crate::lobby::Lobby;
use crate::view::View;
use crate::follower::Follower;
use crate::settings::{self, clean_stored};
use crate::web::{detail, error, object, sse};
use crate::KEY;

pub const HEARTBEAT: Duration = Duration::from_secs(15);

#[derive(Clone)]
pub struct Ctx {
    pub host: Host,
    pub follower: Arc<Follower>,
    pub lyrics: Arc<LyricsFeed>,
    pub timers: Arc<TimersFeed>,
    pub control: Arc<Control>,
    pub lobby: Arc<Lobby>,
    pub view: Arc<View>,
    pub sync: Arc<crate::signagesync::Sync>,
    pub signage: Arc<crate::signagetags::SignageTags>,
    pub link: Arc<crate::pclink::Link>,
}

/// An audit entry as Python's gate wrote for a module route that noted nothing itself.
pub async fn audit(c: &Ctx, parts: &axum::http::request::Parts) {
    let (method, path) = (parts.method.as_str(), parts.uri.path());
    c.host.note(parts, "api.change", &format!("{method} {path}"), "", Some(json!({"method": method, "path": path}))).await;
}

/// A fixed sentence by the error's kind (the detail goes to the log only).
pub fn pp_error(e: &ProError) -> Response {
    tracing::warn!("ProPresenter: {}", e.detail());
    error(e.route_status(), e.operator_message())
}

pub async fn body_now(c: &Ctx) -> Value {
    let mut signage = c.sync.state();
    signage["held"] = c.signage.state().await;
    json!({"settings": c.follower.settings(), "state": c.follower.state(), "lyrics_readers": c.lyrics.viewer_count(), "tags": c.follower.tags().state(), "lobby": c.lobby.state(), "signage": signage})
}

async fn get_body(State(c): State<Ctx>) -> Response {
    Json(body_now(&c).await).into_response()
}

/// `saved`'s computers with the primary's host and port taken from `payload`; the first computer is made when there is none.
fn with_primary_address(saved: &Value, payload: &Value) -> Value {
    let mut computers: Vec<Value> = saved["computers"].as_array().cloned().unwrap_or_default();
    let at = computers.iter().position(|c| c["id"] == saved["primary"]).unwrap_or_else(|| {
        computers.insert(0, json!({"id": "main", "name": "ProPresenter", "host": "", "port": 0}));
        0
    });
    for key in ["host", "port"] {
        if let Some(v) = payload.get(key) {
            computers[at][key] = v.clone();
        }
    }
    Value::Array(computers)
}

/// Save the connection and switches. Only the keys sent change. A new address reconnects at once.
async fn save(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let saved = c.follower.settings();
    let mut merged = saved.clone();
    const OWN: [&str; 10] = ["computers", "host", "port", "lyrics", "timers", "tags", "link", "lobby", "view", "signage"];
    for (k, v) in payload.as_object().into_iter().flatten() {
        if saved.get(k).is_some() && !OWN.contains(&k.as_str()) {
            merged[k] = v.clone();
        }
    }
    if let Some(l) = payload["lyrics"].as_object() {
        for (k, v) in l {
            merged["lyrics"][k] = v.clone();
        }
    }
    if payload.get("timers").is_some() {
        let problem = timer_fields(&payload["timers"]).1;
        if !problem.is_empty() {
            return error(400, &problem);
        }
        merged["timers"] = settings::clean_timer_settings(&payload["timers"], &saved["timers"]);
    }
    if payload.get("tags").is_some() {
        let problem = settings::tag_fields(&payload["tags"]).1;
        if !problem.is_empty() {
            return error(400, problem);
        }
        merged["tags"] = settings::clean_tag_settings(&payload["tags"], &saved["tags"]);
    }
    type Check = fn(&Value) -> (serde_json::Map<String, Value>, &'static str);
    type Clean = fn(&Value, &Value) -> Value;
    let blocks: [(&str, Check, Clean); 3] = [
        ("lobby", settings::lobby_fields, settings::clean_lobby_settings),
        ("view", settings::view_fields, settings::clean_view_settings),
        ("signage", settings::signage_fields, settings::clean_signage_settings),
    ];
    for (key, check, clean) in blocks {
        if payload.get(key).is_some() {
            let problem = check(&payload[key]).1;
            if !problem.is_empty() {
                return error(400, problem);
            }
            merged[key] = clean(&payload[key], &saved[key]);
        }
    }
    if payload.get("link").is_some() {
        let problem = settings::link_fields(&payload["link"]).1;
        if !problem.is_empty() {
            return error(400, &problem);
        }
        merged["link"] = settings::clean_link_settings(&payload["link"], &saved["link"]);
    }
    let items: Vec<Value> = match payload["computers"].as_array() {
        Some(list) => list.clone(),
        None => vec![payload.clone()],
    };
    for item in &items {
        let item = if item.is_object() { item.clone() } else { json!({}) };
        if settings::truthy(&item["host"]) && settings::clean_host(&item["host"]).is_empty() {
            return error(400, "Enter just the machine's name or IP address, with no http:// or port.");
        }
        let blank = matches!(&item["port"], Value::Null) || item["port"] == json!("") || item["port"] == json!(0);
        if item.get("port").is_some() && !blank && settings::clean_port(&item["port"]) == 0 {
            return error(400, "The port is a number from 1 to 65535.");
        }
    }
    if payload["computers"].is_array() {
        merged["computers"] = payload["computers"].clone();
    } else if payload.get("host").is_some() || payload.get("port").is_some() {
        // Before #483 the body held one computer's address: that edits the primary (or makes the first).
        merged["computers"] = with_primary_address(&saved, &payload);
    }
    let clean = clean_stored(&merged);
    if clean["enabled"] == Value::Bool(true) && !(settings::truthy(&clean["host"]) && settings::truthy(&clean["port"])) {
        return error(400, "Enter the machine's address and ProPresenter's port first. The port is in ProPresenter's Network preferences.");
    }
    if c.host.replace_settings(KEY, &clean).is_err() {
        return error(503, "The settings could not be saved right now. Try again in a moment.");
    }
    let on = |v: &Value| if v == &Value::Bool(true) { "on" } else { "off" };
    tracing::info!(
        "ProPresenter settings saved ({}, view-only {}, slide tags {}, follow the plan {}, drive LIVE {})",
        on(&clean["enabled"]),
        on(&clean["view_only"]),
        on(&clean["tags"]["enabled"]),
        on(&clean["link"]["follow"]),
        on(&clean["link"]["drive"])
    );
    audit(&c, &parts).await;
    c.follower.poke().await;
    c.lyrics.refresh();
    c.timers.refresh();
    c.host.announce(OFFERED_CHANGED, ""); // what a panel's menu may offer from ProPresenter changed
    Json(body_now(&c).await).into_response()
}

/// `GET /version` on the address in the form (saved or not). Changes nothing.
async fn test(State(c): State<Ctx>, req: Request) -> Response {
    let payload = match object(req.into_body()).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let host = settings::clean_host(&payload["host"]);
    let port = settings::clean_port(&payload["port"]);
    if host.is_empty() || port == 0 {
        return error(400, "Enter the machine's address and ProPresenter's port.");
    }
    let client = crate::client::Client::new(c.host.net(), &host, u16::try_from(port).unwrap_or(0), Arc::new(|| false));
    match client.version().await {
        Ok(info) => Json(json!({"ok": true, "version": info})).into_response(),
        Err(e) => pp_error(&e),
    }
}

/// ProPresenters announcing themselves on the LAN (mDNS). Nothing is saved or contacted.
async fn discover_route(State(c): State<Ctx>) -> Response {
    Json(json!({"found": discover::discover(c.host.net(), 2.5).await})).into_response()
}

async fn stage_set(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let text = Follower::clean_message(payload["text"].as_str().unwrap_or(""));
    if text.is_empty() {
        return error(400, "Type the message to show.");
    }
    if let Err(e) = c.follower.set_stage_message(&text, None).await {
        return pp_error(&e);
    }
    audit(&c, &parts).await;
    Json(body_now(&c).await).into_response()
}

async fn stage_clear(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    if let Err(e) = c.follower.clear_stage_message().await {
        return pp_error(&e);
    }
    audit(&c, &parts).await;
    Json(body_now(&c).await).into_response()
}

/// Copy the announcement playlist's slides into signage now, even if nothing seems to have changed.
async fn signage_sync(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    if c.follower.settings()["signage"]["enabled"] != Value::Bool(true) {
        return error(409, "Switch on announcement slides for signage first.");
    }
    let out = c.sync.sync(true).await;
    if out["error"].as_str().is_some_and(|e| !e.is_empty()) {
        return error(409, out["error"].as_str().unwrap_or(""));
    }
    audit(&c, &parts).await;
    Json(body_now(&c).await).into_response()
}

// ------------------------------------------------------------------------------------------------------------------ streams
struct Watch {
    rx: tokio::sync::mpsc::Receiver<bytes::Bytes>,
    on_drop: Option<Box<dyn FnOnce() + Send>>,
    beat: Box<dyn Fn() -> Value + Send>,
}

impl Drop for Watch {
    fn drop(&mut self) {
        if let Some(f) = self.on_drop.take() {
            f();
        }
    }
}

fn watch(rx: tokio::sync::mpsc::Receiver<bytes::Bytes>, on_drop: Box<dyn FnOnce() + Send>, beat: Box<dyn Fn() -> Value + Send>) -> Response {
    let stream = futures_util::stream::unfold(Watch { rx, on_drop: Some(on_drop), beat }, |mut w| async move {
        let chunk = match tokio::time::timeout(HEARTBEAT, w.rx.recv()).await {
            Ok(Some(f)) => f,
            Ok(None) => return None,
            Err(_) => frame(&(w.beat)()),
        };
        Some((Ok::<_, std::convert::Infallible>(chunk), w))
    });
    sse(axum::body::Body::from_stream(stream))
}

/// ProPresenter's current slide as lyrics, whole in each message. Repeated at each heartbeat, so a panel's silence timer never fires on a quiet stream.
pub fn lyrics_stream(c: &Ctx, audience: &str) -> Response {
    let (id, rx) = c.lyrics.subscribe(audience);
    let (feed, a1, a2) = (Arc::clone(&c.lyrics), audience.to_owned(), audience.to_owned());
    let beat = Arc::clone(&c.lyrics);
    watch(rx, Box::new(move || feed.unsubscribe(&a1, id)), Box::new(move || beat.payload(&a2)))
}

/// The ProPresenter timers a panel shows, for the panel `pid` (`""` for the global settings).
pub fn timers_stream(c: &Ctx, pid: &str) -> Response {
    let (id, rx) = c.timers.subscribe(pid);
    let (feed, p1, p2) = (Arc::clone(&c.timers), pid.to_owned(), pid.to_owned());
    let beat = Arc::clone(&c.timers);
    watch(rx, Box::new(move || feed.unsubscribe(&p1, id)), Box::new(move || beat.payload(&p2)))
}

/// Refuses the feed `for`: `Err(response)` carries the status Python raised.
pub fn refuse_audience(c: &Ctx, audience: &str) -> Option<Response> {
    if !crate::follower::AUDIENCES.contains(&audience) {
        return Some(detail(400, "unknown audience"));
    }
    // Licensed lyrics: each audience is the operator's switch, and the phone viewer's is off by default.
    if !c.lyrics.allowed(audience) {
        return Some(detail(403, "the lyrics feed is not switched on for this audience"));
    }
    None
}

pub fn router(c: Ctx) -> Router {
    let h = c.host.clone();
    let view = |r| gate(&h, "propresenter.view", r);
    let control = |r| gate(&h, "propresenter.control", r);
    let manage = |r| gate(&h, "propresenter.manage", r);
    let base = "/api/admin/propresenter";
    Router::new()
        .route(base, view(get(get_body)).merge(manage(post(save))))
        .route(&format!("{base}/test"), manage(post(test)))
        .route(&format!("{base}/discover"), manage(post(discover_route)))
        .route(&format!("{base}/signage/sync"), manage(post(signage_sync)))
        .route(&format!("{base}/stage"), control(post(stage_set)).merge(control(delete(stage_clear))))
        .merge(crate::routes_ctl::router(&c))
        .with_state(c)
}
