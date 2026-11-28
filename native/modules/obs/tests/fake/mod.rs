//! A fake obs-websocket 5 server on 127.0.0.1 (`tests/test_obs.py`'s `FakeObs`), and a rig that gives the module recording stand-ins for what core offers it
//! (stream status, tally, alert buttons, the hub, the audit log).
#![allow(dead_code)]

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use axum::body::Body;
use axum::http::request::Parts;
use axum::http::{Method, Request, StatusCode};
use axum::response::Response;
use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use http_body_util::BodyExt;
use relay_core::access::Peer;
use relay_core::caps::*;
use relay_core::future::BoxFuture;
use relay_core::hub::Hub;
use relay_core::{Authorizer, Host, SettingsStore, Shutdown};
use relay_module_obs::machine::Timing;
use relay_module_obs::proto::auth_string;
use relay_module_obs::Obs;
use serde_json::{json, Value};
use tokio::net::TcpListener;
use tokio::sync::broadcast;
use tokio_tungstenite::tungstenite::Message;
use tower::ServiceExt;

pub const JPEG: &[u8] = b"\xff\xd8\xff\xe0fakejpeg";

pub struct State {
    pub password: String,
    pub scenes: Vec<String>,
    pub program: String,
    pub stream: Value,
    pub record: Value,
    pub stats: Value,
    pub captions: Vec<String>,
    pub studio: bool,
    pub preview: Option<String>,
    pub replay_on: bool,
    pub replay_active: bool,
    pub can_pause: bool,
    /// Name to muted; `None`: an input with no audio.
    pub inputs: Vec<(String, Option<bool>)>,
    pub requests: Vec<String>,
    pub identified: u32,
    pub subscriptions: u64,
}

pub struct Fake {
    pub port: u16,
    state: Mutex<State>,
    events: broadcast::Sender<String>,
    kick: broadcast::Sender<()>,
}

impl Fake {
    pub async fn start(password: &str) -> Arc<Self> {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let port = listener.local_addr().expect("addr").port();
        let state = State {
            password: password.to_owned(),
            scenes: vec!["Walk-in".into(), "Worship".into(), "Message".into()],
            program: "Walk-in".into(),
            stream: json!({"outputActive": false, "outputReconnecting": false, "outputDuration": 0, "outputBytes": 0, "outputCongestion": 0.0, "outputSkippedFrames": 0, "outputTotalFrames": 0}),
            record: json!({"outputActive": false, "outputPaused": false, "outputDuration": 0, "outputBytes": 0}),
            stats: json!({"cpuUsage": 3.0, "memoryUsage": 300.0, "activeFps": 30.0, "averageFrameRenderTime": 4.0, "availableDiskSpace": 100000.0, "renderSkippedFrames": 0, "renderTotalFrames": 0, "outputSkippedFrames": 0, "outputTotalFrames": 0}),
            captions: Vec::new(),
            studio: false,
            preview: None,
            replay_on: true,
            replay_active: false,
            can_pause: true,
            inputs: vec![("Pastor mic".into(), Some(false)), ("Music".into(), Some(false)), ("Camera 1".into(), None)],
            requests: Vec::new(),
            identified: 0,
            subscriptions: 0,
        };
        let fake = Arc::new(Self { port, state: Mutex::new(state), events: broadcast::channel(64).0, kick: broadcast::channel(4).0 });
        let me = Arc::clone(&fake);
        tokio::spawn(async move {
            while let Ok((sock, _)) = listener.accept().await {
                tokio::spawn(Arc::clone(&me).serve(sock));
            }
        });
        fake
    }

    pub fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Push an event to every connected client.
    pub fn event(&self, kind: &str, data: Value) {
        let _ = self.events.send(json!({"op": 5, "d": {"eventType": kind, "eventIntent": 4, "eventData": data}}).to_string());
    }

    /// Close every client's connection.
    pub fn drop_clients(&self) {
        let _ = self.kick.send(());
    }

    async fn serve(self: Arc<Self>, sock: tokio::net::TcpStream) {
        let Ok(mut ws) = tokio_tungstenite::accept_async(sock).await else { return };
        let password = self.lock().password.clone();
        let mut hello = json!({"obsWebSocketVersion": "5.7.6", "rpcVersion": 1});
        if !password.is_empty() {
            hello["authentication"] = json!({"challenge": "chal", "salt": "salt"});
        }
        let _ = ws.send(Message::text(json!({"op": 0, "d": hello}).to_string())).await;
        let Some(Ok(Message::Text(t))) = ws.next().await else { return };
        let ident: Value = serde_json::from_str(t.as_str()).unwrap_or_default();
        if !password.is_empty() && ident["d"]["authentication"] != json!(auth_string(&password, "salt", "chal")) {
            let _ = ws.close(None).await;
            return;
        }
        {
            let mut s = self.lock();
            s.identified += 1;
            s.subscriptions = ident["d"]["eventSubscriptions"].as_u64().unwrap_or(0);
        }
        let _ = ws.send(Message::text(json!({"op": 2, "d": {"negotiatedRpcVersion": 1}}).to_string())).await;
        let (mut events, mut kick) = (self.events.subscribe(), self.kick.subscribe());
        loop {
            tokio::select! {
                e = events.recv() => {
                    let Ok(e) = e else { continue };
                    if ws.send(Message::text(e)).await.is_err() { return; }
                }
                _ = kick.recv() => { let _ = ws.close(None).await; return; }
                m = ws.next() => {
                    let Some(Ok(Message::Text(t))) = m else { return };
                    let msg: Value = serde_json::from_str(t.as_str()).unwrap_or_default();
                    let d = &msg["d"];
                    let (ok, out) = self.answer(d["requestType"].as_str().unwrap_or(""), &d["requestData"]);
                    let reply = json!({"op": 7, "d": {"requestType": d["requestType"], "requestId": d["requestId"], "requestStatus": {"result": ok, "code": if ok { 100 } else { 501 }}, "responseData": out}});
                    if ws.send(Message::text(reply.to_string())).await.is_err() { return; }
                }
            }
        }
    }

    fn answer(&self, kind: &str, data: &Value) -> (bool, Value) {
        let mut later: Vec<(&str, Value)> = Vec::new();
        let (ok, out) = {
            let mut s = self.lock();
            s.requests.push(kind.to_owned());
            let name = data["sceneName"].as_str().unwrap_or("").to_owned();
            let mut ok = true;
            let mut out = json!({});
            match kind {
                "GetVersion" => out = json!({"obsVersion": "33.0.1", "obsWebSocketVersion": "5.7.6"}),
                "GetSceneList" => {
                    let n = s.scenes.len();
                    // OBS gives the top scene the highest index.
                    out = json!({"currentProgramSceneName": s.program, "currentPreviewSceneName": s.preview,
                        "scenes": s.scenes.iter().enumerate().rev().map(|(i, x)| json!({"sceneName": x, "sceneIndex": n - 1 - i})).collect::<Vec<_>>()});
                }
                "GetStreamStatus" => out = s.stream.clone(),
                "GetRecordStatus" => out = s.record.clone(),
                "GetStats" => out = s.stats.clone(),
                "SetCurrentProgramScene" => {
                    ok = s.scenes.contains(&name);
                    if ok {
                        s.program = name.clone();
                        later.push(("CurrentProgramSceneChanged", json!({"sceneName": name})));
                    }
                }
                "SendStreamCaption" => {
                    ok = s.stream["outputActive"] == json!(true);
                    if ok {
                        s.captions.push(data["captionText"].as_str().unwrap_or("").to_owned());
                    }
                }
                "GetStudioModeEnabled" => out = json!({"studioModeEnabled": s.studio}),
                "SetStudioModeEnabled" => {
                    s.studio = data["studioModeEnabled"] == json!(true);
                    s.preview = s.studio.then(|| s.program.clone());
                    later.push(("StudioModeStateChanged", json!({"studioModeEnabled": s.studio})));
                }
                "SetCurrentPreviewScene" => {
                    ok = s.studio && s.scenes.contains(&name);
                    if ok {
                        s.preview = Some(name.clone());
                        later.push(("CurrentPreviewSceneChanged", json!({"sceneName": name})));
                    }
                }
                "TriggerStudioModeTransition" => {
                    ok = s.studio;
                    if ok {
                        let (p, v) = (s.program.clone(), s.preview.clone().unwrap_or_default());
                        s.program = v.clone();
                        s.preview = Some(p.clone());
                        later.push(("CurrentProgramSceneChanged", json!({"sceneName": v})));
                        later.push(("CurrentPreviewSceneChanged", json!({"sceneName": p})));
                    }
                }
                "GetReplayBufferStatus" => {
                    ok = s.replay_on;
                    out = json!({"outputActive": s.replay_active});
                }
                "StartReplayBuffer" | "StopReplayBuffer" => {
                    s.replay_active = kind == "StartReplayBuffer";
                    later.push(("ReplayBufferStateChanged", json!({"outputActive": s.replay_active})));
                }
                "SaveReplayBuffer" => {
                    ok = s.replay_active;
                    if ok {
                        later.push(("ReplayBufferSaved", json!({"savedReplayPath": "C:\\Videos\\Replay 1.mkv"})));
                    }
                }
                "GetStreamServiceSettings" => {
                    out = json!({"streamServiceType": "rtmp_common", "streamServiceSettings": {"service": "YouTube - RTMPS", "key": "SECRET-KEY-abcd", "server": "rtmps://a.rtmps.youtube.com:443/live2", "broadcast_id": "dQw4w9WgXcQ"}});
                }
                "GetInputList" => out = json!({"inputs": s.inputs.iter().map(|i| json!({"inputName": i.0, "inputKind": "x"})).collect::<Vec<_>>()}),
                "GetInputMute" => {
                    let found = s.inputs.iter().find(|i| i.0 == data["inputName"].as_str().unwrap_or("")).and_then(|i| i.1);
                    ok = found.is_some();
                    out = json!({"inputMuted": found.unwrap_or(false)});
                }
                "SetInputMute" => {
                    let n = data["inputName"].as_str().unwrap_or("").to_owned();
                    ok = s.inputs.iter().any(|i| i.0 == n && i.1.is_some());
                    if ok {
                        for i in &mut s.inputs {
                            if i.0 == n {
                                i.1 = Some(data["inputMuted"] == json!(true));
                            }
                        }
                        later.push(("InputMuteStateChanged", json!({"inputName": n, "inputMuted": data["inputMuted"]})));
                    }
                }
                "StartStream" | "StopStream" => {
                    let on = kind == "StartStream";
                    s.stream["outputActive"] = json!(on);
                    later.push(("StreamStateChanged", json!({"outputActive": on, "outputState": if on { "OBS_WEBSOCKET_OUTPUT_STARTING" } else { "OBS_WEBSOCKET_OUTPUT_STOPPING" }})));
                    later.push(("StreamStateChanged", json!({"outputActive": on, "outputState": if on { "OBS_WEBSOCKET_OUTPUT_STARTED" } else { "OBS_WEBSOCKET_OUTPUT_STOPPED" }})));
                }
                "StartRecord" | "StopRecord" => {
                    s.record["outputActive"] = json!(kind == "StartRecord");
                    s.record["outputPaused"] = json!(false);
                }
                "PauseRecord" | "ResumeRecord" => {
                    if s.can_pause {
                        s.record["outputPaused"] = json!(kind == "PauseRecord");
                    }
                }
                "GetSourceScreenshot" => out = json!({"imageData": format!("data:image/jpg;base64,{}", base64::engine::general_purpose::STANDARD.encode(JPEG))}),
                _ => {}
            }
            (ok, out)
        };
        for (k, v) in later {
            self.event(k, v);
        }
        (ok, out)
    }
}

// ------------------------------------------------------------------------------------------------------------------- stand-ins for core
#[derive(Default)]
pub struct Board {
    pub streams: Mutex<HashMap<String, StreamEntry>>,
    pub tally: Mutex<HashMap<String, Vec<Value>>>,
    pub presses: Mutex<Vec<(String, String)>>,
    pub on: Mutex<Vec<String>>,
    pub audit: Mutex<Vec<(String, String, String)>>,
    pub notes: Mutex<Vec<(String, String)>>,
    pub refuse: Mutex<Vec<&'static str>>,
}

pub struct Streams(pub Arc<Board>);
impl StreamBoard for Streams {
    fn publish(&self, source: &str, e: StreamEntry) -> Result<(), &'static str> {
        self.0.streams.lock().unwrap().insert(source.to_owned(), e);
        Ok(())
    }
    fn clear(&self, source: &str) {
        self.0.streams.lock().unwrap().remove(source);
    }
}

pub struct Tally(pub Arc<Board>);
impl TallyBoard for Tally {
    fn publish(&self, source: &str, cameras: Vec<Value>) {
        self.0.tally.lock().unwrap().insert(source.to_owned(), cameras);
    }
    fn clear(&self, source: &str) {
        self.0.tally.lock().unwrap().remove(source);
    }
}

pub struct Buttons(pub Arc<Board>);
impl AlertButtons for Buttons {
    fn buttons(&self) -> Vec<(String, String)> {
        vec![("bad".into(), "Bad".into()), ("meh".into(), "Meh".into())]
    }
    fn exists(&self, id: &str) -> bool {
        id == "bad" || id == "meh"
    }
    fn is_on(&self, id: &str) -> bool {
        self.0.on.lock().unwrap().iter().any(|x| x == id)
    }
    fn press(&self, id: &str, sender: &str) -> bool {
        self.0.presses.lock().unwrap().push((id.to_owned(), sender.to_owned()));
        true
    }
}

impl Authorizer for Board {
    fn require<'a>(&'a self, _r: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async move {
            if self.refuse.lock().unwrap().contains(&permission) {
                let mut r = Response::new(Body::empty());
                *r.status_mut() = StatusCode::FORBIDDEN;
                return Err(r);
            }
            Ok(())
        })
    }
    fn note<'a>(&'a self, _r: &'a Parts, action: &'a str, summary: &'a str, _t: &'a str, _d: Option<Value>) -> BoxFuture<'a, ()> {
        Box::pin(async move { self.notes.lock().unwrap().push((action.to_owned(), summary.to_owned())) })
    }
    fn record<'a>(&'a self, action: &'a str, summary: &'a str, target: &'a str) -> BoxFuture<'a, ()> {
        Box::pin(async move { self.audit.lock().unwrap().push((action.to_owned(), summary.to_owned(), target.to_owned())) })
    }
}

pub struct Store(pub Mutex<HashMap<String, Value>>);

impl SettingsStore for Store {
    fn read(&self, key: &str) -> Option<Value> {
        self.0.lock().unwrap().get(key).cloned()
    }
    fn save(&self, key: &str, changes: &Value) -> Result<(), relay_core::AppError> {
        let mut m = self.0.lock().unwrap();
        let mut cur = m.get(key).cloned().unwrap_or_else(|| json!({}));
        for (k, v) in changes.as_object().into_iter().flatten() {
            cur[k] = v.clone();
        }
        m.insert(key.to_owned(), cur);
        Ok(())
    }
    fn replace(&self, key: &str, value: &Value) -> Result<(), relay_core::AppError> {
        self.0.lock().unwrap().insert(key.to_owned(), value.clone());
        Ok(())
    }
    fn add_secret(&self, _key: &str, _field: &str) {}
}

pub struct Rig {
    pub module: Arc<Obs>,
    pub host: Host,
    pub store: Arc<Store>,
    pub board: Arc<Board>,
    pub hub: Arc<Hub>,
    pub fake: Arc<Fake>,
}

pub fn quick() -> Timing {
    Timing {
        poll: Duration::from_millis(100),
        retry: vec![Duration::from_millis(100), Duration::from_millis(200)],
        caption_gap: Duration::from_millis(10),
        pause_check: Duration::from_millis(20),
        window: Duration::from_secs(60),
        unexpected: Duration::from_secs(600),
        preview_min: Duration::from_millis(50),
    }
}

static NEXT: AtomicU64 = AtomicU64::new(0);

pub async fn rig(password: &str) -> Rig {
    let _ = NEXT.fetch_add(1, Ordering::SeqCst);
    let fake = Fake::start(password).await;
    let store = Arc::new(Store(Mutex::new(HashMap::new())));
    let host = Host::new(Shutdown::new()).with_store(store.clone());
    let board = Arc::new(Board::default());
    host.set_authorizer(board.clone());
    let hub = Hub::new();
    host.provide("core", HUB, Arc::new(HubCap(Arc::clone(&hub))));
    host.provide("core", STREAMS, Arc::new(StreamsCap(Arc::new(Streams(board.clone())))));
    host.provide("core", TALLY, Arc::new(TallyCap(Arc::new(Tally(board.clone())))));
    host.provide("core", ALERT_BUTTONS, Arc::new(AlertButtonsCap(Arc::new(Buttons(board.clone())))));
    let module = Arc::new(Obs::new().with_timing(quick()));
    relay_core::Module::attach(&*module, &host);
    Rig { module, host, store, board, hub, fake }
}

impl Rig {
    /// Save one computer pointing at the fake, with `extra` on top, and the password.
    pub fn configure(&self, extra: Value, password: &str) {
        let mut m = json!({"id": "m1", "name": "Stream PC", "host": "127.0.0.1", "port": self.fake.port});
        for (k, v) in extra.as_object().into_iter().flatten() {
            m[k] = v.clone();
        }
        let pw = if password.is_empty() { String::new() } else { json!({"m1": password}).to_string() };
        self.store.replace("obs", &json!({"machines": [m], "passwords": pw})).unwrap();
    }

    pub async fn start(&self) {
        relay_core::Module::start(&*self.module, self.host.clone(), json!({})).await.expect("start");
    }

    pub async fn stop(&self) {
        relay_core::Module::stop(&*self.module).await;
    }

    pub fn machine(&self) -> Arc<relay_module_obs::machine::Machine> {
        self.module.ctx().unwrap().service.machine("m1").expect("machine m1")
    }

    pub fn module_health(&self) -> relay_core::HealthState {
        relay_core::Module::health(&*self.module).state
    }

    /// The saved settings as stored.
    pub fn store_obs(&self) -> Value {
        self.store.read("obs").unwrap_or_default()
    }

    /// The scene-screen key of computer `m1`, as saved.
    pub fn store_key(&self) -> String {
        self.store_obs()["machines"][0]["key"].as_str().unwrap_or("").to_owned()
    }

    pub async fn send(&self, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value, String) {
        let mut req = Request::builder().method(method).uri(uri).header("content-type", "application/json").header("host", "relay.local:8443").body(body.map_or_else(Body::empty, |b| Body::from(b.to_string()))).unwrap();
        req.extensions_mut().insert(Peer([10, 0, 0, 9].into()));
        let res = relay_core::Module::routes(&*self.module).oneshot(req).await.unwrap();
        let status = res.status();
        let bytes = res.into_body().collect().await.unwrap().to_bytes();
        (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null), String::from_utf8_lossy(&bytes).into_owned())
    }
}

/// Wait up to `secs` for `cond`; polls, never a fixed sleep.
pub async fn until(secs: f64, mut cond: impl FnMut() -> bool) -> bool {
    let end = tokio::time::Instant::now() + Duration::from_secs_f64(secs);
    while tokio::time::Instant::now() < end {
        if cond() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    cond()
}
