//! Small helpers every route here uses: JSON bodies in, `{"error": ...}` and `{"detail": ...}` out, and the Server-Sent Events headers.

use axum::body::Body;
use axum::http::{header, HeaderValue, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::Json;
use http_body_util::BodyExt;
use serde_json::{json, Value};

/// `{"error": text}`: what the admin page's own scripts read.
pub fn error(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"error": text}))).into_response()
}

/// `{"detail": text}`: what core's helpers and the feeds answer.
pub fn detail(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"detail": text}))).into_response()
}

pub async fn read(body: Body, limit: usize) -> Result<Vec<u8>, Response> {
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

/// The body as a JSON object, or the 422 FastAPI gives a body that is not one.
pub async fn object(body: Body) -> Result<Value, Response> {
    match serde_json::from_slice::<Value>(&read(body, 1 << 20).await?) {
        Ok(v @ Value::Object(_)) => Ok(v),
        _ => Err(detail(422, "That request is not valid.")),
    }
}

/// Like [`object`], but an empty body is an empty object (a POST that carries nothing).
pub async fn object_or_empty(body: Body) -> Result<Value, Response> {
    let raw = read(body, 1 << 20).await?;
    if raw.iter().all(u8::is_ascii_whitespace) {
        return Ok(json!({}));
    }
    match serde_json::from_slice::<Value>(&raw) {
        Ok(v @ Value::Object(_)) => Ok(v),
        _ => Err(detail(422, "That request is not valid.")),
    }
}

pub fn sse(body: Body) -> Response {
    let mut r = Response::new(body);
    let h = r.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache, no-transform"));
    h.insert("x-accel-buffering", HeaderValue::from_static("no"));
    r
}
