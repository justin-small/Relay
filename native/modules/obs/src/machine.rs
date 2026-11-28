//! One OBS computer (`service.py`'s `Machine`): its held connection, what it last said, its health and its caption queue.
//!
//! Each machine keeps a single obs-websocket connection for everything Relay does with it: following the scene and the outputs, switching scenes (directly
//! or through studio mode), starting and stopping the stream, recording and replay buffer, audio meters and mute, the admin preview and the caption
//! sender. It reconnects with a growing wait, polls the stream, record and stats every `poll`, publishes its own entry in the shared stream status and,
//! when scenes are mapped to camera numbers, in the shared tally.
//!
//! Health, per machine (fixed, not settings):
//! * error: OBS cannot be reached, or the stream stopped without anyone stopping it (it went from live to stopped without passing through "stopping"),
//!   shown for `unexpected_s` unless the stream starts again;
//! * warn: the stream is reconnecting, over 1% of frames were dropped (network), skipped while encoding or lagged while rendering over the last minute,
//!   or a watched audio input marked "warn if muted" is muted while streaming;
//! * ok: streaming or recording with none of the above;
//! * idle: connected and neither streaming nor recording.
//!
//! A machine can raise one of the operator's alert buttons when it turns error, and another when it turns warn: once per problem, never repeated while it
//! lasts. "Cannot be reached" raises only after the machine was connected in this run, so a record PC that is switched off all week does not alert at every
//! start. The stream key is never read into Relay: of OBS's stream settings only the service's name and YouTube's broadcast id are kept.

use std::collections::{BTreeSet, HashMap, VecDeque};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError, Weak};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use relay_core::caps::{AlertButtonsCap, StreamEntry, StreamsCap, TallyCap, ALERT_BUTTONS, STREAMS, TALLY};
use relay_core::text::same_name;
use relay_core::Host;
use serde_json::{json, Value};
use tokio::sync::{Mutex as AsyncMutex, Notify};

use crate::proto::{Connection, ObsError, OnEvent, EVENTS, METERS};
use crate::settings::{caption_lines, text, CAPTION_LINE, SOURCE_LANGUAGE};

/// Called when this computer's scenes may have changed: the service tells core the channels again.
pub type Reporter = Arc<dyn Fn() + Send + Sync>;

pub const MAX_INPUTS: usize = 64;
pub const CAPTION_QUEUE: usize = 20;
pub const PREVIEW_WIDTH: u32 = 480;
pub const HISTORY_S: f64 = 15.0 * 60.0;
pub const METER_HOLD_S: f64 = 1.0;
pub const FLOOR_DB: f64 = -60.0;
/// More than 1% dropped or skipped frames: warn.
pub const DROP_WARN: f64 = 0.01;

/// The waits, shortened by a test so it takes milliseconds, not minutes.
#[derive(Clone, Debug)]
pub struct Timing {
    pub poll: Duration,
    pub retry: Vec<Duration>,
    pub caption_gap: Duration,
    pub pause_check: Duration,
    /// Frames are judged over this long.
    pub window: Duration,
    /// How long an unexpected stop stays an error.
    pub unexpected: Duration,
    /// A screenshot is taken at most this often, however many pages ask.
    pub preview_min: Duration,
}

impl Default for Timing {
    fn default() -> Self {
        Self {
            poll: Duration::from_secs(2),
            retry: [2, 4, 8, 15, 30].map(Duration::from_secs).to_vec(),
            caption_gap: Duration::from_millis(400),
            pause_check: Duration::from_millis(400),
            window: Duration::from_secs(60),
            unexpected: Duration::from_secs(600),
            preview_min: Duration::from_millis(500),
        }
    }
}

/// What a computer's saved settings say, in a form the code reads without digging in JSON.
#[derive(Clone, Debug)]
pub struct Cfg {
    pub id: String,
    pub name: String,
    pub host: String,
    pub port: u16,
    pub captions: bool,
    pub caption_lang: String,
    pub caption_delay: f64,
    pub start_record: bool,
    pub start_stream: bool,
    pub preview: bool,
    pub alert_error: String,
    pub alert_warn: String,
    pub tally: Vec<(i64, String)>,
    pub audio: Vec<(String, bool)>,
}

impl Cfg {
    pub fn from(v: &Value) -> Self {
        let s = |k: &str| v[k].as_str().unwrap_or("").to_owned();
        Self {
            id: s("id"),
            name: s("name"),
            host: s("host"),
            port: v["port"].as_u64().and_then(|p| u16::try_from(p).ok()).unwrap_or(crate::settings::DEFAULT_PORT),
            captions: v["captions"] == Value::Bool(true),
            caption_lang: s("caption_lang"),
            caption_delay: v["caption_delay"].as_f64().unwrap_or(0.0),
            start_record: v["start_record"] == Value::Bool(true),
            start_stream: v["start_stream"] == Value::Bool(true),
            preview: v["preview"] == Value::Bool(true),
            alert_error: s("alert_error"),
            alert_warn: s("alert_warn"),
            tally: v["tally"].as_array().into_iter().flatten().filter_map(|t| Some((t["n"].as_i64()?, t["scene"].as_str()?.to_owned()))).collect(),
            audio: v["audio"].as_array().into_iter().flatten().filter_map(|a| Some((a["name"].as_str()?.to_owned(), a["warn_muted"] == Value::Bool(true)))).collect(),
        }
    }
}

fn epoch() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn int(v: &Value) -> i64 {
    v.as_i64().or_else(|| v.as_f64().map(|f| f as i64)).unwrap_or(0)
}

fn float(v: &Value) -> f64 {
    v.as_f64().unwrap_or(0.0)
}

fn flag(v: &Value) -> bool {
    match v {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Null => false,
        _ => true,
    }
}

fn db(mul: f64) -> f64 {
    if mul > 0.0 { (20.0 * mul.log10()).max(FLOOR_DB) } else { FLOOR_DB }
}

fn round1(f: f64) -> f64 {
    (f * 10.0).round() / 10.0
}

/// The kinds of request each output takes.
pub fn output_request(which: &str, action: &str) -> Option<&'static str> {
    Some(match (which, action) {
        ("stream", "start") => "StartStream",
        ("stream", "stop") => "StopStream",
        ("record", "start") => "StartRecord",
        ("record", "stop") => "StopRecord",
        ("record", "pause") => "PauseRecord",
        ("record", "resume") => "ResumeRecord",
        ("replay", "start") => "StartReplayBuffer",
        ("replay", "stop") => "StopReplayBuffer",
        ("replay", "save") => "SaveReplayBuffer",
        _ => return None,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Link {
    Connecting,
    Connected,
    Unreachable,
}

impl Link {
    fn word(self) -> &'static str {
        match self {
            Link::Connecting => "connecting",
            Link::Connected => "connected",
            Link::Unreachable => "unreachable",
        }
    }
}

struct State {
    link: Link,
    ever_connected: bool,
    error: String,
    version: String,
    scenes: Vec<String>,
    program: String,
    studio: bool,
    preview_scene: String,
    stream: Value,
    record: Value,
    stats: Value,
    replay_available: bool,
    replay_active: bool,
    replay_last: String,
    service: String,
    youtube: String,
    inputs: Vec<String>,
    muted: HashMap<String, bool>,
    /// name -> (dB now, peak dB held, when the peak was held)
    levels: HashMap<String, (f64, f64, Option<Instant>)>,
    stream_state: String,
    unexpected_at: f64,
    /// (seconds since the machine began, skipped, total, render skipped, render total, encode skipped, encode total)
    samples: VecDeque<[f64; 7]>,
    history: VecDeque<(f64, i64, f64, f64)>,
    last_bytes: Option<(f64, i64, i64, i64)>,
    alerted: &'static str,
    captions_sent: u64,
    caption_error: String,
    started_by_start: BTreeSet<&'static str>,
    shot: Option<(Instant, Vec<u8>)>,
}

impl State {
    fn new() -> Self {
        Self {
            link: Link::Connecting,
            ever_connected: false,
            error: String::new(),
            version: String::new(),
            scenes: Vec::new(),
            program: String::new(),
            studio: false,
            preview_scene: String::new(),
            stream: json!({}),
            record: json!({}),
            stats: json!({}),
            replay_available: false,
            replay_active: false,
            replay_last: String::new(),
            service: String::new(),
            youtube: String::new(),
            inputs: Vec::new(),
            muted: HashMap::new(),
            levels: HashMap::new(),
            stream_state: String::new(),
            unexpected_at: 0.0,
            samples: VecDeque::new(),
            history: VecDeque::new(),
            last_bytes: None,
            alerted: "",
            captions_sent: 0,
            caption_error: String::new(),
            started_by_start: BTreeSet::new(),
            shot: None,
        }
    }
}

pub struct Machine {
    pub cfg: Cfg,
    password: String,
    /// The scene screen: whether it is on, and its key.
    screen: Mutex<(bool, String)>,
    host: Host,
    report: Reporter,
    timing: Timing,
    me: Weak<Machine>,
    base: Instant,
    state: Mutex<State>,
    conn: Mutex<Option<Arc<Connection>>>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    captions: Mutex<VecDeque<String>>,
    caption_ready: Notify,
    shot_lock: AsyncMutex<()>,
}

impl Machine {
    pub fn new(host: Host, cfg: &Value, password: String, timing: Timing, report: Reporter) -> Arc<Self> {
        let screen = (cfg["screen_on"] == Value::Bool(true), cfg["key"].as_str().unwrap_or("").to_owned());
        Arc::new_cyclic(|me| Self {
            cfg: Cfg::from(cfg),
            password,
            screen: Mutex::new(screen),
            host,
            report,
            timing,
            me: me.clone(),
            base: Instant::now(),
            state: Mutex::new(State::new()),
            conn: Mutex::new(None),
            task: Mutex::new(None),
            captions: Mutex::new(VecDeque::new()),
            caption_ready: Notify::new(),
            shot_lock: AsyncMutex::new(()),
        })
    }

    pub fn id(&self) -> &str {
        &self.cfg.id
    }

    /// The module id the stream status and tally know this computer by.
    pub fn source(&self) -> String {
        format!("obs_{}", self.cfg.id)
    }

    pub fn screen(&self) -> (bool, String) {
        lock(&self.screen).clone()
    }

    pub fn set_screen(&self, on: bool, key: &str) {
        *lock(&self.screen) = (on, key.to_owned());
    }

    fn mono(&self) -> f64 {
        self.base.elapsed().as_secs_f64()
    }

    pub fn connected_conn(&self) -> Option<Arc<Connection>> {
        lock(&self.conn).clone()
    }

    pub fn is_connected(&self) -> bool {
        lock(&self.state).link == Link::Connected
    }

    pub fn has_connection(&self) -> bool {
        lock(&self.conn).is_some()
    }

    pub fn scenes(&self) -> Vec<String> {
        lock(&self.state).scenes.clone()
    }

    pub fn program(&self) -> String {
        lock(&self.state).program.clone()
    }

    pub fn streaming(&self) -> bool {
        flag(&lock(&self.state).stream["outputActive"])
    }

    pub fn recording(&self) -> bool {
        flag(&lock(&self.state).record["outputActive"])
    }

    pub fn preview_scene(&self) -> String {
        lock(&self.state).preview_scene.clone()
    }

    pub fn captions_sent(&self) -> u64 {
        lock(&self.state).captions_sent
    }

    pub fn caption_error(&self) -> String {
        lock(&self.state).caption_error.clone()
    }

    // ---------------------------------------------------------------------------------------------------------- life cycle
    pub fn start(self: &Arc<Self>) {
        let mut task = lock(&self.task);
        if self.cfg.host.is_empty() || task.as_ref().is_some_and(|t| !t.is_finished()) {
            return;
        }
        *task = Some(tokio::spawn(Arc::clone(self).run()));
    }

    pub async fn stop(&self) {
        let task = lock(&self.task).take();
        if let Some(t) = task {
            t.abort();
            let _ = t.await;
        }
        let conn = lock(&self.conn).take();
        if let Some(c) = conn {
            c.close().await;
        }
        if let Some(s) = self.host.using::<StreamsCap>(STREAMS) {
            s.0.clear(&self.source());
        }
        if let Some(t) = self.host.using::<TallyCap>(TALLY) {
            t.0.clear(&self.source());
        }
    }

    async fn run(self: Arc<Self>) {
        let mut attempt = 0usize;
        let events = EVENTS | if self.cfg.audio.is_empty() { 0 } else { METERS };
        loop {
            let hear: OnEvent = {
                let me = self.me.clone();
                Arc::new(move |kind: &str, data: Value| {
                    if let Some(m) = me.upgrade() {
                        m.event(kind, &data);
                    }
                })
            };
            let outcome = match Connection::open(self.host.net(), &self.cfg.host, self.cfg.port, &self.password, Some(hear), events).await {
                Ok(conn) => {
                    *lock(&self.conn) = Some(Arc::clone(&conn));
                    attempt = 0;
                    let r = self.session(&conn).await;
                    conn.close().await;
                    r
                }
                Err(e) => Err(e),
            };
            {
                let mut st = lock(&self.state);
                st.error = outcome.err().map_or_else(|| "The connection to OBS closed.".to_owned(), |e| e.0);
                st.link = Link::Unreachable;
                st.samples.clear();
                st.last_bytes = None;
            }
            *lock(&self.conn) = None;
            self.publish();
            tokio::time::sleep(self.timing.retry[attempt.min(self.timing.retry.len() - 1)]).await;
            attempt += 1;
        }
    }

    /// Everything while one connection lasts: read it all once, then poll until it closes. `Ok` is a closed connection; `Err` a failed first read.
    async fn session(self: &Arc<Self>, conn: &Arc<Connection>) -> Result<(), ObsError> {
        self.first_read(conn).await?;
        {
            let mut st = lock(&self.state);
            st.link = Link::Connected;
            st.error.clear();
            st.ever_connected = true;
        }
        self.publish();
        let sender = tokio::spawn(Arc::clone(self).send_captions(Arc::clone(conn)));
        let result = async {
            while !conn.is_closed() {
                self.poll(conn).await?;
                self.publish();
                tokio::select! {
                    () = conn.closed() => {}
                    () = tokio::time::sleep(self.timing.poll) => {}
                }
            }
            Ok(())
        }
        .await;
        sender.abort();
        let _ = sender.await;
        result
    }

    // ---------------------------------------------------------------------------------------------------------- reading
    async fn first_read(&self, conn: &Connection) -> Result<(), ObsError> {
        let v = conn.request("GetVersion", None).await?;
        lock(&self.state).version = text(&v["obsVersion"], 20);
        self.read_scenes(conn).await?;
        if let Ok(d) = conn.request("GetStudioModeEnabled", None).await {
            lock(&self.state).studio = flag(&d["studioModeEnabled"]);
        }
        self.read_replay(conn).await;
        self.read_service(conn).await;
        self.read_inputs(conn).await?;
        self.poll(conn).await
    }

    async fn read_scenes(&self, conn: &Connection) -> Result<(), ObsError> {
        let d = conn.request("GetSceneList", None).await?;
        let mut scenes: Vec<(i64, String)> = d["scenes"].as_array().into_iter().flatten().filter_map(|s| Some((int(&s["sceneIndex"]), s["sceneName"].as_str()?.to_owned()))).collect();
        // OBS gives the top scene the highest index: show them in OBS's own order, top first.
        scenes.sort_by_key(|s| std::cmp::Reverse(s.0));
        {
            let mut st = lock(&self.state);
            st.scenes = scenes.into_iter().map(|s| s.1).take(200).collect();
            st.program = d["currentProgramSceneName"].as_str().unwrap_or("").to_owned();
            st.preview_scene = d["currentPreviewSceneName"].as_str().unwrap_or("").to_owned();
        }
        (self.report)();
        Ok(())
    }

    async fn read_replay(&self, conn: &Connection) {
        let r = conn.request("GetReplayBufferStatus", None).await;
        let mut st = lock(&self.state);
        match r {
            Ok(d) => (st.replay_available, st.replay_active) = (true, flag(&d["outputActive"])),
            Err(_) => (st.replay_available, st.replay_active) = (false, false), // not switched on in OBS's output settings
        }
    }

    /// Only the service's name and YouTube's broadcast id: the key and server never leave this function.
    async fn read_service(&self, conn: &Connection) {
        let Ok(d) = conn.request("GetStreamServiceSettings", None).await else { return };
        let kind = d["streamServiceType"].as_str().unwrap_or("");
        let ss = &d["streamServiceSettings"];
        let name = if kind == "rtmp_common" { text(&ss["service"], 40) } else if kind.is_empty() { String::new() } else { "Custom server".to_owned() };
        let bid = ss["broadcast_id"].as_str().filter(|b| b.len() == 11 && b.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')).unwrap_or("");
        let mut st = lock(&self.state);
        st.youtube = if name.to_lowercase().contains("youtube") { bid.to_owned() } else { String::new() };
        st.service = name;
    }

    /// Every input that has audio (`GetInputMute` answers only for those), and the mute of each.
    async fn read_inputs(&self, conn: &Connection) -> Result<(), ObsError> {
        let d = conn.request("GetInputList", None).await?;
        let names: Vec<String> = d["inputs"].as_array().into_iter().flatten().filter_map(|i| i["inputName"].as_str().map(str::to_owned)).take(MAX_INPUTS).collect();
        let (mut audio, mut muted) = (Vec::new(), HashMap::new());
        for n in names {
            if let Ok(m) = conn.request("GetInputMute", Some(json!({"inputName": n}))).await {
                muted.insert(n.clone(), flag(&m["inputMuted"]));
                audio.push(n);
            }
        }
        let mut st = lock(&self.state);
        st.inputs = audio;
        st.muted = muted;
        Ok(())
    }

    async fn poll(&self, conn: &Connection) -> Result<(), ObsError> {
        let st_ = conn.request("GetStreamStatus", None).await?;
        let rec = conn.request("GetRecordStatus", None).await?;
        let stats = conn.request("GetStats", None).await?;
        let (now, mono) = (epoch(), self.mono());
        let mut st = lock(&self.state);
        st.stream = st_.clone();
        st.record = rec;
        st.stats = stats.clone();
        while st.history.front().is_some_and(|h| now - h.0 > HISTORY_S) {
            st.history.pop_front();
        }
        if !flag(&st_["outputActive"]) {
            st.samples.clear();
            st.last_bytes = None;
            return Ok(());
        }
        if st.stream_state.is_empty() || st.stream_state == "OBS_WEBSOCKET_OUTPUT_STOPPED" {
            st.stream_state = "OBS_WEBSOCKET_OUTPUT_STARTED".to_owned();
        }
        let (nbytes, skipped, total) = (int(&st_["outputBytes"]), int(&st_["outputSkippedFrames"]), int(&st_["outputTotalFrames"]));
        if let Some(last) = st.last_bytes {
            if mono > last.0 && nbytes >= last.1 && total >= last.3 {
                let frames = total - last.3;
                let kbps = ((nbytes - last.1) as f64 * 8.0 / 1000.0 / (mono - last.0)).round() as i64;
                let drop = if frames > 0 { ((skipped - last.2) as f64 * 100.0 / frames as f64 * 100.0).round() / 100.0 } else { 0.0 };
                let row = (now, kbps, round1(float(&st_["outputCongestion"]) * 100.0), drop);
                st.history.push_back(row);
            }
        }
        st.last_bytes = Some((mono, nbytes, skipped, total));
        let row = [mono, skipped as f64, total as f64, int(&stats["renderSkippedFrames"]) as f64, int(&stats["renderTotalFrames"]) as f64, int(&stats["outputSkippedFrames"]) as f64, int(&stats["outputTotalFrames"]) as f64];
        if st.samples.back().is_some_and(|last| (1..7).any(|i| row[i] < last[i])) {
            st.samples.clear(); // counters reset: a new stream
        }
        st.samples.push_back(row);
        let window = self.timing.window.as_secs_f64();
        while st.samples.len() > 2 && row[0] - st.samples[1][0] >= window {
            st.samples.pop_front();
        }
        Ok(())
    }

    /// One event from OBS, on the reader's task.
    pub fn event(self: &Arc<Self>, kind: &str, data: &Value) {
        if kind == "InputVolumeMeters" {
            self.meters(data);
            return; // 20 a second: never publish from here
        }
        let mut later: Option<Later> = None;
        {
            let mut st = lock(&self.state);
            match kind {
                "CurrentProgramSceneChanged" => {
                    if let Some(n) = data["sceneName"].as_str().filter(|n| !n.is_empty()) {
                        st.program = n.to_owned();
                    }
                }
                "CurrentPreviewSceneChanged" => st.preview_scene = data["sceneName"].as_str().unwrap_or("").to_owned(),
                "StudioModeStateChanged" => {
                    st.studio = flag(&data["studioModeEnabled"]);
                    if st.studio {
                        later = Some(Later::Scenes);
                    } else {
                        st.preview_scene.clear();
                    }
                }
                "SceneListChanged" | "SceneCreated" | "SceneRemoved" | "SceneNameChanged" => {
                    later = Some(Later::Scenes);
                }
                "InputCreated" | "InputRemoved" | "InputNameChanged" => {
                    later = Some(Later::Inputs);
                }
                "InputMuteStateChanged" => {
                    if let Some(n) = data["inputName"].as_str() {
                        if let Some(m) = st.muted.get_mut(n) {
                            *m = flag(&data["inputMuted"]);
                        }
                    }
                }
                "StreamStateChanged" => {
                    let new = data["outputState"].as_str().unwrap_or("").to_owned();
                    if new == "OBS_WEBSOCKET_OUTPUT_STOPPED" && !matches!(st.stream_state.as_str(), "OBS_WEBSOCKET_OUTPUT_STOPPING" | "OBS_WEBSOCKET_OUTPUT_STOPPED") {
                        st.unexpected_at = epoch();
                    } else if new == "OBS_WEBSOCKET_OUTPUT_STARTING" || new == "OBS_WEBSOCKET_OUTPUT_STARTED" {
                        st.unexpected_at = 0.0;
                        if new == "OBS_WEBSOCKET_OUTPUT_STARTING" {
                            later = Some(Later::Service); // the broadcast OBS picked may have changed
                        }
                    }
                    st.stream_state = new;
                    st.stream["outputActive"] = json!(flag(&data["outputActive"]));
                }
                "RecordStateChanged" => {
                    st.record["outputActive"] = json!(flag(&data["outputActive"]));
                    st.record["outputPaused"] = json!(data["outputState"] == json!("OBS_WEBSOCKET_OUTPUT_PAUSED"));
                }
                "ReplayBufferStateChanged" => (st.replay_available, st.replay_active) = (true, flag(&data["outputActive"])),
                "ReplayBufferSaved" => {
                    if let Some(p) = data["savedReplayPath"].as_str() {
                        st.replay_last = p.replace('\\', "/").rsplit('/').next().unwrap_or("").chars().take(120).collect();
                    }
                }
                _ => {}
            }
        }
        if let Some(l) = later {
            self.later(l);
        }
        self.publish();
    }

    fn meters(&self, data: &Value) {
        let now = Instant::now();
        let mut st = lock(&self.state);
        for i in data["inputs"].as_array().into_iter().flatten() {
            let Some(name) = i["inputName"].as_str().filter(|n| self.cfg.audio.iter().any(|a| a.0 == *n)) else { continue };
            let peak = i["inputLevelsMul"].as_array().into_iter().flatten().filter_map(|c| c.as_array().filter(|c| c.len() >= 2).and_then(|c| c[1].as_f64())).fold(None, |m: Option<f64>, v| Some(m.map_or(v, |m| m.max(v))));
            let level = peak.map_or(FLOOR_DB, db);
            let (_, mut held, mut at) = st.levels.get(name).copied().unwrap_or((FLOOR_DB, FLOOR_DB, None));
            if level >= held || at.is_none_or(|a| now.duration_since(a).as_secs_f64() > METER_HOLD_S) {
                (held, at) = (level, Some(now));
            }
            st.levels.insert(name.to_owned(), (level, held, at));
        }
    }

    fn later(self: &Arc<Self>, what: Later) {
        if lock(&self.conn).is_none() {
            return;
        }
        let me = Arc::clone(self);
        tokio::spawn(async move {
            let Some(conn) = me.connected_conn() else { return };
            match what {
                Later::Scenes => {
                    if me.read_scenes(&conn).await.is_ok() {
                        me.publish();
                    }
                }
                Later::Inputs => {
                    let _ = me.read_inputs(&conn).await;
                }
                Later::Service => me.read_service(&conn).await,
            }
        });
    }

    // ---------------------------------------------------------------------------------------------------------- health
    pub fn rates(&self) -> (f64, f64, f64) {
        let st = lock(&self.state);
        (rate(&st.samples, 1), rate(&st.samples, 3), rate(&st.samples, 5))
    }

    pub fn health(&self) -> (&'static str, String) {
        self.health_at(epoch())
    }

    pub fn health_at(&self, now: f64) -> (&'static str, String) {
        let where_ = &self.cfg.name;
        if self.cfg.host.is_empty() {
            return ("warn", format!("{where_}: no address"));
        }
        let (link, error, live, recording, unexpected_at, reconnecting) = {
            let st = lock(&self.state);
            (st.link, st.error.clone(), flag(&st.stream["outputActive"]), flag(&st.record["outputActive"]), st.unexpected_at, flag(&st.stream["outputReconnecting"]))
        };
        if link != Link::Connected {
            return if link == Link::Connecting {
                ("idle", format!("{where_}: connecting"))
            } else {
                ("error", format!("{where_}: {}", if error.is_empty() { "OBS cannot be reached." } else { &error }))
            };
        }
        if !live && unexpected_at > 0.0 && now - unexpected_at < self.timing.unexpected.as_secs_f64() {
            let mins = ((now - unexpected_at) / 60.0).floor() as i64;
            return ("error", format!("{where_}: the stream stopped by itself {}", if mins < 1 { "a moment ago".to_owned() } else { format!("{mins} min ago") }));
        }
        if live && reconnecting {
            return ("warn", format!("{where_}: the stream is reconnecting"));
        }
        if live {
            let st = lock(&self.state);
            let muted: Vec<&str> = self.cfg.audio.iter().filter(|a| a.1 && st.muted.get(&a.0) == Some(&true)).map(|a| a.0.as_str()).collect();
            if !muted.is_empty() {
                return ("warn", format!("{where_}: {} {} muted while live", muted.join(", "), if muted.len() == 1 { "is" } else { "are" }));
            }
        }
        if live || recording {
            let (net, render, encode) = self.rates();
            let bad: Vec<String> = [("network", net, "dropped (network)"), ("encode", encode, "skipped (encoding)"), ("render", render, "lagged (rendering)")]
                .iter()
                .filter(|(_, r, _)| *r > DROP_WARN)
                .map(|(_, r, w)| format!("{:.1}% of frames {w}", r * 100.0))
                .collect();
            if !bad.is_empty() {
                return ("warn", format!("{where_}: {} in the last minute", bad.join(", ")));
            }
            return ("ok", format!("{where_}: {}", if live { "streaming" } else { "recording" }));
        }
        ("idle", format!("{where_}: connected, not streaming"))
    }

    pub fn publish(&self) {
        if self.cfg.host.is_empty() {
            return;
        }
        let (health, detail) = self.health();
        let (live, since) = {
            let st = lock(&self.state);
            let live = if st.link == Link::Connected { Some(flag(&st.stream["outputActive"])) } else { None };
            (live, (live == Some(true)).then(|| epoch() - int(&st.stream["outputDuration"]) as f64 / 1000.0))
        };
        if let Some(s) = self.host.using::<StreamsCap>(STREAMS) {
            let _ = s.0.publish(&self.source(), StreamEntry { label: self.cfg.name.clone(), on_air: live, health: health.to_owned(), live_since: since, detail });
        }
        self.publish_tally();
        self.alert(health);
    }

    fn publish_tally(&self) {
        let Some(t) = self.host.using::<TallyCap>(TALLY) else { return };
        let st = lock(&self.state);
        if self.cfg.tally.is_empty() || st.link != Link::Connected {
            t.0.clear(&self.source()); // unknown is not "off air"
            return;
        }
        let cams = self.cfg.tally.iter().map(|(n, scene)| json!({"n": n, "name": scene, "program": *scene == st.program, "preview": st.studio && *scene == st.preview_scene})).collect();
        t.0.publish(&self.source(), cams);
    }

    /// Raise the operator's chosen alert button once when this machine turns error (or warn).
    fn alert(&self, health: &str) {
        let rank = |h: &str| match h {
            "warn" => 1,
            "error" => 2,
            _ => 0,
        };
        let bid = {
            let mut st = lock(&self.state);
            if health == "ok" || health == "idle" {
                st.alerted = "";
                return;
            }
            if health == "error" && st.link == Link::Unreachable && !st.ever_connected {
                return;
            }
            if rank(health) <= rank(st.alerted) {
                return;
            }
            st.alerted = if health == "error" { "error" } else { "warn" };
            if health == "error" { self.cfg.alert_error.clone() } else { self.cfg.alert_warn.clone() }
        };
        let Some(buttons) = self.host.using::<AlertButtonsCap>(ALERT_BUTTONS) else { return };
        if bid.is_empty() || !buttons.0.exists(&bid) || buttons.0.is_on(&bid) {
            return;
        }
        buttons.0.press(&bid, &format!("OBS ({})", self.cfg.name));
    }

    // ---------------------------------------------------------------------------------------------------------- acting
    fn connected(&self) -> Result<Arc<Connection>, ObsError> {
        lock(&self.conn).clone().ok_or_else(|| ObsError::new("OBS is not connected."))
    }

    /// OBS's own spelling of the scene called `name`, exact first, then any case; `""` when there is none.
    pub fn scene_named(&self, name: &str) -> String {
        let st = lock(&self.state);
        if st.scenes.iter().any(|s| s == name) {
            return name.to_owned();
        }
        st.scenes.iter().find(|s| same_name(s, name)).cloned().unwrap_or_default()
    }

    pub async fn set_scene(&self, scene: &str) -> Result<(), ObsError> {
        let conn = self.connected()?;
        if !lock(&self.state).scenes.iter().any(|s| s == scene) {
            return Err(ObsError::new("OBS has no scene with that name."));
        }
        conn.request("SetCurrentProgramScene", Some(json!({"sceneName": scene}))).await?;
        lock(&self.state).program = scene.to_owned();
        self.publish();
        Ok(())
    }

    pub async fn set_preview(&self, scene: &str) -> Result<(), ObsError> {
        let conn = self.connected()?;
        let known = {
            let st = lock(&self.state);
            if !st.studio {
                return Err(ObsError::new("Studio mode is off in OBS."));
            }
            st.scenes.iter().any(|s| s == scene)
        };
        if !known {
            return Err(ObsError::new("OBS has no scene with that name."));
        }
        conn.request("SetCurrentPreviewScene", Some(json!({"sceneName": scene}))).await?;
        lock(&self.state).preview_scene = scene.to_owned();
        self.publish();
        Ok(())
    }

    pub async fn take(&self) -> Result<(), ObsError> {
        let conn = self.connected()?;
        if !lock(&self.state).studio {
            return Err(ObsError::new("Studio mode is off in OBS."));
        }
        conn.request("TriggerStudioModeTransition", None).await.map(|_| ())
    }

    pub async fn set_studio(&self, on: bool) -> Result<(), ObsError> {
        let conn = self.connected()?;
        conn.request("SetStudioModeEnabled", Some(json!({"studioModeEnabled": on}))).await?;
        lock(&self.state).studio = on;
        if on {
            self.read_scenes(&conn).await?;
        } else {
            lock(&self.state).preview_scene.clear();
        }
        self.publish();
        Ok(())
    }

    /// Start, stop, pause or resume an output, or save the replay buffer. Sent once, never retried.
    pub async fn output(&self, which: &str, action: &str) -> Result<(), ObsError> {
        let Some(req) = output_request(which, action) else { return Err(ObsError::new("That is not something OBS can do.")) };
        if which == "replay" && !lock(&self.state).replay_available {
            return Err(ObsError::new("The replay buffer is off in OBS: switch it on under Settings > Output."));
        }
        let conn = self.connected()?;
        if which == "stream" && action == "stop" {
            lock(&self.state).stream_state = "OBS_WEBSOCKET_OUTPUT_STOPPING".to_owned(); // an operator's stop is not "stopped by itself"
        }
        conn.request(req, None).await?;
        if which == "record" && action == "pause" {
            // OBS accepts PauseRecord and does nothing when the recording cannot pause (Simple output mode with recording quality "Same as stream"), so
            // look before saying it worked.
            tokio::time::sleep(self.timing.pause_check).await;
            let rec = conn.request("GetRecordStatus", None).await?;
            let stuck = flag(&rec["outputActive"]) && !flag(&rec["outputPaused"]);
            lock(&self.state).record = rec;
            if stuck {
                return Err(ObsError::new("OBS did not pause the recording. In OBS's Simple output mode, set Recording Quality to anything but \"Same as stream\", or use Advanced mode."));
            }
        }
        Ok(())
    }

    /// Relay's Start was pressed: begin the outputs this computer is set to begin with it, if OBS is reachable and not already doing them. A failure is
    /// logged, never raised: Start goes ahead without it.
    pub async fn follow_start(&self) {
        for (which, wanted) in [("record", self.cfg.start_record), ("stream", self.cfg.start_stream)] {
            let busy = {
                let st = lock(&self.state);
                flag(&st.stream_or_record(which)["outputActive"])
            };
            if !wanted || !self.is_connected() || busy {
                continue;
            }
            match self.output(which, "start").await {
                Ok(()) => {
                    lock(&self.state).started_by_start.insert(if which == "record" { "record" } else { "stream" });
                    self.host.record("obs.start", &format!("OBS ({}): started the {which} because Start was pressed", self.cfg.name), self.id()).await;
                }
                Err(_) => tracing::warn!("OBS {}: could not start the {which} with Start", self.cfg.name),
            }
        }
    }

    /// Relay's Stop was pressed: end only what Start began here, never an output the operator started by hand.
    pub async fn follow_stop(&self) {
        let began: Vec<&'static str> = lock(&self.state).started_by_start.iter().copied().collect();
        for which in began {
            if !self.is_connected() {
                break;
            }
            match self.output(which, "stop").await {
                Ok(()) => self.host.record("obs.stop", &format!("OBS ({}): stopped the {which} because Stop was pressed", self.cfg.name), self.id()).await,
                Err(_) => tracing::warn!("OBS {}: could not stop the {which} with Stop", self.cfg.name),
            }
        }
        lock(&self.state).started_by_start.clear();
    }

    pub async fn set_mute(&self, name: &str, muted: bool) -> Result<(), ObsError> {
        let conn = self.connected()?;
        if !lock(&self.state).inputs.iter().any(|i| i == name) {
            return Err(ObsError::new("OBS has no audio input with that name."));
        }
        conn.request("SetInputMute", Some(json!({"inputName": name, "inputMuted": muted}))).await?;
        lock(&self.state).muted.insert(name.to_owned(), muted);
        self.publish();
        Ok(())
    }

    /// The watched inputs' meters and mutes.
    pub fn audio(&self) -> Vec<Value> {
        let (now, st) = (Instant::now(), lock(&self.state));
        self.cfg
            .audio
            .iter()
            .map(|(name, _)| {
                let (level, held, at) = st.levels.get(name).copied().unwrap_or((FLOOR_DB, FLOOR_DB, None));
                let fresh = at.is_some_and(|a| now.duration_since(a).as_secs_f64() <= METER_HOLD_S * 2.0);
                json!({"name": name, "present": st.muted.contains_key(name), "muted": st.muted.get(name) == Some(&true), "db": round1(level), "peak": round1(if fresh { held } else { level })})
            })
            .collect()
    }

    /// A small JPEG of the program scene, shared by every page asking within `preview_min`.
    pub async fn screenshot(&self) -> Result<Vec<u8>, ObsError> {
        let _one = self.shot_lock.lock().await;
        if let Some((at, img)) = lock(&self.state).shot.as_ref() {
            if !img.is_empty() && at.elapsed() < self.timing.preview_min {
                return Ok(img.clone());
            }
        }
        let conn = self.connected()?;
        let program = lock(&self.state).program.clone();
        if program.is_empty() {
            return Err(ObsError::new("OBS is not connected."));
        }
        let d = conn.request("GetSourceScreenshot", Some(json!({"sourceName": program, "imageFormat": "jpg", "imageWidth": PREVIEW_WIDTH, "imageCompressionQuality": 60}))).await?;
        let data = d["imageData"].as_str().unwrap_or("");
        let unreadable = || ObsError::new("OBS sent a picture Relay could not read.");
        let img = STANDARD.decode(data.split_once(',').map_or(data, |p| p.1)).map_err(|_| unreadable())?;
        if !img.starts_with(&[0xff, 0xd8]) {
            return Err(unreadable());
        }
        lock(&self.state).shot = Some((Instant::now(), img.clone()));
        Ok(img)
    }

    // ---------------------------------------------------------------------------------------------------------- captions
    pub fn wants(&self, stream: &str, lang: &str) -> bool {
        if !self.cfg.captions {
            return false;
        }
        if self.cfg.caption_lang == SOURCE_LANGUAGE {
            return stream == "transcription";
        }
        stream == "translation" && lang == self.cfg.caption_lang
    }

    /// A finished line, held for the caption delay (to line up with a delayed stream) before it is queued.
    pub fn caption_line(self: &Arc<Self>, line: String) {
        if self.cfg.caption_delay > 0.0 {
            let me = Arc::clone(self);
            let wait = Duration::from_secs_f64(self.cfg.caption_delay);
            tokio::spawn(async move {
                tokio::time::sleep(wait).await;
                me.queue_caption(line);
            });
        } else {
            self.queue_caption(line);
        }
    }

    pub fn queue_caption(&self, line: String) {
        if lock(&self.conn).is_none() || !self.streaming() {
            return; // nothing to caption: OBS refuses captions while not streaming
        }
        {
            let mut q = lock(&self.captions);
            while q.len() >= CAPTION_QUEUE {
                q.pop_front(); // behind: drop the oldest rather than fall further behind
            }
            q.push_back(line);
        }
        self.caption_ready.notify_one();
    }

    async fn send_captions(self: Arc<Self>, conn: Arc<Connection>) {
        loop {
            let next = lock(&self.captions).pop_front();
            let Some(text) = next else {
                self.caption_ready.notified().await;
                continue;
            };
            for line in caption_lines(&text, CAPTION_LINE) {
                let r = conn.request("SendStreamCaption", Some(json!({"captionText": line}))).await;
                {
                    let mut st = lock(&self.state);
                    match r {
                        Ok(_) => (st.captions_sent, st.caption_error) = (st.captions_sent + 1, String::new()),
                        Err(e) => st.caption_error = e.0,
                    }
                }
                tokio::time::sleep(self.timing.caption_gap).await;
            }
        }
    }

    // ---------------------------------------------------------------------------------------------------------- the channels core lists
    /// This computer's scenes as the channels it reports: each OBS scene is a video source channel in core, so a scene and the camera it shows can be named once.
    pub fn channels(&self) -> Vec<Value> {
        lock(&self.state).scenes.iter().map(|n| json!({"key": format!("{}:{n}", self.cfg.id), "kind": "video", "direction": "source", "gear_name": n})).collect()
    }

    // ---------------------------------------------------------------------------------------------------------- admin view
    pub fn snapshot(&self) -> Value {
        let (health, detail) = self.health();
        let (net, render, encode) = self.rates();
        let st = lock(&self.state);
        let (s, r, stats) = (&st.stream, &st.record, &st.stats);
        let yt = &st.youtube;
        json!({
            "id": self.cfg.id, "state": st.link.word(), "error": st.error, "health": health, "detail": detail,
            "version": st.version, "scenes": st.scenes, "program": st.program,
            "studio": st.studio, "preview_scene": st.preview_scene,
            "stream": {"active": flag(&s["outputActive"]), "reconnecting": flag(&s["outputReconnecting"]), "duration_ms": int(&s["outputDuration"]),
                       "bytes": int(&s["outputBytes"]), "congestion": float(&s["outputCongestion"]), "skipped": int(&s["outputSkippedFrames"]), "total": int(&s["outputTotalFrames"])},
            "record": {"active": flag(&r["outputActive"]), "paused": flag(&r["outputPaused"]), "duration_ms": int(&r["outputDuration"]), "bytes": int(&r["outputBytes"])},
            "replay": {"available": st.replay_available, "active": st.replay_active, "last": st.replay_last},
            "stats": {"cpu": round1(float(&stats["cpuUsage"])), "memory_mb": float(&stats["memoryUsage"]).round() as i64, "fps": round1(float(&stats["activeFps"])),
                      "render_ms": round1(float(&stats["averageFrameRenderTime"])), "disk_mb": float(&stats["availableDiskSpace"]).round() as i64,
                      "render_skipped": int(&stats["renderSkippedFrames"]), "render_total": int(&stats["renderTotalFrames"]),
                      "encode_skipped": int(&stats["outputSkippedFrames"]), "encode_total": int(&stats["outputTotalFrames"])},
            "minute": {"network": (net * 10000.0).round() / 100.0, "render": (render * 10000.0).round() / 100.0, "encode": (encode * 10000.0).round() / 100.0},
            "history": st.history.iter().map(|h| json!([h.0, h.1, h.2, h.3])).collect::<Vec<_>>(),
            "service": st.service,
            "youtube": if yt.is_empty() { Value::Null } else { json!({"id": yt, "watch": format!("https://www.youtube.com/watch?v={yt}"), "studio": format!("https://studio.youtube.com/video/{yt}/livestreaming")}) },
            "inputs": st.inputs,
            "captions_sent": st.captions_sent, "caption_error": st.caption_error,
        })
    }
}

enum Later {
    Scenes,
    Inputs,
    Service,
}

impl State {
    fn stream_or_record(&self, which: &str) -> &Value {
        if which == "record" { &self.record } else { &self.stream }
    }
}

/// Skipped over total for counter pair `i` between the oldest and newest sample in the window.
fn rate(samples: &VecDeque<[f64; 7]>, i: usize) -> f64 {
    if samples.len() < 2 {
        return 0.0;
    }
    let (a, b) = (samples[0], samples[samples.len() - 1]);
    let total = b[i + 1] - a[i + 1];
    if total > 0.0 { (b[i] - a[i]) / total } else { 0.0 }
}
