//! Server-sent events (`app/main.py`'s `/stream` and `/api/admin/status/stream`, ported by #656): the caption feeds, the status feed
//! and the admin status stream, with a heartbeat on every quiet stream and no buffering anywhere.
//!
//! Every stream is a task that writes finished frames into a small channel the response body reads, so a viewer that stops reading
//! never holds the hub: its subscription fills and the hub drops it (`relay_core::hub`), the task ends, and the browser reconnects
//! with `Last-Event-ID`. A task ends the moment the client goes, or the server shuts down.
//!
//! What this module serves is the hub's own streams: `transcription` and `translation`, `status`, and the admin status stream. A
//! stream a module owns (alerts, tally, lyrics, the rundown) is registered with [`Streams::provide`] by that module when it is
//! ported (#660, #661), and answered before the caption feeds as Python's `module_registry.stream` is.
//!
//! These routes are mounted by `relay-server --rust-captions` (#659), which publishes into this hub; without the switch the caption
//! engine is the app's and the front door keeps forwarding `/stream` to it. A stream nobody here owns goes to the app
//! ([`Streams::with_upstream`]), so a module that is still Python's keeps its feed.

use std::collections::HashMap;
use std::future::Future;
use std::pin::Pin;
use std::sync::{Arc, Mutex};
use std::task::{Context, Poll};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::body::Body;
use axum::extract::{Request, State};
use axum::http::{header, HeaderMap, HeaderValue};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use bytes::Bytes;
use futures_core::Stream;
use relay_config::Settings;
use relay_core::hub::{sse_frame, Frame, Hub};
use relay_core::{languages, AppError, ErrorCode, Shutdown};
use serde_json::Value;
use tokio::sync::mpsc;

use crate::forward::{Conn, Upstream};
use crate::status::{self, Show, StreamStatus};

/// A heartbeat on a quiet stream (`HEARTBEAT_S` in `app/main.py`).
pub const HEARTBEAT: Duration = Duration::from_secs(15);
/// The admin status stream sends a meter frame this often between changes.
pub const ADMIN_TICK: Duration = Duration::from_secs(1);
const PING: &[u8] = b": ping\n\n";
/// How many finished frames wait between a stream's task and the socket.
const WRITE_QUEUE: usize = 16;

pub const TRANSCRIPTION: &str = "transcription";
pub const TRANSLATION: &str = "translation";

/// What the query string of a stream request said.
pub struct Query {
    pub params: HashMap<String, String>,
}

impl Query {
    pub fn parse(raw: Option<&str>) -> Self {
        let params = serde_urlencoded::from_str::<Vec<(String, String)>>(raw.unwrap_or("")).unwrap_or_default().into_iter().collect();
        Self { params }
    }

    pub fn get(&self, key: &str) -> Option<&str> {
        self.params.get(key).map(String::as_str)
    }

    /// A flag as FastAPI reads `int = 0`: any non-zero integer is on.
    pub fn flag(&self, key: &str) -> bool {
        self.get(key).and_then(|v| v.trim().parse::<i64>().ok()).is_some_and(|n| n != 0)
    }
}

/// A stream a module owns. It answers `None` when it does not serve this request (the module is off), and the request falls through.
pub type Provider = Arc<dyn Fn(&Query, &HeaderMap) -> Option<Response> + Send + Sync>;

type Guard = Arc<dyn Fn(HeaderMap, Option<Conn>) -> Pin<Box<dyn Future<Output = Result<(), Response>> + Send>> + Send + Sync>;

pub struct Streams {
    pub hub: Arc<Hub>,
    pub show: Arc<dyn Show>,
    pub status: Arc<StreamStatus>,
    settings: Arc<Settings>,
    shutdown: Shutdown,
    providers: Mutex<HashMap<String, Provider>>,
    /// Who may open the admin status stream (`session.view`). `None` lets anyone, which only a test wants.
    guard: Option<Guard>,
    heartbeat: Duration,
    admin_tick: Duration,
    /// The app behind the server: a stream name nobody here owns goes to it (a module that is still Python's).
    upstream: Option<Arc<Upstream>>,
}

impl Streams {
    pub fn new(hub: Arc<Hub>, show: Arc<dyn Show>, settings: Arc<Settings>, shutdown: Shutdown) -> Self {
        Self {
            hub,
            show,
            status: Arc::new(StreamStatus::new()),
            settings,
            shutdown,
            providers: Mutex::new(HashMap::new()),
            guard: None,
            heartbeat: HEARTBEAT,
            admin_tick: ADMIN_TICK,
            upstream: None,
        }
    }

    /// Hand a stream nobody here owns to the app behind the server, as the front door does for every route it does not answer.
    #[must_use]
    pub fn with_upstream(mut self, upstream: Option<Arc<Upstream>>) -> Self {
        self.upstream = upstream;
        self
    }

    /// Require this check before the admin status stream opens.
    #[must_use]
    pub fn guarded<F, Fut>(mut self, check: F) -> Self
    where
        F: Fn(HeaderMap, Option<Conn>) -> Fut + Send + Sync + 'static,
        Fut: Future<Output = Result<(), Response>> + Send + 'static,
    {
        self.guard = Some(Arc::new(move |h, c| Box::pin(check(h, c))));
        self
    }

    /// Shorter beats, for a test that cannot wait fifteen seconds.
    #[must_use]
    pub fn with_timing(mut self, heartbeat: Duration, admin_tick: Duration) -> Self {
        self.heartbeat = heartbeat;
        self.admin_tick = admin_tick;
        self
    }

    /// A module's stream, served at `/stream?stream=<name>` while the module is on.
    pub fn provide(&self, name: &str, provider: Provider) {
        self.providers.lock().unwrap_or_else(|e| e.into_inner()).insert(name.to_owned(), provider);
    }

    pub fn withdraw(&self, name: &str) {
        self.providers.lock().unwrap_or_else(|e| e.into_inner()).remove(name);
    }
}

/// The body of an event stream: frames from a task, until it ends.
struct Frames(mpsc::Receiver<Frame>);

impl Stream for Frames {
    type Item = Result<Bytes, std::convert::Infallible>;

    fn poll_next(mut self: Pin<&mut Self>, cx: &mut Context<'_>) -> Poll<Option<Self::Item>> {
        self.0.poll_recv(cx).map(|f| f.map(Ok))
    }
}

/// An event-stream response whose frames come from `run`, a task given the sender. The task ends when the client goes (`closed`).
pub fn event_stream<F, Fut>(run: F) -> Response
where
    F: FnOnce(mpsc::Sender<Frame>) -> Fut,
    Fut: Future<Output = ()> + Send + 'static,
{
    let (tx, rx) = mpsc::channel(WRITE_QUEUE);
    tokio::spawn(run(tx));
    let mut resp = Response::new(Body::from_stream(Frames(rx)));
    let h = resp.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_static("text/event-stream"));
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static("no-cache, no-transform"));
    h.insert("x-accel-buffering", HeaderValue::from_static("no"));
    resp
}

fn bad(sentence: &'static str) -> Response {
    AppError::with_sentence(ErrorCode::BadRequest, sentence).into_response()
}

fn now() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// The resume point: the browser's `Last-Event-ID` on a native reconnect, or an explicit `?last_id=` the viewer sends when it
/// reconnects by hand.
fn last_id(headers: &HeaderMap, q: &Query) -> Option<u64> {
    let raw = headers.get("last-event-id").and_then(|v| v.to_str().ok()).filter(|v| !v.is_empty()).or_else(|| q.get("last_id"))?;
    raw.trim().parse().ok()
}

async fn stream(State(s): State<Arc<Streams>>, req: Request) -> Response {
    let q = Query::parse(req.uri().query());
    let name = q.get("stream").unwrap_or(TRANSLATION).to_owned();
    if name == status::STREAM {
        return status_feed(&s, q.flag("sound"));
    }
    let owned = s.providers.lock().unwrap_or_else(|e| e.into_inner()).get(&name).cloned();
    if let Some(resp) = owned.and_then(|p| p(&q, req.headers())) {
        return resp;
    }
    if name != TRANSCRIPTION && name != TRANSLATION {
        return match &s.upstream {
            Some(up) => up.forward(req).await,
            None => bad("unknown stream"),
        };
    }
    let lang = if name == TRANSCRIPTION {
        match s.settings.get() {
            Ok(cfg) => cfg["source_language"].as_str().unwrap_or("ENGLISH").to_owned(),
            Err(e) => return e.into_response(),
        }
    } else {
        let lang = q.get("lang").unwrap_or("").to_uppercase();
        if !languages::is_target(&lang) {
            return bad("unknown language");
        }
        lang
    };
    captions(&s, &name, &lang, last_id(req.headers(), &q), q.flag("finals"))
}

/// A caption feed: the resume frames first, then each frame as it is published, with a heartbeat on a quiet stream.
fn captions(s: &Arc<Streams>, name: &str, lang: &str, last: Option<u64>, finals: bool) -> Response {
    let mut sub = s.hub.subscribe(name, lang, last, finals);
    let (beat, shutdown) = (s.heartbeat, s.shutdown.clone());
    event_stream(move |tx| async move {
        loop {
            tokio::select! {
                frame = sub.recv() => match frame {
                    Some(f) => if tx.send(f).await.is_err() { break },
                    // The hub dropped this viewer for falling behind: end the stream so the browser reconnects and resyncs.
                    None => break,
                },
                () = tokio::time::sleep(beat) => if tx.send(Bytes::from_static(PING)).await.is_err() { break },
                () = tx.closed() => break,
                () = shutdown.wait() => break,
            }
        }
    })
}

/// `GET /stream?stream=status`: the whole state now, again on each change, and again every [`status::TICK`].
fn status_feed(s: &Arc<Streams>, sound: bool) -> Response {
    let s = Arc::clone(s);
    event_stream(move |tx| async move {
        let (mut last_sig, mut last_sent): (Option<String>, Option<tokio::time::Instant>) = (None, None);
        loop {
            let msg = status::payload(&*s.show, &s.hub, &s.status, now(), sound);
            let sig = status::signature(&msg);
            let due = last_sent.is_none_or(|t| t.elapsed() >= status::TICK);
            if last_sig.as_deref() != Some(sig.as_str()) || due {
                last_sig = Some(sig);
                last_sent = Some(tokio::time::Instant::now());
                if tx.send(sse_frame(&msg, None)).await.is_err() {
                    break;
                }
            }
            tokio::select! {
                () = tokio::time::sleep(status::POLL) => {}
                () = tx.closed() => break,
                () = s.shutdown.wait() => break,
            }
        }
    })
}

/// `GET /api/admin/status/stream`: the operator's feed. The full status once up front (it carries the whole blocklist), then only on
/// a real change, with a small meter frame every second so the meter stays live.
async fn admin_status(State(s): State<Arc<Streams>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
    if let Some(guard) = &s.guard {
        if let Err(refused) = guard(req.headers().clone(), conn.map(|c| c.0)).await {
            return refused;
        }
    }
    let mut sub = s.hub.subscribe_status();
    event_stream(move |tx| async move {
        let send = |v: Value| {
            let tx = tx.clone();
            async move { tx.send(sse_frame(&v, None)).await.is_ok() }
        };
        if !(send(s.show.engine_status()).await && send(s.show.panels()).await && send(s.show.alerts()).await) {
            return;
        }
        let mut online = s.show.panels_online();
        loop {
            let msg = tokio::select! {
                pushed = sub.recv() => match pushed {
                    Some(m) => (*m).clone(),
                    None => break,
                },
                () = tokio::time::sleep(s.admin_tick) => s.show.meter(),
                () = tx.closed() => break,
                () = s.shutdown.wait() => break,
            };
            if !send(msg).await {
                break;
            }
            // A panel that stopped checking in sends nothing to announce it.
            let now_online = s.show.panels_online();
            if now_online != online {
                online = now_online;
                if !send(s.show.panels()).await {
                    break;
                }
            }
        }
    })
}

pub fn routes(streams: Arc<Streams>) -> Router {
    Router::new().route("/stream", get(stream)).route("/api/admin/status/stream", get(admin_status)).with_state(streams)
}
