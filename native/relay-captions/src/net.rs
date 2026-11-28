//! The real network for captions: HTTPS for the translator, a WebSocket for the transcription session. Both leave through
//! `relay_core::Net` for the `translation` traffic class, so they go out the network port the operator assigned it
//! (`app/netsock.py`, #348), and both check the server's certificate against the bundled roots (Python used certifi's).
//!
//! `http://` and `ws://` are plain, for a test's local server; nothing in Relay's settings produces one.

use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use futures_util::{SinkExt, StreamExt};
use http_body_util::{BodyExt, Full};
use hyper::{Method, Request};
use hyper_util::rt::TokioIo;
use relay_core::future::BoxFuture;
use relay_core::Net;
use serde_json::Value;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::mpsc;
use tokio_rustls::rustls::pki_types::ServerName;
use tokio_rustls::rustls::{ClientConfig, RootCertStore};
use tokio_rustls::TlsConnector;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::Message;
use url::Url;

use crate::session::{Conn, Connect, Incoming, Outgoing, WsRequest};
use crate::translate::Chat;

/// The traffic class both clients use.
pub const CLASS: &str = "translation";
pub const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
/// One chat completion, start to last byte (`TIMEOUT_S`).
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(20);
const PING_EVERY: Duration = Duration::from_secs(20);
const MAX_MESSAGE: usize = 1 << 24;
const BODY_LIMIT: usize = 8 << 20;

trait Io: AsyncRead + AsyncWrite + Unpin + Send {}
impl<T: AsyncRead + AsyncWrite + Unpin + Send> Io for T {}
type Stream = Box<dyn Io>;

fn tls() -> TlsConnector {
    let roots = RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() };
    let config = ClientConfig::builder_with_provider(Arc::new(tokio_rustls::rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .map(|b| b.with_root_certificates(roots).with_no_client_auth());
    // The ring provider supports the default versions, so this cannot fail; an empty config would refuse every connection.
    TlsConnector::from(Arc::new(config.unwrap_or_else(|_| unreachable!("ring supports the default protocol versions"))))
}

/// A connection to the URL's host, with TLS for `https` and `wss`.
async fn dial(net: &Net, url: &Url) -> Result<Stream, String> {
    let host = url.host_str().ok_or("The address has no host.")?.trim_start_matches('[').trim_end_matches(']').to_owned();
    let secure = matches!(url.scheme(), "https" | "wss");
    let port = url.port_or_known_default().ok_or("The address has no port.")?;
    let tcp = net.connect_tcp(CLASS, &host, port, CONNECT_TIMEOUT).await.map_err(|e| e.message)?;
    if !secure {
        return Ok(Box::new(tcp));
    }
    let name = ServerName::try_from(host).map_err(|_| "The address is not a valid host name.".to_owned())?;
    let stream = tokio::time::timeout(CONNECT_TIMEOUT, tls().connect(name, tcp))
        .await
        .map_err(|_| "The secure connection timed out.".to_owned())?
        .map_err(|_| "The secure connection failed.".to_owned())?;
    Ok(Box::new(stream))
}

/// Chat completions over HTTPS.
pub struct Https {
    net: Net,
}

impl Https {
    pub fn new(net: Net) -> Self {
        Self { net }
    }

    async fn post(&self, url: &str, key: &str, body: &Value) -> Result<Value, String> {
        let parsed = Url::parse(url).map_err(|_| "The address is not valid.".to_owned())?;
        let io = dial(&self.net, &parsed).await?;
        let target = match parsed.query() {
            Some(q) => format!("{}?{q}", parsed.path()),
            None => parsed.path().to_owned(),
        };
        let host = match parsed.port() {
            Some(p) => format!("{}:{p}", parsed.host_str().unwrap_or("")),
            None => parsed.host_str().unwrap_or("").to_owned(),
        };
        let request = Request::builder()
            .method(Method::POST)
            .uri(target)
            .header("Host", host)
            .header("Authorization", format!("Bearer {key}"))
            .header("Content-Type", "application/json")
            .header("Accept", "application/json")
            .header("Connection", "close")
            .body(Full::new(Bytes::from(body.to_string())))
            .map_err(|_| "The request could not be made.".to_owned())?;
        let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(io)).await.map_err(|_| "The server did not answer.".to_owned())?;
        let driver = tokio::spawn(connection);
        let result = async {
            let response = sender.send_request(request).await.map_err(|_| "The server did not answer.".to_owned())?;
            let status = response.status().as_u16();
            let mut stream = response.into_body();
            let mut data: Vec<u8> = Vec::new();
            while let Some(frame) = stream.frame().await {
                let frame = frame.map_err(|_| "The reply was cut off.".to_owned())?;
                if let Some(d) = frame.data_ref() {
                    data.extend_from_slice(d);
                    if data.len() > BODY_LIMIT {
                        return Err("The reply was too large.".to_owned());
                    }
                }
            }
            if status != 200 {
                let detail: String = String::from_utf8_lossy(&data).chars().take(300).collect();
                return Err(format!("HTTP {status}: {detail}"));
            }
            serde_json::from_slice::<Value>(&data).map_err(|_| "The reply was not JSON.".to_owned())
        }
        .await;
        driver.abort();
        result
    }
}

impl Chat for Https {
    fn complete<'a>(&'a self, url: &'a str, key: &'a str, body: &'a Value) -> BoxFuture<'a, Result<Value, String>> {
        Box::pin(async move { tokio::time::timeout(REQUEST_TIMEOUT, self.post(url, key, body)).await.unwrap_or_else(|_| Err("The request timed out.".to_owned())) })
    }
}

/// WebSockets through the routed network.
pub struct WebSockets {
    net: Net,
}

impl WebSockets {
    pub fn new(net: Net) -> Self {
        Self { net }
    }

    async fn connect(&self, req: &WsRequest) -> Result<Conn, String> {
        let parsed = Url::parse(&req.url).map_err(|_| "The address is not valid.".to_owned())?;
        let io = dial(&self.net, &parsed).await?;
        let mut request = req.url.as_str().into_client_request().map_err(|_| "The address is not valid.".to_owned())?;
        for (k, v) in &req.headers {
            let name = k.parse::<hyper::header::HeaderName>().map_err(|_| "A header name is not valid.".to_owned())?;
            let value = v.parse::<hyper::header::HeaderValue>().map_err(|_| "A header value is not valid.".to_owned())?;
            request.headers_mut().insert(name, value);
        }
        if !req.subprotocols.is_empty() {
            let joined = req.subprotocols.join(", ");
            let value = joined.parse::<hyper::header::HeaderValue>().map_err(|_| "A header value is not valid.".to_owned())?;
            request.headers_mut().insert("Sec-WebSocket-Protocol", value);
        }
        let mut config = WebSocketConfig::default();
        config.max_message_size = Some(MAX_MESSAGE);
        config.max_frame_size = Some(MAX_MESSAGE);
        let (mut ws, _) = tokio::time::timeout(CONNECT_TIMEOUT, tokio_tungstenite::client_async_with_config(request, io, Some(config)))
            .await
            .map_err(|_| "The connection timed out.".to_owned())?
            .map_err(|e| match e {
                tokio_tungstenite::tungstenite::Error::Http(resp) => format!("HTTP {}", resp.status().as_u16()),
                _ => "The connection was refused.".to_owned(),
            })?;
        let (out_tx, mut out_rx) = mpsc::channel::<Outgoing>(64);
        let (inc_tx, inc_rx) = mpsc::channel::<Incoming>(256);
        tokio::spawn(async move {
            let mut ping = tokio::time::interval(PING_EVERY);
            ping.tick().await;
            loop {
                tokio::select! {
                    out = out_rx.recv() => match out {
                        Some(Outgoing::Text(t)) => if ws.send(Message::text(t)).await.is_err() { let _ = inc_tx.send(Incoming::Closed(Some("The connection was lost.".to_owned()))).await; break; },
                        Some(Outgoing::Binary(b)) => if ws.send(Message::binary(b)).await.is_err() { let _ = inc_tx.send(Incoming::Closed(Some("The connection was lost.".to_owned()))).await; break; },
                        None => { let _ = ws.close(None).await; break; }
                    },
                    msg = ws.next() => match msg {
                        Some(Ok(Message::Text(t))) => { if inc_tx.send(Incoming::Text(t.as_str().to_owned())).await.is_err() { break; } }
                        Some(Ok(Message::Close(_))) | None => { let _ = inc_tx.send(Incoming::Closed(None)).await; break; }
                        Some(Ok(_)) => {}
                        Some(Err(e)) => { let _ = inc_tx.send(Incoming::Closed(Some(format!("ConnectionClosed: {e}")))).await; break; }
                    },
                    _ = ping.tick() => if ws.send(Message::Ping(Bytes::new())).await.is_err() { let _ = inc_tx.send(Incoming::Closed(Some("The connection was lost.".to_owned()))).await; break; },
                }
            }
        });
        Ok(Conn { out: out_tx, inc: inc_rx })
    }
}

impl Connect for WebSockets {
    fn open<'a>(&'a self, req: &'a WsRequest) -> BoxFuture<'a, Result<Conn, String>> {
        Box::pin(self.connect(req))
    }
}
