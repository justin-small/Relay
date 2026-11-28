//! One live transcription session: connect, say hello, pump the room's audio up, read events down, and reconnect with backoff
//! (`app/realtime.py` `RealtimeSession`).
//!
//! The socket is behind [`Connect`] and talks in channels, so a test is a scripted server and the real thing ([`crate::net`]) is a
//! TLS WebSocket through the operator's network port. What the events mean, and when a line is committed, is [`Transcript`].
//!
//! A fault the API names as unfixable by retrying (a rejected key, no quota, a retired model) stops the session in `error` with the
//! API's own words, instead of hammering it with a credential it has refused.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use base64::Engine as _;
use relay_core::future::BoxFuture;
use serde_json::{json, Value};
use tokio::sync::{mpsc, watch, Notify};
use tokio::task::JoinHandle;
use tokio::time::Instant;

use crate::clock;
use crate::transcribe::{Audio, SessionSpec, MAX_AUDIO_FRAME_BYTES};
use crate::transcript::{is_fatal, Emit, Transcript};

pub const MAX_BACKOFF: f64 = 15.0;
/// 24 kHz, 16-bit, mono: what every session is fed (the capture resamples to it).
pub const PCM_BYTES_PER_S: f64 = 24000.0 * 2.0;
/// How often the segmenter looks at the open line.
const SEGMENT_TICK: Duration = Duration::from_millis(200);

/// Where to connect.
#[derive(Clone, Debug)]
pub struct WsRequest {
    pub url: String,
    pub subprotocols: Vec<String>,
    pub headers: Vec<(String, String)>,
}

/// A frame going up.
#[derive(Debug, PartialEq)]
pub enum Outgoing {
    Text(String),
    Binary(Vec<u8>),
}

/// Something coming down. `Closed(Some(why))` is a socket that failed; `Closed(None)` is one that ended cleanly.
#[derive(Debug, PartialEq)]
pub enum Incoming {
    Text(String),
    Closed(Option<String>),
}

/// An open socket as two channels. Dropping `out` closes it.
pub struct Conn {
    pub out: mpsc::Sender<Outgoing>,
    pub inc: mpsc::Receiver<Incoming>,
}

/// Opens a WebSocket. The error is a fixed sentence, or `HTTP <status>` when the server refused the handshake.
pub trait Connect: Send + Sync + 'static {
    fn open<'a>(&'a self, req: &'a WsRequest) -> BoxFuture<'a, Result<Conn, String>>;
}

struct Queue {
    frames: Mutex<VecDeque<Vec<u8>>>,
    wake: Notify,
    closed: AtomicBool,
    capacity: usize,
    dropped: AtomicU64,
}

/// The sending end of an [`AudioFeed`]. A full queue loses its oldest frame to make room, and counts it: a stalled session must not
/// back-pressure the capture, and the newest audio is the audio worth sending.
pub struct AudioSender(Arc<Queue>);

impl AudioSender {
    pub fn push(&self, pcm: Vec<u8>) {
        {
            let mut frames = lock(&self.0.frames);
            if frames.len() >= self.0.capacity {
                frames.pop_front();
                self.0.dropped.fetch_add(1, Ordering::Relaxed);
            }
            frames.push_back(pcm);
        }
        self.0.wake.notify_one();
    }

    /// The reading end has gone.
    pub fn is_closed(&self) -> bool {
        Arc::strong_count(&self.0) == 1
    }
}

impl Drop for AudioSender {
    fn drop(&mut self) {
        self.0.closed.store(true, Ordering::Release);
        self.0.wake.notify_one();
    }
}

/// The room's audio: 24 kHz PCM frames from the capture.
pub struct AudioFeed(Arc<Queue>);

impl AudioFeed {
    /// A queue of `capacity` frames.
    pub fn channel(capacity: usize) -> (AudioSender, AudioFeed) {
        let q = Arc::new(Queue { frames: Mutex::new(VecDeque::new()), wake: Notify::new(), closed: AtomicBool::new(false), capacity: capacity.max(1), dropped: AtomicU64::new(0) });
        (AudioSender(Arc::clone(&q)), AudioFeed(q))
    }

    /// The next frame, or `None` once the sender is gone and the queue is empty.
    pub async fn recv(&mut self) -> Option<Vec<u8>> {
        loop {
            if let Some(f) = lock(&self.0.frames).pop_front() {
                return Some(f);
            }
            if self.0.closed.load(Ordering::Acquire) {
                return lock(&self.0.frames).pop_front();
            }
            self.0.wake.notified().await;
        }
    }

    /// Frames lost to a stalled uplink.
    pub fn dropped(&self) -> u64 {
        self.0.dropped.load(Ordering::Relaxed)
    }

    fn counter(&self) -> Arc<Queue> {
        Arc::clone(&self.0)
    }
}

pub type OnText = Arc<dyn Fn(&str) + Send + Sync>;
pub type OnStatus = Arc<dyn Fn() + Send + Sync>;

struct State {
    state: &'static str,
    error: Option<String>,
    fatal: bool,
    last_delta_ts: Option<f64>,
    connected_at: Option<f64>,
    reconnects: u64,
    audio_s: f64,
}

struct Shared {
    name: String,
    spec: SessionSpec,
    connector: Arc<dyn Connect>,
    on_delta: OnText,
    on_final: OnText,
    on_status: OnStatus,
    state: Mutex<State>,
    dropped: Mutex<Option<Arc<Queue>>>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

pub struct Session {
    shared: Arc<Shared>,
    stop: watch::Sender<bool>,
    task: Mutex<Option<JoinHandle<()>>>,
}

impl Session {
    pub fn new(name: &str, spec: SessionSpec, connector: Arc<dyn Connect>, on_delta: OnText, on_final: OnText, on_status: OnStatus) -> Self {
        let (stop, _) = watch::channel(false);
        Self {
            shared: Arc::new(Shared {
                name: name.to_owned(),
                spec,
                connector,
                on_delta,
                on_final,
                on_status,
                state: Mutex::new(State { state: "idle", error: None, fatal: false, last_delta_ts: None, connected_at: None, reconnects: 0, audio_s: 0.0 }),
                dropped: Mutex::new(None),
            }),
            stop,
            task: Mutex::new(None),
        }
    }

    pub fn name(&self) -> &str {
        &self.shared.name
    }

    pub fn state(&self) -> &'static str {
        lock(&self.shared.state).state
    }

    pub fn fatal(&self) -> bool {
        lock(&self.shared.state).fatal
    }

    pub fn error(&self) -> Option<String> {
        lock(&self.shared.state).error.clone()
    }

    pub fn start(&self, audio: AudioFeed) {
        lock(&self.shared.state).fatal = false;
        *lock(&self.shared.dropped) = Some(audio.counter());
        let _ = self.stop.send(false);
        let shared = Arc::clone(&self.shared);
        let stop = self.stop.subscribe();
        let handle = tokio::spawn(run(shared, audio, stop));
        if let Some(old) = lock(&self.task).replace(handle) {
            old.abort();
        }
    }

    pub async fn stop(&self) {
        let _ = self.stop.send(true);
        let task = lock(&self.task).take();
        if let Some(task) = task {
            // The loop closes its socket and commits the open line on the way out; a stuck connect is cut short.
            if tokio::time::timeout(Duration::from_secs(2), task).await.is_err() {
                tracing::warn!("[{}] did not stop in time", self.shared.name);
            }
        }
        self.shared.set_state("idle", None);
    }

    pub fn health(&self) -> Value {
        let s = lock(&self.shared.state);
        let spec = &self.shared.spec;
        json!({
            "name": self.shared.name,
            "kind": "transcribe",
            "state": s.state,
            "error": s.error,
            "model": spec.model,
            "last_delta_ts": s.last_delta_ts,
            "connected_at": s.connected_at,
            "reconnects": s.reconnects,
            "fatal": s.fatal,
            "backend": spec.backend,
            "audio_s": (s.audio_s * 10.0).round() / 10.0,
            "price_per_hour": spec.price_per_hour,
            "cost_usd": spec.price_per_hour.map(|p| (s.audio_s / 3600.0 * p * 10_000.0).round() / 10_000.0),
            // Frames this session lost to a stalled uplink (see the capture).
            "dropped_audio": lock(&self.shared.dropped).as_ref().map_or(0, |q| q.dropped.load(Ordering::Relaxed)),
        })
    }
}

impl Shared {
    fn set_state(&self, state: &'static str, error: Option<String>) {
        {
            let mut s = lock(&self.state);
            s.state = state;
            s.error = error;
        }
        (self.on_status)();
    }

    fn emit(&self, emits: Vec<Emit>) {
        for e in emits {
            match e {
                Emit::Delta(t) => (self.on_delta)(&t),
                Emit::Final(t) => (self.on_final)(&t),
            }
        }
    }
}

/// How one connection ended.
enum End {
    /// The socket closed, cleanly or not, and the loop may reconnect.
    Closed(Option<String>),
    /// Stop was pressed.
    Stopped,
    /// The API reported an error retrying will not fix.
    Fatal,
}

async fn run(shared: Arc<Shared>, mut audio: AudioFeed, mut stop: watch::Receiver<bool>) {
    let epoch = Instant::now();
    let mut transcript = Transcript::new(shared.spec.dialect, shared.spec.idle_s, shared.spec.max_idle_s);
    let mut backoff = 1.0_f64;
    let request = WsRequest { url: shared.spec.url.clone(), subprotocols: shared.spec.subprotocols.clone(), headers: shared.spec.headers.clone() };
    loop {
        if *stop.borrow() {
            break;
        }
        let first = lock(&shared.state).reconnects == 0;
        shared.set_state(if first { "connecting" } else { "reconnecting" }, None);
        let opened = tokio::select! {
            r = shared.connector.open(&request) => Some(r),
            _ = stop.changed() => None,
        };
        let end = match opened {
            None => End::Stopped,
            Some(Err(msg)) => {
                tracing::warn!("[{}] {msg}", shared.name);
                fail(&shared, &msg);
                End::Closed(Some(msg))
            }
            Some(Ok(conn)) => {
                let mut connected = false;
                let ended = connection(&shared, conn, &mut transcript, &mut audio, &mut stop, epoch, &mut connected).await;
                if connected {
                    // A good connection starts the backoff again at a second.
                    backoff = 1.0;
                }
                if let End::Closed(Some(msg)) = &ended {
                    tracing::warn!("[{}] {msg}", shared.name);
                    // The API already said why in an error event; the close that follows is less useful. Keep the first.
                    fail(&shared, msg);
                }
                ended
            }
        };
        // Whatever the socket had in flight when it died is a line.
        shared.emit(transcript.flush());
        if matches!(end, End::Stopped | End::Fatal) || *stop.borrow() || lock(&shared.state).fatal {
            break;
        }
        let error = {
            let mut s = lock(&shared.state);
            s.reconnects += 1;
            s.error.clone()
        };
        shared.set_state("reconnecting", error);
        tokio::select! {
            () = tokio::time::sleep(Duration::from_secs_f64(backoff)) => {}
            _ = stop.changed() => break,
        }
        backoff = (backoff * 2.0).min(MAX_BACKOFF);
    }
    let (fatal, error) = {
        let s = lock(&shared.state);
        (s.fatal, s.error.clone())
    };
    if fatal {
        shared.set_state("error", error);
    } else {
        shared.set_state("idle", None);
    }
}

/// A failure of the connection itself: the first error text wins when the API already named a fatal one.
fn fail(shared: &Shared, msg: &str) {
    let keep = {
        let s = lock(&shared.state);
        s.fatal && s.error.is_some()
    };
    if keep {
        let error = lock(&shared.state).error.clone();
        shared.set_state("error", error);
        return;
    }
    shared.set_state("error", Some(msg.to_owned()));
    if is_fatal(msg) {
        lock(&shared.state).fatal = true;
    }
}

async fn connection(
    shared: &Shared,
    mut conn: Conn,
    transcript: &mut Transcript,
    audio: &mut AudioFeed,
    stop: &mut watch::Receiver<bool>,
    epoch: Instant,
    connected: &mut bool,
) -> End {
    if conn.out.send(Outgoing::Text(shared.spec.start.to_string())).await.is_err() {
        return End::Closed(Some("The connection closed before it was ready.".to_owned()));
    }
    *connected = true;
    lock(&shared.state).connected_at = Some(clock::wall());
    shared.set_state("connected", None);
    let mut tick = tokio::time::interval(SEGMENT_TICK);
    tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
    // OpenAI: when the current turn began, and when the last frame went, for the commit on a pause.
    let mut turn_started: Option<Instant> = None;
    let mut last_audio = Instant::now();
    loop {
        let commit_at = match (&shared.spec.audio, turn_started) {
            (Audio::OpenAiAppend { commit_gap_s, .. }, Some(_)) => Some(last_audio + Duration::from_secs_f64(*commit_gap_s)),
            _ => None,
        };
        tokio::select! {
            changed = stop.changed() => {
                if changed.is_err() || *stop.borrow() {
                    return End::Stopped;
                }
            }
            msg = conn.inc.recv() => match msg {
                None | Some(Incoming::Closed(None)) => return End::Closed(None),
                Some(Incoming::Closed(Some(why))) => return End::Closed(Some(why)),
                Some(Incoming::Text(raw)) => {
                    let Ok(evt) = serde_json::from_str::<Value>(&raw) else {
                        return End::Closed(Some("The server sent something that was not JSON.".to_owned()));
                    };
                    let now = epoch.elapsed().as_secs_f64();
                    let (emits, fault) = transcript.handle(&evt, now, clock::wall());
                    if let Some(ts) = transcript.last_delta_ts {
                        lock(&shared.state).last_delta_ts = Some(ts);
                    }
                    shared.emit(emits);
                    if let Some(fault) = fault {
                        tracing::error!("[{}] API error: {}", shared.name, fault.message);
                        shared.set_state("error", Some(fault.message));
                        if fault.fatal {
                            lock(&shared.state).fatal = true;
                            return End::Fatal;
                        }
                    }
                }
            },
            frame = audio.recv() => {
                let Some(pcm) = frame else { return End::Closed(None) };
                lock(&shared.state).audio_s += pcm.len() as f64 / PCM_BYTES_PER_S;
                let sent = match &shared.spec.audio {
                    Audio::Binary => send_binary(&conn.out, &pcm).await,
                    Audio::OpenAiAppend { max_turn_s, .. } => {
                        let payload = base64::engine::general_purpose::STANDARD.encode(&pcm);
                        let ok = conn.out.send(Outgoing::Text(json!({"type": "input_audio_buffer.append", "audio": payload}).to_string())).await.is_ok();
                        last_audio = Instant::now();
                        let began = *turn_started.get_or_insert(last_audio);
                        if ok && last_audio.duration_since(began).as_secs_f64() >= *max_turn_s {
                            turn_started = None;
                            commit(&conn.out).await
                        } else {
                            ok
                        }
                    }
                };
                if !sent {
                    return End::Closed(Some("The connection closed while audio was being sent.".to_owned()));
                }
            }
            () = async { tokio::time::sleep_until(commit_at.unwrap_or_else(Instant::now)).await }, if commit_at.is_some() => {
                turn_started = None;
                if !commit(&conn.out).await {
                    return End::Closed(Some("The connection closed while audio was being sent.".to_owned()));
                }
            }
            _ = tick.tick() => {
                shared.emit(transcript.commit_idle(epoch.elapsed().as_secs_f64()));
            }
        }
    }
}

async fn send_binary(out: &mpsc::Sender<Outgoing>, pcm: &[u8]) -> bool {
    for chunk in pcm.chunks(MAX_AUDIO_FRAME_BYTES) {
        if out.send(Outgoing::Binary(chunk.to_vec())).await.is_err() {
            return false;
        }
    }
    true
}

async fn commit(out: &mpsc::Sender<Outgoing>) -> bool {
    out.send(Outgoing::Text(json!({"type": "input_audio_buffer.commit"}).to_string())).await.is_ok()
}
