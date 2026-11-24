//! Signage routes (`routes.py`, #205).
//!
//! Public, on the signage port only (the module's listener; core refuses `/signage` on every other port):
//!
//! ```text
//! GET  /signage/<slug>                 the full-screen player page
//! GET  /signage/_/player.js            its script
//! GET  /signage/_/state/<slug>         what the screen plays now (JSON)
//! GET  /signage/_/events/<slug>        the same, pushed whenever it changes (server-sent events)
//! POST /signage/_/now/<slug>           the player saying which item it is showing
//! POST /signage/_/done/<slug>          the player saying it has played a "once" push to its end
//! GET  /signage/_/media/<sha>.<ext>    an uploaded file, cached by the browser for good
//! ```
//!
//! Admin, on the panel port, each behind `signage.view` or `signage.manage`, with the audit entry Python's routes wrote.

use std::pin::Pin;
use std::sync::Arc;
use std::task::{Context, Poll};
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::{Path, Request, State};
use axum::http::request::Parts;
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post, put};
use axum::{Json, Router};
use futures_core::Stream;
use http_body_util::BodyExt;
use relay_core::access::gate;
use relay_core::schedules;
use relay_core::screens::{showing, LinksCap, LINKS};
use relay_core::Host;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::store::{epoch_secs, Error, Store};
use crate::takeover::ALERT_MODES;
use crate::{media, Service, Slot};

const HEARTBEAT: Duration = Duration::from_secs(15);
const IMMUTABLE: &str = "public, max-age=31536000, immutable";
const UNAVAILABLE: &str = "The database is not available right now. Try again in a moment.";
const BAD_REQUEST: &str = "That request is not valid.";

#[derive(Clone)]
struct Ctx {
    host: Host,
    slot: Slot,
}

fn detail(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"detail": text}))).into_response()
}

fn error(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"error": text}))).into_response()
}

fn refuse(e: Error) -> Response {
    match e {
        Error::Invalid(text, status) => error(status, &text),
        Error::Unavailable => error(503, UNAVAILABLE),
    }
}

fn svc(c: &Ctx) -> Result<Arc<Service>, Response> {
    c.slot.lock().ok().and_then(|s| s.clone()).ok_or_else(|| detail(503, "Signage is not available right now"))
}

fn int(v: &str) -> Result<i64, Response> {
    v.parse().map_err(|_| detail(404, "Not Found"))
}

async fn limited(body: Body, limit: usize) -> Result<Bytes, Response> {
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
    Ok(Bytes::from(out))
}

/// A JSON object body: anything else is the 422 FastAPI sent for `payload: dict`.
async fn object(body: Body) -> Result<Value, Response> {
    let raw = limited(body, 1 << 20).await?;
    match serde_json::from_slice::<Value>(&raw) {
        Ok(v @ Value::Object(_)) => Ok(v),
        _ => Err(detail(422, BAD_REQUEST)),
    }
}

fn screen_gone(slug_ok: bool) -> Response {
    if slug_ok { detail(404, "Not Found") } else { detail(503, "Signage is not available right now") }
}

fn file_response(path: &std::path::Path, mime: &str, cache: &str) -> Response {
    match std::fs::read(path) {
        Ok(bytes) => {
            let mut r = Response::new(Body::from(bytes));
            r.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_str(mime).unwrap_or(HeaderValue::from_static("application/octet-stream")));
            r.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_str(cache).unwrap_or(HeaderValue::from_static("no-store")));
            r
        }
        Err(_) => detail(404, "Not Found"),
    }
}

fn html_escape(s: &str) -> String {
    s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&#34;").replace('\'', "&#39;")
}

// ================================================================== public (signage port)
async fn screen_or_404(s: &Service, slug: &str) -> Result<Value, Response> {
    match s.store.screen_by_slug(slug).await {
        Ok(Some(screen)) => Ok(screen),
        Ok(None) => Err(screen_gone(true)),
        Err(_) => Err(screen_gone(false)),
    }
}

/// What a player needs. The screen's core record (#527) can decide it shows a board instead, or a playlist of its own choosing: that is
/// the `board` redirect and the `playlist_id` the schedule layers sit on.
async fn player_payload(s: &Service, screen: &Value) -> Result<Value, Error> {
    let st = &s.store;
    let now = epoch_secs();
    let core = match screen["core_id"].as_i64() {
        Some(id) => st.screens.get(id).await?,
        None => None,
    };
    let shows = match &core {
        Some(c) => {
            let (date, minute) = schedules::local_of_utc(now.div_euclid(60), &st.tz_name());
            showing(c, |r| st.open_ref(r, date, minute), true)
        }
        None => json!({"kind": ""}),
    };
    let board = shows["ref"].as_str().filter(|r| shows["kind"] == "board" && !r.is_empty());
    if let (Some(board), Some(core)) = (board, &core) {
        let known = st.host.settings("boards").and_then(|b| b.as_array().map(|a| a.iter().any(|x| x["id"] == board))).unwrap_or(false);
        if known {
            return Ok(json!({"screen": {"slug": screen["slug"], "name": screen["name"], "sound": screen["sound"] == true}, "playlist": null, "items": [], "today": "",
                "server_time": now * 1000, "takeover": null, "banner": null,
                "board": {"port": st.host.viewer_port(), "path": format!("/board/{board}?screen=screen-{}", core["id"])}}));
        }
    }
    let mut screen = screen.clone();
    if shows["kind"] == "signage" {
        if let Some(pid) = shows["ref"].as_str().and_then(|r| r.parse::<i64>().ok()) {
            if st.playlist_get(pid).await?.is_some() {
                screen["playlist_id"] = json!(pid);
                screen["group_id"] = Value::Null;
            }
        }
    }
    let mut out = st.player_state(&screen, now).await?;
    let taken = st.takeover_rows().await?;
    out["takeover"] = st.takeover_payload(&screen, &taken).await?.unwrap_or(Value::Null);
    out["banner"] = st.banner(&screen).unwrap_or(Value::Null);
    out["server_time"] = json!(now * 1000);
    Ok(out)
}

async fn player_js(State(c): State<Ctx>) -> Response {
    match svc(&c) {
        Ok(s) => file_response(&s.app_dir.join("modules").join("signage").join("static").join("player.js"), "text/javascript", "no-cache"),
        Err(r) => r,
    }
}

async fn public_media(State(c): State<Ctx>, Path(name): Path<String>) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let Some((path, (_, mime))) = s.store.media.file_path(&name).and_then(|p| media::parse_file_name(&name).and_then(|(_, e)| media::type_of(e)).map(|t| (p, t))) else { return detail(404, "Not Found") };
    file_response(&path, mime, IMMUTABLE)
}

async fn public_state(State(c): State<Ctx>, Path(slug): Path<String>) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let screen = match screen_or_404(&s, &slug).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    match player_payload(&s, &screen).await {
        Ok(v) => {
            let mut r = Json(v).into_response();
            r.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
            r
        }
        Err(e) => refuse(e),
    }
}

struct Frames(mpsc::Receiver<Bytes>);

impl Stream for Frames {
    type Item = Result<Bytes, std::convert::Infallible>;
    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.0.poll_recv(cx).map(|f| f.map(Ok))
    }
}

async fn public_events(State(c): State<Ctx>, Path(slug): Path<String>) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    if let Err(r) = screen_or_404(&s, &slug).await {
        return r;
    }
    let shutdown = c.host.shutdown.clone();
    let (tx, rx) = mpsc::channel::<Bytes>(8);
    tokio::spawn(async move {
        let (mut last_sent, mut seen, mut last_minute) = (None::<String>, u64::MAX, epoch_secs() / 60);
        let mut beat = tokio::time::Instant::now();
        let mut alert_key = s.store.alert_sig();
        loop {
            // Alerts are Relay's own and change without anything in signage being edited: look each second.
            let key_now = s.store.alert_sig();
            if key_now != alert_key {
                alert_key = key_now;
                seen = u64::MAX;
            }
            let version = s.store.live.version();
            if version != seen {
                seen = version;
                let payload = match s.store.screen_by_slug(&slug).await {
                    Ok(Some(screen)) => player_payload(&s, &screen).await.ok(),
                    Ok(None) => Some(json!({"gone": true})),
                    Err(_) => None,
                };
                if let Some(mut p) = payload {
                    let key = {
                        let mut k = p.clone();
                        if let Some(o) = k.as_object_mut() {
                            o.remove("server_time");
                        }
                        k.to_string()
                    };
                    if last_sent.as_deref() != Some(key.as_str()) {
                        last_sent = Some(key);
                        beat = tokio::time::Instant::now();
                        if let Some(o) = p.as_object_mut() {
                            o.entry("server_time").or_insert(json!(epoch_secs() * 1000));
                        }
                        if tx.send(Bytes::from(format!("data: {p}\n\n"))).await.is_err() {
                            break;
                        }
                    }
                }
            }
            if beat.elapsed() >= HEARTBEAT {
                beat = tokio::time::Instant::now();
                if tx.send(Bytes::from_static(b": keepalive\n\n")).await.is_err() {
                    break;
                }
            }
            // The clock can change what plays (a slot starts or ends, an override runs out, the date turns): look again each minute.
            let minute = epoch_secs() / 60;
            if minute != last_minute {
                last_minute = minute;
                seen = u64::MAX;
            }
            tokio::select! {
                () = tokio::time::sleep(Duration::from_secs(1)) => {}
                () = tx.closed() => break,
                () = shutdown.wait() => break,
            }
        }
    });
    let mut resp = Response::new(Body::from_stream(Frames(rx)));
    let h = resp.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache, no-transform"));
    h.insert("x-accel-buffering", HeaderValue::from_static("no"));
    resp
}

async fn public_now(State(c): State<Ctx>, Path(slug): Path<String>, req: Request) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let screen = match screen_or_404(&s, &slug).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    let raw = match limited(req.into_body(), 1024).await {
        Ok(r) => r,
        Err(r) => return r,
    };
    let body: Value = if raw.is_empty() { json!({}) } else { match serde_json::from_slice(&raw) { Ok(v) => v, Err(_) => return detail(400, "Bad request") } };
    let item = body.get("item").and_then(Value::as_i64);
    let label = body.get("label").and_then(Value::as_str).unwrap_or("");
    s.store.live.report(&slug, item, label);
    if let Some(id) = screen["core_id"].as_i64() {
        let _ = s.store.screens.touch(id, "").await;
    }
    Json(json!({"ok": true})).into_response()
}

async fn public_done(State(c): State<Ctx>, Path(slug): Path<String>, req: Request) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    if let Err(r) = screen_or_404(&s, &slug).await {
        return r;
    }
    let raw = match limited(req.into_body(), 256).await {
        Ok(r) => r,
        Err(r) => return r,
    };
    let body: Value = if raw.is_empty() { json!({}) } else { match serde_json::from_slice(&raw) { Ok(v) => v, Err(_) => return detail(400, "Bad request") } };
    if let Some(id) = body.get("id").and_then(Value::as_i64) {
        s.store.live.done(&slug, id);
    }
    Json(json!({"ok": true})).into_response()
}

async fn player_page(State(c): State<Ctx>, Path(slug): Path<String>) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let screen = match screen_or_404(&s, &slug).await {
        Ok(x) => x,
        Err(r) => return r,
    };
    let path = s.app_dir.join("modules").join("signage").join("static").join("player.js");
    let v = std::fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok()).map_or(0, |d| d.as_secs());
    let slug_json = Value::String(slug.clone()).to_string();
    let html = format!(
        "<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n<meta name=\"robots\" content=\"noindex\">\n<title>{name}</title>\n<style>\n  html, body {{ margin:0; height:100%; background:#000; overflow:hidden; cursor:none; }}\n  #stage {{ position:fixed; inset:0; background:#000; }}\n</style>\n</head>\n<body>\n<div id=\"stage\"></div>\n<script src=\"/signage/_/player.js?v={v}\"></script>\n<script>\n(function () {{\n  var slug = {slug_json};\n  var player = new SignagePlayer(document.getElementById('stage'), {{\n    nowUrl: '/signage/_/now/' + slug,\n    doneUrl: '/signage/_/done/' + slug\n  }});\n  player.follow('/signage/_/state/' + slug, '/signage/_/events/' + slug);\n  // Browsers only go full screen after a click or key press; a kiosk browser already is.\n  function full() {{\n    var d = document.documentElement, f = d.requestFullscreen || d.webkitRequestFullscreen;\n    if (f && !document.fullscreenElement) {{ try {{ f.call(d); }} catch (e) {{ /* not allowed */ }} }}\n  }}\n  document.addEventListener('click', full);\n  document.addEventListener('keydown', full);\n}})();\n</script>\n</body>\n</html>\n",
        name = html_escape(screen["name"].as_str().unwrap_or(""))
    );
    let mut r = Response::new(Body::from(html));
    let h = r.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/html; charset=utf-8"));
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache"));
    h.insert("referrer-policy", HeaderValue::from_static("no-referrer"));
    r
}

// ================================================================== admin
async fn screen_urls(c: &Ctx, slug: &str, port: i64) -> Vec<String> {
    let ips = c.host.using::<LinksCap>(LINKS).map(|l| (l.0)("viewers")).unwrap_or_default();
    ips.iter().map(|ip| format!("http://{}:{port}/signage/{slug}", if ip.contains(':') && !ip.starts_with('[') { format!("[{ip}]") } else { ip.clone() })).collect()
}

fn port_info(st: &Store) -> Value {
    let configured = st.settings()["port"].as_i64().unwrap_or(8090);
    json!({"configured": configured, "bound": configured, "restart": false})
}

async fn admin_state(State(c): State<Ctx>) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let st = &s.store;
    let built = async {
        let (mut screens, lists, groups, lib) = (st.screens().await?, st.playlists().await?, st.groups().await?, st.media_list().await?);
        let (slot_rows, over, taken) = (st.slots().await?, st.overrides().await?, st.takeover_rows().await?);
        let names: std::collections::HashMap<i64, String> = lists.iter().map(|p| (p["id"].as_i64().unwrap_or(0), p["name"].as_str().unwrap_or("").to_owned())).collect();
        let group_names: std::collections::HashMap<i64, String> = groups.iter().map(|g| (g["id"].as_i64().unwrap_or(0), g["name"].as_str().unwrap_or("").to_owned())).collect();
        let items: std::collections::HashMap<i64, &Value> = lists.iter().flat_map(|p| p["items"].as_array().into_iter().flatten()).map(|i| (i["id"].as_i64().unwrap_or(0), i)).collect();
        let now = epoch_secs();
        let info = port_info(st);
        for sc in &mut screens {
            let plan = st.resolve(sc, now, Some(&slot_rows), Some(&over)).await?;
            let tk = st.takeover_pick(sc, &taken);
            sc["taken_over"] = tk.map_or(Value::Null, |t| json!({"kind": t["kind"], "label": t["label"], "from": t["target"], "until": t["expires_at"]}));
            sc["banner"] = st.banner(sc).unwrap_or(Value::Null);
            let pid = plan["playlist_id"].as_i64();
            sc["playing_playlist"] = match pid {
                Some(p) => json!({"id": p, "name": names.get(&p).cloned().unwrap_or_default(), "from": plan["layer"], "why": plan["why"], "until": plan["until"]}),
                None => Value::Null,
            };
            if pid.is_none() {
                sc["why"] = plan["why"].clone();
            }
            let slug = sc["slug"].as_str().unwrap_or("").to_owned();
            sc["urls"] = json!(screen_urls(&c, &slug, info["configured"].as_i64().unwrap_or(8090)).await);
            sc["now"] = match st.live.playing(&slug) {
                Some(rep) => {
                    let it = rep.item_id.and_then(|i| items.get(&i).copied());
                    let label = it.and_then(|i| i["name"].as_str()).filter(|n| !n.is_empty()).map_or_else(|| rep.label.clone(), str::to_owned);
                    let nowf = now as f64;
                    json!({"label": label, "thumb": it.and_then(|i| i["thumb"].as_str()).unwrap_or(""), "for_s": (nowf - rep.since) as i64, "seen_s": (nowf - rep.seen) as i64})
                }
                None => Value::Null,
            };
            sc["group_name"] = json!(sc["group_id"].as_i64().and_then(|g| group_names.get(&g).cloned()).unwrap_or_default());
        }
        let named: Vec<Value> = st.host.settings("schedules").and_then(|v| v.as_array().cloned()).unwrap_or_default().iter().map(|x| json!({"ref": format!("{}{}", schedules::REF_PREFIX, x["id"].as_str().unwrap_or("")), "name": x["name"], "summary": schedules::summary(x)})).collect();
        let (used, free) = st.media.disk();
        Ok::<Value, Error>(json!({
            "settings": st.settings(), "port": info, "screens": screens, "groups": groups, "playlists": lists, "media": lib,
            "storage": {"used": used, "free": free, "low": free >= 0 && (free as u64) < media::LOW_DISK},
            "limits": {"image": media::IMAGE_LIMIT, "video": media::VIDEO_LIMIT},
            "sources": st.source_kinds(),
            "slots": slot_rows, "overrides": over, "takeovers": taken,
            "views": crate::takeover::VIEWS.iter().map(|v| json!({"id": v, "label": crate::takeover::view(v).map_or("", |x| x.0)})).collect::<Vec<_>>(),
            "timezone": st.tz_name(),
            "timezones": schedules::US_TIMEZONES.iter().map(|(id, label)| json!({"id": id, "label": label})).collect::<Vec<_>>(),
            "named_schedules": named, "today": st.today(None),
        }))
    };
    match built.await {
        Ok(v) => Json(v).into_response(),
        Err(e) => refuse(e),
    }
}

fn query_of(req: &Request, key: &str) -> Option<String> {
    req.uri().query()?.split('&').filter_map(|p| p.split_once('=')).find(|(k, _)| *k == key).map(|(_, v)| v.to_owned())
}

async fn admin_preview(State(c): State<Ctx>, req: Request) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let (screen, playlist) = (query_of(&req, "screen").unwrap_or_default(), query_of(&req, "playlist").and_then(|p| p.parse::<i64>().ok()).unwrap_or(0));
    let st = &s.store;
    let now = epoch_secs();
    let built = async {
        if !screen.is_empty() {
            let Some(sc) = st.screen_by_slug(&screen).await? else { return Ok(None) };
            Ok(Some(st.player_state(&sc, now).await?))
        } else {
            let Some(p) = st.playlist_get(playlist).await? else { return Ok(None) };
            let day = st.today(Some(now));
            let items = st.playable_items(p["id"].as_i64().unwrap_or(0), &day).await?;
            Ok::<_, Error>(Some(json!({"screen": {"slug": "", "name": p["name"], "sound": false}, "playlist": {"id": p["id"], "name": p["name"], "from": ""}, "items": items, "today": day})))
        }
    };
    let mut state = match built.await {
        Ok(Some(v)) => v,
        Ok(None) => return detail(404, "Not Found"),
        Err(e) => return refuse(e),
    };
    for it in state["items"].as_array_mut().into_iter().flatten() {
        if let Some(rest) = it["url"].as_str().and_then(|u| u.strip_prefix(media::PUBLIC_PREFIX)).map(str::to_owned) {
            it["url"] = json!(format!("/api/admin/signage/file/{rest}"));
        }
    }
    state["server_time"] = json!(now * 1000);
    Json(state).into_response()
}

async fn admin_source_choices(State(c): State<Ctx>, Path(kind): Path<String>) -> Response {
    if kind == "media" {
        return Json(json!({"choices": []})).into_response();
    }
    match c.host.using::<relay_core::screens::SignageSource>(&format!("{}{kind}", relay_core::screens::SIGNAGE_SOURCE)) {
        Some(src) => Json(json!({"choices": (src.choices)().into_iter().take(500).collect::<Vec<_>>()})).into_response(),
        None => detail(404, "Not Found"),
    }
}

macro_rules! manage {
    ($c:expr, $req:expr) => {{
        let s = match svc(&$c) {
            Ok(s) => s,
            Err(r) => return r,
        };
        let (parts, body) = $req.into_parts();
        (s, parts, body)
    }};
}

async fn note(c: &Ctx, parts: &Parts, action: &str, summary: &str, target: &str, detail: Option<Value>) {
    c.host.note(parts, action, summary, target, detail).await;
}

async fn admin_settings(State(c): State<Ctx>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let st = &s.store;
    let mut change = serde_json::Map::new();
    if let Some(p) = payload.get("port") {
        let port = match p {
            Value::Number(n) => n.as_i64(),
            Value::String(s) => s.trim().parse().ok(),
            _ => None,
        };
        let Some(port) = port.filter(|p| (1024..=65535).contains(p)) else { return error(400, "The port is a number from 1024 to 65535.") };
        let admin = st.host.settings("admin_port").and_then(|v| v.as_i64()).unwrap_or(8443);
        if port == i64::from(st.host.viewer_port()) || port == admin {
            return error(400, "Relay already uses that port. Choose another.");
        }
        change.insert("port".into(), json!(port));
    }
    if let Some(u) = payload.get("override_until") {
        if !matches!(u.as_str(), Some("next_change" | "cleared")) {
            return error(400, "A hand-chosen playlist lasts until the next scheduled change or until cleared.");
        }
        change.insert("override_until".into(), u.clone());
    }
    if let Some(a) = payload.get("alert_screens") {
        if !a.as_str().is_some_and(|a| ALERT_MODES.contains(&a)) {
            return error(400, "Alerts show on all screens, on chosen groups, or not at all.");
        }
        change.insert("alert_screens".into(), a.clone());
    }
    if let Some(g) = payload.get("alert_groups") {
        let Some(ids) = g.as_array().filter(|a| a.iter().all(|i| i.is_i64())) else { return error(400, "Choose groups from the list.") };
        let known: Vec<i64> = match st.groups().await {
            Ok(gs) => gs.iter().filter_map(|g| g["id"].as_i64()).collect(),
            Err(e) => return refuse(e),
        };
        let mut keep: Vec<i64> = ids.iter().filter_map(Value::as_i64).filter(|i| known.contains(i)).collect();
        keep.sort_unstable();
        keep.dedup();
        change.insert("alert_groups".into(), json!(keep));
    }
    if change.is_empty() {
        return error(400, "Nothing to change.");
    }
    let change = Value::Object(change);
    if st.host.save_settings("signage", &change).is_err() {
        return error(500, "The signage settings could not be saved.");
    }
    st.live.bump();
    note(&c, &parts, "signage.settings", "Changed the signage settings", "", Some(change)).await;
    Json(json!({"settings": st.settings(), "port": port_info(st)})).into_response()
}

async fn slot_write(c: Ctx, id: Option<String>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let sid = match id.as_deref().map(int).transpose() {
        Ok(v) => v,
        Err(r) => return r,
    };
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match s.store.slot_save(sid, &payload).await {
        Ok(row) => {
            let (action, summary) = if sid.is_none() { ("signage.slot_create", "Added a signage schedule slot") } else { ("signage.slot_update", "Changed a signage schedule slot") };
            note(&c, &parts, action, summary, &row["id"].to_string(), None).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn slot_create(State(c): State<Ctx>, req: Request) -> Response {
    slot_write(c, None, req).await
}

async fn slot_update(State(c): State<Ctx>, Path(sid): Path<String>, req: Request) -> Response {
    slot_write(c, Some(sid), req).await
}

async fn slot_delete(State(c): State<Ctx>, Path(sid): Path<String>, req: Request) -> Response {
    let (s, parts, _) = manage!(c, req);
    let id = match int(&sid) {
        Ok(i) => i,
        Err(r) => return r,
    };
    match s.store.slot_delete(id).await {
        Ok(()) => {
            note(&c, &parts, "signage.slot_delete", "Deleted a signage schedule slot", &sid, None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn override_set(State(c): State<Ctx>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let default = s.store.settings()["override_until"].as_str().unwrap_or("next_change").to_owned();
    let until = payload.get("until").and_then(Value::as_str).filter(|u| !u.is_empty()).map_or(default, str::to_owned);
    if !["next_change", "cleared"].contains(&until.as_str()) {
        return error(400, "A hand-chosen playlist lasts until the next scheduled change or until cleared.");
    }
    let num = |k: &str| match payload.get(k) {
        None | Some(Value::Null) => Some(0),
        Some(Value::Number(n)) => n.as_i64(),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    };
    let (Some(target_id), Some(playlist_id)) = (num("target_id"), num("playlist_id")) else { return error(400, "Choose a screen or a group.") };
    let target = payload.get("target").and_then(Value::as_str).unwrap_or("").to_owned();
    match s.store.override_set(&target, target_id, playlist_id, &until).await {
        Ok(row) => {
            note(&c, &parts, "signage.override_set", "Put a signage playlist on by hand", &format!("{} {}", row["target"].as_str().unwrap_or(""), row["target_id"]), Some(json!({"playlist": row["playlist_id"], "until": until}))).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn override_clear(State(c): State<Ctx>, req: Request) -> Response {
    let target = query_of(&req, "target").unwrap_or_default();
    let target_id = query_of(&req, "target_id").and_then(|t| t.parse::<i64>().ok()).unwrap_or(0);
    let (s, parts, _) = manage!(c, req);
    let t = if ["screen", "group", "all"].contains(&target.as_str()) { target.as_str() } else { "" };
    match s.store.override_clear(t, target_id).await {
        Ok(()) => {
            note(&c, &parts, "signage.override_clear", "Cleared a hand-chosen signage playlist", &format!("{target} {target_id}"), None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn takeover_set(State(c): State<Ctx>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let num = |k: &str| match payload.get(k) {
        None | Some(Value::Null) => Some(0),
        Some(Value::Number(n)) => n.as_i64(),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    };
    let (Some(target_id), Some(seconds)) = (num("target_id"), num("seconds")) else { return error(400, "Choose a screen or a group, and a number of seconds.") };
    let (target, text) = (payload.get("target").and_then(Value::as_str).unwrap_or("").to_owned(), |k: &str| payload.get(k).and_then(Value::as_str).unwrap_or("").to_owned());
    let kind = payload.get("kind").and_then(Value::as_str).unwrap_or("");
    let row = match kind {
        "push" => {
            let mode = payload.get("mode").and_then(Value::as_str).filter(|m| !m.is_empty()).unwrap_or("cleared").to_owned();
            s.store.push_set(&target, target_id, &text("media_id"), &mode, seconds).await
        }
        "live" => s.store.live_view_set(&target, target_id, &text("view")).await,
        _ => return error(400, "Push a file or put a live view on."),
    };
    match row {
        Ok(row) => {
            let summary = if kind == "push" { "Pushed a file to signage screens" } else { "Put a live view on signage screens" };
            note(&c, &parts, "signage.takeover_set", summary, &format!("{} {}", row["target"].as_str().unwrap_or(""), row["target_id"]), Some(json!({"kind": kind, "view": row["view"], "mode": row["mode"], "seconds": row["seconds"]}))).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn takeover_clear(State(c): State<Ctx>, req: Request) -> Response {
    let target = query_of(&req, "target").unwrap_or_default();
    let target_id = query_of(&req, "target_id").and_then(|t| t.parse::<i64>().ok()).unwrap_or(0);
    let (s, parts, _) = manage!(c, req);
    if !["screen", "group", "all"].contains(&target.as_str()) {
        return error(400, "Choose a screen, a group or all screens.");
    }
    match s.store.takeover_clear(&target, target_id).await {
        Ok(()) => {
            note(&c, &parts, "signage.takeover_clear", "Returned signage screens to their playlist", &format!("{target} {target_id}"), None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn takeover_clear_all(State(c): State<Ctx>, req: Request) -> Response {
    let (s, parts, _) = manage!(c, req);
    match s.store.takeover_clear_all().await {
        Ok(n) => {
            note(&c, &parts, "signage.takeover_clear_all", "Returned every signage screen to its playlist", "", Some(json!({"cleared": n}))).await;
            Json(json!({"ok": true, "cleared": n})).into_response()
        }
        Err(e) => refuse(e),
    }
}

// ------------------------------------------------------------------ media
async fn media_upload(State(c): State<Ctx>, req: Request) -> Response {
    if req.headers().get(header::CONTENT_LENGTH).and_then(|v| v.to_str().ok()).and_then(|v| v.parse::<u64>().ok()).is_some_and(|n| n > media::VIDEO_LIMIT) {
        return error(413, "That file is too large. Images can be up to 25 MB and videos up to 500 MB.");
    }
    let (name, width, height, duration) = (
        query_of(&req, "name").unwrap_or_default(),
        query_of(&req, "width").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0),
        query_of(&req, "height").and_then(|v| v.parse::<i64>().ok()).unwrap_or(0),
        query_of(&req, "duration").and_then(|v| v.parse::<f64>().ok()).unwrap_or(0.0),
    );
    let (s, parts, body) = manage!(c, req);
    let (sha, ext, size) = match s.store.media.receive(body).await {
        Ok(r) => r,
        Err(e) => return refuse(e.into()),
    };
    let name = percent_decode(&name);
    let name = if name.is_empty() { format!("Upload.{ext}") } else { name };
    match s.store.media_add(&name, &sha, &ext, size as i64, width, height, duration).await {
        Ok(row) => {
            note(&c, &parts, "signage.media_upload", "Uploaded signage media", row["name"].as_str().unwrap_or(""), None).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

/// A query value as the browser wrote it (`+` is a space, `%XX` a byte).
fn percent_decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => out.push(b' '),
            b'%' if b.get(i + 1..i + 3).is_some_and(|h| h.iter().all(u8::is_ascii_hexdigit)) => {
                out.push(u8::from_str_radix(std::str::from_utf8(&b[i + 1..i + 3]).unwrap_or("00"), 16).unwrap_or(0));
                i += 2;
            }
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

async fn media_thumb_put(State(c): State<Ctx>, Path(mid): Path<String>, req: Request) -> Response {
    let (s, _, body) = manage!(c, req);
    let data = match limited(body, media::THUMB_LIMIT).await {
        Ok(d) => d,
        Err(r) => return r,
    };
    match s.store.media_thumb(&mid, &data).await {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => refuse(e),
    }
}

async fn media_thumb_get(State(c): State<Ctx>, Path(mid): Path<String>) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    let row = match s.store.media_get(&mid).await {
        Ok(r) => r,
        Err(e) => return refuse(e),
    };
    match row.and_then(|r| s.store.media.thumb_path(r["sha256"].as_str().unwrap_or(""))) {
        Some(p) => file_response(&p, "image/webp", "private, max-age=86400"),
        None => detail(404, "Not Found"),
    }
}

async fn media_file(State(c): State<Ctx>, Path(name): Path<String>) -> Response {
    let s = match svc(&c) {
        Ok(s) => s,
        Err(r) => return r,
    };
    match s.store.media.file_path(&name).and_then(|p| media::parse_file_name(&name).and_then(|(_, e)| media::type_of(e)).map(|t| (p, t))) {
        Some((p, (_, mime))) => file_response(&p, mime, "private, max-age=86400"),
        None => detail(404, "Not Found"),
    }
}

async fn media_rename(State(c): State<Ctx>, Path(mid): Path<String>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match s.store.media_rename(&mid, payload.get("name").unwrap_or(&Value::Null)).await {
        Ok(()) => {
            note(&c, &parts, "signage.media_rename", "Renamed signage media", payload["name"].as_str().unwrap_or(""), None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn media_delete(State(c): State<Ctx>, Path(mid): Path<String>, req: Request) -> Response {
    let (s, parts, _) = manage!(c, req);
    let row = s.store.media_get(&mid).await.ok().flatten();
    match s.store.media_delete(&mid).await {
        Ok(()) => {
            note(&c, &parts, "signage.media_delete", "Deleted signage media", row.as_ref().and_then(|r| r["name"].as_str()).unwrap_or(""), None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

// ------------------------------------------------------------------ playlists, groups, screens
async fn playlist_create(State(c): State<Ctx>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match s.store.playlist_create(payload.get("name").unwrap_or(&Value::Null)).await {
        Ok(row) => {
            note(&c, &parts, "signage.playlist_create", "Added a signage playlist", row["name"].as_str().unwrap_or(""), None).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn playlist_rename(State(c): State<Ctx>, Path(pid): Path<String>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let id = match int(&pid) {
        Ok(i) => i,
        Err(r) => return r,
    };
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match s.store.playlist_rename(id, payload.get("name").unwrap_or(&Value::Null)).await {
        Ok(()) => {
            note(&c, &parts, "signage.playlist_rename", "Renamed a signage playlist", payload["name"].as_str().unwrap_or(""), None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn playlist_duplicate(State(c): State<Ctx>, Path(pid): Path<String>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let id = match int(&pid) {
        Ok(i) => i,
        Err(r) => return r,
    };
    let name = match limited(body, 1 << 16).await {
        Ok(b) => serde_json::from_slice::<Value>(&b).ok().and_then(|v| v["name"].as_str().map(str::to_owned)).unwrap_or_default(),
        Err(r) => return r,
    };
    match s.store.playlist_duplicate(id, &name).await {
        Ok(row) => {
            note(&c, &parts, "signage.playlist_duplicate", "Duplicated a signage playlist", row["name"].as_str().unwrap_or(""), None).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn playlist_delete(State(c): State<Ctx>, Path(pid): Path<String>, req: Request) -> Response {
    let (s, parts, _) = manage!(c, req);
    let id = match int(&pid) {
        Ok(i) => i,
        Err(r) => return r,
    };
    let row = s.store.playlist_get(id).await.ok().flatten();
    match s.store.playlist_delete(id).await {
        Ok(()) => {
            note(&c, &parts, "signage.playlist_delete", "Deleted a signage playlist", row.as_ref().and_then(|r| r["name"].as_str()).unwrap_or(""), None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn playlist_items(State(c): State<Ctx>, Path(pid): Path<String>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let id = match int(&pid) {
        Ok(i) => i,
        Err(r) => return r,
    };
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match s.store.playlist_set_items(id, payload.get("items").unwrap_or(&Value::Null)).await {
        Ok(()) => {
            let row = s.store.playlist_get(id).await.ok().flatten();
            note(&c, &parts, "signage.playlist_items", "Changed a signage playlist's items", row.as_ref().and_then(|r| r["name"].as_str()).unwrap_or(""), Some(json!({"items": payload["items"].as_array().map_or(0, Vec::len)}))).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn group_write(c: Ctx, id: Option<String>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let gid = match id.as_deref().map(int).transpose() {
        Ok(v) => v,
        Err(r) => return r,
    };
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match s.store.group_save(gid, &payload).await {
        Ok(row) => {
            let (action, summary) = if gid.is_none() { ("signage.group_create", "Added a screen group") } else { ("signage.group_update", "Changed a screen group") };
            note(&c, &parts, action, summary, row["name"].as_str().unwrap_or(""), None).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn group_create(State(c): State<Ctx>, req: Request) -> Response {
    group_write(c, None, req).await
}

async fn group_update(State(c): State<Ctx>, Path(gid): Path<String>, req: Request) -> Response {
    group_write(c, Some(gid), req).await
}

async fn group_delete(State(c): State<Ctx>, Path(gid): Path<String>, req: Request) -> Response {
    let (s, parts, _) = manage!(c, req);
    let id = match int(&gid) {
        Ok(i) => i,
        Err(r) => return r,
    };
    match s.store.group_delete(id).await {
        Ok(()) => {
            note(&c, &parts, "signage.group_delete", "Deleted a screen group", &gid, None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn screen_write(c: Ctx, id: Option<String>, req: Request) -> Response {
    let (s, parts, body) = manage!(c, req);
    let sid = match id.as_deref().map(int).transpose() {
        Ok(v) => v,
        Err(r) => return r,
    };
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match s.store.screen_save(sid, &payload).await {
        Ok(row) => {
            let (action, summary) = if sid.is_none() { ("signage.screen_create", "Added a signage screen") } else { ("signage.screen_update", "Changed a signage screen") };
            note(&c, &parts, action, summary, row["slug"].as_str().unwrap_or(""), None).await;
            Json(row).into_response()
        }
        Err(e) => refuse(e),
    }
}

async fn screen_create(State(c): State<Ctx>, req: Request) -> Response {
    screen_write(c, None, req).await
}

async fn screen_update(State(c): State<Ctx>, Path(sid): Path<String>, req: Request) -> Response {
    screen_write(c, Some(sid), req).await
}

async fn screen_delete(State(c): State<Ctx>, Path(sid): Path<String>, req: Request) -> Response {
    let (s, parts, _) = manage!(c, req);
    let id = match int(&sid) {
        Ok(i) => i,
        Err(r) => return r,
    };
    match s.store.screen_delete(id).await {
        Ok(()) => {
            note(&c, &parts, "signage.screen_delete", "Deleted a signage screen", &sid, None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => refuse(e),
    }
}

pub(crate) fn router(host: Host, slot: Slot) -> Router {
    let c = Ctx { host: host.clone(), slot };
    let view = |r| gate(&host, "signage.view", r);
    let manage = |r| gate(&host, "signage.manage", r);
    Router::new()
        // public, on the signage port
        .route("/signage/_/player.js", get(player_js))
        .route("/signage/_/media/{name}", get(public_media))
        .route("/signage/_/state/{slug}", get(public_state))
        .route("/signage/_/events/{slug}", get(public_events))
        .route("/signage/_/now/{slug}", post(public_now))
        .route("/signage/_/done/{slug}", post(public_done))
        .route("/signage/{slug}", get(player_page))
        // the operator's side
        .route("/api/admin/signage", view(get(admin_state)))
        .route("/api/admin/signage/preview", view(get(admin_preview)))
        .route("/api/admin/signage/sources/{kind}", view(get(admin_source_choices)))
        .route("/api/admin/signage/settings", manage(put(admin_settings)))
        .route("/api/admin/signage/slots", manage(post(slot_create)))
        .route("/api/admin/signage/slots/{sid}", manage(put(slot_update).delete(slot_delete)))
        .route("/api/admin/signage/override", manage(put(override_set).delete(override_clear)))
        .route("/api/admin/signage/takeover", manage(put(takeover_set).delete(takeover_clear)))
        .route("/api/admin/signage/takeovers", manage(delete(takeover_clear_all)))
        .route("/api/admin/signage/media", manage(post(media_upload)))
        .route("/api/admin/signage/media/{mid}/thumb", view(get(media_thumb_get)).merge(manage(put(media_thumb_put))))
        .route("/api/admin/signage/file/{name}", view(get(media_file)))
        .route("/api/admin/signage/media/{mid}", manage(axum::routing::patch(media_rename).delete(media_delete)))
        .route("/api/admin/signage/playlists", manage(post(playlist_create)))
        .route("/api/admin/signage/playlists/{pid}", manage(axum::routing::patch(playlist_rename).delete(playlist_delete)))
        .route("/api/admin/signage/playlists/{pid}/duplicate", manage(post(playlist_duplicate)))
        .route("/api/admin/signage/playlists/{pid}/items", manage(put(playlist_items)))
        .route("/api/admin/signage/groups", manage(post(group_create)))
        .route("/api/admin/signage/groups/{gid}", manage(put(group_update).delete(group_delete)))
        .route("/api/admin/signage/screens", manage(post(screen_create)))
        .route("/api/admin/signage/screens/{sid}", manage(put(screen_update).delete(screen_delete)))
        .with_state(c)
}

