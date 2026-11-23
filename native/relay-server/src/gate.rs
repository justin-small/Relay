//! The port rules (`_Gate` in `app/main.py`): what each listener answers, and what every response carries.
//!
//! * A module's own port (a `Listener`, #205) answers only that module's routes under its prefixes, and those routes answer on
//!   no other port, so a device pointed at it (a TV) reaches nothing else.
//! * The viewer port never reaches the panel: `/admin`, `/api/admin`, `/home` and every module's panel prefix answer 404 there,
//!   so the link handed to a room cannot reach the login form.
//! * Every response gets the Permissions-Policy (unless the route set its own), and a response on the HTTPS panel socket gets
//!   the panel's security headers and HSTS.
//!
//! The rules read the path the way the app does: percent-decoded, so `/%61dmin` is `/admin`.

use std::sync::{Arc, RwLock};

use axum::extract::{Request, State};
use axum::http::header::{HeaderName, HeaderValue};
use axum::http::StatusCode;
use axum::middleware::Next;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

use crate::tls::HSTS_MAX_AGE;

/// Everything under these prefixes is refused on the viewer port.
pub const ADMIN_PREFIXES: [&str; 3] = ["/admin", "/api/admin", "/home"];
/// The path of the board flasher, the one page that may use Web Serial.
pub const FLASH_PATH: &str = "/admin/flash";
/// Relay's own talk between the Rust server and the app behind it (`/api/internal/front`): never answered to a client.
pub const INTERNAL_PREFIX: &str = "/api/internal";

pub const PERMISSIONS_POLICY: &str = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()";
pub const FLASH_PERMISSIONS_POLICY: &str = "camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=(self)";

/// Which kind of socket a request arrived on.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Kind {
    /// Plain HTTP for viewers.
    Viewer,
    /// HTTPS for the operator panel.
    Panel,
    /// A module's own plain-HTTP port, answering only these prefixes.
    Module(Arc<Vec<String>>),
}

/// The prefixes the modules reserve, whether the module is on or not.
#[derive(Clone, Debug, Default)]
pub struct Prefixes {
    /// Served only on a listener of the module's own.
    pub listener: Vec<String>,
    /// Served only on the HTTPS panel.
    pub panel: Vec<String>,
}

#[derive(Clone)]
pub struct GateState {
    pub kind: Kind,
    pub prefixes: Arc<RwLock<Prefixes>>,
}

/// `path` is `prefix` or below it.
pub fn under(path: &str, prefixes: &[String]) -> bool {
    prefixes.iter().any(|p| path == p || path.starts_with(&format!("{}/", p.trim_end_matches('/'))))
}

pub fn is_admin_path(path: &str) -> bool {
    ADMIN_PREFIXES.iter().any(|p| path == *p || path.starts_with(&format!("{p}/")))
}

/// The request path as the app sees it: percent-decoded (a malformed escape is left as it is).
pub fn decoded_path(raw: &str) -> String {
    let bytes = raw.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).ok().and_then(|h| u8::from_str_radix(h, 16).ok());
            if let Some(b) = hex {
                out.push(b);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

pub fn permissions_policy_for(path: &str) -> &'static str {
    if path == FLASH_PATH {
        FLASH_PERMISSIONS_POLICY
    } else {
        PERMISSIONS_POLICY
    }
}

/// True when a listener of this kind refuses `path`.
pub fn refuses(kind: &Kind, prefixes: &Prefixes, path: &str) -> bool {
    if under(path, &[INTERNAL_PREFIX.to_owned()]) {
        return true;
    }
    match kind {
        Kind::Module(own) => !under(path, own),
        Kind::Viewer => under(path, &prefixes.listener) || is_admin_path(path) || under(path, &prefixes.panel),
        Kind::Panel => under(path, &prefixes.listener),
    }
}

fn not_found() -> Response {
    (StatusCode::NOT_FOUND, Json(json!({"error": "Not found"}))).into_response()
}

fn set(resp: &mut Response, name: &'static str, value: &str) {
    if let Ok(v) = HeaderValue::from_str(value) {
        resp.headers_mut().insert(HeaderName::from_static(name), v);
    }
}

pub async fn gate(State(state): State<GateState>, req: Request, next: Next) -> Response {
    let path = decoded_path(req.uri().path());
    let refused = {
        let prefixes = state.prefixes.read().map(|p| p.clone()).unwrap_or_default();
        refuses(&state.kind, &prefixes, &path)
    };
    if refused {
        return not_found();
    }
    let mut resp = next.run(req).await;
    if state.kind == Kind::Panel {
        set(&mut resp, "x-content-type-options", "nosniff");
        // SAMEORIGIN, not DENY: the overlay builder previews the real /overlay page in a frame on the panel itself.
        set(&mut resp, "x-frame-options", "SAMEORIGIN");
        set(&mut resp, "referrer-policy", "no-referrer");
        set(&mut resp, "strict-transport-security", &format!("max-age={HSTS_MAX_AGE}"));
    }
    if !resp.headers().contains_key("permissions-policy") {
        set(&mut resp, "permissions-policy", permissions_policy_for(&path));
    }
    resp
}
