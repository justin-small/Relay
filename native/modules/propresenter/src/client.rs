//! The one place Relay talks HTTP to ProPresenter (`propresenter.py`'s `Client`, `Splitter` and the helpers that read its answers).
//!
//! Every read and every write goes through [`Client`], which is what makes view-only mode a guarantee: reads are allowed, every write is
//! refused there, whoever calls. A GET that acts (trigger, clear, start...) is a write too: [`Client::read`] refuses it and it goes through
//! [`Client::action`]. Relay only ever talks out to the address the operator typed, through the `propresenter` traffic class's network
//! port (`Host::net()`); ProPresenter's API has no authentication.
//!
//! What a live ProPresenter 22 does that its published description does not say is written up in docs/reference/propresenter-live-test.md:
//! the live stream's messages are `{"url": ..., "data": ...}` separated by a blank line (`\r\n\r\n`), not by HTTP chunks, so [`Splitter`] cuts
//! them wherever the bytes land.

use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::Request;
use hyper_util::rt::TokioIo;
use relay_core::Net;
use serde_json::{json, Map, Value};
use tokio::task::JoinHandle;

/// The traffic class every connection to ProPresenter goes through (#348).
pub const TRAFFIC: &str = "propresenter";
pub const REQUEST_TIMEOUT: Duration = Duration::from_secs(3);
pub const STALL: Duration = Duration::from_secs(10);
pub const MAX_BODY_BYTES: usize = 8 * 1024 * 1024;
pub const MAX_MESSAGE_BYTES: usize = 8 * 1024 * 1024;

/// What a ProPresenter call can go wrong as. `Invalid` is a request Relay refused to build (a GET that acts, sent as a read).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Network,
    Timeout,
    Http,
    ViewOnly,
    Bad,
    Invalid,
}

/// A call that did not work. The text is for the log; the admin page gets [`ProError::operator_message`].
#[derive(Debug, Clone)]
pub struct ProError {
    pub kind: Kind,
    pub status: u16,
    detail: String,
}

impl ProError {
    pub fn new(kind: Kind, detail: impl Into<String>) -> Self {
        Self { kind, status: 0, detail: detail.into() }
    }

    pub fn with_status(mut self, status: u16) -> Self {
        self.status = status;
        self
    }

    pub fn view_only(what: &str) -> Self {
        Self::new(Kind::ViewOnly, format!("View-only mode is on, so Relay did not {what}."))
    }

    /// For the log only: it can carry ProPresenter's own reply.
    pub fn detail(&self) -> &str {
        &self.detail
    }

    /// A fixed sentence for the admin page, chosen by the error's kind (and status), never ProPresenter's or the socket's own text.
    pub fn operator_message(&self) -> &'static str {
        match self.kind {
            Kind::ViewOnly => "View-only mode is on, so Relay did not change anything in ProPresenter.",
            Kind::Timeout => "ProPresenter did not answer in time.",
            Kind::Network => "Could not reach ProPresenter. Check the address and port, and that ProPresenter is running.",
            Kind::Bad => "That address answered, but not like ProPresenter.",
            Kind::Invalid => "That request was not valid.",
            Kind::Http => match self.status {
                404 => "ProPresenter said not found: is Network switched on in its preferences?",
                400 => "ProPresenter refused the request (400).",
                401 => "ProPresenter refused the request (401).",
                403 => "ProPresenter refused the request (403).",
                409 => "ProPresenter refused the request (409).",
                500 => "ProPresenter refused the request (500).",
                502 => "ProPresenter refused the request (502).",
                503 => "ProPresenter refused the request (503).",
                _ => "ProPresenter refused the request.",
            },
        }
    }

    /// The HTTP status the admin routes answer with.
    pub fn route_status(&self) -> u16 {
        match self.kind {
            Kind::ViewOnly => 409,
            Kind::Timeout => 504,
            Kind::Invalid => 400,
            _ => 502,
        }
    }
}

impl std::fmt::Display for ProError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for ProError {}

// ------------------------------------------------------------------------------------------------------------- what acts
/// Path segments that make a GET do something. ProPresenter triggers, clears and starts timers with GET, so "it is a GET" does not mean "it
/// only reads": `read` refuses these, and they go through `action`.
const ACTION_SEGMENTS: [&str; 16] = [
    "trigger", "clear", "start", "stop", "reset", "pause", "unpause", "focus", "next", "previous", "find_my_mouse", "go_to_time", "increment", "play", "toggle", "activate",
];

pub fn is_action_path(path: &str) -> bool {
    path.split('?').next().unwrap_or("").trim_matches('/').split('/').filter(|s| !s.is_empty()).any(|s| ACTION_SEGMENTS.contains(&s))
}

/// Never sent, view-only or not. The bulk timer calls start (or stop, or reset) EVERY timer the church has, and `capture/start` was followed by
/// a 4.5 minute stall on a live install: `^/?v1/(timers/(start|stop|reset)|capture/start)/?$`.
pub fn never_sent(path: &str) -> bool {
    let base = path.split('?').next().unwrap_or("").trim_matches('/');
    matches!(base, "v1/timers/start" | "v1/timers/stop" | "v1/timers/reset" | "v1/capture/start")
}

// ---------------------------------------------------------------------------------------------------------------- the client
type ViewOnlyFn = Arc<dyn Fn() -> bool + Send + Sync>;

#[derive(Clone)]
pub struct Client {
    pub host: String,
    pub port: u16,
    net: Net,
    view_only: ViewOnlyFn,
}

struct Reply {
    status: u16,
    content_type: Option<String>,
    body: Vec<u8>,
}

impl Client {
    /// `view_only` is read on every call, so switching it takes effect at once.
    pub fn new(net: &Net, host: &str, port: u16, view_only: ViewOnlyFn) -> Self {
        Self { host: host.to_owned(), port, net: net.clone(), view_only }
    }

    pub fn is_view_only(&self) -> bool {
        (self.view_only)()
    }

    fn authority(&self) -> String {
        format!("{}:{}", self.host, self.port)
    }

    fn unreachable(&self) -> ProError {
        ProError::new(Kind::Network, format!("Could not reach ProPresenter at {}:{}.", self.host, self.port))
    }

    fn refused(&self, e: &relay_core::RouteError) -> ProError {
        ProError::new(Kind::Network, format!("Could not reach ProPresenter at {}:{} ({e}).", self.host, self.port))
    }

    fn bare_host(&self) -> &str {
        self.host.trim_start_matches('[').trim_end_matches(']')
    }

    /// The single place a request leaves Relay.
    async fn send(&self, method: &str, path: &str, body: Option<&Value>, timeout: Duration) -> Result<Reply, ProError> {
        let work = async {
            let tcp = self.net.connect_tcp(TRAFFIC, self.bare_host(), self.port, timeout).await.map_err(|e| self.refused(&e))?;
            let url = format!("/{}", path.trim_start_matches('/'));
            let data = body.map(|b| serde_json::to_vec(b).unwrap_or_default()).unwrap_or_default();
            let mut req = Request::builder().method(method).uri(url).header("Host", self.authority()).header("Accept", "application/json").header("Connection", "close");
            if body.is_some() {
                req = req.header("Content-Type", "application/json");
            }
            let req = req.header("Content-Length", data.len()).body(Full::new(Bytes::from(data))).map_err(|_| ProError::new(Kind::Invalid, "bad request"))?;
            let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(tcp)).await.map_err(|_| self.unreachable())?;
            let conn = tokio::spawn(connection);
            let result = async {
                let response = sender.send_request(req).await.map_err(|_| self.unreachable())?;
                let status = response.status().as_u16();
                let content_type = response.headers().get("content-type").and_then(|v| v.to_str().ok()).map(str::to_owned);
                let mut incoming = response.into_body();
                let mut out = Vec::new();
                while let Some(frame) = incoming.frame().await {
                    let frame = frame.map_err(|_| self.unreachable())?;
                    if let Some(d) = frame.data_ref() {
                        out.extend_from_slice(d);
                        if out.len() > MAX_BODY_BYTES {
                            return Err(ProError::new(Kind::Bad, "ProPresenter sent a reply that is too large."));
                        }
                    }
                }
                Ok(Reply { status, content_type, body: out })
            }
            .await;
            conn.abort();
            result
        };
        let reply = match tokio::time::timeout(timeout, work).await {
            Ok(r) => r?,
            Err(_) => return Err(ProError::new(Kind::Timeout, "ProPresenter did not answer in time.")),
        };
        if reply.status == 404 {
            return Err(ProError::new(Kind::Http, "ProPresenter said not found: is Network switched on in its preferences?").with_status(404));
        }
        if reply.status >= 400 {
            let text: String = String::from_utf8_lossy(&reply.body).trim().chars().take(200).collect();
            return Err(ProError::new(Kind::Http, format!("ProPresenter refused the request ({}) {text}", reply.status).trim().to_owned()).with_status(reply.status));
        }
        Ok(reply)
    }

    async fn json(&self, method: &str, path: &str, body: Option<&Value>, timeout: Duration) -> Result<Value, ProError> {
        let reply = self.send(method, path, body, timeout).await?;
        let text = String::from_utf8_lossy(&reply.body);
        let text = text.trim();
        if text.is_empty() {
            return Ok(Value::Null);
        }
        Ok(serde_json::from_str(text).unwrap_or_else(|_| Value::String(text.to_owned())))
    }

    async fn write(&self, what: &str, method: &str, path: &str, body: Option<&Value>) -> Result<Value, ProError> {
        if self.is_view_only() {
            return Err(ProError::view_only(what));
        }
        self.json(method, path, body, REQUEST_TIMEOUT).await
    }

    // -- reads ----------------------------------------------------------------------------------------------------------------
    pub async fn read(&self, path: &str) -> Result<Value, ProError> {
        self.read_within(path, REQUEST_TIMEOUT).await
    }

    pub async fn read_within(&self, path: &str, timeout: Duration) -> Result<Value, ProError> {
        if is_action_path(path) {
            return Err(ProError::new(Kind::Invalid, format!("{path} acts on ProPresenter; use action()")));
        }
        self.json("GET", path, None, timeout).await
    }

    /// A GET whose answer is an image (a thumbnail): `(content type, bytes)`. Like `read`, it never acts.
    pub async fn read_image(&self, path: &str) -> Result<(String, Vec<u8>), ProError> {
        if is_action_path(path) {
            return Err(ProError::new(Kind::Invalid, format!("{path} acts on ProPresenter; use action()")));
        }
        let reply = self.send("GET", path, None, REQUEST_TIMEOUT).await?;
        Ok((reply.content_type.unwrap_or_else(|| "application/octet-stream".into()), reply.body))
    }

    /// `GET /version`: unversioned, answers whenever Network is on.
    pub async fn version(&self) -> Result<Value, ProError> {
        let data = self.json("GET", "/version", None, REQUEST_TIMEOUT).await?;
        if !data.is_object() {
            return Err(ProError::new(Kind::Bad, "That address answered, but not like ProPresenter."));
        }
        Ok(data)
    }

    // -- writes (all refused in view-only mode) -------------------------------------------------------------------------------
    /// A GET that acts: trigger/next, clear/layer/slide ...
    pub async fn action(&self, path: &str) -> Result<Value, ProError> {
        if never_sent(path) {
            return Err(ProError::new(Kind::Invalid, format!("{path} is never sent")));
        }
        self.write(&format!("send {path}"), "GET", path, None).await
    }

    pub async fn put(&self, path: &str, body: &Value) -> Result<Value, ProError> {
        self.write(&format!("change {path}"), "PUT", path, Some(body)).await
    }

    pub async fn post(&self, path: &str, body: Option<&Value>) -> Result<Value, ProError> {
        self.write(&format!("send {path}"), "POST", path, body).await
    }

    pub async fn delete(&self, path: &str) -> Result<Value, ProError> {
        self.write(&format!("delete {path}"), "DELETE", path, None).await
    }

    // -- the live stream (a read: it asks ProPresenter to tell, and changes nothing) ---------------------------------------------
    pub async fn open_stream(&self, paths: &[&str]) -> Result<LiveStream, ProError> {
        let tcp = self.net.connect_tcp(TRAFFIC, self.bare_host(), self.port, STALL + Duration::from_secs(5)).await.map_err(|e| self.refused(&e))?;
        let data = serde_json::to_vec(paths).unwrap_or_default();
        let req = Request::builder()
            .method("POST")
            .uri("/v1/status/updates")
            .header("Host", self.authority())
            .header("Content-Type", "application/json")
            .header("Content-Length", data.len())
            .body(Full::new(Bytes::from(data)))
            .map_err(|_| ProError::new(Kind::Invalid, "bad request"))?;
        let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(tcp)).await.map_err(|_| self.unreachable())?;
        let conn = tokio::spawn(connection);
        let response = match tokio::time::timeout(STALL + Duration::from_secs(5), sender.send_request(req)).await {
            Ok(Ok(r)) => r,
            Ok(Err(_)) => {
                conn.abort();
                return Err(self.unreachable());
            }
            Err(_) => {
                conn.abort();
                return Err(ProError::new(Kind::Timeout, "ProPresenter did not answer in time."));
            }
        };
        let status = response.status().as_u16();
        let mut body = response.into_body();
        if status != 200 {
            let mut text = Vec::new();
            while let Some(Ok(frame)) = body.frame().await {
                if let Some(d) = frame.data_ref() {
                    text.extend_from_slice(d);
                }
                if text.len() >= 300 {
                    break;
                }
            }
            conn.abort();
            let text: String = String::from_utf8_lossy(&text).chars().take(300).collect();
            return Err(ProError::new(Kind::Http, format!("ProPresenter refused the live connection ({status}) {text}")).with_status(status));
        }
        Ok(LiveStream { body, split: Splitter::default(), conn, _sender: sender })
    }
}

/// An open `POST /v1/status/updates`. Dropping it hangs up.
pub struct LiveStream {
    body: hyper::body::Incoming,
    split: Splitter,
    conn: JoinHandle<Result<(), hyper::Error>>,
    _sender: hyper::client::conn::http1::SendRequest<Full<Bytes>>,
}

impl LiveStream {
    /// The next messages: `Ok(None)` when ProPresenter closed the connection.
    pub async fn next(&mut self) -> Result<Option<Vec<Value>>, ProError> {
        loop {
            match self.body.frame().await {
                None => return Ok(None),
                Some(Err(_)) => return Err(ProError::new(Kind::Network, "The live connection to ProPresenter ended.")),
                Some(Ok(frame)) => {
                    if let Some(d) = frame.data_ref() {
                        let got = self.split.feed(d)?;
                        if !got.is_empty() {
                            return Ok(Some(got));
                        }
                    }
                }
            }
        }
    }
}

impl Drop for LiveStream {
    fn drop(&mut self) {
        self.conn.abort();
    }
}

/// Cuts the byte stream into messages at the blank line. HTTP chunk boundaries mean nothing: one message can span several chunks and one chunk
/// can hold several messages.
#[derive(Default)]
pub struct Splitter {
    buf: Vec<u8>,
}

impl Splitter {
    const SEP: &'static [u8] = b"\r\n\r\n";

    pub fn feed(&mut self, data: &[u8]) -> Result<Vec<Value>, ProError> {
        self.buf.extend_from_slice(data);
        let mut out = Vec::new();
        while let Some(at) = self.buf.windows(Self::SEP.len()).position(|w| w == Self::SEP) {
            let piece: Vec<u8> = self.buf.drain(..at + Self::SEP.len()).take(at).collect();
            if piece.iter().all(u8::is_ascii_whitespace) {
                continue;
            }
            let Ok(msg) = serde_json::from_slice::<Value>(&piece) else {
                tracing::debug!("ProPresenter sent a message that is not JSON; skipped");
                continue;
            };
            if msg.is_object() && msg["url"].is_string() {
                out.push(msg);
            }
        }
        if self.buf.len() > MAX_MESSAGE_BYTES {
            return Err(ProError::new(Kind::Bad, "ProPresenter sent a message that never ended."));
        }
        Ok(out)
    }
}

// ------------------------------------------------------------------------------------------------ reading what it answers
fn objects(v: &Value) -> Vec<&Value> {
    v.as_array().into_iter().flatten().filter(|g| g.is_object()).collect()
}

pub fn text(v: &Value, key: &str) -> String {
    match v.get(key) {
        Some(Value::String(t)) => t.clone(),
        Some(Value::Null) | None => String::new(),
        Some(other) => other.to_string(),
    }
}

/// The presentation's groups in CUE order: the arrangement's list, so a chorus that repeats appears each time it is played and cue indexes (what
/// trigger and thumbnails take) line up. `arrangement` (a UUID or a name) is the one a playlist item chose; without it, or when it is not found,
/// the presentation's current arrangement. Without a usable arrangement it is the groups as listed.
pub fn cue_groups(detail: &Value, arrangement: Option<&str>) -> Vec<Value> {
    let groups = objects(&detail["groups"]);
    let by_id = |u: &str| groups.iter().find(|g| g["uuid"].as_str() == Some(u) && !u.is_empty()).copied();
    let arrs: Vec<&Value> = objects(&detail["arrangements"]).into_iter().filter(|a| a["id"].is_object()).collect();
    let usable = |arr: &Value| -> Option<Vec<Value>> {
        let ids = arr["groups"].as_array().filter(|a| !a.is_empty())?;
        let ordered: Option<Vec<Value>> = ids.iter().map(|u| u.as_str().and_then(by_id).cloned()).collect();
        ordered
    };
    let wanted = arrangement.unwrap_or("").trim().to_owned();
    let current = detail["current_arrangement"].as_str().unwrap_or("").to_owned();
    for (key, field) in [(wanted.as_str(), "uuid"), (wanted.as_str(), "name"), (current.as_str(), "uuid")] {
        if key.is_empty() {
            continue;
        }
        for arr in &arrs {
            if arr["id"][field].as_str() == Some(key) {
                if let Some(got) = usable(arr) {
                    return got;
                }
            }
        }
    }
    groups.into_iter().cloned().collect()
}

/// Every cue of a presentation in play order: `{index, group, color (#rrggbb), text, label, notes, enabled}`. One list for the slide grid, a
/// playlist item's strip and the item details.
pub fn cue_list(detail: &Value, arrangement: Option<&str>) -> Vec<Value> {
    let mut out = Vec::new();
    for g in cue_groups(detail, arrangement) {
        for sl in objects(&g["slides"]) {
            let color = Some(hex_color(&g["color"])).filter(|c| !c.is_empty()).unwrap_or_else(|| hex_color(&sl["color"]));
            out.push(json!({
                "index": out.len(), "group": text(&g, "name").trim(), "color": color, "text": text(sl, "text"), "label": text(sl, "label"),
                "notes": text(sl, "notes"), "enabled": sl["enabled"] != Value::Bool(false),
            }));
        }
    }
    out
}

/// The notes of every slide of a presentation, as listed (not in arrangement order): what deck-wide slide-note tags are read from.
pub fn slide_notes(detail: &Value) -> Vec<String> {
    objects(&detail["groups"]).into_iter().flat_map(|g| objects(&g["slides"]).into_iter().map(|sl| text(sl, "notes")).collect::<Vec<_>>()).collect()
}

pub fn hex_color(color: &Value) -> String {
    if !color.is_object() {
        return String::new();
    }
    let channel = |k: &str| match color.get(k) {
        None | Some(Value::Null) => Some(0u8),
        Some(v) => v.as_f64().map(|f| (f * 255.0).round().clamp(0.0, 255.0) as u8),
    };
    match (channel("red"), channel("green"), channel("blue")) {
        (Some(r), Some(g), Some(b)) => format!("#{r:02x}{g:02x}{b:02x}"),
        _ => String::new(),
    }
}

/// ProPresenter's "HH:MM:SS" (negative once a timer overruns, "-00:00:02") as seconds, or `None`.
pub fn parse_timer(text: &Value) -> Option<f64> {
    let t = match text {
        Value::String(t) => t.trim().to_owned(),
        Value::Null => String::new(),
        other => other.to_string(),
    };
    let neg = t.starts_with('-');
    let body = t.trim_start_matches(['-', '+']);
    let parts: Vec<&str> = body.split(':').collect();
    if !(1..=3).contains(&parts.len()) || !parts.iter().all(|p| !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit())) {
        return None;
    }
    let secs = parts.iter().try_fold(0u64, |acc, p| p.parse::<u64>().ok().map(|n| acc * 60 + n))? as f64;
    Some(if neg { -secs } else { secs })
}

/// A JSON object of only `keys` copied from `v`.
pub fn pick(v: &Value, keys: &[&str]) -> Value {
    let mut out = Map::new();
    for k in keys {
        out.insert((*k).to_owned(), v.get(*k).cloned().unwrap_or(Value::Null));
    }
    Value::Object(out)
}
