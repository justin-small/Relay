//! The strangler (#650): what the server does not serve itself goes to the running FastAPI app, request by request. Response
//! bodies are handed on frame by frame, never collected, so server-sent events keep their rhythm; a websocket upgrade becomes a
//! byte pipe once both sides have switched protocols; an app that is down is a 502 and the listener keeps serving.
//!
//! The app sits on a loopback port over plain HTTP and trusts what this side tells it, because the peer is loopback:
//!
//! * `X-Forwarded-For` / `X-Forwarded-Proto`: who the client is and whether it came over TLS (the app refuses first-run setup
//!   without the client's address). A header the client sent is replaced, never appended to.
//! * `X-Relay-Port`: the port the client reached, so the app's own port rules (`app/serving.py`) see 80, 443 or a module's
//!   port and not the loopback port it is really listening on.
//! * `X-Relay-Front`: dropped from the client's request; only this side's own calls (`front.rs`) carry the token.

use std::net::SocketAddr;
use std::sync::Arc;

use axum::body::Body;
use axum::extract::Request;
use axum::http::header::{HeaderName, HeaderValue, CONNECTION, UPGRADE};
use axum::http::{StatusCode, Uri};
use axum::response::{IntoResponse, Response};
use axum::Json;
use hyper_util::client::legacy::{connect::HttpConnector, Client};
use hyper_util::rt::{TokioExecutor, TokioIo};
use serde_json::json;

/// Set on every request by the listener: who connected, to which port, and whether over TLS.
#[derive(Clone, Copy, Debug)]
pub struct Conn {
    pub peer: SocketAddr,
    pub local_port: u16,
    pub secure: bool,
}

/// The app behind the server.
pub struct Upstream {
    base: String,
    client: Client<HttpConnector, Body>,
}

fn header(name: &'static str) -> HeaderName {
    HeaderName::from_static(name)
}

impl Upstream {
    /// `base` is `http://127.0.0.1:18080`; a trailing slash is dropped.
    pub fn new(base: &str) -> Arc<Self> {
        let mut http = HttpConnector::new();
        http.set_nodelay(true);
        let client = Client::builder(TokioExecutor::new()).pool_max_idle_per_host(512).build(http);
        Arc::new(Self { base: base.trim_end_matches('/').to_owned(), client })
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    pub async fn forward(&self, mut req: Request) -> Response {
        let Some(conn) = req.extensions().get::<Conn>().copied() else {
            return failure(StatusCode::INTERNAL_SERVER_ERROR, "No connection details");
        };
        let target = req.uri().path_and_query().map(|p| p.as_str()).unwrap_or("/");
        let uri: Uri = match format!("{}{target}", self.base).parse() {
            Ok(u) => u,
            Err(_) => return failure(StatusCode::BAD_REQUEST, "Bad request target"),
        };
        *req.uri_mut() = uri;
        let h = req.headers_mut();
        h.remove(header("x-relay-front"));
        h.insert(header("x-forwarded-for"), value(&conn.peer.ip().to_string()));
        h.insert(header("x-forwarded-proto"), HeaderValue::from_static(if conn.secure { "https" } else { "http" }));
        h.insert(header("x-relay-port"), value(&conn.local_port.to_string()));

        let upgrading = req.headers().get(UPGRADE).is_some()
            && req
                .headers()
                .get(CONNECTION)
                .and_then(|v| v.to_str().ok())
                .is_some_and(|v| v.to_ascii_lowercase().contains("upgrade"));
        let client_side = upgrading.then(|| hyper::upgrade::on(&mut req));
        let mut resp = match self.client.request(req).await {
            Ok(r) => r,
            Err(e) => {
                tracing::debug!(error = %e, "the app did not answer");
                return failure(StatusCode::BAD_GATEWAY, "Relay's app did not answer");
            }
        };
        if resp.status() == StatusCode::SWITCHING_PROTOCOLS {
            if let Some(client_side) = client_side {
                let upstream_side = hyper::upgrade::on(&mut resp);
                tokio::spawn(async move {
                    if let (Ok(c), Ok(u)) = (client_side.await, upstream_side.await) {
                        let (mut c, mut u) = (TokioIo::new(c), TokioIo::new(u));
                        let _ = tokio::io::copy_bidirectional(&mut c, &mut u).await;
                    }
                });
            }
        }
        resp.map(Body::new)
    }
}

fn value(text: &str) -> HeaderValue {
    HeaderValue::from_str(text).unwrap_or_else(|_| HeaderValue::from_static(""))
}

/// A fixed sentence, never the transport's own text.
fn failure(status: StatusCode, detail: &'static str) -> Response {
    (status, Json(json!({"detail": detail}))).into_response()
}

/// What answers a path nothing serves when there is no app behind the server.
pub fn nothing_here() -> Response {
    (StatusCode::NOT_FOUND, Json(json!({"detail": "Not found"}))).into_response()
}
