//! What every page reads about the running relay, and what Start and Stop mean (`app/engine.py`, `app/stream.py` and
//! `app/modules/captions/pipeline.py`).
//!
//! **The stream** is the show: a running flag and a clock, opened by Start, with captions as one part of it while the operator wants
//! them (`stream_captions`, on by default). **The pipeline** is the captions part: one capture, one transcription session, one
//! translator. **The engine's status** is what the admin pages and the viewers are told: the stream, the meter, the targets.
//!
//! The pieces that live outside captions come in through traits: the settings ([`Settings`]), the audio capture ([`Capture`]), the
//! chat model ([`Chat`]), the socket ([`Connect`]) and what the other modules add to a page ([`Outside`]). The server supplies the
//! real ones; a test supplies its own.

use std::path::PathBuf;
use std::sync::{Arc, Mutex, MutexGuard, Weak};

use relay_core::hub::{Hub, LineEvent};
use serde_json::{json, Map, Value};
use tokio::task::JoinHandle;

use crate::aimodels::{self, Route};
use crate::clock;
use crate::recorder::Recorder;
use crate::redact::{self, read_blocklist_file};
use crate::session::{AudioFeed, Connect, Session};
use crate::transcribe::{Backend, Backends};
use crate::translate::{Chat, Setup, Translator};

pub const TRANSCRIPTION_STREAM: &str = "transcription";
pub const TRANSLATION_STREAM: &str = "translation";
pub const STARTED: &str = "stream.started";
pub const STOPPED: &str = "stream.stopped";

pub const REHEARSAL_REFUSAL: &str = "Rehearsal mode: capture is disabled so nothing is billed. The viewer pages are showing canned captions. Restart Relay without RELAY_DEMO=1 to go live.";

/// A fixed sentence for each way a start can be refused, by key. `capture` has none: the audio device's own sentence is in the
/// capture's error.
pub fn start_message(key: &str) -> Option<&'static str> {
    Some(match key {
        "rehearsal" => REHEARSAL_REFUSAL,
        "no_key" => "No OpenAI API key set. Add one in the admin panel.",
        "no_vercel_key" => "No Vercel AI Gateway API key set. Add one in the admin panel, or pick OpenAI to transcribe.",
        "no_translation_key" => "Captions are running, but translating them needs a Vercel AI Gateway key or an OpenAI key. Add one in the admin panel.",
        "no_backend_key" => "The transcription backend needs a key that is not set. Add it in the admin panel.",
        _ => return None,
    })
}

/// Why a start was refused: `key` names its sentence in [`start_message`].
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct StartRefused {
    pub key: &'static str,
    pub message: String,
}

/// Where the settings live (config.json, with its secrets sealed): the server's.
pub trait Settings: Send + Sync + 'static {
    /// The whole settings object, as `config.get()` returns it.
    fn get(&self) -> Value;
    /// Merge a patch in and save it (`config.save`).
    fn save(&self, patch: &Value);
    fn blocklist_path(&self) -> PathBuf;
    fn recordings_path(&self) -> PathBuf;
}

/// The audio capture: the room's sound, resampled to 24 kHz and fanned out to every consumer (see [`crate::capture`] for the
/// meter, gate and resampler it is built from).
pub trait Capture: Send + Sync + 'static {
    /// Open the input lane. The error is the capture's own fixed sentence, naming the device.
    fn start(&self, lane: &str, gate_silence: bool) -> Result<(), String>;
    fn stop(&self);
    /// `running`, `device`, `level`, `rms_dbfs`, `peak_dbfs`, `clipping`, `clipped_samples`, `speaking`, and the rest.
    fn status(&self) -> Value;
    fn add_consumer(&self) -> (u64, AudioFeed);
    fn remove_consumer(&self, id: u64);
}

/// What the rest of Relay adds to the pages captions feed, and the things captions tells it.
pub trait Outside: Send + Sync + 'static {
    /// Rehearsal mode (`RELAY_DEMO=1`).
    fn demo(&self) -> bool {
        crate::demo::demo_mode()
    }
    fn schedule_status(&self) -> Value {
        json!({})
    }
    /// What the stream-watching modules say, with their reasons (admin only).
    fn streams(&self) -> Value {
        json!([])
    }
    /// Top bar pills the modules that are on ask for.
    fn pills(&self) -> Value {
        json!([])
    }
    /// Each module's health state for the sidebar's dots.
    fn module_health(&self) -> Value {
        json!({})
    }
    /// Whatever the modules that are on add to the viewers' payload; these override the empty defaults.
    fn module_status(&self) -> Map<String, Value> {
        Map::new()
    }
    /// `stream.started` / `stream.stopped`, for every module that follows Start.
    fn announce(&self, _event: &str) {}
}

/// An `Outside` that adds nothing.
pub struct Alone;
impl Outside for Alone {}

/// The parts the engine is made of, as the server hands them over.
pub struct Parts {
    pub hub: Arc<Hub>,
    pub settings: Arc<dyn Settings>,
    pub capture: Arc<dyn Capture>,
    pub chat: Arc<dyn Chat>,
    pub connect: Arc<dyn Connect>,
    pub outside: Arc<dyn Outside>,
    pub backends: Backends,
    pub recorder: Recorder,
}

#[derive(Default)]
struct View {
    running: bool,
    started_at: Option<f64>,
    last_error: Option<String>,
    backend: Option<BackendView>,
    translation_route: Option<Route>,
    session: Option<Arc<Session>>,
    translator: Option<Translator>,
    consumer: Option<u64>,
    stream_running: bool,
    stream_started_at: Option<f64>,
    /// The Captions module is on: it has added itself to the stream.
    attached: bool,
    demo: Option<JoinHandle<()>>,
}

#[derive(Clone)]
struct BackendView {
    id: String,
    label: String,
    price_per_hour: Option<f64>,
    warning: Option<String>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

fn title(s: &str) -> String {
    let mut c = s.chars();
    c.next().map_or(String::new(), |f| f.to_uppercase().collect::<String>() + &c.as_str().to_lowercase())
}

pub fn target_label(target: &str) -> String {
    relay_core::languages::target(target).map_or_else(|| title(target), |l| l.label.to_owned())
}

pub fn target_script(target: &str) -> String {
    relay_core::languages::target(target).map_or("latin".to_owned(), |l| l.script.to_owned())
}

/// The enabled targets in `cfg`, by name.
fn enabled_targets(cfg: &Value) -> Vec<String> {
    let mut out: Vec<String> = cfg
        .get("targets")
        .and_then(Value::as_object)
        .map(|t| t.iter().filter(|(_, spec)| spec.get("enabled").and_then(Value::as_bool).unwrap_or(false)).map(|(n, _)| n.clone()).collect())
        .unwrap_or_default();
    out.sort();
    out
}

fn source_language(cfg: &Value) -> String {
    cfg.get("source_language").and_then(Value::as_str).unwrap_or("ENGLISH").to_owned()
}

/// Live captions: the engine, the stream and the pipeline.
pub struct Captions {
    weak: Weak<Captions>,
    hub: Arc<Hub>,
    settings: Arc<dyn Settings>,
    capture: Arc<dyn Capture>,
    chat: Arc<dyn Chat>,
    connect: Arc<dyn Connect>,
    outside: Arc<dyn Outside>,
    backends: Mutex<Backends>,
    recorder: Arc<Mutex<Recorder>>,
    view: Mutex<View>,
    /// One start, stop or change at a time (the pipeline's `_lock`).
    op: tokio::sync::Mutex<()>,
    /// Start and Stop of the stream.
    stream_op: tokio::sync::Mutex<()>,
}

impl Captions {
    pub fn new(parts: Parts) -> Arc<Self> {
        Arc::new_cyclic(|weak| Self {
            weak: weak.clone(),
            hub: parts.hub,
            settings: parts.settings,
            capture: parts.capture,
            chat: parts.chat,
            connect: parts.connect,
            outside: parts.outside,
            backends: Mutex::new(parts.backends),
            recorder: Arc::new(Mutex::new(parts.recorder)),
            view: Mutex::new(View::default()),
            op: tokio::sync::Mutex::new(()),
            stream_op: tokio::sync::Mutex::new(()),
        })
    }

    pub fn hub(&self) -> &Arc<Hub> {
        &self.hub
    }

    pub fn recorder(&self) -> &Arc<Mutex<Recorder>> {
        &self.recorder
    }

    pub fn backends(&self) -> MutexGuard<'_, Backends> {
        lock(&self.backends)
    }

    /// The Captions module is switched on: it adds itself to the stream. Off: Start opens the stream and runs no captions.
    pub fn attach(&self) {
        lock(&self.view).attached = true;
    }

    /// The module is switched off. Its pipeline goes with it; [`Captions::shutdown`] stops what is running.
    pub fn detach(&self) {
        lock(&self.view).attached = false;
    }

    pub fn attached(&self) -> bool {
        lock(&self.view).attached
    }

    fn push_status_later(&self) -> Arc<dyn Fn() + Send + Sync> {
        let weak = self.weak.clone();
        Arc::new(move || {
            if let Some(me) = weak.upgrade() {
                me.push_status();
            }
        })
    }

    // ---- what the pipeline holds, or nothing while captions are off -------------------------------------------------------
    pub fn running(&self) -> bool {
        let v = lock(&self.view);
        v.attached && v.running
    }

    pub fn started_at(&self) -> Option<f64> {
        lock(&self.view).started_at
    }

    pub fn last_error(&self) -> Option<String> {
        let v = lock(&self.view);
        if v.attached {
            v.last_error.clone()
        } else {
            None
        }
    }

    fn sessions(&self) -> Vec<Value> {
        let v = lock(&self.view);
        if !v.attached {
            return Vec::new();
        }
        let mut out = Vec::new();
        if let Some(s) = &v.session {
            out.push(s.health());
        }
        if let Some(t) = &v.translator {
            out.push(t.health());
        }
        out
    }

    fn live_names(&self) -> Vec<String> {
        let v = lock(&self.view);
        if !v.attached {
            return Vec::new();
        }
        v.translator.as_ref().map(Translator::targets).unwrap_or_default()
    }

    /// The targets that are on now, as the viewers' picker lists them.
    pub fn live_targets(&self) -> Vec<Value> {
        let state = lock(&self.view).translator.as_ref().map(Translator::state).unwrap_or("idle");
        let mut out: Vec<(String, Value)> = self
            .live_names()
            .into_iter()
            .map(|t| {
                let (iso, label) = (crate::translate::iso(&t), target_label(&t));
                (label.clone(), json!({"target": t, "label": label, "iso": iso, "script": target_script(&t), "state": state}))
            })
            .collect();
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out.into_iter().map(|(_, v)| v).collect()
    }

    // ---- status -----------------------------------------------------------------------------------------------------------
    pub fn stream_status(&self) -> Value {
        let cfg = self.settings.get();
        let v = lock(&self.view);
        json!({
            "running": v.stream_running,
            "started_at": v.stream_started_at,
            "captions": cfg.get("stream_captions").and_then(Value::as_bool).unwrap_or(true),
            "captions_available": v.attached,
        })
    }

    pub fn status(&self) -> Value {
        let cfg = self.settings.get();
        let (running, backend, route, stream_running, stream_started) = {
            let v = lock(&self.view);
            (v.attached && v.running, v.backend.clone(), v.translation_route.clone(), v.stream_running, v.stream_started_at)
        };
        let live: Vec<String> = self.live_names();
        let backends = lock(&self.backends);
        let mut targets: Vec<(&String, &Value)> = cfg.get("targets").and_then(Value::as_object).map(|t| t.iter().collect()).unwrap_or_default();
        targets.sort_by(|a, b| a.0.cmp(b.0));
        json!({
            // The show is running: Start was pressed. Captions are one part of it, `captions_running`.
            "running": stream_running,
            "started_at": stream_started,
            "captions_running": running,
            "stream": self.stream_status(),
            "error": self.last_error(),
            // What transcribes the main feed, and the sentence for why it is not what the operator chose.
            "backend": if running { backend.as_ref().map(|b| json!({"id": b.id, "label": b.label, "warning": b.warning, "price_per_hour": b.price_per_hour})) } else { None },
            // Where translation runs, and the sentence for why it is not the provider the operator chose.
            "translation": if running { route.as_ref().map(|r| json!({"provider": r.provider, "label": r.label(), "model": r.model, "warning": r.warning})) } else { None },
            "backends": backends.list().iter().map(|b| json!({"id": b.id, "label": b.label, "available": b.available(&cfg), "price_per_hour": b.price_per_hour})).collect::<Vec<_>>(),
            "audio": self.capture_status(),
            "sessions": self.sessions(),
            "viewers": self.hub.viewer_count(),
            "streams": self.outside.streams(),
            "blocklist": self.hub.blocklist(),
            "blocklist_file": self.settings.blocklist_path().display().to_string(),
            "blocklist_max": redact::MAX_TERMS,
            "source_language": source_language(&cfg),
            "schedule": self.outside.schedule_status(),
            "targets": targets.iter().map(|(name, spec)| json!({
                "target": name,
                "label": target_label(name),
                "language_label": target_label(name),
                "enabled": spec.get("enabled").and_then(Value::as_bool).unwrap_or(false),
                "live": live.contains(name),
            })).collect::<Vec<_>>(),
        })
    }

    fn capture_status(&self) -> Value {
        if lock(&self.view).attached {
            self.capture.status()
        } else {
            json!({})
        }
    }

    /// The small, high-frequency slice of status: level, speech and each session's state. Pushed at 1 Hz so the admin meter stays
    /// live without re-sending the full status (which carries the whole blocklist).
    pub fn meter(&self) -> Value {
        let a = self.capture_status();
        let num = |k: &str, d: f64| a.get(k).and_then(Value::as_f64).unwrap_or(d);
        let (stream_running, started_at) = {
            let v = lock(&self.view);
            (v.stream_running, v.stream_started_at)
        };
        let sessions: Vec<Value> = self.sessions().iter().map(|s| json!({"name": s["name"], "state": s["state"]})).collect();
        json!({
            "type": "meter",
            "running": stream_running,
            "started_at": started_at,
            "level": num("level", 0.0),
            "rms_dbfs": num("rms_dbfs", -60.0),
            "peak_dbfs": num("peak_dbfs", -60.0),
            "clipping": a.get("clipping").and_then(Value::as_bool).unwrap_or(false),
            "clipped_samples": a.get("clipped_samples").and_then(Value::as_i64).unwrap_or(0),
            "speaking": a.get("speaking").and_then(Value::as_bool).unwrap_or(false),
            "sessions": sessions,
        })
    }

    /// The viewer-facing target set, as pushed over the caption SSE whenever it changes, so viewers no longer poll `/api/targets`.
    pub fn targets_payload(&self) -> Value {
        let cfg = self.settings.get();
        let src = relay_core::languages::SOURCE;
        let mut out = json!({
            "type": "targets",
            "source": {"lang": source_language(&cfg), "label": src.label, "iso": src.iso, "script": src.script},
            "live": self.live_targets(),
            // Planning Center service feeds a panel can pick: empty unless the module is on and connected.
            "service": [],
            // ProPresenter feeds a panel can pick: both false unless its module is on and set up.
            "propresenter": {"lyrics": false, "timers": false},
            // The status feed is core, so always offered.
            "status": true,
            "running": self.running(),
            "font_px": cfg.get("default_font_px").cloned().unwrap_or(Value::Null),
            // Top bar pills the modules that are on ask for (id, text, count, href).
            "pills": self.outside.pills(),
            // Each module's health state for the sidebar's dots ({id: state}).
            "module_health": self.outside.module_health(),
        });
        if let Some(map) = out.as_object_mut() {
            // Whatever the modules that are on add: they override the empty defaults above.
            map.extend(self.outside.module_status());
        }
        out
    }

    /// Tell the admin pages and the viewers: the status over the admin stream, the target set over every caption stream.
    pub fn push_status(&self) {
        self.hub.push_status(self.status());
        self.hub.broadcast_viewers(&self.targets_payload());
    }

    // ---- the pipeline -----------------------------------------------------------------------------------------------------
    /// Start the captions part: open the capture, the transcription session and the translator.
    pub async fn start_captions(&self) -> Result<Value, StartRefused> {
        // Rehearsal only fakes the viewer feed. A real start here would open billed sessions with the configured key, which is
        // what a rehearsal exists to avoid, so refuse before touching anything. Not recorded in `last_error`: the refusal answers
        // one press, and a sticky error would sit in the panel for the whole rehearsal.
        if self.outside.demo() {
            return Err(StartRefused { key: "rehearsal", message: REHEARSAL_REFUSAL.to_owned() });
        }
        let _op = self.op.lock().await;
        let cfg = self.settings.get();
        let (backend, warning) = {
            let backends = lock(&self.backends);
            let (b, w) = backends.resolve(&cfg);
            (b.clone(), w)
        };
        let key = backend.key(&cfg);
        if backend.key_setting.is_some() && key.is_empty() {
            let refusal = match backend.key_setting.as_deref() {
                Some("openai_api_key") => "no_key",
                Some("vercel_api_key") => "no_vercel_key",
                _ => "no_backend_key",
            };
            let message = start_message(refusal).unwrap_or_default().to_owned();
            lock(&self.view).last_error = Some(message.clone());
            return Err(StartRefused { key: refusal, message });
        }
        lock(&self.view).backend = Some(BackendView { id: backend.id.clone(), label: backend.label.clone(), price_per_hour: backend.price_per_hour, warning });

        let history = cfg.get("history_lines").and_then(Value::as_u64).unwrap_or(40) as usize;
        self.hub.set_history_lines(history);
        self.hub.set_blocklist(&read_blocklist_file(&self.settings.blocklist_path()));
        self.close_text_pipeline().await;
        let lane = match cfg.get("audio_lane") {
            Some(Value::String(s)) => s.clone(),
            Some(other) if !other.is_null() => other.to_string(),
            _ => String::new(),
        };
        let gate = cfg.get("realtime").and_then(|r| r.get("gate_silence")).and_then(Value::as_bool).unwrap_or(true);
        if let Err(message) = self.capture.start(&lane, gate) {
            // The capture's own sentence (fixed text naming the device), never an exception's.
            let message = if message.is_empty() { "The audio input could not be opened.".to_owned() } else { message };
            lock(&self.view).last_error = Some(message.clone());
            return Err(StartRefused { key: "capture", message });
        }
        {
            let mut v = lock(&self.view);
            v.running = true;
            v.started_at = Some(clock::wall());
        }
        let src = source_language(&cfg);
        self.hub.reset(TRANSCRIPTION_STREAM, &src);
        let enabled = enabled_targets(&cfg);
        // The recorded window is exactly this start to the matching stop.
        self.begin_recording(&cfg, &src, &enabled);
        self.open_text_pipeline(&enabled, &cfg, &backend, &key, &src);
        // With no target enabled there is nothing to translate, and the captions still run; say so.
        let mut error = if enabled.is_empty() { Some("No target language is enabled, so captions run without translation. Turn one on.".to_owned()) } else { None };
        if !enabled.is_empty() && lock(&self.view).translation_route.as_ref().is_some_and(|r| r.key.is_empty()) {
            error = start_message("no_translation_key").map(str::to_owned);
        }
        lock(&self.view).last_error = error;
        self.push_status();
        Ok(self.status())
    }

    pub async fn stop_captions(&self) -> Value {
        let _op = self.op.lock().await;
        self.close_text_pipeline().await;
        self.capture.stop();
        {
            let mut v = lock(&self.view);
            v.running = false;
            v.started_at = None;
        }
        self.end_recording();
        self.push_status();
        self.status()
    }

    pub async fn restart(&self) -> Value {
        if lock(&self.view).running {
            self.stop_captions().await;
            return match self.start_captions().await {
                Ok(status) => status,
                Err(_) => self.status(),
            };
        }
        self.status()
    }

    /// Open a run file, if recording is on. A failure is logged by the recorder and leaves no sink: the event still goes ahead.
    fn begin_recording(&self, cfg: &Value, source: &str, targets: &[String]) {
        self.hub.remove_line_sink("recorder");
        let rec = cfg.get("recording").cloned().unwrap_or(Value::Null);
        if !rec.get("enabled").and_then(Value::as_bool).unwrap_or(false) {
            return;
        }
        let keep = rec.get("keep_runs").and_then(Value::as_i64).unwrap_or(20);
        let started = {
            let mut r = lock(&self.recorder);
            r.configure(&self.settings.recordings_path(), keep);
            r.start_run(source, targets).is_some()
        };
        if started {
            let recorder = Arc::clone(&self.recorder);
            self.hub.add_line_sink(
                "recorder",
                Arc::new(move |e: &LineEvent| {
                    lock(&recorder).append(&e.stream, &e.lang, e.seq, &e.text, e.started, e.ended);
                }),
            );
        }
    }

    fn end_recording(&self) {
        self.hub.remove_line_sink("recorder");
        lock(&self.recorder).finish_run();
    }

    /// A target is a key in the next request, not a session.
    pub async fn apply_target(&self, target: &str, enabled: bool) {
        let _op = self.op.lock().await;
        let translator = lock(&self.view).translator.clone();
        if let Some(t) = translator {
            if enabled {
                self.hub.reset(TRANSLATION_STREAM, target);
                t.add_target(target);
            } else {
                t.remove_target(target);
            }
        }
        self.push_status();
    }

    /// Point the running translator at the provider and model the AI models page now picks, without touching the transcription
    /// session or any other feed.
    pub async fn apply_ai(&self) -> Value {
        let _op = self.op.lock().await;
        let translator = lock(&self.view).translator.clone();
        if let Some(t) = translator {
            if let Some(route) = aimodels::resolve(&self.settings.get(), "translation") {
                t.use_route(route.url, &route.key, &route.model);
                lock(&self.view).translation_route = Some(route);
            }
        }
        self.push_status();
        self.status()
    }

    fn open_text_pipeline(&self, enabled: &[String], cfg: &Value, backend: &Backend, key: &str, src: &str) {
        let rt = cfg.get("realtime").cloned().unwrap_or(Value::Null);
        // Translation goes where the AI models page says (Vercel by default, the OpenAI key as fallback), whichever backend transcribes.
        let route = aimodels::resolve(cfg, "translation").unwrap_or_else(|| unreachable!("the translation task exists"));
        let hub = Arc::clone(&self.hub);
        let on_final: crate::translate::Final = {
            let hub = Arc::clone(&hub);
            Arc::new(move |target, text| {
                hub.publish_final(TRANSLATION_STREAM, target, Some(text));
            })
        };
        let source_label = relay_core::languages::SOURCE.label;
        let translator = Translator::new(
            Arc::clone(&self.chat),
            &Setup {
                url: route.url,
                key: &route.key,
                model: &route.model,
                reasoning_effort: rt.get("text_reasoning_effort").and_then(Value::as_str).unwrap_or(""),
                source_label,
            },
            on_final,
            self.push_status_later(),
        );
        translator.set_targets(enabled.iter().cloned());
        lock(&self.view).translation_route = Some(route);

        let spec = (backend.session)(cfg, key, src);
        let lang = src.to_owned();
        let on_delta: crate::session::OnText = {
            let (hub, lang) = (Arc::clone(&hub), lang.clone());
            Arc::new(move |d| hub.publish_delta(TRANSCRIPTION_STREAM, &lang, d))
        };
        // Translate the line as published, blocklist applied: the list is words in the source language, which no filter on the far
        // side could catch once they are Arabic or Chinese. A removed word is never sent to the model, so it cannot reach any language.
        let on_final_source: crate::session::OnText = {
            let (hub, translator, lang) = (Arc::clone(&hub), translator.clone(), lang);
            Arc::new(move |text| {
                let line = hub.publish_final(TRANSCRIPTION_STREAM, &lang, Some(text));
                translator.submit(&line);
            })
        };
        let session = Arc::new(Session::new("transcribe", spec, Arc::clone(&self.connect), on_delta, on_final_source, self.push_status_later()));
        translator.start();
        let (consumer, feed) = self.capture.add_consumer();
        session.start(feed);
        let mut v = lock(&self.view);
        v.consumer = Some(consumer);
        v.session = Some(session);
        v.translator = Some(translator);
    }

    async fn close_text_pipeline(&self) {
        let (session, translator, consumer) = {
            let mut v = lock(&self.view);
            (v.session.take(), v.translator.take(), v.consumer.take())
        };
        if let Some(s) = session {
            s.stop().await;
        }
        if let Some(id) = consumer {
            self.capture.remove_consumer(id);
        }
        if let Some(t) = translator {
            t.stop();
        }
    }

    /// Stop everything that is running (the module is switched off, or Relay is shutting down).
    pub async fn shutdown(&self) {
        let running = lock(&self.view).running;
        if running {
            self.stop_captions().await;
        }
        if let Some(demo) = lock(&self.view).demo.take() {
            demo.abort();
        }
    }

    // ---- per-target toggling ----------------------------------------------------------------------------------------------
    /// Switch a target on or off: saved, and followed by a running translator at once.
    pub async fn set_target(&self, target: &str, enabled: bool) -> Result<Value, UnknownTarget> {
        let cfg = self.settings.get();
        if !relay_core::languages::target_names(&source_language(&cfg)).contains(&target) {
            return Err(UnknownTarget);
        }
        let mut spec = cfg.get("targets").and_then(|t| t.get(target)).cloned().unwrap_or_else(|| json!({}));
        spec["enabled"] = json!(enabled);
        self.settings.save(&json!({"targets": {target: spec}}));
        if lock(&self.view).attached {
            self.apply_target(target, enabled).await;
        } else {
            self.push_status();
        }
        Ok(self.status())
    }

    // ---- the stream: what Start and Stop mean ------------------------------------------------------------------------------
    fn captions_wanted(&self) -> bool {
        self.settings.get().get("stream_captions").and_then(Value::as_bool).unwrap_or(true)
    }

    /// Open the stream. Captions start with it when wanted; if they are refused (no key, no audio) the whole start is refused and
    /// nothing is left half started.
    pub async fn start(&self) -> Result<Value, StartRefused> {
        let _s = self.stream_op.lock().await;
        if lock(&self.view).stream_running {
            return Ok(self.status());
        }
        let wanted = self.captions_wanted() && lock(&self.view).attached;
        if wanted {
            // A refused part leaves nothing half started.
            if let Err(e) = self.start_captions().await {
                if lock(&self.view).running {
                    self.stop_captions().await;
                }
                return Err(e);
            }
        }
        {
            let mut v = lock(&self.view);
            v.stream_running = true;
            v.stream_started_at = Some(clock::wall());
        }
        self.outside.announce(STARTED);
        self.push_status();
        Ok(self.status())
    }

    pub async fn stop(&self) -> Value {
        let _s = self.stream_op.lock().await;
        let was = {
            let mut v = lock(&self.view);
            let was = v.stream_running;
            v.stream_running = false;
            v.stream_started_at = None;
            was
        };
        if lock(&self.view).running {
            self.stop_captions().await;
        }
        if was {
            self.outside.announce(STOPPED);
        }
        self.push_status();
        self.status()
    }

    /// The operator turned captions on or off in settings: follow it live, so a running stream gains or loses its captions without a
    /// stop. A refused start stays in the pipeline's own error for the panel.
    pub async fn apply_parts(&self) {
        let _s = self.stream_op.lock().await;
        let (stream, running, attached) = {
            let v = lock(&self.view);
            (v.stream_running, v.running, v.attached)
        };
        if !stream || !attached {
            return;
        }
        let wanted = self.captions_wanted();
        if wanted && !running {
            if self.outside.demo() {
                return;
            }
            let _ = self.start_captions().await;
        } else if !wanted && running {
            self.stop_captions().await;
        }
        self.push_status();
    }

    // ---- rehearsal ----------------------------------------------------------------------------------------------------------
    /// In rehearsal mode, drive the viewer pages from the scripted transcript. `pace` scales every pause (1.0 is real).
    pub fn start_rehearsal(&self, pace: f64) {
        let cfg = self.settings.get();
        let target = enabled_targets(&cfg).into_iter().next().unwrap_or_else(|| "SPANISH".to_owned());
        let handle = crate::demo::start(Arc::clone(&self.hub), &source_language(&cfg), &target, pace);
        if let Some(old) = lock(&self.view).demo.replace(handle) {
            old.abort();
        }
        tracing::warn!("REHEARSAL MODE: captions are canned. No OpenAI session is open.");
    }

    /// `/api/targets`: the payload, and in rehearsal mode with nothing live, the enabled targets as live in state `demo`.
    pub fn api_targets(&self) -> Value {
        let mut data = self.targets_payload();
        if let Some(map) = data.as_object_mut() {
            map.remove("type");
            let nothing_live = map.get("live").and_then(Value::as_array).is_none_or(Vec::is_empty);
            if nothing_live && self.outside.demo() {
                let cfg = self.settings.get();
                let live: Vec<Value> = enabled_targets(&cfg)
                    .iter()
                    .map(|n| json!({"target": n, "label": target_label(n), "iso": crate::translate::iso(n), "script": target_script(n), "state": "demo"}))
                    .collect();
                map.insert("live".to_owned(), json!(live));
                map.insert("running".to_owned(), json!(true));
            }
        }
        data
    }
}

/// `set_target` named a language Relay does not offer.
#[derive(Debug, PartialEq, Eq)]
pub struct UnknownTarget;
