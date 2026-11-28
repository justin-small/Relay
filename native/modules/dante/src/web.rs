//! Small helpers the routes use: JSON bodies in, `{"error": ...}` out.

use axum::body::Body;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use http_body_util::BodyExt;
use serde_json::{json, Value};

/// `{"error": text}`: what the admin page's own script reads.
pub fn error(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"error": text}))).into_response()
}

/// `{"detail": text}`: what FastAPI answers a body that is not an object.
pub fn detail(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"detail": text}))).into_response()
}

/// The body as a JSON object, or the 422 FastAPI gives a body that is not one.
pub async fn object(body: Body) -> Result<Value, Response> {
    let mut out = Vec::new();
    let mut body = body;
    while let Some(frame) = body.frame().await {
        let Ok(frame) = frame else { return Err(detail(400, "Bad request")) };
        if let Some(d) = frame.data_ref() {
            out.extend_from_slice(d);
            if out.len() > 1 << 20 {
                return Err(detail(413, "request too large"));
            }
        }
    }
    match serde_json::from_slice::<Value>(&out) {
        Ok(v @ Value::Object(_)) => Ok(v),
        _ => Err(detail(422, "That request is not valid.")),
    }
}

/// The value of `name` in a raw query string, percent-decoded (`+` is a space).
pub fn query_param(query: Option<&str>, name: &str) -> String {
    for pair in query.unwrap_or("").split('&') {
        if let Some((k, v)) = pair.split_once('=') {
            if k == name {
                return decode(v);
            }
        }
    }
    String::new()
}

fn decode(s: &str) -> String {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        match b[i] {
            b'+' => out.push(b' '),
            b'%' if i + 2 < b.len() && b[i + 1].is_ascii_hexdigit() && b[i + 2].is_ascii_hexdigit() => {
                out.push(u8::from_str_radix(&s[i + 1..i + 3], 16).unwrap_or(b'%'));
                i += 2;
            }
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// The body as a JSON object, or an empty one when it is missing, not JSON or not an object (the Dante routes read a body that way).
pub async fn lenient(body: Body) -> Value {
    let mut out = Vec::new();
    let mut body = body;
    while let Some(frame) = body.frame().await {
        let Ok(frame) = frame else { return json!({}) };
        if let Some(d) = frame.data_ref() {
            out.extend_from_slice(d);
            if out.len() > 1 << 20 {
                return json!({});
            }
        }
    }
    match serde_json::from_slice::<Value>(&out) {
        Ok(v @ Value::Object(_)) => v,
        _ => json!({}),
    }
}
