//! Alerts, stage messages and panels over HTTP (#660): `app/main.py`'s `/api/alerts/*`, `/api/panels/*`, `/api/admin/alerts*`,
//! `/api/admin/panels*` and `/stream?stream=alert`, on `relay-show`. The rules and the fixed sentences are the Python routes' own.
//!
//! The panels' and senders' routes are on the viewer port with no login, as they always were; what a stray client can do is
//! bounded by `relay-show` (a few short strings, a fixed number of buttons, panels and streams). The admin routes sit behind the
//! accounts, and write the audit entry Python's gate wrote for the same route.
//!
//! Switch it on with `relay-server --rust-show` (or `RELAY_RUST_SHOW=1`). The Python modules that call alerts and panels (OBS,
//! REAPER, Dante, NDI, ProPresenter, Planning Center, tones, alert events) still hold their own in-memory copy until they are
//! ported (#661), so do not run them against this switch.

// axum's `Response` is the error half of every helper here (the answer to send instead): that is the idiom, and each is built once.
#![allow(clippy::result_large_err)]

use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::Duration;

use axum::body::{Body, Bytes};
use axum::extract::{Path, Request, State};
use axum::http::{header, HeaderMap, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use relay_show::alerts::{self, Alerts, Env, History, Row, Store};
use relay_show::firmware::{self, Kind};
use relay_show::panels::{self, Cfg, PanelError, Registry, Update};
use relay_show::screens::{self, Screens};
use serde_json::{json, Map, Value};
use sqlx::SqlitePool;
use tokio::sync::mpsc;

use crate::accounts::{detail, Shared};
use crate::forward::{Conn, Upstream};
use crate::streams::{event_stream, Query};

const HEARTBEAT: Duration = Duration::from_secs(15);
const PING: &[u8] = b": ping\n\n";
const BAD_JSON: &str = "body must be JSON";
const NOT_OBJECT: &str = "body must be a JSON object";
const TOO_LARGE: &str = "request too large";
const UNKNOWN_PANEL: &str = "unknown panel";
const BAD_REQUEST: &str = "That request is not valid.";
const SIGN_IN: &str = "Sign in required";
const KEEP_DAYS: i64 = 30;
const MAX_ROWS: i64 = 50_000;
const PAGE: i64 = 100;

/// config.json's `alerts` and `panels` blocks, through `relay_config::Settings`.
pub struct ConfigStore(pub Arc<relay_config::Settings>);

impl Store for ConfigStore {
    fn read(&self) -> Value {
        self.0.get().ok().and_then(|c| c.get("alerts").cloned()).unwrap_or(Value::Null)
    }
    fn save(&self, clean: &Value) {
        if let Err(e) = self.0.save(&json!({"alerts": clean}), &["alerts"]) {
            tracing::error!("the alert setup was not saved: {e:?}");
        }
    }
}

impl Cfg for ConfigStore {
    fn panels(&self) -> Value {
        self.0.get().ok().and_then(|c| c.get("panels").cloned()).unwrap_or(Value::Null)
    }
    fn save_panels(&self, stored: &Value) {
        if let Err(e) = self.0.save(&json!({"panels": stored}), &["panels"]) {
            tracing::error!("the panel list was not saved: {e:?}");
        }
    }
    fn config(&self) -> Value {
        self.0.get().unwrap_or(Value::Null)
    }
}

/// Alerts reaching panels, and the places screens are in. The place members are read in the background, since an alert is
/// decided on a thread that cannot wait for the database.
struct Link {
    panels: std::sync::OnceLock<Arc<Registry>>,
    places: RwLock<HashMap<i64, Vec<(String, String)>>>,
}

impl Env for Link {
    fn push_panel(&self, pid: &str, message: &Value) -> usize {
        self.panels.get().map_or(0, |p| p.push_alert(pid, message))
    }
    fn panel_name(&self, pid: &str) -> String {
        self.panels.get().map(|p| p.display_name(pid)).unwrap_or_default()
    }
    fn in_place(&self, place: i64) -> Option<Vec<(String, String)>> {
        self.places.read().unwrap_or_else(|e| e.into_inner()).get(&place).cloned()
    }
}

/// History rows written as each alert ends and each stage message is shown. A database that is down loses the row.
struct Rows(SqlitePool);

impl History for Rows {
    fn record(&self, row: Row) {
        let pool = self.0.clone();
        let Ok(handle) = tokio::runtime::Handle::try_current() else { return };
        handle.spawn(async move {
            let r = sqlx::query("insert into alert_history (at, kind, name, text, level, sender, targets, outcome, seen_on, seen_after_s, reply) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
                .bind(if row.at.is_empty() { relay_auth::now() } else { row.at })
                .bind(row.kind)
                .bind(row.name)
                .bind(row.text)
                .bind(row.level)
                .bind(row.sender)
                .bind(alerts::compact(&json!(row.targets)))
                .bind(row.outcome)
                .bind(row.seen_on)
                .bind(row.seen_after_s)
                .bind(row.reply)
                .execute(&pool)
                .await;
            if r.is_err() {
                tracing::warn!("alert history: a row was lost");
            }
        });
    }
}

/// Everything the routes share.
pub struct ShowApi {
    pub acc: Shared,
    pub alerts: Arc<Alerts>,
    pub panels: Arc<Registry>,
    pub screens: Arc<Screens>,
    fw: Arc<firmware::Store>,
    pool: SqlitePool,
    upstream: Option<Arc<Upstream>>,
    clock: Arc<dyn relay_show::Clock>,
}

pub type Api = Arc<ShowApi>;

fn lock<T>(l: &RwLock<T>) -> std::sync::RwLockWriteGuard<'_, T> {
    l.write().unwrap_or_else(|e| e.into_inner())
}

/// Start alerts and panels on `relay.db` and `config.json`. `None` (and the reason in the log) when the database does not have the
/// screens and history tables yet: the app behind the server serves them instead.
pub async fn start(pool: SqlitePool, acc: Shared, config_path: &std::path::Path, upstream: Option<Arc<Upstream>>, shutdown: relay_core::Shutdown) -> Option<Api> {
    if let Err(why) = screens::check_tables(&pool).await {
        tracing::error!("alerts and panels are served by the app behind this server, not by it: {why}");
        return None;
    }
    let settings = Arc::new(relay_config::Settings::new(config_path));
    let store = Arc::new(ConfigStore(settings));
    let clock = relay_show::SystemClock::shared();
    let link = Arc::new(Link { panels: std::sync::OnceLock::new(), places: RwLock::default() });
    let screens = Arc::new(Screens::new(pool.clone()));
    let state_dir = config_path.parent().map(std::path::Path::to_path_buf).unwrap_or_default();
    let fw = Arc::new(firmware::Store::new(&state_dir));
    let registry = Registry::new(store.clone(), fw.clone(), clock.clone());
    let _ = link.panels.set(registry.clone());
    let alerts = Alerts::new(link.clone(), store, Arc::new(Rows(pool.clone())), clock.clone());
    {
        let alerts = alerts.clone();
        registry.set_alerts_for(Box::new(move |pid| alerts.active_for_panel(pid)));
    }
    refresh_places(&screens, &link).await;
    {
        // Once a second: end alerts nobody tapped in time, so the sender sees "Not seen" when it happens. The place members are
        // read again every few seconds; the history is trimmed every hour.
        let (alerts, screens, link, pool, shutdown) = (alerts.clone(), screens.clone(), link.clone(), pool.clone(), shutdown.clone());
        tokio::spawn(async move {
            let mut n: u64 = 0;
            loop {
                tokio::select! {
                    () = shutdown.wait() => break,
                    () = tokio::time::sleep(Duration::from_secs(1)) => {}
                }
                alerts.tick();
                n += 1;
                if n.is_multiple_of(5) {
                    refresh_places(&screens, &link).await;
                }
                if n.is_multiple_of(3600) {
                    prune(&pool).await;
                }
            }
        });
    }
    prune(&pool).await;
    Some(Arc::new(ShowApi { acc, alerts, panels: registry, screens, fw, pool, upstream, clock }))
}

async fn refresh_places(screens: &Screens, link: &Link) {
    let Ok(places) = screens.places().await else { return };
    let mut map = HashMap::new();
    for p in places {
        if let Some(id) = p["id"].as_i64() {
            if let Ok(found) = screens.in_place(id).await {
                map.insert(id, found);
            }
        }
    }
    *lock(&link.places) = map;
}

/// Delete rows older than [`KEEP_DAYS`], then any beyond the newest [`MAX_ROWS`].
async fn prune(pool: &SqlitePool) {
    let _ = sqlx::query("delete from alert_history where at < ?").bind(relay_auth::later(-KEEP_DAYS * 86400)).execute(pool).await;
    let _ = sqlx::query("delete from alert_history where id <= (select id from alert_history order by id desc limit 1 offset ?)").bind(MAX_ROWS).execute(pool).await;
}

// ------------------------------------------------------------------------------------------------------------------ helpers
type Ext = Option<axum::Extension<Conn>>;

async fn read_limited(req: Request, limit: usize) -> Result<Bytes, Response> {
    if let Some(n) = req.headers().get(header::CONTENT_LENGTH).and_then(|v| v.to_str().ok()).and_then(|v| v.parse::<usize>().ok()) {
        if n > limit {
            return Err(detail(413, TOO_LARGE));
        }
    }
    axum::body::to_bytes(req.into_body(), limit).await.map_err(|_| detail(413, TOO_LARGE))
}

fn json_object(raw: &[u8]) -> Result<Map<String, Value>, Response> {
    match serde_json::from_slice::<Value>(if raw.is_empty() { b"{}" } else { raw }) {
        Ok(Value::Object(m)) => Ok(m),
        Ok(_) => Err(detail(400, NOT_OBJECT)),
        Err(_) => Err(detail(400, BAD_JSON)),
    }
}

/// `{"ok": false, "error": <fixed sentence>}`: what a sender, panel or screen is told when an alert request is refused.
fn refused(r: &alerts::Refused) -> Response {
    (StatusCode::from_u16(r.status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"ok": false, "error": r.sentence()}))).into_response()
}

fn ok_with(state: Value) -> Response {
    let mut out = json!({"ok": true});
    if let (Some(o), Some(s)) = (out.as_object_mut(), state.as_object()) {
        o.extend(s.clone());
    }
    Json(out).into_response()
}

// ------------------------------------------------------------------------------------------------------------------ panels (public)
async fn panel_checkin(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let peer = conn.as_ref().map(|c| c.0.peer.ip().to_string()).unwrap_or_default();
    let raw = match read_limited(req, panels::CHECKIN_MAX_BYTES).await {
        Ok(r) => r,
        Err(r) => return r,
    };
    let body = match serde_json::from_slice::<Value>(&raw) {
        Ok(v @ Value::Object(_)) => v,
        Ok(_) => return detail(400, NOT_OBJECT),
        Err(_) => return detail(400, BAD_JSON),
    };
    match api.panels.checkin(&body, &peer) {
        Ok(reply) => {
            // A panel that checks in is a screen of type panel, named as the Panels page names it. The check-in itself succeeds either way.
            let (screens, pid, panel) = (api.screens.clone(), body["id"].as_str().unwrap_or("").to_owned(), api.panels.panel(body["id"].as_str().unwrap_or("")));
            if let Some(p) = panel {
                tokio::spawn(async move {
                    match screens.adopt(&format!("panel:{pid}"), &p.name, "panel", &p.ip, "", true).await {
                        Ok(core) => {
                            if let Some(id) = core["id"].as_i64() {
                                let _ = screens.sync_panel_name(id, &p.name).await;
                            }
                        }
                        Err(_) => tracing::warn!("A panel checked in but could not be listed as a screen"),
                    }
                });
            }
            Json(reply).into_response()
        }
        Err(PanelError::Invalid(s)) => detail(400, &s),
        Err(PanelError::Full) => {
            tracing::warn!("Panel registry full ({}); refused a check-in from {peer}", panels::MAX_PANELS);
            detail(429, "too many panels")
        }
        Err(_) => detail(400, BAD_REQUEST),
    }
}

async fn panel_events(State(api): State<Api>, Path(pid): Path<String>) -> Response {
    if !panels::valid_id(&json!(pid)) || !api.panels.has_checked_in(&pid) {
        return detail(404, UNKNOWN_PANEL);
    }
    let rx = api.panels.subscribe(&pid);
    let reg = api.panels.clone();
    stream_of(rx, move || reg.unsubscribe(&pid), None)
}

/// An event stream fed from a subscriber's queue, with a heartbeat on a quiet one (`beat` says what to send instead of a bare ping).
fn stream_of(mut rx: mpsc::Receiver<Bytes>, done: impl FnOnce() + Send + 'static, beat: Option<Arc<dyn Fn() -> Bytes + Send + Sync>>) -> Response {
    event_stream(move |tx| async move {
        loop {
            tokio::select! {
                frame = rx.recv() => match frame {
                    Some(f) => if tx.send(f).await.is_err() { break },
                    None => break,
                },
                () = tokio::time::sleep(HEARTBEAT) => {
                    let ping = beat.as_ref().map_or_else(|| Bytes::from_static(PING), |b| b());
                    if tx.send(ping).await.is_err() { break }
                }
                () = tx.closed() => break,
            }
        }
        done();
    })
}

async fn panel_update_report(State(api): State<Api>, Path(pid): Path<String>, req: Request) -> Response {
    let raw = match read_limited(req, panels::UPDATE_REPORT_MAX_BYTES).await {
        Ok(r) => r,
        Err(r) => return r,
    };
    let body = match serde_json::from_slice::<Value>(&raw) {
        Ok(v @ Value::Object(_)) => v,
        Ok(_) => return detail(400, NOT_OBJECT),
        Err(_) => return detail(400, BAD_JSON),
    };
    if !api.panels.report_update(&pid, &body) {
        return detail(404, UNKNOWN_PANEL);
    }
    Json(json!({"ok": true})).into_response()
}

async fn panel_firmware_image(State(api): State<Api>, Path((board, name)): Path<(String, String)>) -> Response {
    let version = name.strip_suffix(".bin").unwrap_or("");
    let path = (firmware::parse_version(version).is_some() && firmware::board(&board).is_some()).then(|| api.fw.image_path_for(version, &board)).flatten();
    match path.and_then(|p| std::fs::read(p).ok()) {
        Some(bytes) => file_response(bytes),
        None => detail(404, "no such firmware"),
    }
}

fn file_response(bytes: Vec<u8>) -> Response {
    let mut resp = Response::new(Body::from(bytes));
    resp.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("application/octet-stream"));
    resp.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp
}

// ------------------------------------------------------------------------------------------------------------------ alerts (public)
fn signin_required(api: &Api) -> bool {
    api.alerts.settings()["require_signin"] == true
}

/// With sign-in required, a phone sends only as an account holding `alerts.send`, and only over the operator port (HTTPS), where
/// the session cookie lives (#175). The account's name is what the sender is called.
async fn account(api: &Api, conn: &Ext, headers: &HeaderMap) -> Result<String, Response> {
    if !conn.as_ref().is_some_and(|c| c.0.secure) {
        return Err(detail(401, SIGN_IN));
    }
    let caller = api.acc.caller(headers, conn.as_ref().map(|c| c.0), Some("alerts.send")).await?;
    Ok(caller.user.name.clone())
}

async fn alert_body(req: Request) -> Result<(HeaderMap, Map<String, Value>), Response> {
    let headers = req.headers().clone();
    let raw = read_limited(req, alerts::BODY_MAX_BYTES).await?;
    Ok((headers, json_object(&raw)?))
}

async fn alert_message_send(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let (headers, body) = match alert_body(req).await {
        Ok(b) => b,
        Err(r) => return r,
    };
    let mut sender = alerts::clean_name(body.get("from").unwrap_or(&Value::Null));
    if signin_required(&api) {
        match account(&api, &conn, &headers).await {
            Ok(n) => sender = alerts::clean_name_str(&n),
            Err(r) => return r,
        }
    }
    match api.alerts.send_message(&sender, body.get("preset"), body.get("text"), body.get("banner"), body.get("level")) {
        Ok(state) => ok_with(state),
        Err(e) => refused(&e),
    }
}

async fn alert_message_clear(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let headers = req.headers().clone();
    if signin_required(&api) {
        if let Err(r) = account(&api, &conn, &headers).await {
            return r;
        }
    }
    let raw = match read_limited(req, alerts::BODY_MAX_BYTES).await {
        Ok(r) => r,
        Err(r) => return r,
    };
    let body = match json_object(&raw) {
        Ok(b) => b,
        Err(r) => return r,
    };
    let id = match body.get("id") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Some(s.as_str()),
        Some(_) => return detail(400, "id must be a message id"),
    };
    ok_with(api.alerts.clear_messages(id))
}

async fn alert_messages_stream(State(api): State<Api>) -> Response {
    let rx = api.alerts.subscribe_messages();
    let a = api.alerts.clone();
    let beat: Arc<dyn Fn() -> Bytes + Send + Sync> = Arc::new(move || Bytes::from(format!("data: {}\n\n", alerts::compact(&a.message_state()))));
    stream_of(rx, || {}, Some(beat))
}

pub fn sender_stream(api: &Api) -> Response {
    // Repeated at each heartbeat, so a panel's silence timer never fires on a quiet stream.
    let rx = api.alerts.subscribe_sender();
    let a = api.alerts.clone();
    let beat: Arc<dyn Fn() -> Bytes + Send + Sync> = Arc::new(move || Bytes::from(format!("data: {}\n\n", alerts::compact(&a.state()))));
    stream_of(rx, || {}, Some(beat))
}

async fn alert_send(State(api): State<Api>, Path(button): Path<String>, conn: Ext, req: Request) -> Response {
    let (headers, body) = match alert_body(req).await {
        Ok(b) => b,
        Err(r) => return r,
    };
    let mut sender = alerts::clean_name(body.get("from").unwrap_or(&Value::Null));
    match body.get("panel") {
        None | Some(Value::Null) => {
            if signin_required(&api) {
                match account(&api, &conn, &headers).await {
                    Ok(n) => sender = alerts::clean_name_str(&n),
                    Err(r) => return r,
                }
            }
        }
        Some(p) => {
            let Some(pid) = p.as_str().filter(|s| panels::valid_id(&json!(s)) && api.panels.known(s)) else { return detail(404, UNKNOWN_PANEL) };
            sender = api.panels.display_name(pid);
        }
    }
    match api.alerts.press(&button, &sender, body.get("level")) {
        Ok(state) => ok_with(state),
        Err(e) => refused(&e),
    }
}

async fn alert_cancel(State(api): State<Api>, Path(button): Path<String>, conn: Ext, req: Request) -> Response {
    if signin_required(&api) {
        if let Err(r) = account(&api, &conn, req.headers()).await {
            return r;
        }
    }
    match api.alerts.cancel(&button) {
        Ok(state) => ok_with(state),
        Err(e) => refused(&e),
    }
}

async fn alert_ack(State(api): State<Api>, req: Request) -> Response {
    let (_, body) = match alert_body(req).await {
        Ok(b) => b,
        Err(r) => return r,
    };
    let panel = body.get("panel").filter(|v| !v.is_null());
    if panel.is_some_and(|p| !panels::valid_id(p)) {
        return detail(400, "panel must be a panel id");
    }
    let screen = body.get("screen").filter(|v| !v.is_null());
    if screen.is_some_and(|s| !s.is_string()) {
        return detail(400, "screen must be a name");
    }
    match api.alerts.ack(body.get("id").unwrap_or(&Value::Null), panel.and_then(Value::as_str), screen.and_then(Value::as_str), body.get("reply")) {
        Ok(state) => Json(state).into_response(),
        Err(e) => refused(&e),
    }
}

async fn alert_screen_stream(State(api): State<Api>, req: Request) -> Response {
    let q = Query::parse(req.uri().query());
    let name = alerts::clean_name_str(q.get("name").unwrap_or(""));
    if name.is_empty() {
        return detail(400, "name the screen");
    }
    let rx = match api.alerts.subscribe_screen(&name) {
        Ok(rx) => rx,
        Err(e) => return detail(e.status, e.sentence()),
    };
    // An alert screen is a core screen (#527): the one called this name, or a new one, so every page lists it once.
    let screens = api.screens.clone();
    let key = format!("alert:{}", name.to_lowercase());
    let named = name.clone();
    tokio::spawn(async move {
        if screens.adopt(&key, &named, "browser", "", "", true).await.is_err() {
            tracing::warn!("An alert screen opened but could not be listed as a screen");
        }
    });
    let a = api.alerts.clone();
    stream_of(rx, move || a.screen_closed(&name), None)
}

/// `/stream`: the alert feed here, every other feed to the app behind the server (the caption feeds are still published there).
async fn stream_or_forward(State(api): State<Api>, req: Request) -> Response {
    let q = Query::parse(req.uri().query());
    if q.get("stream") == Some("alert") {
        return sender_stream(&api);
    }
    match &api.upstream {
        Some(up) => up.forward(req).await,
        None => crate::forward::nothing_here(),
    }
}

// ------------------------------------------------------------------------------------------------------------------ alerts (admin)
pub fn alerts_frame(api: &Api) -> Value {
    let mut out = json!({"type": "alerts_admin"});
    if let (Some(o), Some(v)) = (out.as_object_mut(), api.alerts.admin_view().as_object()) {
        o.extend(v.clone());
    }
    out
}

async fn guard(api: &Api, conn: Ext, headers: &HeaderMap, perm: &'static str) -> Result<crate::accounts::Caller, Response> {
    api.acc.caller(headers, conn.map(|c| c.0), Some(perm)).await
}

async fn admin_alerts_get(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "alerts.view").await {
        return r;
    }
    Json(alerts_frame(&api)).into_response()
}

async fn body_value(req: Request) -> Result<Value, Response> {
    let raw = axum::body::to_bytes(req.into_body(), 1 << 20).await.unwrap_or_default();
    match serde_json::from_slice::<Value>(&raw) {
        Ok(v @ Value::Object(_)) => Ok(v),
        _ => Err(detail(422, BAD_REQUEST)),
    }
}

async fn admin_alerts_save(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "alerts.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let payload = match body_value(req).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match api.alerts.save(&payload) {
        Ok(_) => {
            api.acc.record(&who, "alerts.change", "Changed the alert setup", "", None).await;
            Json(alerts_frame(&api)).into_response()
        }
        Err(e) => detail(400, &e),
    }
}

async fn admin_alerts_test(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "alerts.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let payload = match body_value(req).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match api.alerts.test(payload.get("panel"), payload.get("screen")) {
        Ok(took) => {
            api.acc.record(&who, "alerts.test", "Sent a test to a panel or screen", "", None).await;
            Json(json!({"ok": true, "delivered": took})).into_response()
        }
        Err(e) => detail(e.status, e.sentence()),
    }
}

async fn admin_alerts_history(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "alerts.history").await {
        return r;
    }
    let q = Query::parse(req.uri().query());
    let before = q.get("before").and_then(|b| b.trim().parse::<i64>().ok()).filter(|b| *b != 0);
    let limit = q.get("limit").and_then(|b| b.trim().parse::<i64>().ok()).unwrap_or(PAGE).clamp(1, 500);
    let query = match before {
        Some(b) => sqlx::query("select id, at, kind, name, text, level, sender, targets, outcome, seen_on, seen_after_s, reply from alert_history where id < ? order by id desc limit ?").bind(b),
        None => sqlx::query("select id, at, kind, name, text, level, sender, targets, outcome, seen_on, seen_after_s, reply from alert_history order by id desc limit ?"),
    };
    let rows = match query.bind(limit + 1).fetch_all(&api.pool).await {
        Ok(r) => r,
        Err(_) => return detail(503, "The database is unavailable, so the history cannot be read."),
    };
    use sqlx::Row as _;
    let entries: Vec<Value> = rows
        .iter()
        .take(limit as usize)
        .map(|r| {
            let targets: Value = serde_json::from_str(&r.get::<String, _>("targets")).unwrap_or(json!([]));
            json!({"id": r.get::<i64, _>("id"), "at": r.get::<String, _>("at"), "kind": r.get::<String, _>("kind"), "name": r.get::<String, _>("name"),
                "text": r.get::<String, _>("text"), "level": r.get::<String, _>("level"), "sender": r.get::<String, _>("sender"),
                "targets": if targets.is_array() { targets } else { json!([]) }, "outcome": r.get::<String, _>("outcome"), "seen_on": r.get::<String, _>("seen_on"),
                "seen_after_s": r.get::<Option<i64>, _>("seen_after_s"), "reply": r.get::<String, _>("reply")})
        })
        .collect();
    Json(json!({"entries": entries, "more": rows.len() as i64 > limit, "keep_days": KEEP_DAYS})).into_response()
}

// ------------------------------------------------------------------------------------------------------------------ panels (admin)
fn firmware_boards(api: &Api, rows: &[Value]) -> Vec<Value> {
    let now = api.clock.epoch();
    firmware::active_boards()
        .map(|b| {
            let info = api.fw.current(b.id, Kind::App);
            let mine: Vec<&Value> = rows.iter().filter(|r| r["board"] == b.id && r["online"] == true).collect();
            json!({"board": b.id, "label": b.label, "firmware": info.map(|i| i.public(now)), "online": mine.len(),
                "outdated": mine.iter().filter(|r| r["update_available"] == true).count()})
        })
        .collect()
}

pub fn panels_frame(api: &Api) -> Value {
    let rows = api.panels.listing();
    let boards = firmware_boards(api, &rows);
    let default = boards.iter().find(|b| b["board"] == firmware::DEFAULT_BOARD).map(|b| b["firmware"].clone()).unwrap_or(Value::Null);
    json!({"type": "panels", "panels": rows, "limit": panels::MAX_PANELS, "offline_after_s": panels::OFFLINE_AFTER_S, "boards": boards, "firmware": default})
}

/// A board named by the admin page, or the CrowPanel when it names none. 400 for one Relay does not know or that is on hold.
fn board_arg(board: &str) -> Result<&'static str, Response> {
    let board = if board.is_empty() { firmware::DEFAULT_BOARD } else { board };
    match firmware::board(board) {
        None => Err(detail(400, "unknown board")),
        Some(b) if b.on_hold => Err(detail(400, &format!("the {} is on hold; Relay keeps no firmware for it", b.label))),
        Some(b) => Ok(b.id),
    }
}

async fn admin_panels_get(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "panels.view").await {
        return r;
    }
    Json(panels_frame(&api)).into_response()
}

async fn admin_firmware_get(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "panels.view").await {
        return r;
    }
    let q = Query::parse(req.uri().query());
    let board = match board_arg(q.get("board").unwrap_or("")) {
        Ok(b) => b,
        Err(r) => return r,
    };
    let rows = api.panels.listing();
    Json(json!({"firmware": api.fw.current(board, Kind::App).map(|i| i.public(api.clock.epoch())), "boards": firmware_boards(&api, &rows)})).into_response()
}

async fn upload(api: &Api, conn: Ext, req: Request, kind: Kind, limit: usize, action: &str, summary: &str) -> Result<(Info, crate::accounts::Caller), Response> {
    let who = guard(api, conn, req.headers(), "panels.manage").await?;
    let q = Query::parse(req.uri().query());
    let want = match q.get("board").filter(|b| !b.is_empty()) {
        Some(b) => Some(board_arg(b)?),
        None => None,
    };
    let raw = read_limited(req, limit).await?;
    let filename = q.get("filename").unwrap_or("").to_owned();
    let (fw, now) = (api.fw.clone(), api.clock.epoch());
    let done = tokio::task::spawn_blocking(move || fw.store(&raw, &filename, want, kind, now)).await.map_err(|_| detail(500, "The image could not be saved."))?;
    let info = done.map_err(|e| detail(400, &e))?;
    api.acc.record(&who, action, summary, "", None).await;
    Ok((info, who))
}

use relay_show::firmware::Info;

async fn admin_firmware_upload(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    match upload(&api, conn, req, Kind::App, firmware::SLOT_BYTES, "panels.firmware_upload", "Uploaded panel firmware").await {
        Ok((info, _)) => {
            api.panels.firmware_changed(Some(&info.board));
            tracing::info!("Panel firmware {} for {} uploaded ({} bytes)", info.version, info.board, info.size);
            Json(json!({"firmware": info.public(api.clock.epoch())})).into_response()
        }
        Err(r) => r,
    }
}

async fn admin_firmware_delete(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "panels.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let q = Query::parse(req.uri().query());
    let board = match board_arg(q.get("board").unwrap_or("")) {
        Ok(b) => b,
        Err(r) => return r,
    };
    let (fw, b) = (api.fw.clone(), board.to_owned());
    let had = tokio::task::spawn_blocking(move || fw.remove(&b, Kind::App)).await.unwrap_or(false);
    api.panels.firmware_changed(Some(board));
    api.acc.record(&who, "panels.firmware_delete", "Deleted the uploaded panel firmware", "", None).await;
    Json(json!({"ok": true, "removed": had})).into_response()
}

async fn admin_firmware_update(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "panels.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let payload = match body_value(req).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let now = payload["now"] == true;
    let out = match payload.get("id").filter(|v| !v.is_null()) {
        None => {
            let board = match board_arg(payload.get("board").and_then(Value::as_str).unwrap_or("")) {
                Ok(b) => b,
                Err(r) => return r,
            };
            if api.fw.current(board, Kind::App).is_none() {
                return detail(409, &format!("no firmware has been uploaded for the {}", firmware::board_label(board)));
            }
            Json(api.panels.request_update_all(now, Some(board))).into_response()
        }
        Some(id) => match api.panels.request_update(id.as_str().unwrap_or(""), now) {
            Ok(row) => Json(json!({"panel": row})).into_response(),
            Err(e) => return panel_error(e),
        },
    };
    api.acc.record(&who, "panels.firmware_update", "Sent a firmware update to panels", "", None).await;
    out
}

fn panel_error(e: PanelError) -> Response {
    match e {
        PanelError::Unknown => detail(404, UNKNOWN_PANEL),
        PanelError::Invalid(s) => detail(400, &s),
        PanelError::Conflict(s) => detail(409, &s),
        PanelError::Full => detail(429, "too many panels"),
    }
}

async fn admin_flash_get(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "panels.manage").await {
        return r;
    }
    let q = Query::parse(req.uri().query());
    let board = match board_arg(q.get("board").unwrap_or("")) {
        Ok(b) => b,
        Err(r) => return r,
    };
    Json(json!({"flash": api.fw.current(board, Kind::Flash).map(|i| i.public(api.clock.epoch()))})).into_response()
}

async fn admin_flash_upload(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    match upload(&api, conn, req, Kind::Flash, firmware::FLASH_BYTES, "panels.flash_upload", "Uploaded a board flash image").await {
        Ok((info, _)) => {
            tracing::info!("Panel USB image {} for {} uploaded ({} bytes)", info.version, info.board, info.size);
            Json(json!({"flash": info.public(api.clock.epoch())})).into_response()
        }
        Err(r) => r,
    }
}

async fn admin_flash_delete(State(api): State<Api>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "panels.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let q = Query::parse(req.uri().query());
    let board = match board_arg(q.get("board").unwrap_or("")) {
        Ok(b) => b.to_owned(),
        Err(r) => return r,
    };
    let fw = api.fw.clone();
    let had = tokio::task::spawn_blocking(move || fw.remove(&board, Kind::Flash)).await.unwrap_or(false);
    api.acc.record(&who, "panels.flash_delete", "Deleted the board flash image", "", None).await;
    Json(json!({"ok": true, "removed": had})).into_response()
}

async fn admin_flash_file(State(api): State<Api>, Path(name): Path<String>, conn: Ext, req: Request) -> Response {
    if let Err(r) = guard(&api, conn, req.headers(), "panels.manage").await {
        return r;
    }
    let (board, suffix) = name.rsplit_once('.').unwrap_or(("", ""));
    let usable = firmware::board(board).is_some_and(|b| !b.on_hold);
    let info = if usable { api.fw.current(board, Kind::Flash) } else { None };
    let Some(info) = info.filter(|_| suffix == "json" || suffix == "bin") else { return detail(404, "no such image") };
    if suffix == "json" {
        let mut resp = Json(info.manifest()).into_response();
        resp.headers_mut().insert(header::CACHE_CONTROL, HeaderValue::from_static("no-store"));
        return resp;
    }
    match api.fw.path(board, Kind::Flash).and_then(|p| std::fs::read(p).ok()) {
        Some(bytes) => file_response(bytes),
        None => detail(404, "no such image"),
    }
}

async fn admin_panel_update(State(api): State<Api>, Path(pid): Path<String>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "panels.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    let payload = match body_value(req).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let mut up = Update::default();
    if let Some(name) = payload.get("name") {
        match name.as_str() {
            Some(s) => up.name = Some(s.to_owned()),
            None => return detail(400, "name must be a string"),
        }
    }
    up.feeds = payload.get("feeds").cloned();
    if let Some(mode) = payload.get("service_mode") {
        match mode.as_str() {
            Some(s) => up.service_mode = Some(s.to_owned()),
            None => return detail(400, "service_mode must be a string"),
        }
    }
    if payload.get("service_type").is_some() || payload.get("feeds").is_some_and(Value::is_array) {
        // Empty while the Planning Center module is off: no panel can be set to a service then.
        let followed = followed_service_types(&api).await;
        if let Some(st) = payload.get("service_type") {
            let text = if st.is_string() { st.as_str().unwrap_or("").to_owned() } else { st.to_string() };
            if !(st.is_null() || text.is_empty()) && !followed.contains(&text) {
                return detail(400, "Relay does not follow that service type");
            }
            up.service_type = Some(st.clone());
        }
        for feed in payload.get("feeds").and_then(Value::as_array).into_iter().flatten() {
            if feed["stream"] == panels::SERVICE && !followed.contains(&match feed.get("service_type") {
                Some(Value::String(s)) => s.clone(),
                Some(Value::Null) | None => String::new(),
                Some(other) => other.to_string(),
            }) {
                return detail(400, "Relay does not follow that service type");
            }
        }
    }
    up.rotation = payload.get("rotation").cloned();
    up.lock = payload.get("lock").cloned();
    up.pin = payload.get("pin").cloned();
    up.unlock_min = payload.get("unlock_min").cloned();
    up.return_min = payload.get("return_min").cloned();
    if let Some(t) = payload.get("timers") {
        if !t.is_null() {
            let problem = panels::timer_fields(t).1;
            if !problem.is_empty() || !t.is_object() {
                return detail(400, if problem.is_empty() { "timers must be an object, or null" } else { &problem });
            }
        }
        up.timers = Some(t.clone());
    }
    match api.panels.update(&pid, &up) {
        Ok(row) => {
            api.panels.timers_changed(); // a panel reading the timers is told its new thresholds at once
            api.acc.record(&who, "panels.change", "Changed a panel", &pid, None).await;
            Json(row).into_response()
        }
        Err(e) => panel_error(e),
    }
}

/// The Planning Center service types Relay follows: asked of the app behind the server until that module is ported (#661).
async fn followed_service_types(_api: &Api) -> Vec<String> {
    Vec::new()
}

async fn admin_panel_reboot(State(api): State<Api>, Path(pid): Path<String>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "panels.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    match api.panels.reboot(&pid) {
        Ok(row) => {
            tracing::info!("Panel {} asked to restart", row["name"].as_str().unwrap_or(""));
            api.acc.record(&who, "panels.reboot", "Rebooted a panel", &pid, None).await;
            Json(json!({"panel": row})).into_response()
        }
        Err(e) => panel_error(e),
    }
}

async fn admin_panel_forget(State(api): State<Api>, Path(pid): Path<String>, conn: Ext, req: Request) -> Response {
    let who = match guard(&api, conn, req.headers(), "panels.manage").await {
        Ok(w) => w,
        Err(r) => return r,
    };
    if !api.panels.forget(&pid) {
        return detail(404, UNKNOWN_PANEL);
    }
    api.acc.record(&who, "panels.forget", "Forgot a panel", &pid, None).await;
    Json(json!({"ok": true})).into_response()
}

/// The routes, with `/stream` here (the alert feed, the rest forwarded to the app).
pub fn routes(api: Api) -> Router {
    routes_with(api, true)
}

/// The routes without `/stream` when the caption engine answers it (it hands the alert feed on to [`sender_stream`]).
pub fn routes_with(api: Api, stream: bool) -> Router {
    let router = Router::new()
        // panels, on the viewer port
        .route("/api/panels/checkin", post(panel_checkin))
        .route("/api/panels/firmware/{board}/{name}", get(panel_firmware_image))
        .route("/api/panels/{id}/events", get(panel_events))
        .route("/api/panels/{id}/update", post(panel_update_report))
        // alerts, on the viewer port
        .route("/api/alerts/messages/send", post(alert_message_send))
        .route("/api/alerts/messages/clear", post(alert_message_clear))
        .route("/api/alerts/messages/stream", get(alert_messages_stream))
        .route("/api/alerts/ack", post(alert_ack))
        .route("/api/alerts/screen", get(alert_screen_stream))
        .route("/api/alerts/{button}/send", post(alert_send))
        .route("/api/alerts/{button}/cancel", post(alert_cancel))
        // the operator's side
        .route("/api/admin/alerts", get(admin_alerts_get).post(admin_alerts_save))
        .route("/api/admin/alerts/test", post(admin_alerts_test))
        .route("/api/admin/alerts/history", get(admin_alerts_history))
        .route("/api/admin/panels", get(admin_panels_get))
        .route("/api/admin/panels/firmware", get(admin_firmware_get).post(admin_firmware_upload).delete(admin_firmware_delete))
        .route("/api/admin/panels/firmware/update", post(admin_firmware_update))
        .route("/api/admin/panels/flash", get(admin_flash_get).post(admin_flash_upload).delete(admin_flash_delete))
        .route("/api/admin/panels/flash/{name}", get(admin_flash_file))
        .route("/api/admin/panels/{id}", post(admin_panel_update).delete(admin_panel_forget))
        .route("/api/admin/panels/{id}/reboot", post(admin_panel_reboot));
    let router = if stream { router.route("/stream", get(stream_or_forward)) } else { router };
    router.with_state(api)
}

/// What a module that moves LIVE from a panel needs of the panel registry (`relay_core::caps::PanelDirectory`).
pub struct PanelsForModules(pub Arc<Registry>);

impl relay_core::caps::PanelDirectory for PanelsForModules {
    fn known(&self, id: &str) -> bool {
        panels::valid_id(&json!(id)) && self.0.known(id)
    }

    fn control_key(&self, id: &str) -> Option<String> {
        self.0.control_key(id)
    }

    fn service(&self, id: &str) -> Value {
        self.0.service(id)
    }

    fn display_name(&self, id: &str) -> String {
        self.0.display_name(id)
    }
}
