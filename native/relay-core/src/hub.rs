//! Caption fan-out (`app/hub.py`, ported by #656).
//!
//! One [`Channel`] per (stream, lang). Each holds the current open segment plus a ring buffer of committed lines, so a viewer that
//! joins late sees context at once instead of a blank screen. The wire shape (server to viewer) is the Python hub's, byte for byte:
//!
//! ```text
//! id: <seq>\n                                      (only when seq is set: a browser sends it back as Last-Event-ID)
//! data: {"stream","lang","seq","delta","final","ts"[,"text"]}\n\n
//! ```
//!
//! `text` (the whole open segment) rides every [`RESYNC_EVERY`]th delta and every committed line, so a viewer that missed deltas can
//! resync without replaying them. A frame is serialised once at publish time and the same [`Bytes`] goes to every subscriber.
//!
//! A subscriber is a bounded queue of [`MAX_QUEUE`] frames. One that falls that far behind is dropped, and its stream ends: the
//! browser reconnects with its last id and the hub replays only what it missed ([`Hub::subscribe`]).
//!
//! The blocklist is a [`Redact`] the hub asks for per channel; the real one (`app/redact.py`) arrives with captions (#659). The
//! default passes text through.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

use bytes::Bytes;
use serde_json::{json, Value};
use tokio::sync::mpsc;

pub const MAX_QUEUE: usize = 256;
/// On an open segment, resend the whole text only every Nth delta as a resync anchor; the deltas between carry the increment.
pub const RESYNC_EVERY: u64 = 20;
/// The status stream's queue (`Hub.subscribe_status`).
pub const STATUS_QUEUE: usize = 32;

pub type Frame = Bytes;

/// The streaming blocklist filter, one per channel.
pub trait Redact: Send {
    /// Take a caption delta, return the text that is safe to show now.
    fn feed(&mut self, delta: &str) -> String;
    /// Release everything held back (the segment ended).
    fn flush(&mut self) -> String;
    /// Redact a complete string in one pass, independent of the stream.
    fn whole(&mut self, text: &str) -> String;
    /// Clear buffered text at a segment boundary.
    fn reset(&mut self);
    fn set_terms(&mut self, terms: &[String]);
}

/// No blocklist: every delta passes through.
pub struct PassThrough;

impl Redact for PassThrough {
    fn feed(&mut self, delta: &str) -> String {
        delta.to_owned()
    }
    fn flush(&mut self) -> String {
        String::new()
    }
    fn whole(&mut self, text: &str) -> String {
        text.to_owned()
    }
    fn reset(&mut self) {}
    fn set_terms(&mut self, _terms: &[String]) {}
}

pub type RedactorFactory = Box<dyn Fn(&[String]) -> Box<dyn Redact> + Send + Sync>;

/// A committed line, handed to every sink after redaction.
#[derive(Clone, Debug, PartialEq)]
pub struct LineEvent {
    pub stream: String,
    pub lang: String,
    pub seq: u64,
    pub text: String,
    /// When the line's first delta arrived (its own commit time when it never had one).
    pub started: f64,
    pub ended: f64,
}

pub type LineSink = Arc<dyn Fn(&LineEvent) + Send + Sync>;

/// A ready-to-write SSE frame. A non-zero `seq` becomes the `id:` line.
pub fn sse_frame(msg: &Value, seq: Option<u64>) -> Frame {
    let mut out = Vec::with_capacity(128);
    if let Some(seq) = seq.filter(|s| *s != 0) {
        out.extend_from_slice(format!("id: {seq}\n").as_bytes());
    }
    out.extend_from_slice(b"data: ");
    // Serialising a Value cannot fail.
    let _ = serde_json::to_writer(&mut out, msg);
    out.extend_from_slice(b"\n\n");
    Bytes::from(out)
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

#[derive(Clone)]
struct Line {
    seq: u64,
    text: String,
    ts: f64,
}

struct Sub {
    id: u64,
    tx: mpsc::Sender<Frame>,
}

pub struct Channel {
    stream: String,
    lang: String,
    redactor: Box<dyn Redact>,
    history: VecDeque<Line>,
    history_cap: usize,
    open_text: String,
    /// The seq of the latest delta in the open segment.
    open_seq: u64,
    /// Deltas since the last resync anchor.
    delta_count: u64,
    subscribers: Vec<Sub>,
    /// Finished-lines-only viewers (the touch panels). Kept apart so a delta is fanned to one set and never filtered per
    /// subscriber: the frame is still serialised once.
    final_subscribers: Vec<Sub>,
    last_delta_ts: Option<f64>,
    /// The hub's seq when this channel was last reset. A viewer whose last id is at or below it saw nothing since, so its screen is
    /// from before the reset.
    reset_seq: u64,
    open_started_ts: Option<f64>,
}

impl Channel {
    fn snapshot(&self, finals_only: bool, now: f64) -> Value {
        let lines: Vec<Value> = self.history.iter().map(|l| json!({"seq": l.seq, "text": l.text, "ts": l.ts})).collect();
        json!({
            "type": "snapshot",
            "stream": self.stream,
            "lang": self.lang,
            "lines": lines,
            "open": if finals_only { "" } else { self.open_text.as_str() },
            "ts": now,
        })
    }

    fn push(subs: &mut Vec<Sub>, frame: &Frame) {
        // A full queue means a viewer too slow to keep up; a closed one has gone. Either leaves.
        subs.retain(|s| s.tx.try_send(frame.clone()).is_ok());
    }

    fn fanout(&mut self, frame: &Frame, finals: bool) {
        Self::push(&mut self.subscribers, frame);
        if finals {
            Self::push(&mut self.final_subscribers, frame);
        }
    }
}

struct Inner {
    channels: Vec<Channel>,
    seq: u64,
    status: Vec<(u64, mpsc::Sender<Arc<Value>>)>,
    history_lines: usize,
    blocklist: Vec<String>,
}

pub struct Hub {
    inner: Mutex<Inner>,
    factory: RedactorFactory,
    clock: Box<dyn Fn() -> f64 + Send + Sync>,
    sinks: Mutex<Vec<(String, LineSink)>>,
    next_id: AtomicU64,
}

fn wall_clock() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
}

/// A subscription to one channel. Dropping it unsubscribes.
pub struct Subscription {
    rx: mpsc::Receiver<Frame>,
    hub: Arc<Hub>,
    stream: String,
    lang: String,
    id: u64,
}

impl Subscription {
    /// The next frame; `None` once the hub dropped this viewer (it fell behind) or closed.
    pub async fn recv(&mut self) -> Option<Frame> {
        self.rx.recv().await
    }

    /// The next frame if one is waiting now.
    pub fn try_recv(&mut self) -> Result<Frame, mpsc::error::TryRecvError> {
        self.rx.try_recv()
    }
}

impl Drop for Subscription {
    fn drop(&mut self) {
        let mut inner = lock(&self.hub.inner);
        if let Some(ch) = inner.channels.iter_mut().find(|c| c.stream == self.stream && c.lang == self.lang) {
            ch.subscribers.retain(|s| s.id != self.id);
            ch.final_subscribers.retain(|s| s.id != self.id);
        }
    }
}

/// A subscription to the admin status stream. Dropping it unsubscribes.
pub struct StatusSubscription {
    rx: mpsc::Receiver<Arc<Value>>,
    hub: Arc<Hub>,
    id: u64,
}

impl StatusSubscription {
    /// The next pushed status; `None` once the hub closed.
    pub async fn recv(&mut self) -> Option<Arc<Value>> {
        self.rx.recv().await
    }

    /// The next status if one is waiting now.
    pub fn try_recv(&mut self) -> Result<Arc<Value>, mpsc::error::TryRecvError> {
        self.rx.try_recv()
    }
}

impl Drop for StatusSubscription {
    fn drop(&mut self) {
        lock(&self.hub.inner).status.retain(|(id, _)| *id != self.id);
    }
}

impl Hub {
    /// A hub with no blocklist.
    pub fn new() -> Arc<Self> {
        Self::with(Box::new(|_| Box::new(PassThrough)), Box::new(wall_clock))
    }

    /// A hub whose channels filter through `factory`'s redactors, and whose timestamps come from `clock` (a test's own clock keeps
    /// its frames comparable).
    pub fn with(factory: RedactorFactory, clock: Box<dyn Fn() -> f64 + Send + Sync>) -> Arc<Self> {
        Arc::new(Self {
            inner: Mutex::new(Inner { channels: Vec::new(), seq: 0, status: Vec::new(), history_lines: 40, blocklist: Vec::new() }),
            factory,
            clock,
            sinks: Mutex::new(Vec::new()),
            next_id: AtomicU64::new(1),
        })
    }

    fn now(&self) -> f64 {
        (self.clock)()
    }

    /// How many committed lines a channel keeps for a late viewer. Applies to channels made after the call (as in Python).
    pub fn set_history_lines(&self, n: usize) {
        lock(&self.inner).history_lines = n.max(1);
    }

    /// Modules that take every committed line too (the OBS caption sender, the recorder): added in a module's `attach` and removed
    /// in its `detach`, by name. One that panics never stops the line.
    pub fn add_line_sink(&self, name: &str, sink: LineSink) {
        let mut sinks = lock(&self.sinks);
        if !sinks.iter().any(|(n, _)| n == name) {
            sinks.push((name.to_owned(), sink));
        }
    }

    pub fn remove_line_sink(&self, name: &str) {
        lock(&self.sinks).retain(|(n, _)| n != name);
    }

    /// Apply a blocklist to every channel, current and future. Takes effect on the next delta.
    pub fn set_blocklist(&self, terms: &[String]) {
        let mut inner = lock(&self.inner);
        inner.blocklist = terms.to_vec();
        for ch in &mut inner.channels {
            ch.redactor.set_terms(terms);
        }
    }

    pub fn blocklist(&self) -> Vec<String> {
        lock(&self.inner).blocklist.clone()
    }

    fn index(&self, inner: &mut Inner, stream: &str, lang: &str) -> usize {
        if let Some(i) = inner.channels.iter().position(|c| c.stream == stream && c.lang == lang) {
            return i;
        }
        inner.channels.push(Channel {
            stream: stream.to_owned(),
            lang: lang.to_owned(),
            redactor: (self.factory)(&inner.blocklist),
            history: VecDeque::new(),
            history_cap: inner.history_lines,
            open_text: String::new(),
            open_seq: 0,
            delta_count: 0,
            subscribers: Vec::new(),
            final_subscribers: Vec::new(),
            last_delta_ts: None,
            reset_seq: 0,
            open_started_ts: None,
        });
        inner.channels.len() - 1
    }

    /// Clear a channel (a new run): its open text, its history and its redactor, and tell its viewers with a snapshot.
    pub fn reset(&self, stream: &str, lang: &str) {
        let now = self.now();
        let mut inner = lock(&self.inner);
        let i = self.index(&mut inner, stream, lang);
        let seq = inner.seq;
        let ch = &mut inner.channels[i];
        ch.open_text.clear();
        ch.open_seq = 0;
        ch.delta_count = 0;
        ch.open_started_ts = None;
        ch.redactor.reset();
        ch.history.clear();
        ch.reset_seq = seq;
        let snap = sse_frame(&ch.snapshot(false, now), None);
        ch.fanout(&snap, true);
        if !ch.final_subscribers.is_empty() {
            let finals = sse_frame(&ch.snapshot(true, now), None);
            Channel::push(&mut ch.final_subscribers, &finals);
        }
    }

    /// One more piece of the open segment. The blocklist is applied here, before any viewer sees it; the redactor may withhold the
    /// whole delta when it could still become a banned term, and then there is nothing to send yet.
    pub fn publish_delta(&self, stream: &str, lang: &str, delta: &str) {
        if delta.is_empty() {
            return;
        }
        let now = self.now();
        let mut inner = lock(&self.inner);
        let i = self.index(&mut inner, stream, lang);
        let visible = inner.channels[i].redactor.feed(delta);
        if visible.is_empty() {
            return;
        }
        inner.seq += 1;
        let seq = inner.seq;
        let ch = &mut inner.channels[i];
        if ch.open_text.is_empty() {
            ch.open_started_ts = Some(now);
        }
        ch.open_text.push_str(&visible);
        ch.open_seq = seq;
        ch.last_delta_ts = Some(now);
        ch.delta_count += 1;
        let mut msg = json!({"stream": stream, "lang": lang, "seq": seq, "delta": visible, "final": false, "ts": now});
        // The whole text only on the periodic anchor; the viewer appends the delta otherwise.
        if ch.delta_count.is_multiple_of(RESYNC_EVERY) {
            msg["text"] = json!(ch.open_text);
        }
        let frame = sse_frame(&msg, Some(seq));
        ch.fanout(&frame, false);
    }

    /// Commit a line. Returns it as published (blocklist applied), or "" when nothing was left to publish. `text` is the settled
    /// segment from the API when there is one: it is redacted in one pass so the committed line is authoritative whatever the
    /// delta chunking was.
    pub fn publish_final(&self, stream: &str, lang: &str, text: Option<&str>) -> String {
        let now = self.now();
        let event;
        {
            let mut inner = lock(&self.inner);
            let i = self.index(&mut inner, stream, lang);
            let ch = &mut inner.channels[i];
            let tail = ch.redactor.flush();
            let final_text = match text {
                Some(t) => ch.redactor.whole(t).trim().to_owned(),
                None => format!("{}{}", ch.open_text, tail).trim().to_owned(),
            };
            let started = ch.open_started_ts;
            ch.redactor.reset();
            ch.open_text.clear();
            ch.open_seq = 0;
            ch.delta_count = 0;
            ch.open_started_ts = None;
            if final_text.is_empty() {
                return String::new();
            }
            inner.seq += 1;
            let seq = inner.seq;
            let ch = &mut inner.channels[i];
            ch.last_delta_ts = Some(now);
            ch.history.push_back(Line { seq, text: final_text.clone(), ts: now });
            while ch.history.len() > ch.history_cap {
                ch.history.pop_front();
            }
            let msg = json!({"stream": stream, "lang": lang, "seq": seq, "delta": "", "text": final_text, "final": true, "ts": now});
            let frame = sse_frame(&msg, Some(seq));
            ch.fanout(&frame, true);
            event = LineEvent { stream: stream.to_owned(), lang: lang.to_owned(), seq, text: final_text, started: started.unwrap_or(now), ended: now };
        }
        // The sinks run without the hub's lock, so one that publishes or subscribes cannot deadlock it.
        let sinks: Vec<LineSink> = lock(&self.sinks).iter().map(|(_, s)| Arc::clone(s)).collect();
        for sink in sinks {
            if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| sink(&event))).is_err() {
                tracing::warn!("a line sink failed; the line was still published");
            }
        }
        event.text
    }

    /// A control message (a target-set change) to every viewer on every channel. No `id:`: it is not a caption.
    pub fn broadcast_viewers(&self, msg: &Value) {
        let frame = sse_frame(msg, None);
        for ch in &mut lock(&self.inner).channels {
            ch.fanout(&frame, true);
        }
    }

    /// Subscribe to a channel. The queue is primed with what the viewer needs first: on a reconnect with a known position only the
    /// committed lines after `last_id` and the open segment, so the reader keeps their place; otherwise a snapshot, which the viewer
    /// treats as a rebuild.
    ///
    /// Two resumes look servable and are not, and both get the snapshot. One from before the last reset: the viewer missed the empty
    /// reset snapshot and, with nothing committed since, would keep the old run's lines on screen. And one from a server that has
    /// since restarted: its id is ahead of anything this hub has issued, so every line committed since would compare lower and be
    /// skipped.
    pub fn subscribe(self: &Arc<Self>, stream: &str, lang: &str, last_id: Option<u64>, finals_only: bool) -> Subscription {
        let now = self.now();
        let mut inner = lock(&self.inner);
        let i = self.index(&mut inner, stream, lang);
        let seq = inner.seq;
        let ch = &mut inner.channels[i];
        let frames = Self::resume_frames(ch, seq, last_id, finals_only, now);
        let (tx, rx) = mpsc::channel(MAX_QUEUE + frames.len());
        for f in frames {
            // The queue was made to hold them.
            let _ = tx.try_send(f);
        }
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        let sub = Sub { id, tx };
        if finals_only { ch.final_subscribers.push(sub) } else { ch.subscribers.push(sub) }
        Subscription { rx, hub: Arc::clone(self), stream: stream.to_owned(), lang: lang.to_owned(), id }
    }

    fn resume_frames(ch: &Channel, hub_seq: u64, last_id: Option<u64>, finals_only: bool, now: f64) -> Vec<Frame> {
        if let Some(last) = last_id.filter(|l| *l != 0) {
            if ch.reset_seq < last && last <= hub_seq {
                let oldest = ch.history.front().map(|l| l.seq);
                if oldest.is_none_or(|o| o <= last + 1) {
                    let mut frames = Vec::new();
                    for line in ch.history.iter().filter(|l| l.seq > last) {
                        let msg = json!({"stream": ch.stream, "lang": ch.lang, "seq": line.seq, "delta": "", "text": line.text, "final": true, "ts": line.ts});
                        frames.push(sse_frame(&msg, Some(line.seq)));
                    }
                    if !finals_only && !ch.open_text.is_empty() && ch.open_seq > last {
                        let msg = json!({"stream": ch.stream, "lang": ch.lang, "seq": ch.open_seq, "delta": "", "text": ch.open_text, "final": false, "ts": ch.last_delta_ts});
                        frames.push(sse_frame(&msg, Some(ch.open_seq)));
                    }
                    return frames;
                }
            }
        }
        vec![sse_frame(&ch.snapshot(finals_only, now), None)]
    }

    /// How many viewers are connected, across every channel.
    pub fn viewer_count(&self) -> usize {
        lock(&self.inner).channels.iter().map(|c| c.subscribers.len() + c.final_subscribers.len()).sum()
    }

    // -- the admin status stream ---------------------------------------------------------------------------------------------

    pub fn subscribe_status(self: &Arc<Self>) -> StatusSubscription {
        let (tx, rx) = mpsc::channel(STATUS_QUEUE);
        let id = self.next_id.fetch_add(1, Ordering::Relaxed);
        lock(&self.inner).status.push((id, tx));
        StatusSubscription { rx, hub: Arc::clone(self), id }
    }

    /// Push a change to every status subscriber. One whose queue is full is dropped, and its stream ends.
    pub fn push_status(&self, payload: Value) {
        let payload = Arc::new(payload);
        lock(&self.inner).status.retain(|(_, tx)| tx.try_send(Arc::clone(&payload)).is_ok());
    }

    pub fn status_count(&self) -> usize {
        lock(&self.inner).status.len()
    }
}
