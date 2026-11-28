//! Start, Stop and the status pages read (`app/engine.py`, `app/stream.py`, `app/modules/captions/pipeline.py`), with the capture,
//! the socket and the chat model played by the test.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use relay_captions::capture::Consumers;
use relay_captions::engine::{Alone, Capture, Captions, Outside, Parts, Settings, REHEARSAL_REFUSAL, TRANSCRIPTION_STREAM};
use relay_captions::recorder::{read_lines, Recorder};
use relay_captions::redact;
use relay_captions::session::{AudioFeed, Conn, Connect, Incoming, Outgoing, WsRequest};
use relay_captions::transcribe::Backends;
use relay_captions::translate::Chat;
use relay_core::future::BoxFuture;
use relay_core::hub::Hub;
use serde_json::{json, Value};
use tokio::sync::mpsc;

// ---- fakes -------------------------------------------------------------------------------------------------------------

struct Mem {
    cfg: Mutex<Value>,
    dir: PathBuf,
}

fn merge(into: &mut Value, patch: &Value) {
    match (into, patch) {
        (Value::Object(a), Value::Object(b)) => {
            for (k, v) in b {
                merge(a.entry(k.clone()).or_insert(Value::Null), v);
            }
        }
        (slot, v) => *slot = v.clone(),
    }
}

impl Settings for Mem {
    fn get(&self) -> Value {
        self.cfg.lock().unwrap().clone()
    }
    fn save(&self, patch: &Value) {
        merge(&mut self.cfg.lock().unwrap(), patch);
    }
    fn blocklist_path(&self) -> PathBuf {
        self.dir.join("blocklist.txt")
    }
    fn recordings_path(&self) -> PathBuf {
        self.dir.join("recordings")
    }
}

struct Mic {
    consumers: Arc<Consumers>,
    open: AtomicBool,
    fail: Mutex<Option<String>>,
    started: Mutex<Vec<(String, bool)>>,
}

impl Capture for Mic {
    fn start(&self, lane: &str, gate: bool) -> Result<(), String> {
        if let Some(e) = self.fail.lock().unwrap().clone() {
            return Err(e);
        }
        self.started.lock().unwrap().push((lane.to_owned(), gate));
        self.open.store(true, Ordering::SeqCst);
        Ok(())
    }
    fn stop(&self) {
        self.open.store(false, Ordering::SeqCst);
    }
    fn status(&self) -> Value {
        json!({"running": self.open.load(Ordering::SeqCst), "level": 0.25, "rms_dbfs": -12.0, "peak_dbfs": -6.0, "clipping": false, "clipped_samples": 0, "speaking": true})
    }
    fn add_consumer(&self) -> (u64, AudioFeed) {
        self.consumers.add()
    }
    fn remove_consumer(&self, id: u64) {
        self.consumers.remove(id);
    }
}

struct Socket {
    events: Mutex<VecDeque<Vec<Value>>>,
    connects: AtomicUsize,
    sent: Arc<Mutex<Vec<Outgoing>>>,
}

impl Connect for Socket {
    fn open<'a>(&'a self, _req: &'a WsRequest) -> BoxFuture<'a, Result<Conn, String>> {
        Box::pin(async move {
            self.connects.fetch_add(1, Ordering::SeqCst);
            let events = self.events.lock().unwrap().pop_front().unwrap_or_default();
            let (out_tx, mut out_rx) = mpsc::channel::<Outgoing>(256);
            let (inc_tx, inc_rx) = mpsc::channel::<Incoming>(256);
            let sent = Arc::clone(&self.sent);
            tokio::spawn(async move {
                for e in events {
                    let _ = inc_tx.send(Incoming::Text(e.to_string())).await;
                }
                while let Some(o) = out_rx.recv().await {
                    sent.lock().unwrap().push(o);
                }
            });
            Ok(Conn { out: out_tx, inc: inc_rx })
        })
    }
}

struct Model {
    calls: Mutex<Vec<(String, Value)>>,
}

impl Chat for Model {
    fn complete<'a>(&'a self, url: &'a str, _key: &'a str, body: &'a Value) -> BoxFuture<'a, Result<Value, String>> {
        Box::pin(async move {
            self.calls.lock().unwrap().push((url.to_owned(), body.clone()));
            let user: Value = serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
            let code = user["languages"].as_object().unwrap().keys().next().unwrap().clone();
            let text = format!("[{code}] {}", user["text"].as_str().unwrap());
            Ok(json!({"choices": [{"message": {"content": json!({code: text}).to_string()}}]}))
        })
    }
}

#[derive(Default)]
struct Room {
    demo: AtomicBool,
    announced: Mutex<Vec<String>>,
}

impl Outside for Room {
    fn demo(&self) -> bool {
        self.demo.load(Ordering::SeqCst)
    }
    fn announce(&self, event: &str) {
        self.announced.lock().unwrap().push(event.to_owned());
    }
    fn pills(&self) -> Value {
        json!([{"id": "obs", "text": "OBS", "count": 1, "href": "/admin/obs"}])
    }
    fn module_status(&self) -> serde_json::Map<String, Value> {
        let mut m = serde_json::Map::new();
        m.insert("propresenter".into(), json!({"lyrics": true, "timers": false}));
        m
    }
}

struct Rig {
    cap: Arc<Captions>,
    mem: Arc<Mem>,
    mic: Arc<Mic>,
    socket: Arc<Socket>,
    model: Arc<Model>,
    room: Arc<Room>,
    hub: Arc<Hub>,
    dir: PathBuf,
}

fn tmp(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("relay-captions-engine-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn base_cfg() -> Value {
    json!({
        "source_language": "ENGLISH",
        "targets": {"SPANISH": {"enabled": true}, "FRENCH": {"enabled": false}},
        "audio_lane": "lane1",
        "history_lines": 40,
        "default_font_px": 48,
        "stream_captions": true,
        "vercel_api_key": "vk",
        "realtime": {"backend": "scribe", "gate_silence": true},
        "recording": {"enabled": false, "keep_runs": 3},
    })
}

fn rig(name: &str, cfg: Value, scripts: Vec<Vec<Value>>) -> Rig {
    let dir = tmp(name);
    let mem = Arc::new(Mem { cfg: Mutex::new(cfg), dir: dir.clone() });
    let hub = Hub::with(Box::new(redact::factory()), Box::new(relay_captions::clock::wall));
    let mic = Arc::new(Mic { consumers: Consumers::new(), open: AtomicBool::new(false), fail: Mutex::new(None), started: Mutex::new(Vec::new()) });
    let socket = Arc::new(Socket { events: Mutex::new(scripts.into()), connects: AtomicUsize::new(0), sent: Arc::new(Mutex::new(Vec::new())) });
    let model = Arc::new(Model { calls: Mutex::new(Vec::new()) });
    let room = Arc::new(Room::default());
    let cap = Captions::new(Parts {
        hub: Arc::clone(&hub),
        settings: mem.clone(),
        capture: mic.clone(),
        chat: model.clone(),
        connect: socket.clone(),
        outside: room.clone(),
        backends: Backends::builtin(),
        recorder: Recorder::default(),
    });
    cap.attach();
    Rig { cap, mem, mic, socket, model, room, hub, dir }
}

async fn until(what: &str, mut ok: impl FnMut() -> bool) {
    for _ in 0..400 {
        if ok() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("never: {what}");
}

fn finals(sub: &mut relay_core::hub::Subscription) -> Vec<String> {
    let mut out = Vec::new();
    while let Ok(frame) = sub.try_recv() {
        let text = String::from_utf8_lossy(&frame).into_owned();
        if let Some(json) = text.lines().find_map(|l| l.strip_prefix("data: ")) {
            let v: Value = serde_json::from_str(json).unwrap();
            if v["final"] == true {
                out.push(v["text"].as_str().unwrap().to_owned());
            }
        }
    }
    out
}

fn say(text: &str) -> Vec<Value> {
    vec![json!({"type": "transcript-delta", "delta": text}), json!({"type": "transcript-final", "text": text})]
}

// ---- tests -------------------------------------------------------------------------------------------------------------

#[tokio::test]
async fn start_runs_captions_and_the_lines_reach_the_viewers_in_both_languages() {
    let r = rig("start", base_cfg(), vec![say("Good morning.")]);
    let mut source = r.hub.subscribe(TRANSCRIPTION_STREAM, "ENGLISH", None, false);
    let mut spanish = r.hub.subscribe("translation", "SPANISH", None, false);
    let status = r.cap.start().await.unwrap();
    assert_eq!((status["running"].clone(), status["captions_running"].clone()), (json!(true), json!(true)));
    assert_eq!(status["stream"]["captions_available"], true);
    assert_eq!(status["backend"]["id"], "scribe");
    assert_eq!(*r.mic.started.lock().unwrap(), [("lane1".to_owned(), true)]);
    assert_eq!(*r.room.announced.lock().unwrap(), ["stream.started"]);
    until("the translation", || r.model.calls.lock().unwrap().len() == 1).await;
    let mut seen = (Vec::new(), Vec::new());
    until("the viewers", || {
        seen.0.extend(finals(&mut source));
        seen.1.extend(finals(&mut spanish));
        !seen.1.is_empty()
    })
    .await;
    assert_eq!(seen.0, ["Good morning."]);
    assert_eq!(seen.1, ["[es] Good morning."]);
    let (url, body) = r.model.calls.lock().unwrap()[0].clone();
    assert_eq!(url, "https://ai-gateway.vercel.sh/v1/chat/completions");
    assert_eq!(body["model"], "openai/gpt-6-luna");
    let live = r.cap.live_targets();
    assert_eq!(live.len(), 1, "only the enabled language is live");
    assert_eq!(live[0]["target"], "SPANISH");
    let status = r.cap.status();
    assert_eq!(status["sessions"].as_array().unwrap().len(), 2, "the transcription session and the translator");
    assert_eq!(status["translation"]["provider"], "vercel");
    let spanish_row = status["targets"].as_array().unwrap().iter().find(|t| t["target"] == "SPANISH").unwrap().clone();
    assert_eq!((spanish_row["enabled"].clone(), spanish_row["live"].clone()), (json!(true), json!(true)));
    r.cap.stop().await;
    assert_eq!(*r.room.announced.lock().unwrap(), ["stream.started", "stream.stopped"]);
}

#[tokio::test]
async fn stop_closes_everything_and_the_status_goes_quiet() {
    let r = rig("stop", base_cfg(), vec![]);
    r.cap.start().await.unwrap();
    until("a consumer", || r.mic.consumers.len() == 1).await;
    let status = r.cap.stop().await;
    assert_eq!((status["running"].clone(), status["captions_running"].clone(), status["started_at"].clone()), (json!(false), json!(false), Value::Null));
    assert!(!r.mic.open.load(Ordering::SeqCst), "the capture is closed");
    assert!(r.mic.consumers.is_empty(), "the session's audio queue is removed");
    assert_eq!(status["sessions"], json!([]));
    assert_eq!(status["backend"], Value::Null);
    assert!(r.cap.live_targets().is_empty());
}

#[tokio::test]
async fn rehearsal_mode_refuses_a_real_start_with_its_own_sentence() {
    let r = rig("rehearsal", base_cfg(), vec![]);
    r.room.demo.store(true, Ordering::SeqCst);
    let err = r.cap.start().await.unwrap_err();
    assert_eq!((err.key, err.message.as_str()), ("rehearsal", REHEARSAL_REFUSAL));
    assert!(r.mic.started.lock().unwrap().is_empty(), "nothing was opened");
    assert_eq!(r.cap.last_error(), None, "the refusal answers one press and is not sticky");
    assert_eq!(r.socket.connects.load(Ordering::SeqCst), 0);
    assert_eq!(r.cap.status()["running"], false);
}

#[tokio::test]
async fn a_missing_key_refuses_the_start_and_names_the_key() {
    let mut cfg = base_cfg();
    cfg["vercel_api_key"] = json!("");
    cfg["openai_api_key"] = json!("");
    let r = rig("nokey", cfg, vec![]);
    let err = r.cap.start().await.unwrap_err();
    assert_eq!(err.key, "no_vercel_key");
    assert!(err.message.starts_with("No Vercel AI Gateway API key set."));
    assert_eq!(r.cap.last_error().as_deref(), Some(err.message.as_str()), "this one stays in the panel");
    assert_eq!(r.cap.status()["running"], false);
    // OpenAI chosen with no key names the OpenAI key.
    r.mem.save(&json!({"realtime": {"backend": "openai"}}));
    assert_eq!(r.cap.start().await.unwrap_err().key, "no_key");
}

#[tokio::test]
async fn an_install_with_only_the_openai_key_runs_on_openai_and_says_why() {
    let mut cfg = base_cfg();
    cfg["vercel_api_key"] = json!("");
    cfg["openai_api_key"] = json!("ok");
    let r = rig("fallback", cfg, vec![]);
    let status = r.cap.start().await.unwrap();
    assert_eq!(status["backend"]["id"], "openai");
    assert!(status["backend"]["warning"].as_str().unwrap().contains("needs a Vercel AI Gateway key"));
    assert_eq!(status["translation"]["provider"], "openai");
    r.cap.stop().await;
}

#[tokio::test]
async fn a_capture_that_cannot_open_refuses_the_start_with_the_devices_own_sentence() {
    let r = rig("capture", base_cfg(), vec![]);
    *r.mic.fail.lock().unwrap() = Some("The input \"Scarlett\" could not be opened.".to_owned());
    let err = r.cap.start().await.unwrap_err();
    assert_eq!((err.key, err.message.as_str()), ("capture", "The input \"Scarlett\" could not be opened."));
    assert_eq!(r.cap.status()["running"], false, "nothing is left half started");
    assert!(r.room.announced.lock().unwrap().is_empty());
    assert_eq!(r.socket.connects.load(Ordering::SeqCst), 0);
}

#[tokio::test]
async fn captions_say_so_when_nothing_is_enabled_or_nothing_can_translate() {
    let mut cfg = base_cfg();
    cfg["targets"]["SPANISH"]["enabled"] = json!(false);
    let r = rig("notargets", cfg, vec![]);
    r.cap.start().await.unwrap();
    assert_eq!(r.cap.last_error().as_deref(), Some("No target language is enabled, so captions run without translation. Turn one on."));
    assert!(r.cap.running(), "and the captions still run");
    r.cap.stop().await;
    // A target on, and the transcription key saved, but a translation key that is not: the translation task has the same key here, so
    // blank it after the fact by routing translation to the OpenAI key.
    r.mem.save(&json!({"targets": {"SPANISH": {"enabled": true}}, "ai": {"tasks": {"translation": {"provider": "openai"}}}}));
    r.cap.start().await.unwrap();
    assert!(r.cap.last_error().unwrap().starts_with("Captions are running, but translating them needs"));
    r.cap.stop().await;
}

#[tokio::test]
async fn a_blocked_word_never_reaches_the_viewers_the_model_or_the_file() {
    let mut cfg = base_cfg();
    cfg["recording"]["enabled"] = json!(true);
    let r = rig("blocked", cfg, vec![say("Well damn that is fast.")]);
    std::fs::write(r.mem.blocklist_path(), "# words\ndamn\n").unwrap();
    r.cap.start().await.unwrap();
    until("the model asked", || r.model.calls.lock().unwrap().len() == 1).await;
    let body = r.model.calls.lock().unwrap()[0].1.clone();
    let user: Value = serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
    assert_eq!(user["text"], "Well that is fast.", "the model is sent the line as published");
    until("two lines recorded", || {
        let recorder = r.cap.recorder().lock().unwrap();
        recorder.run_id().and_then(|id| recorder.run_dir(id)).is_some_and(|d| read_lines(&d).len() == 2)
    })
    .await;
    r.cap.stop().await;
    let run = r.mem.recordings_path().read_dir().unwrap().next().unwrap().unwrap().path();
    let rows = read_lines(&run);
    let texts: Vec<&str> = rows.iter().map(|v| v["text"].as_str().unwrap()).collect();
    assert!(texts.contains(&"Well that is fast.") && texts.contains(&"[es] Well that is fast."), "{texts:?}");
    assert!(!std::fs::read_to_string(run.join("lines.jsonl")).unwrap().contains("damn"));
    let meta: Value = serde_json::from_str(&std::fs::read_to_string(run.join("meta.json")).unwrap()).unwrap();
    assert_eq!((meta["lines"].clone(), meta["ended"].is_null(), meta["targets"].clone()), (json!(2), false, json!(["SPANISH"])));
}

#[tokio::test]
async fn switching_a_target_is_saved_and_a_running_translator_follows_at_once() {
    let r = rig("target", base_cfg(), vec![say("Hello.")]);
    r.cap.start().await.unwrap();
    until("translated", || r.model.calls.lock().unwrap().len() == 1).await;
    let status = r.cap.set_target("FRENCH", true).await.unwrap();
    assert_eq!(r.mem.get()["targets"]["FRENCH"]["enabled"], true, "saved");
    assert_eq!(status["targets"].as_array().unwrap().iter().filter(|t| t["live"] == true).count(), 2);
    let status = r.cap.set_target("SPANISH", false).await.unwrap();
    assert_eq!(r.cap.live_targets().len(), 1);
    assert_eq!(status["targets"].as_array().unwrap().iter().find(|t| t["target"] == "SPANISH").unwrap()["enabled"], false);
    assert!(r.cap.set_target("KLINGON", true).await.is_err(), "unknown");
    assert!(r.cap.set_target("ENGLISH", true).await.is_err(), "the source language is not offered");
    r.cap.stop().await;
}

#[tokio::test]
async fn apply_ai_moves_the_translator_without_touching_the_session() {
    let r = rig("ai", base_cfg(), vec![]);
    r.cap.start().await.unwrap();
    until("connected", || r.socket.connects.load(Ordering::SeqCst) == 1).await;
    assert_eq!(r.cap.status()["translation"]["model"], "openai/gpt-6-luna");
    r.mem.save(&json!({"ai": {"tasks": {"translation": {"model": "openai/other-model"}}}}));
    let status = r.cap.apply_ai().await;
    assert_eq!(status["translation"]["model"], "openai/other-model");
    assert_eq!(r.socket.connects.load(Ordering::SeqCst), 1, "the transcription session was not cycled");
    r.cap.stop().await;
}

#[tokio::test]
async fn restart_cycles_the_session() {
    let r = rig("restart", base_cfg(), vec![]);
    r.cap.start().await.unwrap();
    until("first connect", || r.socket.connects.load(Ordering::SeqCst) == 1).await;
    let status = r.cap.restart().await;
    assert_eq!(status["captions_running"], true);
    until("second connect", || r.socket.connects.load(Ordering::SeqCst) == 2).await;
    assert_eq!(r.mic.started.lock().unwrap().len(), 2);
    r.cap.stop().await;
    assert_eq!(r.cap.restart().await["captions_running"], false, "restarting a stopped relay does nothing");
}

#[tokio::test]
async fn with_captions_turned_off_start_opens_the_stream_and_runs_none() {
    let mut cfg = base_cfg();
    cfg["stream_captions"] = json!(false);
    let r = rig("nocaptions", cfg, vec![]);
    let status = r.cap.start().await.unwrap();
    assert_eq!((status["running"].clone(), status["captions_running"].clone()), (json!(true), json!(false)));
    assert!(r.mic.started.lock().unwrap().is_empty());
    // Turning them on in settings is followed live, without a stop; off again stops only the captions.
    r.mem.save(&json!({"stream_captions": true}));
    r.cap.apply_parts().await;
    assert_eq!(r.cap.status()["captions_running"], true);
    r.mem.save(&json!({"stream_captions": false}));
    r.cap.apply_parts().await;
    let status = r.cap.status();
    assert_eq!((status["running"].clone(), status["captions_running"].clone()), (json!(true), json!(false)));
    assert!(!r.mic.open.load(Ordering::SeqCst));
    r.cap.stop().await;
}

#[tokio::test]
async fn a_refused_part_refuses_the_whole_start() {
    let mut cfg = base_cfg();
    cfg["vercel_api_key"] = json!("");
    let r = rig("refused", cfg, vec![]);
    assert!(r.cap.start().await.is_err());
    let status = r.cap.status();
    assert_eq!(status["running"], false, "the stream did not open");
    assert!(r.room.announced.lock().unwrap().is_empty(), "no module was told to start");
    // Start twice is one start.
    r.mem.save(&json!({"vercel_api_key": "vk"}));
    r.cap.start().await.unwrap();
    r.cap.start().await.unwrap();
    assert_eq!(r.room.announced.lock().unwrap().len(), 1);
    r.cap.stop().await;
}

#[tokio::test]
async fn while_the_module_is_off_the_engine_answers_with_captions_idle() {
    let r = rig("off", base_cfg(), vec![]);
    r.cap.detach();
    let status = r.cap.start().await.unwrap();
    assert_eq!((status["running"].clone(), status["captions_running"].clone()), (json!(true), json!(false)), "Start opens the stream and runs no captions");
    assert_eq!(status["stream"]["captions_available"], false);
    assert_eq!(status["audio"], json!({}));
    assert!(r.mic.started.lock().unwrap().is_empty());
    assert_eq!(r.cap.meter()["level"], 0.0);
    r.cap.stop().await;
}

#[tokio::test]
async fn the_meter_is_the_small_slice_and_the_targets_payload_carries_what_modules_add() {
    let r = rig("payload", base_cfg(), vec![]);
    r.cap.start().await.unwrap();
    let meter = r.cap.meter();
    assert_eq!(meter["type"], "meter");
    assert_eq!((meter["level"].clone(), meter["rms_dbfs"].clone(), meter["speaking"].clone()), (json!(0.25), json!(-12.0), json!(true)));
    assert_eq!(meter["sessions"][0]["name"], "transcribe");
    let t = r.cap.targets_payload();
    assert_eq!((t["type"].clone(), t["running"].clone(), t["font_px"].clone(), t["status"].clone()), (json!("targets"), json!(true), json!(48), json!(true)));
    assert_eq!(t["source"], json!({"lang": "ENGLISH", "label": "English", "iso": "en", "script": "latin"}));
    assert_eq!(t["pills"][0]["id"], "obs");
    assert_eq!(t["propresenter"], json!({"lyrics": true, "timers": false}), "a module's answer overrides the default");
    assert_eq!(t["service"], json!([]));
    r.cap.stop().await;
}

#[tokio::test]
async fn in_rehearsal_mode_the_enabled_targets_show_as_live_and_the_script_drives_the_viewers() {
    let r = rig("demo", base_cfg(), vec![]);
    r.room.demo.store(true, Ordering::SeqCst);
    let api = r.cap.api_targets();
    assert!(api.get("type").is_none());
    assert_eq!((api["live"][0]["target"].clone(), api["live"][0]["state"].clone(), api["running"].clone()), (json!("SPANISH"), json!("demo"), json!(true)));
    let mut source = r.hub.subscribe(TRANSCRIPTION_STREAM, "ENGLISH", None, false);
    let mut spanish = r.hub.subscribe("translation", "SPANISH", None, false);
    r.cap.start_rehearsal(0.01);
    let mut got = (Vec::new(), Vec::new());
    until("a canned line in both languages", || {
        got.0.extend(finals(&mut source));
        got.1.extend(finals(&mut spanish));
        !got.0.is_empty() && !got.1.is_empty()
    })
    .await;
    assert_eq!(got.0[0], "Good afternoon everyone, and thank you for coming.");
    assert_eq!(got.1[0], "Buenas tardes a todos, y gracias por venir.");
    r.cap.shutdown().await;
    let _ = r.dir;
    let _ = Alone;
}
