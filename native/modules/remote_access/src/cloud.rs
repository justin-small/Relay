//! The calls this install makes to Relay's cloud (relay-cloud, a Cloudflare Worker at cloud.relaysystems.dev).
//!
//! Enrolling spends an enrollment code and gives this install a tunnel and a viewer address `<slug>.cloud.relaysystems.dev`.
//! The install makes its own secret, sends it once, and proves itself with it afterwards; the cloud stores only a hash. The
//! cloud holds the Cloudflare API token; this install only ever holds its own tunnel's token.
//!
//! Errors come back as a kind, and the operator sees a fixed sentence for that kind, never the exception's text. Every
//! connection opens through `Net` for the `remote_access` class, so it leaves by the port the operator assigned (#348).

use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Method, Request};
use hyper_util::rt::TokioIo;
use relay_core::Net;
use rustls::pki_types::ServerName;
use rustls::{ClientConfig, RootCertStore};
use serde_json::{json, Value};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio_rustls::TlsConnector;

/// Calls to Relay's cloud and the tunnel go out through this class's port.
pub const TRAFFIC: &str = "remote_access";
pub const DEFAULT_URL: &str = "https://cloud.relaysystems.dev";
const TIMEOUT: Duration = Duration::from_secs(30);

/// What the operator reads for each kind of failure.
pub const MESSAGES: &[(&str, &str)] = &[
    ("bad_code", "That enrollment code isn't right. Check it and try again."),
    ("code_used", "That enrollment code has already been used. Ask for a new one."),
    ("code_expired", "That enrollment code has expired. Ask for a new one."),
    ("provision_failed", "Relay's cloud couldn't set up the address just now. Try again in a minute."),
    ("server_error", "Relay's cloud couldn't finish that just now. Try again in a minute."),
    ("unauthorised", "Relay's cloud no longer knows this install: its address was released. Enroll again with a new code."),
    ("network", "Couldn't reach Relay's cloud. Check this computer's internet connection."),
    ("realtime_unavailable", "Relay's cloud has no Cloudflare Realtime app set up for remote intercom yet."),
    ("upstream_unreachable", "Relay's cloud couldn't reach Cloudflare Realtime just now. Try again in a minute."),
    ("upstream_error", "Cloudflare Realtime refused that just now. Try again in a minute."),
    ("too_many_sessions", "This install has too many open remote intercom sessions. Try again in a few minutes."),
    ("rate_limited", "Relay's cloud is busy. Try again in a minute."),
    ("not_found", "Relay's cloud no longer has that remote intercom session."),
    ("unexpected", "Relay's cloud answered in a way this version of Relay doesn't understand. Try again later."),
];

pub fn message_for(kind: &str) -> &'static str {
    MESSAGES.iter().find(|(k, _)| *k == kind).map_or(MESSAGES[MESSAGES.len() - 1].1, |(_, m)| m)
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct CloudError {
    /// One of the `MESSAGES` kinds; anything else is "unexpected".
    pub kind: &'static str,
    pub status: u16,
    /// A fixed sentence from the router when a routed port was the problem ("Chosen port is down"), for the "network" kind.
    pub detail: Option<String>,
}

impl CloudError {
    pub fn new(kind: &str, status: u16) -> Self {
        let kind = MESSAGES.iter().find(|(k, _)| *k == kind).map_or("unexpected", |(k, _)| *k);
        Self { kind, status, detail: None }
    }

    fn network(detail: Option<String>) -> Self {
        Self { kind: "network", status: 0, detail }
    }

    pub fn message(&self) -> String {
        match (&self.detail, self.kind) {
            (Some(d), "network") => d.clone(),
            _ => message_for(self.kind).to_owned(),
        }
    }
}

/// The Cloudflare-issued certificates Relay's cloud serves are checked against the bundled roots, as Python checks against
/// certifi: the frozen app has no system store it can rely on everywhere.
fn tls() -> TlsConnector {
    let roots = RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() };
    let config = ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .map(|b| b.with_root_certificates(roots).with_no_client_auth());
    // The ring provider supports the default versions, so this cannot fail; an empty config would refuse every connection.
    TlsConnector::from(Arc::new(config.unwrap_or_else(|_| unreachable!("ring supports the default protocol versions"))))
}

struct Url {
    https: bool,
    host: String,
    port: u16,
    base_path: String,
}

fn parse(base: &str) -> Option<Url> {
    let (https, rest) = match base.split_once("://")? {
        ("https", r) => (true, r),
        ("http", r) => (false, r),
        _ => return None,
    };
    let (authority, path) = rest.split_once('/').map_or((rest, ""), |(a, p)| (a, p));
    let (host, port) = match authority.rsplit_once(':') {
        Some((h, p)) => (h, p.parse().ok()?),
        None => (authority, if https { 443 } else { 80 }),
    };
    (!host.is_empty()).then(|| Url { https, host: host.to_owned(), port, base_path: path.trim_end_matches('/').to_owned() })
}

async fn exchange<S>(io: S, request: Request<Full<Bytes>>) -> Option<(u16, Bytes)>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(io)).await.ok()?;
    let driver = tokio::spawn(connection);
    let response = sender.send_request(request).await.ok()?;
    let status = response.status().as_u16();
    let body = response.into_body().collect().await.ok()?.to_bytes();
    driver.abort();
    Some((status, body))
}

/// Calls to the cloud, through the routed network.
#[derive(Clone)]
pub struct Cloud {
    net: Net,
}

impl Cloud {
    pub fn new(net: Net) -> Self {
        Self { net }
    }

    /// One call to the cloud, with the install's secret as a bearer token when it has one. Also used by `intercom_remote`'s
    /// calls to the same API (#277).
    pub async fn call(&self, base: &str, method: &str, path: &str, body: Option<&Value>, secret: &str) -> Result<Value, CloudError> {
        let url = parse(base).ok_or_else(|| CloudError::network(None))?;
        let mut builder = Request::builder()
            .method(Method::from_bytes(method.as_bytes()).map_err(|_| CloudError::new("unexpected", 0))?)
            .uri(format!("{}{}", url.base_path, path))
            .header("Host", &url.host)
            .header("Content-Type", "application/json")
            .header("User-Agent", "Relay")
            .header("Connection", "close");
        if !secret.is_empty() {
            builder = builder.header("Authorization", format!("Bearer {secret}"));
        }
        let payload = body.map(|b| b.to_string().into_bytes()).unwrap_or_default();
        let request = builder
            .header("Content-Length", payload.len())
            .body(Full::new(Bytes::from(payload)))
            .map_err(|_| CloudError::new("unexpected", 0))?;

        let work = async {
            let tcp = self.net.connect_tcp(TRAFFIC, &url.host, url.port, TIMEOUT).await.map_err(|e| CloudError::network(Some(e.message)))?;
            let answered = if url.https {
                let name = ServerName::try_from(url.host.clone()).map_err(|_| CloudError::network(None))?;
                let stream = tls().connect(name, tcp).await.map_err(|_| CloudError::network(None))?;
                exchange(stream, request).await
            } else {
                exchange(tcp, request).await
            };
            answered.ok_or_else(|| CloudError::network(None))
        };
        let (status, raw) = tokio::time::timeout(TIMEOUT, work).await.map_err(|_| CloudError::network(None))??;
        if !(200..300).contains(&status) {
            let kind = serde_json::from_slice::<Value>(&raw)
                .ok()
                .and_then(|v| v.get("error").and_then(Value::as_str).map(str::to_owned))
                .unwrap_or_default();
            return Err(CloudError::new(if kind.is_empty() { "unexpected" } else { &kind }, status));
        }
        if raw.is_empty() {
            return Ok(json!({}));
        }
        match serde_json::from_slice::<Value>(&raw) {
            Ok(v) if v.is_object() => Ok(v),
            _ => Err(CloudError::new("unexpected", status)),
        }
    }

    /// The fields an enroll or check-in answers with, checked.
    fn install(out: &Value) -> Result<Install, CloudError> {
        let text = |k: &str| out.get(k).and_then(Value::as_str).filter(|s| !s.is_empty()).map(str::to_owned);
        match (text("install_id"), text("hostname"), text("tunnel_token")) {
            (Some(install_id), Some(hostname), Some(tunnel_token)) => Ok(Install { install_id, hostname, tunnel_token }),
            _ => Err(CloudError::new("unexpected", 0)),
        }
    }

    /// Spend an enrollment code. Sending the same code and secret again answers with the same install.
    pub async fn enroll(&self, base: &str, code: &str, secret: &str, viewer_port: u16, version: &str) -> Result<Install, CloudError> {
        let body = json!({ "code": code, "secret": secret, "viewer_port": viewer_port, "relay_version": version });
        Self::install(&self.call(base, "POST", "/v1/enroll", Some(&body), "").await?)
    }

    /// Tell the cloud where the viewer port is and which version runs; get the current tunnel token back.
    pub async fn check_in(&self, base: &str, secret: &str, viewer_port: u16, version: &str) -> Result<Install, CloudError> {
        let body = json!({ "viewer_port": viewer_port, "relay_version": version });
        Self::install(&self.call(base, "PUT", "/v1/install", Some(&body), secret).await?)
    }

    /// Give the address up: the tunnel and DNS record are deleted and the slug is never reused.
    pub async fn release(&self, base: &str, secret: &str) -> Result<(), CloudError> {
        self.call(base, "DELETE", "/v1/install", None, secret).await.map(|_| ())
    }

    pub async fn health(&self, base: &str) -> Result<bool, CloudError> {
        Ok(self.call(base, "GET", "/health", None, "").await?.get("ok").and_then(Value::as_bool).unwrap_or(false))
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Install {
    pub install_id: String,
    pub hostname: String,
    pub tunnel_token: String,
}
