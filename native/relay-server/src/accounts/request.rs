//! What the account routes read from a request and how they answer: who is asking, the credential and cookie helpers, the
//! fixed JSON and redirect responses, and the small body parsers.

use std::net::IpAddr;

use axum::body::Bytes;
use axum::http::header::{HeaderMap, HeaderValue, CACHE_CONTROL, LOCATION, SET_COOKIE};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use relay_auth::{Error, User};
use serde_json::{json, Map, Value};

use super::{BAD_REQUEST, DB_DOWN, HOME_PATH};
use crate::forward::Conn;

// ---------------------------------------------------------------- the request

/// Everything the account routes read from a request, taken before the body is.
pub(super) struct Who {
    pub(super) headers: HeaderMap,
    pub(super) ip: String,
    pub(super) https: bool,
}

pub(super) fn loopback(peer: IpAddr) -> bool {
    peer.is_loopback() || matches!(peer, IpAddr::V6(v6) if v6.to_ipv4_mapped().is_some_and(|v4| v4.is_loopback()))
}

impl Who {
    pub(super) fn of(headers: &HeaderMap, conn: Option<Conn>) -> Self {
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

    pub(super) fn cookie(&self, name: &str) -> String {
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

    pub(super) fn user_agent(&self) -> &str {
        self.headers.get("user-agent").and_then(|v| v.to_str().ok()).unwrap_or("")
    }

    /// `Authorization: Bearer …`: the credential, `""` for any other Authorization header, `None` when there is none.
    pub(super) fn bearer(&self) -> Option<String> {
        let auth = self.headers.get("authorization")?.to_str().ok()?.trim().to_owned();
        let (scheme, cred) = auth.split_once(' ').unwrap_or((auth.as_str(), ""));
        Some(if scheme.eq_ignore_ascii_case("bearer") { cred.trim().to_owned() } else { String::new() })
    }
}

pub(super) fn no_store(mut resp: Response) -> Response {
    resp.headers_mut().insert(CACHE_CONTROL, HeaderValue::from_static("no-store"));
    resp
}

pub(crate) fn detail(status: u16, sentence: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({ "detail": sentence }))).into_response()
}

/// A refusal or a database failure as the JSON answer. The sentence is always one of the fixed ones.
pub(super) fn failure(e: Error) -> Response {
    match e {
        Error::Refused(r) => detail(r.status(), r.message()),
        Error::Unavailable => detail(503, DB_DOWN),
    }
}

pub(super) fn set_cookie(resp: &mut Response, who: &Who, name: &str, token: &str, max_age: i64) {
    // `Secure` only when this request came over TLS: setting it on a plain-HTTP sign-in would have the browser drop the cookie.
    let secure = if who.https { "; Secure" } else { "" };
    let text = format!("{name}={token}; HttpOnly; SameSite=Strict; Max-Age={max_age}; Path=/{secure}");
    if let Ok(v) = HeaderValue::from_str(&text) {
        resp.headers_mut().append(SET_COOKIE, v);
    }
}

pub(super) fn see_other(to: &str) -> Response {
    let mut resp = StatusCode::SEE_OTHER.into_response();
    if let Ok(v) = HeaderValue::from_str(to) {
        resp.headers_mut().insert(LOCATION, v);
    }
    resp
}

pub(super) fn home_for(user: &User) -> &'static str {
    if user.can("admin.open") { "/admin" } else { HOME_PATH }
}
// ---------------------------------------------------------------- finding the user (`require`)

/// The signed-in user for a request, or the response to send instead. `perm` None is "just signed in".
pub(super) struct Need {
    pub(super) perm: Option<&'static str>,
    pub(super) pending_ok: bool,
    pub(super) tokens: bool,
}

pub(super) const SIGNED: Need = Need { perm: None, pending_ok: false, tokens: true };
// ---------------------------------------------------------------- bodies

pub(super) fn body_object(body: &Bytes) -> Result<Map<String, Value>, Response> {
    match serde_json::from_slice::<Value>(body) {
        Ok(Value::Object(m)) => Ok(m),
        _ => Err(detail(422, BAD_REQUEST)),
    }
}

pub(super) fn text<'a>(m: &'a Map<String, Value>, key: &str) -> &'a str {
    m.get(key).and_then(Value::as_str).unwrap_or("")
}

pub(super) fn text_opt<'a>(m: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    m.get(key).and_then(Value::as_str)
}

pub(super) fn form(body: &Bytes) -> std::collections::HashMap<String, String> {
    serde_urlencoded::from_bytes(body).unwrap_or_default()
}

pub(super) fn field(map: &std::collections::HashMap<String, String>, key: &str, limit: usize) -> String {
    map.get(key).map(|v| v.chars().take(limit).collect()).unwrap_or_default()
}

pub(super) fn title(role: &str) -> String {
    let mut c = role.chars();
    c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
}
