//! obs-websocket 5 over a routed TCP connection (`client.py`): the handshake, requests and events.
//!
//! The protocol is JSON with an `op` code. OBS sends Hello (0), with an `authentication` challenge when a password is set; Relay answers Identify (1)
//! and waits for Identified (2). After that a Request (6) carries `requestType`, `requestId` and `requestData`, its RequestResponse (7) carries the same
//! id, a `requestStatus` and `responseData`, and OBS pushes Events (5) for the subscriptions named in Identify.
//!
//! The password never leaves this computer: Identify carries `base64(sha256(base64(sha256(password + salt)) + challenge))`.

use std::collections::HashMap;
use std::fmt;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use futures_util::stream::{SplitSink, SplitStream};
use futures_util::{SinkExt, StreamExt};
use relay_core::Net;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use tokio::net::TcpStream; // allow-socket: the type of the stream Host::net() hands back, never opened here
use tokio::sync::{oneshot, Mutex as AsyncMutex, Notify};
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::Message;
use tokio_tungstenite::WebSocketStream;

use crate::settings::DEFAULT_PORT;

/// The traffic class OBS's connections are routed by.
pub const TRAFFIC: &str = "obs";
pub const TIMEOUT: Duration = Duration::from_secs(5);
pub const RPC_VERSION: u64 = 1;
/// Event subscriptions: General (1), Scenes (4), Inputs (8), Outputs (64) and Ui (1024, studio mode). The 20-a-second audio meters
/// (`InputVolumeMeters`, `1 << 16`) are added only when a computer has audio inputs to watch.
pub const EVENTS: u64 = 1 | 4 | 8 | 64 | 1024;
pub const METERS: u64 = 1 << 16;
/// A screenshot is the largest answer.
pub const MAX_MESSAGE: usize = 4 * 1024 * 1024;

/// A failure safe to show on the admin page: never built from an exception or from OBS's own text.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObsError(pub String);

impl ObsError {
    pub fn new(s: impl Into<String>) -> Self {
        Self(s.into())
    }
}

impl fmt::Display for ObsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

impl std::error::Error for ObsError {}

fn closed() -> ObsError {
    ObsError::new("The connection to OBS closed.")
}

pub fn auth_string(password: &str, salt: &str, challenge: &str) -> String {
    let secret = STANDARD.encode(Sha256::digest(format!("{password}{salt}").as_bytes()));
    STANDARD.encode(Sha256::digest(format!("{secret}{challenge}").as_bytes()))
}

/// The address a computer is reached at (`ws://host:port`), with an IPv6 literal in brackets.
pub fn url_for(host: &str, port: u16) -> Result<String, ObsError> {
    if host.is_empty() {
        return Err(ObsError::new("Enter the OBS computer's address first."));
    }
    let host = if host.contains(':') { format!("[{host}]") } else { host.to_owned() };
    Ok(format!("ws://{host}:{}", if port == 0 { DEFAULT_PORT } else { port }))
}

pub type OnEvent = Arc<dyn Fn(&str, Value) + Send + Sync>;
type Waiting = Arc<Mutex<HashMap<String, oneshot::Sender<Result<Value, ObsError>>>>>;
type Ws = WebSocketStream<TcpStream>;

/// One identified connection. `request` may be called from any task; one reader task routes answers to their callers and events to `on_event`.
pub struct Connection {
    sink: AsyncMutex<SplitSink<Ws, Message>>,
    waiting: Waiting,
    ids: AtomicU64,
    closed: Arc<AtomicBool>,
    gone: Arc<Notify>,
    reader: Mutex<Option<tokio::task::JoinHandle<()>>>,
}

async fn recv_json(ws: &mut Ws) -> Result<Value, ObsError> {
    let bad = || ObsError::new("OBS sent something that is not obs-websocket 5.");
    loop {
        let msg = tokio::time::timeout(TIMEOUT, ws.next()).await.map_err(|_| ObsError::new("OBS did not answer in time."))?;
        match msg {
            Some(Ok(Message::Text(t))) => return serde_json::from_str::<Value>(t.as_str()).ok().filter(Value::is_object).ok_or_else(bad),
            Some(Ok(Message::Binary(_))) => return Err(bad()),
            Some(Ok(Message::Ping(_) | Message::Pong(_) | Message::Frame(_))) => {}
            Some(Ok(Message::Close(_))) | Some(Err(_)) | None => return Err(closed()),
        }
    }
}

async fn identify(ws: &mut Ws, password: &str, events: u64) -> Result<(), ObsError> {
    let not_hello = || ObsError::new("OBS did not say hello. Is this the obs-websocket port (4455 by default)?");
    let hello = recv_json(ws).await.map_err(|e| if e == closed() { not_hello() } else { e })?;
    if hello["op"] != json!(0) {
        return Err(not_hello());
    }
    let mut ident = json!({"rpcVersion": RPC_VERSION, "eventSubscriptions": events});
    let auth = hello["d"]["authentication"].as_object();
    if let Some(a) = auth {
        if password.is_empty() {
            return Err(ObsError::new("OBS asks for a password. Enter the one in Tools > WebSocket Server Settings."));
        }
        ident["authentication"] = json!(auth_string(password, a.get("salt").and_then(Value::as_str).unwrap_or(""), a.get("challenge").and_then(Value::as_str).unwrap_or("")));
    }
    ws.send(Message::text(json!({"op": 1, "d": ident}).to_string())).await.map_err(|_| closed())?;
    match recv_json(ws).await {
        Ok(m) if m["op"] == json!(2) => Ok(()),
        Ok(_) => Err(ObsError::new("OBS did not accept Relay's connection.")),
        // OBS closes the connection (code 4009) on a wrong password.
        Err(e) if e == closed() && auth.is_some() => Err(ObsError::new("OBS refused the password.")),
        Err(e) if e == closed() => Err(ObsError::new("OBS closed the connection while connecting.")),
        Err(e) => Err(e),
    }
}

impl Connection {
    /// Reach OBS through the `obs` traffic class's network port and identify. `on_event` hears each event (`eventType`, `eventData`) on the reader's task.
    pub async fn open(net: &Net, host: &str, port: u16, password: &str, on_event: Option<OnEvent>, events: u64) -> Result<Arc<Self>, ObsError> {
        let url = url_for(host, port)?;
        let unreachable = || ObsError::new("Could not reach OBS. Check that OBS is open, the WebSocket server is on (Tools > WebSocket Server Settings), and the address and port.");
        let tcp = net.connect_tcp(TRAFFIC, host, if port == 0 { DEFAULT_PORT } else { port }, TIMEOUT).await.map_err(|_| unreachable())?;
        let config = WebSocketConfig::default().max_message_size(Some(MAX_MESSAGE)).max_frame_size(Some(MAX_MESSAGE));
        let (mut ws, _) = tokio::time::timeout(TIMEOUT, tokio_tungstenite::client_async_with_config(url, tcp, Some(config))).await.map_err(|_| unreachable())?.map_err(|_| unreachable())?;
        identify(&mut ws, password, events).await?;
        let (sink, stream) = ws.split();
        let waiting: Waiting = Arc::default();
        let (closed_flag, gone) = (Arc::new(AtomicBool::new(false)), Arc::new(Notify::new()));
        let reader = tokio::spawn(read(stream, Arc::clone(&waiting), on_event, Arc::clone(&closed_flag), Arc::clone(&gone)));
        Ok(Arc::new(Self { sink: AsyncMutex::new(sink), waiting, ids: AtomicU64::new(1), closed: closed_flag, gone, reader: Mutex::new(Some(reader)) }))
    }

    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::SeqCst)
    }

    /// Resolves when the connection is gone (immediately if it already is).
    pub async fn closed(&self) {
        loop {
            let note = self.gone.notified();
            tokio::pin!(note);
            note.as_mut().enable();
            if self.is_closed() {
                return;
            }
            note.await;
        }
    }

    /// Send one request and return its `responseData`, or a short reason fit for the page.
    pub async fn request(&self, kind: &str, data: Option<Value>) -> Result<Value, ObsError> {
        self.request_within(kind, data, TIMEOUT).await
    }

    pub async fn request_within(&self, kind: &str, data: Option<Value>, timeout: Duration) -> Result<Value, ObsError> {
        if self.is_closed() {
            return Err(closed());
        }
        let rid = self.ids.fetch_add(1, Ordering::SeqCst).to_string();
        let (tx, rx) = oneshot::channel();
        self.waiting.lock().unwrap_or_else(PoisonError::into_inner).insert(rid.clone(), tx);
        let mut body = json!({"requestType": kind, "requestId": rid});
        if let Some(d) = data.filter(|d| !d.as_object().is_some_and(serde_json::Map::is_empty)) {
            body["requestData"] = d;
        }
        let forget = || {
            self.waiting.lock().unwrap_or_else(PoisonError::into_inner).remove(&rid);
        };
        if self.sink.lock().await.send(Message::text(json!({"op": 6, "d": body}).to_string())).await.is_err() {
            forget();
            return Err(closed());
        }
        let answer = tokio::time::timeout(timeout, rx).await;
        forget();
        let d = match answer {
            Err(_) => return Err(ObsError::new("OBS did not answer in time.")),
            Ok(Err(_)) => return Err(closed()),
            Ok(Ok(r)) => r?,
        };
        let status = &d["requestStatus"];
        if status["result"] != Value::Bool(true) {
            return Err(ObsError::new(format!("OBS refused {kind} (code {}).", status["code"].as_i64().unwrap_or(0))));
        }
        Ok(if d["responseData"].is_object() { d["responseData"].clone() } else { json!({}) })
    }

    /// Close the connection and stop its reader.
    pub async fn close(&self) {
        let task = self.reader.lock().unwrap_or_else(PoisonError::into_inner).take();
        if let Some(t) = task {
            t.abort();
            let _ = t.await;
        }
        self.finish();
        let _ = self.sink.lock().await.close().await;
    }

    fn finish(&self) {
        finish(&self.waiting, &self.closed, &self.gone);
    }
}

fn finish(waiting: &Waiting, closed_flag: &AtomicBool, gone: &Notify) {
    closed_flag.store(true, Ordering::SeqCst);
    let pending: Vec<_> = waiting.lock().unwrap_or_else(PoisonError::into_inner).drain().collect();
    for (_, tx) in pending {
        let _ = tx.send(Err(closed()));
    }
    gone.notify_waiters();
}

async fn read(mut stream: SplitStream<Ws>, waiting: Waiting, on_event: Option<OnEvent>, closed_flag: Arc<AtomicBool>, gone: Arc<Notify>) {
    while let Some(Ok(msg)) = stream.next().await {
        let Message::Text(t) = msg else {
            if matches!(msg, Message::Close(_)) {
                break;
            }
            continue;
        };
        let Ok(msg) = serde_json::from_str::<Value>(t.as_str()) else { continue };
        let d = msg["d"].clone();
        match msg["op"].as_i64() {
            Some(7) => {
                let tx = waiting.lock().unwrap_or_else(PoisonError::into_inner).remove(d["requestId"].as_str().unwrap_or(""));
                if let Some(tx) = tx {
                    let _ = tx.send(Ok(d));
                }
            }
            Some(5) => {
                if let Some(f) = &on_event {
                    let data = if d["eventData"].is_null() { json!({}) } else { d["eventData"].clone() };
                    f(d["eventType"].as_str().unwrap_or(""), data);
                }
            }
            _ => {}
        }
    }
    finish(&waiting, &closed_flag, &gone);
}
