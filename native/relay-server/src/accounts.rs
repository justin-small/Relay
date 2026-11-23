//! Accounts over HTTP (#655): the sign-in, PIN, password and sign-out forms of `app/main.py`, the signed-in user's own routes
//! (`/api/admin/me*`), users, trusted devices, API tokens and the audit log. The rules are in `relay-auth`; this file is what
//! `require(...)` and the route functions of Python do: find who is asking, check the permission, answer with the fixed
//! sentence of the refusal, and write the audit entry once the change succeeded.
//!
//! The session cookie and the API token are the same ones the Python app reads (`relay_admin`, sha256 in `sessions`), so a
//! route still forwarded to Python sees the user this server signed in. Python keeps writing the audit entry for the routes it
//! still serves; this file writes it for these. First-run setup (`/admin/setup`, `/admin/import`) stays with Python until #656:
//! it stores the OpenAI key, which needs the sealed secret store.

// axum's `Response` is the error half of every helper here (the answer to send instead): that is the idiom, and each is built once.
#![allow(clippy::result_large_err)]

use std::net::IpAddr;
use std::path::PathBuf;
use std::sync::Arc;

use axum::body::Bytes;
use axum::extract::{Path, Request, State};
use axum::http::header::{HeaderMap, HeaderValue, CACHE_CONTROL, LOCATION, SET_COOKIE};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{delete, get, post, put};
use axum::{Json, Router};
use relay_auth::lockout::{self, Lockout, FAIL_DELAY};
use relay_auth::{Actor, Auth, Error, NewUser, Record, Refusal, Search, User, UserChanges};
use relay_core::Config;
use serde_json::{json, Map, Value};
use subtle::ConstantTimeEq;

use crate::forward::Conn;
use crate::pages::Pages;

pub const ADMIN_COOKIE: &str = "relay_admin";
pub const DEVICE_COOKIE: &str = "relay_device";
const DEVICE_COOKIE_MAX_AGE: i64 = 2 * 365 * 24 * 3600;
pub const HOME_PATH: &str = "/home";
const DB_DOWN: &str = "The user database is not available. Relay is still captioning; try again in a moment.";
const SIGNIN_REQUIRED: &str = "Sign in required";
const NOT_ALLOWED: &str = "Not allowed";
const BAD_REQUEST: &str = "That request is not valid.";
const MISMATCH: &str = "The two passwords do not match.";
const BAD_API_TOKEN: &str = "That API token is not valid, has expired, or its user is turned off.";

/// What the account routes share.
pub struct Accounts {
    pub auth: Auth,
    pub lockout: Lockout,
    pub pages: Pages,
    pub config_path: PathBuf,
    /// Waits this long after a failed sign-in (`lockout::FAIL_DELAY`); the tests set it to nothing.
    pub fail_delay: std::time::Duration,
}

pub type Shared = Arc<Accounts>;

/// Who asked, for a route outside this file: the user and the address the audit entry names.
pub struct Caller {
    pub user: User,
    ip: String,
}

impl Accounts {
    pub fn new(auth: Auth, pages: Pages, config_path: PathBuf) -> Shared {
        Arc::new(Self { auth, lockout: Lockout::new(), pages, config_path, fail_delay: FAIL_DELAY })
    }
}

// ---------------------------------------------------------------- the request

/// Everything the account routes read from a request, taken before the body is.
struct Who {
    headers: HeaderMap,
    ip: String,
    https: bool,
}

fn loopback(peer: IpAddr) -> bool {
    peer.is_loopback() || matches!(peer, IpAddr::V6(v6) if v6.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback()))
}

impl Who {
    fn of(headers: &HeaderMap, conn: Option<Conn>) -> Self {
        let peer = conn.map(|c| c.peer.ip());
        let trusted = peer.is_some_and(loopback);
        // The first address of X-Forwarded-For, but only from a loopback peer: a direct client could invent it to dodge its own
        // lock-out (app/main.py `_client_ip`).
        let forwarded = trusted
            .then(|| headers.get("x-forwarded-for").and_then(|v| v.to_str().ok()))
            .flatten()
            .and_then(|v| v.split(',').next())
            .map(str::trim)
            .filter(|s| !s.is_empty());
        let ip = forwarded.map(str::to_owned).or_else(|| peer.map(|p| p.to_string())).unwrap_or_else(|| "unknown".into());
        let https = conn.is_some_and(|c| c.secure)
            || (trusted && headers.get("x-forwarded-proto").and_then(|v| v.to_str().ok()).is_some_and(|v| v.trim().eq_ignore_ascii_case("https")));
        Self { headers: headers.clone(), ip, https }
    }

    fn cookie(&self, name: &str) -> String {
        self.headers
            .get_all("cookie")
            .iter()
            .filter_map(|v| v.to_str().ok())
            .flat_map(|v| v.split(';'))
            .filter_map(|p| p.trim().split_once('='))
            .find(|(k, _)| *k == name)
            .map(|(_, v)| v.to_owned())
            .unwrap_or_default()
    }

    fn user_agent(&self) -> &str {
        self.headers.get("user-agent").and_then(|v| v.to_str().ok()).unwrap_or("")
    }

    /// `Authorization: Bearer …`: the credential, `""` for any other Authorization header, `None` when there is none.
    fn bearer(&self) -> Option<String> {
        let auth = self.headers.get("authorization")?.to_str().ok()?.trim().to_owned();
        let (scheme, cred) = auth.split_once(' ').unwrap_or((auth.as_str(), ""));
        Some(if scheme.eq_ignore_ascii_case("bearer") { cred.trim().to_owned() } else { String::new() })
    }
}

fn no_store(mut resp: Response) -> Response {
    resp.headers_mut().insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp
}

pub(crate) fn detail(status: u16, sentence: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({ "detail": sentence }))).into_response()
}

/// A refusal or a database failure as the JSON answer. The sentence is always one of the fixed ones.
fn failure(e: Error) -> Response {
    match e {
        Error::Refused(r) => detail(r.status(), r.message()),
        Error::Unavailable => detail(503, DB_DOWN),
    }
}

fn set_cookie(resp: &mut Response, who: &Who, name: &str, token: &str, max_age: i64) {
    // `Secure` only when this request came over TLS: setting it on a plain-HTTP sign-in would have the browser drop the cookie.
    let secure = if who.https { "; Secure" } else { "" };
    let text = format!("{name}={token}; HttpOnly; SameSite=Strict; Max-Age={max_age}; Path=/{secure}");
    if let Ok(v) = HeaderValue::from_str(&text) {
        resp.headers_mut().append(SET_COOKIE, v);
    }
}

fn see_other(to: &str) -> Response {
    let mut resp = StatusCode::SEE_OTHER.into_response();
    if let Ok(v) = HeaderValue::from_str(to) {
        resp.headers_mut().insert(LOCATION, v);
    }
    resp
}

fn home_for(user: &User) -> &'static str {
    if user.can("admin.open") { "/admin" } else { HOME_PATH }
}

// ---------------------------------------------------------------- finding the user (`require`)

/// The signed-in user for a request, or the response to send instead. `perm` None is "just signed in".
struct Need {
    perm: Option<&'static str>,
    pending_ok: bool,
    tokens: bool,
}

const SIGNED: Need = Need { perm: None, pending_ok: false, tokens: true };

impl Accounts {
    async fn any_users(&self) -> Result<bool, Error> {
        Ok(self.auth.count_users().await? > 0)
    }

    async fn user_for(&self, who: &Who, need: &Need) -> Result<User, Response> {
        let user = match who.bearer() {
            Some(cred) => {
                let Some(user) = self.auth.token_user(&cred).await.map_err(failure)? else { return Err(detail(401, BAD_API_TOKEN)) };
                if !need.tokens {
                    return Err(detail(403, NOT_ALLOWED));
                }
                Some(user)
            }
            None => self.auth.session_user(&who.cookie(ADMIN_COOKIE)).await.map_err(failure)?,
        };
        let user = match user {
            Some(u) => Some(u),
            None => self.setup_owner(who).await.map_err(failure)?,
        };
        let Some(user) = user else { return Err(detail(401, SIGNIN_REQUIRED)) };
        if user.must_change_password && !need.pending_ok {
            return Err(detail(403, "password_change_required"));
        }
        if need.perm.is_some_and(|p| !user.can(p)) {
            return Err(detail(403, NOT_ALLOWED));
        }
        Ok(user)
    }

    /// For a route that lives outside this file (the admin status stream, #656): is the caller signed in, with `perm`? The refusal is
    /// the response to send (`require(perm)` in `app/users.py`).
    pub async fn require(&self, headers: &HeaderMap, conn: Option<Conn>, perm: &'static str) -> Result<(), Response> {
        let who = Who::of(headers, conn);
        self.user_for(&who, &Need { perm: Some(perm), pending_ok: false, tokens: true }).await.map(|_| ())
    }

    /// The signed-in user behind a request, for a route that lives outside this file (the audio routes, #657): `perm` None is "just
    /// signed in". The refusal is the response to send. Keep the [`Caller`] to write the audit entry once the change succeeded.
    pub async fn caller(&self, headers: &HeaderMap, conn: Option<Conn>, perm: Option<&'static str>) -> Result<Caller, Response> {
        let who = Who::of(headers, conn);
        let user = self.user_for(&who, &Need { perm, pending_ok: false, tokens: true }).await?;
        Ok(Caller { user, ip: who.ip })
    }

    /// Write one audit entry for `caller` (never fails the request).
    pub async fn record(&self, caller: &Caller, action: &str, summary: &str, target: &str, detail: Option<Value>) {
        self.auth.record(action, summary, Record { actor: Some(&caller.user), target, detail: detail.as_ref(), ip: &caller.ip }).await;
    }

    /// `x-admin-token` is honoured only while no account exists, and then acts as an Owner with no row (id 0).
    async fn setup_owner(&self, who: &Who) -> Result<Option<User>, Error> {
        let supplied = who.headers.get("x-admin-token").and_then(|v| v.to_str().ok()).unwrap_or("");
        if supplied.is_empty() || self.any_users().await? {
            return Ok(None);
        }
        let real = Config::load(&self.config_path)
            .ok()
            .and_then(|c| c.get("admin_token"))
            .and_then(|v| v.as_str().map(|s| s.trim().to_owned()))
            .unwrap_or_default();
        if real.is_empty() || !bool::from(supplied.as_bytes().ct_eq(real.as_bytes())) {
            return Ok(None);
        }
        Ok(Some(User {
            id: 0,
            username: "setup".into(),
            name: "Setup token".into(),
            role: "owner".into(),
            disabled: false,
            must_change_password: false,
            pin_set: false,
            token_id: 0,
        }))
    }

    async fn note(&self, who: &Who, user: &User, action: &str, summary: &str, target: &str, detail: Option<Value>) {
        self.auth.record(action, summary, Record { actor: Some(user), target, detail: detail.as_ref(), ip: &who.ip }).await;
    }

    /// Count a failed attempt and make it cost the fixed delay; the one that starts a lock-out is written to the audit log.
    async fn fail(&self, ip: &str) {
        tokio::time::sleep(self.fail_delay).await;
        if self.lockout.failed(ip) {
            let summary = format!(
                "Sign-in from this address locked for {} min after {} failed attempts",
                lockout::LOCKOUT.as_secs() / 60,
                lockout::MAX_FAILS
            );
            self.auth.record("auth.locked", &summary, Record { ip, ..Default::default() }).await;
        }
    }
}

macro_rules! need {
    ($acc:expr, $who:expr, $need:expr) => {
        match $acc.user_for(&$who, &$need).await {
            Ok(u) => u,
            Err(resp) => return resp,
        }
    };
}

// ---------------------------------------------------------------- bodies

fn body_object(body: &Bytes) -> Result<Map<String, Value>, Response> {
    match serde_json::from_slice::<Value>(body) {
        Ok(Value::Object(m)) => Ok(m),
        _ => Err(detail(422, BAD_REQUEST)),
    }
}

fn text<'a>(m: &'a Map<String, Value>, key: &str) -> &'a str {
    m.get(key).and_then(Value::as_str).unwrap_or("")
}

fn text_opt<'a>(m: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    m.get(key).and_then(Value::as_str)
}

fn form(body: &Bytes) -> std::collections::HashMap<String, String> {
    serde_urlencoded::from_bytes(body).unwrap_or_default()
}

fn field(map: &std::collections::HashMap<String, String>, key: &str, limit: usize) -> String {
    map.get(key).map(|v| v.chars().take(limit).collect()).unwrap_or_default()
}

fn title(role: &str) -> String {
    let mut c = role.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}

// ---------------------------------------------------------------- the forms

fn render(acc: &Accounts, template: &str, ctx: Value, status: u16) -> Response {
    match acc.pages.render(template, &ctx) {
        Ok(html) => no_store((StatusCode::from_u16(status).unwrap_or(StatusCode::OK), Html(html)).into_response()),
        Err(e) => {
            tracing::error!("page {template} did not render: {e}");
            detail(500, "Something went wrong inside Relay")
        }
    }
}

/// The sign-in form again, with a sentence. On a trusted device with PIN users it is the PIN picker unless `pw`.
async fn login_page(acc: &Accounts, who: &Who, error: &str, status: u16, username: &str, pw: bool, pin_user: Option<i64>) -> Response {
    let device = acc.auth.device_for(&who.cookie(DEVICE_COOKIE)).await.ok().flatten();
    let pins = if device.is_some() { acc.auth.pin_users().await.unwrap_or_default() } else { Vec::new() };
    let mode = if !pins.is_empty() && !pw { "pin" } else { "signin" };
    let pin_users: Vec<Value> = pins.iter().map(|(id, name)| json!({"id": id, "name": name})).collect();
    render(
        acc,
        "login.html",
        json!({"mode": mode, "error": error, "username": username, "pin_users": pin_users, "pin_user": pin_user,
               "device_name": device.map(|d| d.name).unwrap_or_default()}),
        status,
    )
}

fn password_page(acc: &Accounts, error: &str, status: u16, voluntary: bool) -> Response {
    render(acc, "password_change.html", json!({"error": error, "voluntary": voluntary}), status)
}

fn signed_in(who: &Who, session: &relay_auth::Session, to: &str) -> Response {
    let mut resp = see_other(to);
    set_cookie(&mut resp, who, ADMIN_COOKIE, &session.token, session.max_age);
    resp
}

/// The audit sentence for each refused sign-in, by refusal.
fn signin_refused(r: Refusal) -> &'static str {
    match r {
        Refusal::BadLogin => "Failed sign-in: wrong username or password",
        Refusal::Disabled => "Sign-in refused: the account is turned off",
        Refusal::WrongPin => "Failed sign-in: wrong PIN",
        Refusal::PinCleared => "PIN cleared after too many wrong tries",
        Refusal::NoDevice => "PIN sign-in refused: not a trusted device",
        _ => "Failed sign-in",
    }
}

/// A refused sign-in, against the account it named when there is one. What was typed as a username is never written: it is
/// sometimes a password typed in the wrong box.
async fn audit_failed(acc: &Accounts, who: &Who, r: Refusal, named: Option<&User>, way: &str, device: &str) {
    let mut d = json!({"with": way, "reason": r.key()});
    if !device.is_empty() {
        d["device"] = json!(device);
    }
    let target = named.map_or("(unknown username)", |u| u.username.as_str());
    acc.auth.record("auth.failed", signin_refused(r), Record { target, detail: Some(&d), ip: &who.ip, actor: None }).await;
}

async fn admin_login(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let body = axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default();
    let f = form(&body);
    let username = field(&f, "username", 100);
    let password = f.get("password").cloned().unwrap_or_default();
    if let Some(left) = acc.lockout.locked(&who.ip) {
        tracing::warn!("Admin login from {} refused, locked for {}s", who.ip, left.as_secs());
        return login_page(&acc, &who, &lockout::sentence(left), 429, &username, true, None).await;
    }
    match acc.auth.count_users().await {
        Ok(0) => return see_other("/admin"), // no accounts yet: the first-run form is Python's
        Ok(_) => {}
        Err(e) => return failure(e),
    }
    let user = match acc.auth.authenticate(&username, &password).await {
        Ok(u) => u,
        Err(Error::Refused(r)) => {
            let named = acc.auth.get_by_username(&username).await.ok().flatten();
            audit_failed(&acc, &who, r, named.as_ref(), "password", "").await;
            if r == Refusal::BadLogin {
                acc.fail(&who.ip).await;
            }
            return login_page(&acc, &who, r.message(), r.status(), &username, true, None).await;
        }
        Err(e) => return failure(e),
    };
    acc.auth.record("auth.password", "Signed in with their password", Record { actor: Some(&user), ip: &who.ip, ..Default::default() }).await;
    match acc.auth.start_session(&user, "password", None, who.user_agent()).await {
        Ok(s) => {
            acc.lockout.clear(&who.ip);
            signed_in(&who, &s, home_for(&user))
        }
        Err(e) => failure(e),
    }
}

async fn admin_login_pin(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let body = axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default();
    let f = form(&body);
    let raw = f.get("user_id").map(String::as_str).unwrap_or("");
    let uid: i64 = if raw.is_ascii() && !raw.is_empty() && raw.len() < 10 && raw.bytes().all(|b| b.is_ascii_digit()) { raw.parse().unwrap_or(-1) } else { -1 };
    if let Some(left) = acc.lockout.locked(&who.ip) {
        return login_page(&acc, &who, &lockout::sentence(left), 429, "", false, Some(uid)).await;
    }
    let device = match acc.auth.device_for(&who.cookie(DEVICE_COOKIE)).await {
        Ok(d) => d,
        Err(e) => return failure(e),
    };
    let pin = field(&f, "pin", 16);
    let user = match acc.auth.authenticate_pin(uid, &pin, device.as_ref().map(|d| d.id)).await {
        Ok(u) => u,
        Err(Error::Refused(r)) => {
            let named = if uid > 0 { acc.auth.get_user(uid).await.ok().flatten() } else { None };
            audit_failed(&acc, &who, r, named.as_ref(), "PIN", device.as_ref().map_or("", |d| d.name.as_str())).await;
            if matches!(r, Refusal::WrongPin | Refusal::PinCleared) {
                acc.fail(&who.ip).await;
            }
            // Without a trusted device there is no picker to go back to: the password form, with the sentence.
            return login_page(&acc, &who, r.message(), r.status(), "", r == Refusal::NoDevice, Some(uid)).await;
        }
        Err(e) => return failure(e),
    };
    let Some(device) = device else { return detail(401, Refusal::NoDevice.message()) };
    acc.auth
        .record("auth.pin", "Signed in with their PIN", Record { actor: Some(&user), detail: Some(&json!({"device": device.name})), ip: &who.ip, ..Default::default() })
        .await;
    match acc.auth.start_session(&user, "pin", Some(device.id), who.user_agent()).await {
        Ok(s) => {
            acc.lockout.clear(&who.ip);
            signed_in(&who, &s, home_for(&user))
        }
        Err(e) => failure(e),
    }
}

async fn admin_password(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let body = axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default();
    let f = form(&body);
    let again = f.get("voluntary").is_some_and(|v| v == "1");
    let user = match acc.auth.session_user(&who.cookie(ADMIN_COOKIE)).await {
        Ok(Some(u)) => u,
        Ok(None) => return see_other("/admin"),
        Err(e) => return failure(e),
    };
    if let Some(left) = acc.lockout.locked(&who.ip) {
        return password_page(&acc, &lockout::sentence(left), 429, again);
    }
    let (current, new, confirm) = (f.get("current").cloned().unwrap_or_default(), f.get("new").cloned().unwrap_or_default(), f.get("confirm").cloned().unwrap_or_default());
    if new != confirm {
        return password_page(&acc, MISMATCH, 400, again);
    }
    match acc.auth.change_own_password(&user, &current, &new, Some(&who.cookie(ADMIN_COOKIE))).await {
        Ok(()) => {
            acc.auth.record("account.password", "Changed their own password", Record { actor: Some(&user), ip: &who.ip, ..Default::default() }).await;
            see_other(home_for(&user))
        }
        Err(Error::Refused(r)) => {
            if r == Refusal::WrongPassword {
                acc.fail(&who.ip).await;
            }
            password_page(&acc, r.message(), r.status(), again)
        }
        Err(e) => failure(e),
    }
}

async fn admin_logout(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let token = who.cookie(ADMIN_COOKIE);
    if !token.is_empty() {
        // Ends it server-side, so a copied cookie is dead too. If the database is down the browser still forgets its cookie.
        if let Ok(Some(user)) = acc.auth.session_user(&token).await {
            let _ = acc.auth.end_session(&token).await;
            acc.auth.record("auth.signout", "Signed out", Record { actor: Some(&user), ip: &who.ip, ..Default::default() }).await;
        } else {
            let _ = acc.auth.end_session(&token).await;
        }
    }
    let mut resp = see_other("/admin");
    if let Ok(v) = HeaderValue::from_str(&format!("{ADMIN_COOKIE}=; Max-Age=0; Path=/; SameSite=lax; expires=Thu, 01 Jan 1970 00:00:00 GMT")) {
        resp.headers_mut().append(SET_COOKIE, v);
    }
    resp
}

// ---------------------------------------------------------------- the signed-in user's own routes

async fn me_payload(acc: &Accounts, who: &Who, user: &User) -> Result<Value, Response> {
    let device = acc.auth.device_for(&who.cookie(DEVICE_COOKIE)).await.map_err(failure)?;
    let prefs = if user.id != 0 { acc.auth.all_prefs(user.id).await.map_err(failure)? } else { Value::Object(Map::new()) };
    Ok(json!({
        "user": {"id": user.id, "username": user.username, "name": user.name, "role": user.role, "must_change_password": user.must_change_password},
        "permissions": user.permissions().into_iter().collect::<Vec<_>>(),
        "pin_set": user.pin_set,
        "device": {"trusted": device.is_some(), "name": device.map(|d| d.name)},
        "prefs": prefs,
    }))
}

async fn api_me(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { pending_ok: true, ..SIGNED });
    match me_payload(&acc, &who, &user).await {
        Ok(v) => Json(v).into_response(),
        Err(r) => r,
    }
}

async fn api_me_password(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { pending_ok: true, tokens: false, ..SIGNED });
    let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
        Ok(b) => b,
        Err(r) => return r,
    };
    let Some(new) = text_opt(&body, "new") else { return failure(Refusal::BadPassword.into()) };
    let Some(current) = text_opt(&body, "current") else { return failure(Refusal::WrongPassword.into()) };
    match acc.auth.change_own_password(&user, current, new, Some(&who.cookie(ADMIN_COOKIE))).await {
        Ok(()) => Json(json!({"ok": true})).into_response(),
        Err(e) => failure(e),
    }
}

async fn api_me_pin_set(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { tokens: false, ..SIGNED });
    let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
        Ok(b) => b,
        Err(r) => return r,
    };
    let Some(pin) = text_opt(&body, "pin") else { return failure(Refusal::BadPin.into()) };
    let Some(password) = text_opt(&body, "password") else { return failure(Refusal::WrongPassword.into()) };
    match acc.auth.set_pin(&user, password, pin).await {
        Ok(()) => {
            acc.note(&who, &user, "account.pin_set", "Set their PIN", "", None).await;
            Json(json!({"ok": true, "pin_set": true})).into_response()
        }
        Err(e) => failure(e),
    }
}

async fn api_me_pin_clear(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { tokens: false, ..SIGNED });
    match acc.auth.clear_pin(user.id).await {
        Ok(()) => {
            acc.note(&who, &user, "account.pin_clear", "Removed their PIN", "", None).await;
            Json(json!({"ok": true, "pin_set": false})).into_response()
        }
        Err(e) => failure(e),
    }
}

async fn api_me_tokens(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { perm: Some("tokens.own"), tokens: false, ..SIGNED });
    match acc.auth.list_tokens(Some(user.id)).await {
        Ok(tokens) => Json(json!({
            "tokens": tokens,
            "expiry_days": relay_auth::TOKEN_EXPIRY_DAYS,
            "default_days": relay_auth::TOKEN_DEFAULT_DAYS,
        }))
        .into_response(),
        Err(e) => failure(e),
    }
}

async fn api_me_token_create(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { perm: Some("tokens.own"), tokens: false, ..SIGNED });
    let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
        Ok(b) => b,
        Err(r) => return r,
    };
    // `days` is 30, 90, 365 or null for never; absent is the default, and anything else (a bool, a string) is refused.
    let days = match body.get("days") {
        None => Some(relay_auth::TOKEN_DEFAULT_DAYS),
        Some(Value::Null) => None,
        Some(Value::Number(n)) => match n.as_i64() {
            Some(d) => Some(d),
            None => return failure(Refusal::BadExpiry.into()),
        },
        Some(_) => return failure(Refusal::BadExpiry.into()),
    };
    match acc.auth.create_token(&user, text(&body, "name"), days).await {
        Ok((secret, row)) => {
            let expires = row.expires.clone().unwrap_or_else(|| "never".into());
            acc.note(&who, &user, "token.create", "Made an API token", &row.name, Some(json!({"expires": expires}))).await;
            no_store(Json(json!({"token": row, "secret": secret})).into_response())
        }
        Err(e) => failure(e),
    }
}

async fn api_me_token_revoke(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { tokens: false, ..SIGNED });
    let mine = match acc.auth.list_tokens(Some(user.id)).await {
        Ok(all) => all.into_iter().find(|t| t.id == id),
        Err(e) => return failure(e),
    };
    let Some(mine) = mine else { return failure(Refusal::NotFound.into()) };
    match acc.auth.revoke_token(id, &user).await {
        Ok(()) => {
            acc.note(&who, &user, "token.revoke", "Revoked one of their API tokens", &mine.name, None).await;
            Json(json!({"ok": true})).into_response()
        }
        Err(e) => failure(e),
    }
}

async fn api_tokens(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let _ = need!(acc, who, Need { perm: Some("tokens.manage"), ..SIGNED });
    match acc.auth.list_tokens(None).await {
        Ok(tokens) => Json(json!({"tokens": tokens})).into_response(),
        Err(e) => failure(e),
    }
}

async fn api_token_revoke(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { perm: Some("tokens.manage"), ..SIGNED });
    let had = match acc.auth.list_tokens(None).await {
        Ok(all) => all.into_iter().find(|t| t.id == id),
        Err(e) => return failure(e),
    };
    if let Err(e) = acc.auth.revoke_token(id, &user).await {
        return failure(e);
    }
    if let Some(had) = had {
        let summary = format!("Revoked {}'s API token", had.user_name);
        acc.note(&who, &user, "token.revoke", &summary, &had.name, Some(json!({"owner": had.username}))).await;
    }
    Json(json!({"ok": true})).into_response()
}

// ---------------------------------------------------------------- users

fn roles() -> Value {
    Value::Array(relay_auth::perms::ROLES.iter().enumerate().map(|(i, r)| json!({"id": r, "label": title(r), "rank": i + 1})).collect())
}

async fn api_users(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let _ = need!(acc, who, Need { perm: Some("users.view"), ..SIGNED });
    match acc.auth.list_users().await {
        Ok(users) => Json(json!({"users": users, "roles": roles(), "permissions": relay_auth::perms::catalogue()})).into_response(),
        Err(e) => failure(e),
    }
}

async fn api_user_create(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
    let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
        Ok(b) => b,
        Err(r) => return r,
    };
    let new = NewUser { username: text(&body, "username"), name: text(&body, "name"), role: text(&body, "role"), password: text(&body, "password"), temporary: true };
    match acc.auth.create_user(new, Some(&actor)).await {
        Ok(made) => {
            let summary = format!("Added {} as {}", made.name, title(&made.role));
            acc.note(&who, &actor, "user.create", &summary, &made.username, Some(json!({"role": made.role}))).await;
            Json(json!({"user": made})).into_response()
        }
        Err(e) => failure(e),
    }
}

async fn api_user_update(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
    let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
        Ok(b) => b,
        Err(r) => return r,
    };
    let disabled = match body.get("disabled") {
        None => None,
        Some(Value::Bool(b)) => Some(*b),
        Some(_) => return detail(400, BAD_REQUEST),
    };
    let changes = UserChanges { name: text_opt(&body, "name"), role: text_opt(&body, "role"), disabled };
    let before = acc.auth.get_user(id).await.ok().flatten();
    match acc.auth.update_user(id, changes, Some(&actor)).await {
        Ok(after) => {
            let (summary, changes) = user_change(before.as_ref(), &after);
            acc.note(&who, &actor, "user.update", &summary, &after.username, Some(json!({"changes": changes}))).await;
            Json(json!({"user": after})).into_response()
        }
        Err(e) => failure(e),
    }
}

/// What changed about a user, from and to: the role change is the one an audit is most often asked for.
fn user_change(before: Option<&User>, after: &User) -> (String, Value) {
    let mut changes = Map::new();
    let mut words: Vec<String> = Vec::new();
    if let Some(b) = before {
        if b.role != after.role {
            changes.insert("role".into(), json!([b.role, after.role]));
            words.push(format!("moved {} from {} to {}", after.name, title(&b.role), title(&after.role)));
        }
        if b.disabled != after.disabled {
            changes.insert("disabled".into(), json!([b.disabled, after.disabled]));
            words.push(format!("turned {} {}", after.name, if after.disabled { "off" } else { "on" }));
        }
        if b.name != after.name {
            changes.insert("name".into(), json!([b.name, after.name]));
            words.push(format!("renamed {} to {}", b.name, after.name));
        }
    }
    let summary = if words.is_empty() { format!("saved {} unchanged", after.name) } else { words.join("; ") };
    let mut c = summary.chars();
    let summary = c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default();
    (summary, Value::Object(changes))
}

async fn api_user_password(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
    let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
        Ok(b) => b,
        Err(r) => return r,
    };
    if let Err(e) = acc.auth.set_password(id, text(&body, "password"), true, Some(&actor)).await {
        return failure(e);
    }
    if let Ok(Some(target)) = acc.auth.get_user(id).await {
        let summary = format!("Reset {}'s password", target.name);
        acc.note(&who, &actor, "user.password_reset", &summary, &target.username, None).await;
    }
    Json(json!({"ok": true})).into_response()
}

async fn api_user_signout(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
    let target = match acc.auth.get_user(id).await {
        Ok(Some(t)) => t,
        Ok(None) => return failure(Refusal::NotFound.into()),
        Err(e) => return failure(e),
    };
    if relay_auth::perms::rank(&target.role) > relay_auth::perms::rank(&actor.role) {
        return failure(Refusal::AccountAboveYou.into());
    }
    if let Err(e) = acc.auth.end_sessions(id, None).await {
        return failure(e);
    }
    let summary = format!("Signed {} out everywhere", target.name);
    acc.note(&who, &actor, "user.signout", &summary, &target.username, None).await;
    Json(json!({"ok": true})).into_response()
}

async fn api_user_delete(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
    let target = acc.auth.get_user(id).await.ok().flatten();
    if let Err(e) = acc.auth.delete_user(id, Some(&actor)).await {
        return failure(e);
    }
    if let Some(t) = target {
        let summary = format!("Deleted {} ({})", t.name, title(&t.role));
        acc.note(&who, &actor, "user.delete", &summary, &t.username, Some(json!({"role": t.role}))).await;
    }
    Json(json!({"ok": true})).into_response()
}

// ---------------------------------------------------------------- trusted devices

async fn api_devices(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let _ = need!(acc, who, Need { perm: Some("users.view"), ..SIGNED });
    let here = match acc.auth.device_for(&who.cookie(DEVICE_COOKIE)).await {
        Ok(d) => d.map(|d| d.id),
        Err(e) => return failure(e),
    };
    match acc.auth.list_devices().await {
        Ok(devices) => Json(json!({"devices": devices, "this": here})).into_response(),
        Err(e) => failure(e),
    }
}

/// Trust THIS browser: it gets the relay_device cookie, and from then on may sign in with a PIN.
async fn api_device_trust(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
    if user.id <= 0 {
        return detail(403, NOT_ALLOWED); // the setup token has no account to record it against
    }
    let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
        Ok(b) => b,
        Err(r) => return r,
    };
    match acc.auth.trust_device(text_opt(&body, "name").unwrap_or(""), &user, who.user_agent()).await {
        Ok((token, row)) => {
            acc.note(&who, &user, "device.trust", "Trusted a browser for PIN sign-in", &row.name, None).await;
            let mut resp = Json(json!({"device": row, "this": row.id})).into_response();
            set_cookie(&mut resp, &who, DEVICE_COOKIE, &token, DEVICE_COOKIE_MAX_AGE);
            resp
        }
        Err(e) => failure(e),
    }
}

async fn api_device_revoke(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let user = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
    let had = acc.auth.list_devices().await.ok().and_then(|all| all.into_iter().find(|d| d.id == id));
    if let Err(e) = acc.auth.revoke_device(id).await {
        return failure(e);
    }
    if let Some(had) = had {
        acc.note(&who, &user, "device.revoke", "Forgot a trusted device", &had.name, None).await;
    }
    Json(json!({"ok": true})).into_response()
}

// ---------------------------------------------------------------- the audit log

#[derive(serde::Deserialize, Default)]
struct AuditQuery {
    #[serde(default)]
    who: String,
    #[serde(default)]
    kind: String,
    #[serde(default)]
    q: String,
    #[serde(default)]
    days: i64,
    #[serde(default)]
    before: i64,
}

async fn api_audit(State(acc): State<Shared>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    let who = Who::of(req.headers(), conn.map(|c| c.0));
    let _ = need!(acc, who, Need { perm: Some("audit.view"), ..SIGNED });
    let q: AuditQuery = req.uri().query().and_then(|q| serde_urlencoded::from_str(q).ok()).unwrap_or_default();
    let actor = if q.who == "system" {
        Some(Actor::System)
    } else if q.who.is_ascii() && !q.who.is_empty() && q.who.len() < 10 && q.who.bytes().all(|b| b.is_ascii_digit()) {
        q.who.parse().ok().map(Actor::Id)
    } else {
        None
    };
    let kind: String = q.kind.chars().take(64).collect();
    let text: String = q.q.trim().chars().take(100).collect();
    let search = Search {
        actor,
        kind: Some(kind.as_str()).filter(|k| !k.is_empty()),
        q: Some(text.as_str()).filter(|t| !t.is_empty()),
        days: Some(q.days).filter(|d| (1..=3650).contains(d)),
        before: Some(q.before).filter(|b| *b > 0),
        limit: None,
    };
    let found = match acc.auth.search_audit(search).await {
        Ok(f) => f,
        Err(e) => return failure(e),
    };
    let (people, kinds) = match (acc.auth.audit_people().await, acc.auth.audit_kinds().await) {
        (Ok(p), Ok(k)) => (p, k),
        (Err(e), _) | (_, Err(e)) => return failure(e),
    };
    let people: Vec<Value> = people.into_iter().map(|(id, name, username)| json!({"id": id, "name": name, "username": username})).collect();
    Json(json!({"entries": found.entries, "more": found.more, "people": people, "kinds": kinds, "keep_days": relay_auth::KEEP_DAYS})).into_response()
}

// ---------------------------------------------------------------- the routes

/// Every route this file answers. Anything else on /admin or /api/admin is forwarded to the Python app.
pub fn routes(acc: Shared) -> Router {
    Router::new()
        .route("/admin/login", post(admin_login))
        .route("/admin/login/pin", post(admin_login_pin))
        .route("/admin/password", post(admin_password))
        .route("/admin/logout", post(admin_logout))
        .route("/api/admin/me", get(api_me))
        .route("/api/admin/me/password", post(api_me_password))
        .route("/api/admin/me/pin", post(api_me_pin_set).delete(api_me_pin_clear))
        .route("/api/admin/me/tokens", get(api_me_tokens).post(api_me_token_create))
        .route("/api/admin/me/tokens/{id}", delete(api_me_token_revoke))
        .route("/api/admin/tokens", get(api_tokens))
        .route("/api/admin/tokens/{id}", delete(api_token_revoke))
        .route("/api/admin/users", get(api_users).post(api_user_create))
        .route("/api/admin/users/{id}", put(api_user_update).delete(api_user_delete))
        .route("/api/admin/users/{id}/password", post(api_user_password))
        .route("/api/admin/users/{id}/signout", post(api_user_signout))
        .route("/api/admin/trusted-devices", get(api_devices).post(api_device_trust))
        .route("/api/admin/trusted-devices/{id}", delete(api_device_revoke))
        .route("/api/admin/audit", get(api_audit))
        .with_state(acc)
}
