//! The follower: a background task holding one `POST /v1/status/updates` connection with a FIXED list of paths (`propresenter.py`'s `Follower`).
//!
//! What a live ProPresenter 22 does that its published description does not say is written up in docs/reference/propresenter-live-test.md; the
//! code follows that file:
//!
//! * messages are `{"url": ..., "data": ...}` separated by a blank line, not by HTTP chunks (`client::Splitter`);
//! * the stream MISSES slide changes, so a slide, slide-index or presentation message is only a signal and `GET /v1/status/slide` is re-read; a
//!   result with the same text is ignored;
//! * no presentation means no lyrics, whatever `status/slide` still says;
//! * `timer/system_time` arrives every 1-3 s unasked and is the heartbeat; when it stops the connection is dropped and reopened with backoff;
//! * one unknown path fails the whole request (400), so the list is never built from outside input;
//! * ProPresenter can stall for minutes, so every request has a short timeout.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use relay_core::caps::PRESENTATION_UP;
use relay_core::text::{same_name, squeeze};
use relay_core::Host;
use serde_json::{json, Map, Value};
use tokio::sync::{mpsc, Notify};

use crate::client::{self, cue_groups, cue_list, parse_timer, slide_notes, Client, Kind, ProError};
use crate::settings::{self, clean_stored, configured};
use crate::slidetags::{Deck, Observed, Tags};
use crate::KEY;

/// What the stream is asked for. Fixed: one path ProPresenter does not know fails the whole request.
pub const STREAM_PATHS: [&str; 13] = [
    "status/slide",
    "presentation/current",
    "presentation/slide_index",
    "timers/current",
    "stage/message",
    "status/layers",
    "playlist/active",
    "look/current",
    "announcement/active",
    "announcement/slide_index",
    "status/screens",
    "status/audience_screens",
    "status/stage_screens",
];
/// Re-read every resync.
pub const RESYNC_DATA: [&str; 4] = ["timers/current", "status/layers", "playlist/active", "look/current"];
/// Every `SLOW_EVERY` resyncs.
pub const SLOW_DATA: [&str; 3] = ["status/screens", "status/audience_screens", "status/stage_screens"];
pub const SLOW_EVERY: u64 = 5;
pub const HEARTBEAT_PATH: &str = "timer/system_time";
pub const MISSES_FOR_DOWN: u32 = 2;
pub const STAGE_MESSAGE_MAX: usize = 200;
/// The timers' configuration (count down or up) is re-read this many resyncs apart.
pub const TIMERS_RESYNC_EVERY: u64 = 20;
pub const AUDIENCES: [&str; 4] = ["viewer", "screen", "overlay", "panel"];

/// How long things wait. Tests make them short.
#[derive(Clone, Debug)]
pub struct Timing {
    /// The slide position is also read this often, since the stream misses changes.
    pub resync: Duration,
    /// No heartbeat for this long: the connection is dropped and reopened.
    pub stall: Duration,
    pub backoff: Vec<Duration>,
}

impl Default for Timing {
    fn default() -> Self {
        Self { resync: Duration::from_secs(1), stall: client::STALL, backoff: [1.0, 2.0, 4.0, 8.0, 15.0].map(Duration::from_secs_f64).to_vec() }
    }
}

pub type Listener = Arc<dyn Fn() + Send + Sync>;
/// How the follower builds a client for a saved address: the real one, or a test's.
pub type MakeClient = Arc<dyn Fn(&str, u16, Arc<dyn Fn() -> bool + Send + Sync>) -> Client + Send + Sync>;

pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

#[derive(Clone, Debug, PartialEq, Default)]
pub struct Side {
    pub text: String,
    pub notes: String,
    pub uuid: String,
}

fn side(v: &Value) -> Side {
    Side { text: crate::client::text(v, "text").trim_matches(['\r', '\n']).to_owned(), notes: crate::client::text(v, "notes"), uuid: crate::client::text(v, "uuid") }
}

/// A presentation (or an announcement) as the follower knows it.
#[derive(Clone, Debug, PartialEq)]
pub struct Described {
    pub uuid: String,
    pub name: String,
    pub path: String,
    pub library: String,
    pub groups: Vec<(String, usize)>,
    pub cues: Vec<Value>,
}

impl Described {
    pub fn to_json(&self) -> Value {
        json!({"uuid": self.uuid, "name": self.name, "path": self.path, "library": self.library, "groups": self.groups.iter().map(|(n, c)| json!([n, c])).collect::<Vec<_>>(), "cues": self.cues})
    }
}

/// `/Libraries/<name>/` in a presentation's path.
fn library_of(path: &str) -> String {
    let p = path.replace('\\', "/");
    let mut from = 0;
    while let Some(at) = p[from..].find("/Libraries/") {
        let rest = &p[from + at + "/Libraries/".len()..];
        if let Some(end) = rest.find('/') {
            if end > 0 {
                return rest[..end].trim().to_owned();
            }
        }
        from += at + 1;
    }
    String::new()
}

pub fn describe(uuid: &str, name: &str, detail: &Value, with_text: bool) -> Described {
    let path = crate::client::text(detail, "presentation_path");
    let groups = cue_groups(detail, None).iter().map(|g| (crate::client::text(g, "name").trim().to_owned(), g["slides"].as_array().map_or(0, Vec::len))).collect();
    let id = if detail["id"].is_object() { &detail["id"] } else { &Value::Null };
    let shown = id["name"].as_str().unwrap_or(name).trim().to_owned();
    let keys: &[&str] = if with_text { &["group", "color", "label", "text", "notes"] } else { &["group", "color", "label"] };
    let cues = cue_list(detail, None).iter().map(|c| client::pick(c, keys)).collect();
    Described { uuid: uuid.to_owned(), name: shown, library: library_of(&path), path, groups, cues }
}

/// `^\s*(verse|chorus|pre[- ]?chorus|bridge|tag|refrain|intro|outro|interlude|ending|vamp)\b`, ignoring case.
pub fn song_group(name: &str) -> bool {
    let low = name.trim_start().to_lowercase();
    ["verse", "chorus", "pre-chorus", "pre chorus", "prechorus", "bridge", "tag", "refrain", "intro", "outro", "interlude", "ending", "vamp"]
        .iter()
        .any(|w| low.strip_prefix(w).is_some_and(|rest| rest.chars().next().is_none_or(|c| !(c.is_alphanumeric() || c == '_'))))
}

struct State {
    rev: u64,
    client: Option<Client>,
    up: bool,
    connecting: bool,
    misses: u32,
    error: String,
    version: Option<Value>,
    presentation: Option<Described>,
    announced: Option<String>,
    slide_index: Option<i64>,
    current: Side,
    next: Side,
    announcement: Option<Described>,
    announcement_index: Option<i64>,
    deck: Option<Arc<Deck>>,
    data: Map<String, Value>,
    media: Option<Value>,
    timers_read_at: f64,
    timer_kinds: BTreeMap<String, String>,
    kinds_asked: BTreeSet<String>,
    resyncs: u64,
    ticks: u64,
    stage_message: String,
    stage_owner: Option<String>,
    announced_down: bool,
    sig: String,
    conn_key: Option<(bool, String, i64)>,
    warned_view_only: bool,
    pending_slide: bool,
    pending_presentation: bool,
    pending_announcement: bool,
    presentation_hint: Value,
    announcement_hint: Option<Value>,
}

impl Default for State {
    fn default() -> Self {
        Self {
            rev: 0,
            client: None,
            up: false,
            connecting: false,
            misses: 0,
            error: String::new(),
            version: None,
            presentation: None,
            announced: None,
            slide_index: None,
            current: Side::default(),
            next: Side::default(),
            announcement: None,
            announcement_index: None,
            deck: None,
            data: Map::new(),
            media: None,
            timers_read_at: 0.0,
            timer_kinds: BTreeMap::new(),
            kinds_asked: BTreeSet::new(),
            resyncs: 0,
            ticks: 0,
            stage_message: String::new(),
            stage_owner: None,
            announced_down: false,
            sig: String::new(),
            conn_key: None,
            warned_view_only: false,
            pending_slide: false,
            pending_presentation: false,
            pending_announcement: false,
            presentation_hint: Value::Null,
            announcement_hint: None,
        }
    }
}

enum Event {
    Message(Value),
    End(Option<ProError>),
}

pub struct Follower {
    host: Host,
    make_client: MakeClient,
    timing: Timing,
    state: Mutex<State>,
    listeners: Mutex<Vec<Listener>>,
    wake: Notify,
    hangup: Notify,
    stop: Mutex<Option<Arc<AtomicBool>>>,
    tags: Arc<Tags>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

impl Follower {
    pub fn new(host: Host, make_client: MakeClient, timing: Timing, tags: Arc<Tags>) -> Arc<Self> {
        Arc::new(Self {
            host,
            make_client,
            timing,
            state: Mutex::new(State::default()),
            listeners: Mutex::new(Vec::new()),
            wake: Notify::new(),
            hangup: Notify::new(),
            stop: Mutex::new(None),
            tags,
        })
    }

    fn st(&self) -> MutexGuard<'_, State> {
        lock(&self.state)
    }

    pub fn host(&self) -> &Host {
        &self.host
    }

    pub fn tags(&self) -> &Arc<Tags> {
        &self.tags
    }

    pub fn timing(&self) -> &Timing {
        &self.timing
    }

    /// The saved settings, cleaned.
    pub fn settings(&self) -> Value {
        clean_stored(&self.host.settings(KEY).unwrap_or_else(|| json!({})))
    }

    pub fn configured(&self) -> bool {
        configured(&self.settings())
    }

    pub fn on_change(&self, f: Listener) {
        lock(&self.listeners).push(f);
    }

    pub fn rev(&self) -> u64 {
        self.st().rev
    }

    pub fn is_up(&self) -> bool {
        self.st().up
    }

    pub fn client(&self) -> Option<Client> {
        self.st().client.clone()
    }

    /// The client for the saved address even when the follower is not connected (a one-off call from a route), or `None` while not set up.
    pub fn client_for_calls(&self) -> Result<Client, ProError> {
        let s = self.settings();
        if !configured(&s) {
            return Err(ProError::new(Kind::Network, "ProPresenter is not set up."));
        }
        Ok(self.build_client(&s))
    }

    fn build_client(&self, s: &Value) -> Client {
        let host = self.host.clone();
        (self.make_client)(
            &settings::s(s, "host"),
            u16::try_from(s["port"].as_i64().unwrap_or(0)).unwrap_or(0),
            Arc::new(move || clean_stored(&host.settings(KEY).unwrap_or_else(|| json!({})))["view_only"] == Value::Bool(true)),
        )
    }

    pub fn data(&self, path: &str) -> Value {
        self.st().data.get(path).cloned().unwrap_or(Value::Null)
    }

    pub fn presentation(&self) -> Option<Described> {
        self.st().presentation.clone()
    }

    pub fn announcement(&self) -> Option<Described> {
        self.st().announcement.clone()
    }

    pub fn slide_index(&self) -> Option<i64> {
        self.st().slide_index
    }

    pub fn announcement_index(&self) -> Option<i64> {
        self.st().announcement_index
    }

    pub fn current(&self) -> Side {
        self.st().current.clone()
    }

    pub fn next_side(&self) -> Side {
        self.st().next.clone()
    }

    pub fn media(&self) -> Option<Value> {
        self.st().media.clone()
    }

    pub fn timers_read_at(&self) -> f64 {
        self.st().timers_read_at
    }

    pub fn stage_message(&self) -> String {
        self.st().stage_message.clone()
    }

    // -- lifecycle -------------------------------------------------------------------------------------------------------------
    /// ProPresenter's network port changed (#348): drop the live connection and open it again through the port it goes through now.
    pub async fn reconnect(&self) {
        self.hang_up();
        self.poke().await;
    }

    /// The settings changed: reconnect now (a new address) or go quiet (switched off).
    pub async fn poke(&self) {
        self.wake.notify_one();
        tokio::task::yield_now().await;
    }

    fn hang_up(&self) {
        if let Some(stop) = lock(&self.stop).take() {
            stop.store(true, Ordering::SeqCst);
        }
        self.hangup.notify_waiters();
    }

    pub fn shutdown(&self) {
        self.hang_up();
    }

    fn key(s: &Value) -> (bool, String, i64) {
        (s["enabled"] == Value::Bool(true), settings::s(s, "host"), s["port"].as_i64().unwrap_or(0))
    }

    async fn sleep(&self, d: Duration) {
        let _ = tokio::time::timeout(d, self.wake.notified()).await;
    }

    pub async fn run(self: Arc<Self>) {
        let mut attempt = 0usize;
        loop {
            let s = self.settings();
            if !configured(&s) {
                self.reset_state();
                attempt = 0;
                self.sleep(Duration::from_secs(1)).await;
                continue;
            }
            let key = Self::key(&s);
            let client = self.build_client(&s);
            {
                let mut st = self.st();
                st.conn_key = Some(key.clone());
                st.client = Some(client.clone());
                st.connecting = true;
            }
            self.note_change();
            match self.session(&client, &key).await {
                Ok(()) => attempt = 0,
                Err(e) => self.miss(e.detail()),
            }
            self.hang_up();
            let again = self.settings();
            if configured(&again) && Self::key(&again) == key {
                let wait = self.timing.backoff.get(attempt.min(self.timing.backoff.len().saturating_sub(1))).copied().unwrap_or(Duration::from_secs(1));
                self.sleep(wait).await;
                attempt += 1;
            }
        }
    }

    // -- one connection --------------------------------------------------------------------------------------------------------
    /// Hold the live connection until it drops, stalls, or the settings change.
    async fn session(self: &Arc<Self>, client: &Client, key: &(bool, String, i64)) -> Result<(), ProError> {
        let mut stream = client.open_stream(&STREAM_PATHS).await?;
        let stop = Arc::new(AtomicBool::new(false));
        *lock(&self.stop) = Some(Arc::clone(&stop));
        let (tx, mut rx) = mpsc::unbounded_channel::<Event>();
        let reader = tokio::spawn(async move {
            loop {
                match stream.next().await {
                    Ok(Some(msgs)) => {
                        for m in msgs {
                            if tx.send(Event::Message(m)).is_err() {
                                return;
                            }
                        }
                    }
                    Ok(None) => {
                        let _ = tx.send(Event::End(None));
                        return;
                    }
                    Err(e) => {
                        let _ = tx.send(Event::End(Some(e)));
                        return;
                    }
                }
            }
        });
        let result = self.session_loop(client, key, &stop, &mut rx).await;
        reader.abort();
        result
    }

    async fn session_loop(self: &Arc<Self>, client: &Client, key: &(bool, String, i64), stop: &AtomicBool, rx: &mut mpsc::UnboundedReceiver<Event>) -> Result<(), ProError> {
        let (mut last_beat, mut last_sync) = (Instant::now(), Instant::now());
        let mut first = true;
        if self.st().version.is_none() {
            if let Ok(v) = client.version().await {
                self.st().version = Some(v);
            }
        }
        loop {
            let s = self.settings();
            if stop.load(Ordering::SeqCst) || !configured(&s) || &Self::key(&s) != key {
                return Ok(());
            }
            let got = tokio::select! {
                got = tokio::time::timeout(Duration::from_secs(1), rx.recv()) => got.ok(),
                () = self.hangup.notified() => return Ok(()),
            };
            if let Some(ev) = got {
                let Some(ev) = ev else { return Err(ProError::new(Kind::Network, "ProPresenter closed the live connection.")) };
                let mut batch = vec![ev];
                // Several messages arrive together on connect: look at the slide once they are all in.
                while let Ok(more) = rx.try_recv() {
                    batch.push(more);
                }
                let mut ended = None;
                for item in batch {
                    match item {
                        Event::End(e) => {
                            ended = Some(e);
                            break;
                        }
                        Event::Message(m) => {
                            if first {
                                first = false;
                                self.answered();
                            }
                            if m["url"].as_str().unwrap_or("").trim_matches('/') == HEARTBEAT_PATH {
                                last_beat = Instant::now();
                            }
                            self.handle(&m);
                        }
                    }
                }
                self.flush_signals(client).await;
                if let Some(e) = ended {
                    return Err(e.unwrap_or_else(|| ProError::new(Kind::Network, "ProPresenter closed the live connection.")));
                }
            }
            if last_beat.elapsed() > self.timing.stall {
                return Err(ProError::new(Kind::Timeout, "ProPresenter stopped sending its heartbeat."));
            }
            if last_sync.elapsed() >= self.timing.resync && !first {
                last_sync = Instant::now();
                self.resync(client).await;
            }
        }
    }

    fn handle(&self, msg: &Value) {
        let mut url = msg["url"].as_str().unwrap_or("").trim_matches('/').to_owned();
        if let Some(rest) = url.strip_prefix("v1/") {
            url = rest.to_owned();
        }
        let data = msg["data"].clone();
        if !STREAM_PATHS.contains(&url.as_str()) {
            return; // unrequested paths (the heartbeat is one) are ignored
        }
        let mut changed = false;
        {
            let mut st = self.st();
            match url.as_str() {
                "stage/message" => {
                    st.stage_message = data.as_str().unwrap_or("").to_owned();
                    if st.stage_message.is_empty() {
                        st.stage_owner = None;
                    }
                    changed = true;
                }
                "presentation/current" => {
                    st.pending_presentation = true;
                    st.pending_slide = true;
                    st.presentation_hint = data;
                }
                "presentation/slide_index" => {
                    let idx = data["presentation_index"]["index"].as_i64();
                    st.slide_index = idx;
                    st.pending_slide = true;
                }
                "status/slide" => st.pending_slide = true,
                "announcement/active" => {
                    st.pending_announcement = true;
                    st.announcement_hint = Some(data);
                }
                "announcement/slide_index" => {
                    st.announcement_index = announcement_position(&data).1;
                    st.pending_announcement = true;
                }
                _ => {
                    if url == "timers/current" {
                        st.timers_read_at = now();
                    }
                    st.data.insert(url, data);
                    changed = true;
                }
            }
        }
        if changed {
            self.note_change();
        }
    }

    /// React to the signals gathered: read the presentation if it changed, then the slide. The stream misses slide changes, so the slide is always
    /// read from `GET /v1/status/slide`, never taken from the push.
    async fn flush_signals(&self, client: &Client) {
        let mut changed = false;
        let (pres, hint) = {
            let mut st = self.st();
            let take = std::mem::take(&mut st.pending_presentation);
            (take, std::mem::take(&mut st.presentation_hint))
        };
        if pres {
            changed = self.read_presentation(client, &hint).await;
            if changed {
                self.st().pending_slide = true; // a new presentation always has its slide read before anyone is told
            }
        }
        if std::mem::take(&mut self.st().pending_slide) {
            self.read_slide(client).await;
        }
        if changed {
            self.note_change();
        }
        let (ann, hint) = {
            let mut st = self.st();
            (std::mem::take(&mut st.pending_announcement), st.announcement_hint.take())
        };
        if ann {
            match hint {
                None => self.resync_announcement(client).await, // only the position was pushed
                Some(h) => self.read_announcement(&h),
            }
        }
    }

    /// A live ProPresenter does not push every slide change (sometimes none at all), so every second the slide position is read as well: two small
    /// GETs. The presentation is read again only when the position says a different one is up (or none).
    async fn resync(&self, client: &Client) {
        let body = match client.read("/v1/presentation/slide_index").await {
            Ok(b) => b,
            Err(e) => {
                tracing::debug!("Resync failed: {e}");
                return;
            }
        };
        let idx = &body["presentation_index"];
        let uuid = idx["presentation_id"]["uuid"].as_str().filter(|u| !u.is_empty()).map(str::to_owned);
        let known = self.st().presentation.as_ref().map(|p| p.uuid.clone());
        self.st().slide_index = idx["index"].as_i64();
        let mut changed = false;
        if uuid != known {
            match client.read("/v1/presentation/current").await {
                Ok(data) => changed = self.read_presentation(client, &data).await,
                Err(e) => {
                    tracing::debug!("Resync failed: {e}");
                    return;
                }
            }
        }
        self.read_slide(client).await;
        if changed {
            self.note_change();
        }
        self.resync_announcement(client).await;
        // The same stream also misses timer and layer changes (a stopped timer kept reading "running"), and the control page shows both, so they
        // are re-read with the slide. Screens change rarely: every few turns.
        let ticks = {
            let mut st = self.st();
            st.ticks += 1;
            st.ticks
        };
        let slow = if ticks % SLOW_EVERY == 1 { &SLOW_DATA[..] } else { &[] };
        for path in RESYNC_DATA.iter().chain(slow) {
            let Ok(body) = client.read(&format!("/v1/{path}")).await else { continue };
            if !body.is_null() {
                let mut st = self.st();
                st.data.insert((*path).to_owned(), body);
                if *path == "timers/current" {
                    st.timers_read_at = now();
                }
            }
        }
        self.read_timer_kinds(client).await;
        self.read_media(client).await;
        self.note_change();
    }

    /// Where the presentation layer's media is (`transport/presentation/time`) and what it is (`transport/presentation/current`), read with the
    /// slide: the stream is not trusted to send them. Only while the slide or media layer has something on it; otherwise nothing is asked.
    async fn read_media(&self, client: &Client) {
        let layers = self.data("status/layers");
        if !(layers.is_object() && (settings::truthy(&layers["slide"]) || settings::truthy(&layers["media"]))) {
            self.st().media = None;
            return;
        }
        let (at, cur) = match tokio::try_join!(client.read("/v1/transport/presentation/time"), client.read("/v1/transport/presentation/current")) {
            Ok(both) => both,
            Err(e) => {
                tracing::debug!("Media read failed: {e}");
                self.st().media = None;
                return;
            }
        };
        let cur = if cur.is_object() { cur } else { json!({}) };
        let ok = |v: &Value| v.as_f64().filter(|f| v.is_number() && *f >= 0.0 && f.is_finite());
        let name: String = squeeze(&client::text(&cur, "name")).chars().take(120).collect();
        self.st().media = Some(json!({
            "time": ok(&at).unwrap_or(0.0), "duration": ok(&cur["duration"]).unwrap_or(0.0), "playing": cur["is_playing"] == Value::Bool(true),
            "name": name, "audio_only": cur["audio_only"] == Value::Bool(true), "read_at": now(),
        }));
    }

    /// Which timers count down and which count up, from `GET /v1/timers` (the configuration; `timers/current` says only the time and the state).
    /// Read when a timer is new to Relay and every `TIMERS_RESYNC_EVERY` resyncs, since a timer can be edited in ProPresenter.
    async fn read_timer_kinds(&self, client: &Client) {
        let resyncs = {
            let mut st = self.st();
            st.resyncs += 1;
            st.resyncs
        };
        let ids: BTreeSet<String> = self.timers().iter().map(|t| client::text(t, "id")).collect();
        let known = self.st().kinds_asked.clone();
        if ids.is_empty() || (ids.is_subset(&known) && resyncs % TIMERS_RESYNC_EVERY != 0) {
            return;
        }
        let body = match client.read("/v1/timers").await {
            Ok(b) => b,
            Err(e) => {
                tracing::debug!("Could not read the timers' configuration: {e}");
                return;
            }
        };
        let mut kinds = BTreeMap::new();
        for item in body.as_array().into_iter().flatten() {
            if let Some(uuid) = item["id"]["uuid"].as_str() {
                kinds.insert(uuid.to_owned(), if item.get("elapsed_time").is_some() { "up" } else { "down" }.to_owned());
            }
        }
        let mut st = self.st();
        st.timer_kinds = kinds;
        st.kinds_asked = ids;
    }

    /// Every timer ProPresenter has, as read: `{id, name, state, dir, value}`, `value` in seconds as of `timers_read_at` (negative while overrun). A
    /// timer whose time cannot be read is left out.
    pub fn timers(&self) -> Vec<Value> {
        let st = self.st();
        let mut out = Vec::new();
        for item in st.data.get("timers/current").and_then(Value::as_array).into_iter().flatten() {
            let ident = &item["id"];
            let Some(uuid) = ident["uuid"].as_str() else { continue };
            let Some(value) = parse_timer(&item["time"]) else { continue };
            let name: String = squeeze(&client::text(ident, "name")).chars().take(48).collect();
            let state = item["state"].as_str().filter(|s| !s.is_empty()).unwrap_or("stopped");
            out.push(json!({"id": uuid, "name": name, "state": state, "dir": st.timer_kinds.get(uuid).map_or("down", String::as_str), "value": value}));
        }
        out
    }

    /// Read which presentation is up. True when it changed: the caller reads the slide next and only then calls `note_change`, so a new title is
    /// never announced with the previous (or no) slide text.
    async fn read_presentation(&self, client: &Client, data: &Value) -> bool {
        let pres = &data["presentation"];
        let uuid = pres["id"]["uuid"].as_str().filter(|u| !u.is_empty()).map(str::to_owned);
        let Some(uuid) = uuid else {
            let mut st = self.st();
            if st.presentation.is_some() {
                st.presentation = None;
                st.deck = None;
                st.slide_index = None;
                st.current = Side::default();
                st.next = Side::default();
                return true;
            }
            return false;
        };
        if self.st().presentation.as_ref().is_some_and(|p| p.uuid == uuid) {
            return false;
        }
        let name = client::text(&pres["id"], "name").trim().to_owned();
        {
            let mut st = self.st();
            st.presentation = Some(describe(&uuid, &name, &Value::Null, false));
            st.deck = None;
            st.current = Side::default();
            st.next = Side::default();
        }
        match client.read(&format!("/v1/presentation/{}", encode(&uuid))).await {
            Ok(detail) => {
                let inner = &detail["presentation"];
                let deck = self.tags.deck_for(&uuid, &slide_notes(inner));
                let mut st = self.st();
                st.presentation = Some(describe(&uuid, &name, inner, false));
                st.deck = Some(deck);
            }
            // The slide text still works without the library and group names; the song test says "not a song" until they are known.
            Err(e) => tracing::info!("Could not read the presentation's details: {e}"),
        }
        true
    }

    async fn resync_announcement(&self, client: &Client) {
        let body = match client.read("/v1/announcement/slide_index").await {
            Ok(b) => b,
            Err(e) => {
                tracing::debug!("Announcement resync failed: {e}");
                return;
            }
        };
        let (uuid, idx) = announcement_position(&body);
        self.st().announcement_index = idx;
        let known = self.st().announcement.as_ref().map(|a| a.uuid.clone());
        if uuid != known {
            match client.read("/v1/announcement/active").await {
                Ok(data) => self.read_announcement(&data),
                Err(e) => tracing::debug!("Announcement resync failed: {e}"),
            }
        }
    }

    /// `announcement/active` carries the whole presentation (groups and slides), so no second read.
    fn read_announcement(&self, data: &Value) {
        let ann = &data["announcement"];
        let uuid = ann["id"]["uuid"].as_str().filter(|u| !u.is_empty());
        let Some(uuid) = uuid else {
            let changed = {
                let mut st = self.st();
                if st.announcement.is_some() || st.announcement_index.is_some() {
                    st.announcement = None;
                    st.announcement_index = None;
                    true
                } else {
                    false
                }
            };
            if changed {
                self.note_change();
            }
            return;
        };
        let desc = describe(uuid, client::text(&ann["id"], "name").trim(), ann, true);
        let changed = {
            let mut st = self.st();
            if st.announcement.as_ref() == Some(&desc) {
                false
            } else {
                st.announcement = Some(desc);
                true
            }
        };
        if changed {
            self.note_change();
        }
    }

    pub fn is_song(&self) -> bool {
        let Some(pres) = self.presentation() else { return false };
        let lib = settings::s(&self.settings(), "songs_library");
        if !lib.is_empty() && same_name(&pres.library, &lib) {
            return true;
        }
        pres.groups.iter().any(|(n, _)| song_group(n))
    }

    /// The part (Verse 1, Chorus) the current slide is in, when the groups are known.
    pub fn group_name(&self) -> String {
        let st = self.st();
        let (Some(pres), Some(idx)) = (&st.presentation, st.slide_index) else { return String::new() };
        let mut seen = 0i64;
        for (name, count) in &pres.groups {
            if idx < seen + *count as i64 {
                return name.clone();
            }
            seen += *count as i64;
        }
        String::new()
    }

    async fn read_slide(&self, client: &Client) {
        let body = match client.read("/v1/status/slide").await {
            Ok(b) => b,
            Err(e) => {
                tracing::info!("Could not read the slide: {e}");
                return;
            }
        };
        let (cur, nxt) = (side(&body["current"]), side(&body["next"]));
        let changed = {
            let mut st = self.st();
            if cur == st.current && nxt == st.next {
                false // the same text pushed twice, or a push that changed nothing
            } else {
                st.current = cur;
                st.next = nxt;
                true
            }
        };
        if changed {
            self.note_change();
        }
    }

    // -- health ----------------------------------------------------------------------------------------------------------------
    /// First message on a connection: ProPresenter is there.
    fn answered(&self) {
        {
            let mut st = self.st();
            st.connecting = false;
            st.misses = 0;
            st.error.clear();
            if !st.up {
                st.up = true;
                if st.announced_down {
                    tracing::info!("ProPresenter is back");
                } else {
                    tracing::info!("ProPresenter connected");
                }
                st.announced_down = false;
            }
        }
        self.note_change();
    }

    /// A failed connect or a stall. One is noise; the second in a row marks ProPresenter down, announced once, until it answers again.
    fn miss(&self, why: &str) {
        {
            let mut st = self.st();
            st.misses += 1;
            st.error = why.to_owned();
            if st.misses >= MISSES_FOR_DOWN {
                if !st.announced_down {
                    tracing::warn!("ProPresenter is not answering: {why}");
                    st.announced_down = true;
                }
                st.up = false;
                st.connecting = false;
                st.presentation = None;
                st.slide_index = None;
                st.deck = None;
                st.current = Side::default();
                st.next = Side::default();
                st.announcement = None;
                st.announcement_index = None;
            }
        }
        self.note_change();
    }

    fn reset_state(&self) {
        let had = {
            let mut st = self.st();
            let had = st.up || st.misses > 0 || st.client.is_some();
            st.up = false;
            st.connecting = false;
            st.misses = 0;
            st.error.clear();
            st.client = None;
            st.version = None;
            st.announced_down = false;
            st.presentation = None;
            st.slide_index = None;
            st.deck = None;
            st.current = Side::default();
            st.next = Side::default();
            st.announcement = None;
            st.announcement_index = None;
            st.stage_message.clear();
            st.stage_owner = None;
            st.data = Map::new();
            st.media = None;
            st.timers_read_at = 0.0;
            st.timer_kinds.clear();
            st.kinds_asked.clear();
            had
        };
        if had {
            self.hang_up();
            self.note_change();
        }
    }

    // -- what is on screen -----------------------------------------------------------------------------------------------------
    /// What the lyrics feed shows now. Empty with no presentation, whatever status/slide still says.
    pub fn lyrics(&self) -> Value {
        let s = self.settings();
        let song = self.is_song();
        let part = self.group_name();
        let st = self.st();
        let showing = st.up && st.presentation.is_some() && (song || s["songs_only"] != Value::Bool(true));
        json!({
            "live": showing,
            "song": song,
            "title": if showing { st.presentation.as_ref().map_or("", |p| p.name.as_str()) } else { "" },
            "part": if showing { part.as_str() } else { "" },
            "index": if showing { json!(st.slide_index) } else { Value::Null },
            "text": if showing { st.current.text.as_str() } else { "" },
            "next": if showing { st.next.text.as_str() } else { "" },
        })
    }

    fn part(cue: Option<&Value>) -> Value {
        let c = cue.cloned().unwrap_or(Value::Null);
        json!({"part": client::text(&c, "group"), "part_color": client::text(&c, "color"), "label": client::text(&c, "label")})
    }

    /// What is up, in the words an operator uses: the item's title, which part (Verse, Chorus) and its colour, the slide number, and the notes for
    /// this slide and the next. `None` with no presentation up.
    pub fn item(&self) -> Option<Value> {
        let st = self.st();
        let (pres, idx) = (st.presentation.as_ref()?, st.slide_index);
        let at = |i: i64| usize::try_from(i).ok().and_then(|i| pres.cues.get(i));
        let here = idx.and_then(at);
        let ahead = idx.and_then(|i| at(i + 1));
        let active = st.data.get("playlist/active").map_or(Value::Null, |p| p["presentation"].clone());
        let entry = active["item"].is_object().then(|| &active["item"]);
        let mut out = Self::part(here);
        out["title"] = json!(pres.name);
        out["slide_number"] = json!(idx.map(|i| i + 1));
        out["slide_count"] = json!(pres.cues.len());
        out["notes"] = json!(st.current.notes);
        let mut next = Self::part(ahead);
        next["text"] = json!(st.next.text);
        next["notes"] = json!(st.next.notes);
        out["next"] = next;
        out["playlist"] = json!(client::text(&active["playlist"], "name"));
        out["playlist_item"] = entry.map_or(Value::Null, |e| json!({"name": client::text(e, "name"), "index": e["index"]}));
        Some(reorder_item(out))
    }

    pub fn announcement_state(&self) -> Option<Value> {
        let st = self.st();
        let (ann, idx) = (st.announcement.as_ref()?, st.announcement_index);
        let here = idx.and_then(|i| usize::try_from(i).ok()).and_then(|i| ann.cues.get(i));
        let mut out = json!({"uuid": ann.uuid, "title": ann.name, "slide_number": idx.map(|i| i + 1), "slide_count": ann.cues.len()});
        for (k, v) in Self::part(here).as_object().into_iter().flatten() {
            out[k] = v.clone();
        }
        out["text"] = json!(here.map_or(String::new(), |c| client::text(c, "text")));
        out["notes"] = json!(here.map_or(String::new(), |c| client::text(c, "notes")));
        Some(out)
    }

    /// Audience and stage screens: on or off (`null` until read), and the screens ProPresenter lists.
    pub fn screens(&self) -> Value {
        let flag = |p: &str| self.data(p).as_bool().map_or(Value::Null, Value::Bool);
        let mut listed = Vec::new();
        for o in self.data("status/screens").as_array().into_iter().flatten() {
            let Some(ident) = o["id"].as_object().map(|_| &o["id"]) else { continue };
            let size = if o["size"].is_object() { &o["size"] } else { &Value::Null };
            listed.push(json!({
                "uuid": client::text(ident, "uuid"), "name": client::text(ident, "name").trim(), "type": client::text(o, "screen_type"),
                "width": size["width"], "height": size["height"],
            }));
        }
        json!({"audience": flag("status/audience_screens"), "stage": flag("status/stage_screens"), "list": listed})
    }

    /// For the admin page.
    pub fn state(&self) -> Value {
        let s = self.settings();
        let is_song = self.is_song();
        let part = self.group_name();
        let item = self.item();
        let announcement = self.announcement_state();
        let screens = self.screens();
        let st = self.st();
        let look = st.data.get("look/current").cloned().unwrap_or(Value::Null);
        let pres = st.presentation.as_ref().map(|p| json!({"uuid": p.uuid, "name": p.name, "library": p.library, "song": is_song, "part": part}));
        json!({
            "configured": configured(&s),
            "connected": st.up,
            "connecting": st.connecting && !st.up,
            "error": if st.up { "" } else { st.error.as_str() },
            "view_only": s["view_only"] == Value::Bool(true),
            "version": st.version,
            "presentation": pres,
            "slide_index": st.slide_index,
            "current": {"text": st.current.text, "notes": st.current.notes},
            "next": {"text": st.next.text, "notes": st.next.notes},
            "stage_message": st.stage_message,
            "layers": st.data.get("status/layers").cloned().unwrap_or(Value::Null),
            "playlist_active": st.data.get("playlist/active").cloned().unwrap_or(Value::Null),
            "look_id": if look.is_object() { look["id"]["uuid"].clone() } else { Value::Null },
            "look": if look.is_object() { look["id"]["name"].clone() } else { Value::Null },
            "timers": st.data.get("timers/current").cloned().filter(settings::truthy).unwrap_or_else(|| json!([])),
            "item": item,
            "announcement": announcement,
            "screens": screens,
        })
    }

    /// Tell the slide-note tags what is live. Only a followed, connected ProPresenter with tags switched on has anything live; a comparison when
    /// nothing changed.
    fn observe_tags(&self) {
        let s = self.settings();
        let tg = &s["tags"];
        let (on, pres, index, notes, deck) = {
            let st = self.st();
            let on = tg["enabled"] == Value::Bool(true) && configured(&s) && st.up;
            (on, st.presentation.clone().filter(|_| on), st.slide_index, st.current.notes.clone(), st.deck.clone())
        };
        self.tags.observe(&Observed {
            enabled: on,
            settle_s: tg["settle_s"].as_f64().unwrap_or(settings::SETTLE_DEFAULT_S),
            uuid: pres.as_ref().map(|p| p.uuid.as_str()),
            title: pres.as_ref().map_or("", |p| p.name.as_str()),
            index,
            notes: if pres.is_some() { &notes } else { "" },
            deck: deck.as_deref(),
        });
    }

    /// Everything a listener would redraw, as one string: when it is the same as last time nobody is told.
    fn signature(&self) -> String {
        let lyrics = self.lyrics();
        let st = self.st();
        json!([
            st.up, st.connecting, st.error, st.presentation.as_ref().map(Described::to_json), st.slide_index,
            [st.current.text, st.current.notes, st.current.uuid], [st.next.text, st.next.notes, st.next.uuid], st.stage_message,
            st.data, lyrics, st.timer_kinds, st.announcement.as_ref().map(Described::to_json), st.announcement_index,
        ])
        .to_string()
    }

    pub fn note_change(&self) {
        self.observe_tags();
        self.announce_presentation();
        let sig = self.signature();
        let fire = {
            let mut st = self.st();
            if sig == st.sig {
                false
            } else {
                st.sig = sig;
                st.rev += 1;
                true
            }
        };
        if fire {
            let listeners: Vec<Listener> = lock(&self.listeners).clone();
            for f in listeners {
                f();
            }
        }
    }

    /// Tell whoever listens (REAPER's song markers, #413) when a new presentation is up.
    fn announce_presentation(&self) {
        let name = {
            let mut st = self.st();
            let pres = if st.up { st.presentation.clone() } else { None };
            let uuid = pres.as_ref().map(|p| p.uuid.clone());
            if uuid == st.announced {
                return;
            }
            st.announced = uuid;
            pres.map(|p| p.name).filter(|n| !n.is_empty())
        };
        if let Some(name) = name {
            self.host.announce(PRESENTATION_UP, &name);
        }
    }

    // -- stage message ---------------------------------------------------------------------------------------------------------
    pub fn clean_message(text: &str) -> String {
        squeeze(text).chars().take(STAGE_MESSAGE_MAX).collect()
    }

    pub fn need_client(&self) -> Result<Client, ProError> {
        let st = self.st();
        match (&st.client, configured(&self.settings())) {
            (Some(c), true) => Ok(c.clone()),
            _ => Err(ProError::new(Kind::Network, "ProPresenter is not set up.")),
        }
    }

    /// `PUT /v1/stage/message` takes a bare JSON string. Refused in view-only mode.
    pub async fn set_stage_message(&self, text: &str, owner: Option<&str>) -> Result<(), ProError> {
        let text = Self::clean_message(text);
        if text.is_empty() {
            return self.clear_stage_message().await;
        }
        self.need_client()?.put("/v1/stage/message", &json!(text)).await?;
        {
            let mut st = self.st();
            st.stage_message = text;
            st.stage_owner = owner.map(str::to_owned);
        }
        self.note_change();
        Ok(())
    }

    pub async fn clear_stage_message(&self) -> Result<(), ProError> {
        self.need_client()?.delete("/v1/stage/message").await?;
        {
            let mut st = self.st();
            st.stage_message.clear();
            st.stage_owner = None;
        }
        self.note_change();
        Ok(())
    }

    // -- Alerts (#145): an alert may also show on the stage display and goes away when it ends ---------------------------------
    pub fn alert_started(self: &Arc<Self>, alert_id: &str, text: &str) {
        self.spawn_alert(alert_id.to_owned(), Some(text.to_owned()));
    }

    pub fn alert_ended(self: &Arc<Self>, alert_id: &str) {
        self.spawn_alert(alert_id.to_owned(), None);
    }

    fn spawn_alert(self: &Arc<Self>, id: String, text: Option<String>) {
        if tokio::runtime::Handle::try_current().is_err() {
            return;
        }
        let me = Arc::clone(self);
        tokio::spawn(async move { me.alert_stage(&id, text.as_deref()).await });
    }

    pub async fn alert_stage(&self, alert_id: &str, text: Option<&str>) {
        if !self.configured() {
            return;
        }
        let done = match text {
            Some(t) => self.set_stage_message(t, Some(alert_id)).await,
            // Only the alert that wrote the message takes it down: a newer alert, or the operator's own message, is not wiped by an older alert ending.
            None if self.st().stage_owner.as_deref() == Some(alert_id) => self.clear_stage_message().await,
            None => Ok(()),
        };
        match done {
            Ok(()) => {}
            Err(e) if e.kind == Kind::ViewOnly => {
                let first = !std::mem::replace(&mut self.st().warned_view_only, true);
                if first {
                    tracing::info!("An alert asked for the ProPresenter stage display, but view-only mode is on");
                }
            }
            Err(e) => tracing::warn!("Could not update the ProPresenter stage display for an alert: {e}"),
        }
    }
}

/// `(presentation UUID, cue index)` from an `announcement/slide_index` body; `(None, None)` when none is up.
fn announcement_position(data: &Value) -> (Option<String>, Option<i64>) {
    let idx = &data["announcement_index"];
    if !idx.is_object() {
        return (None, None);
    }
    (idx["presentation_id"]["uuid"].as_str().filter(|u| !u.is_empty()).map(str::to_owned), idx["index"].as_i64())
}

/// Percent-encodes a path segment (what `urllib.parse.quote` leaves alone: letters, digits and `_.-~`).
pub fn encode(segment: &str) -> String {
    let mut out = String::new();
    for b in segment.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-' | b'~' | b'/') {
            out.push(char::from(b));
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// The keys of `item()` in the order Python writes them.
fn reorder_item(v: Value) -> Value {
    let mut out = Map::new();
    for k in ["title", "slide_number", "slide_count", "part", "part_color", "label", "notes", "next", "playlist", "playlist_item"] {
        if let Some(x) = v.get(k) {
            out.insert(k.to_owned(), x.clone());
        }
    }
    Value::Object(out)
}
