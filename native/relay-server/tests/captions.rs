//! Live captions over HTTP (#659), on a real database and a real config.json, with the audio, the socket and the chat model played
//! by the test: what `tests/test_stream.py`, `tests/test_realtime.py` and the Python routes check, through the router the server serves.

use std::collections::VecDeque;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::http::{header, Method, Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use relay_auth::{Auth, Cost, NewUser};
use relay_captions::capture::Consumers;
use relay_captions::clock;
use relay_captions::engine::{Alone, Capture, Captions, Parts};
use relay_captions::recorder::Recorder;
use relay_captions::session::{AudioFeed, Conn, Connect, Incoming, Outgoing, WsRequest};
use relay_captions::transcribe::Backends;
use relay_captions::translate::Chat;
use relay_core::future::BoxFuture;
use relay_core::hub::Hub;
use relay_server::accounts::Accounts;
use relay_server::captions::{assemble, routes, Assemble, ConfigSettings};
use relay_server::forward::Conn as Peer;
use relay_server::pages::Pages;
use serde_json::{json, Value};
use tokio::sync::mpsc;
use tower::ServiceExt;

const LAN: &str = "192.168.1.50:5000";

fn password() -> &'static str {
    static P: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    P.get_or_init(|| format!("{:016x}{:016x}", rand::random::<u64>(), rand::random::<u64>()))
}

struct Mic {
    consumers: Arc<Consumers>,
    open: AtomicBool,
}

impl Capture for Mic {
    fn start(&self, _lane: &str, _gate: bool) -> Result<(), String> {
        self.open.store(true, Ordering::SeqCst);
        Ok(())
    }
    fn stop(&self) {
        self.open.store(false, Ordering::SeqCst);
    }
    fn status(&self) -> Value {
        json!({"running": self.open.load(Ordering::SeqCst), "level": 0.1, "rms_dbfs": -20.0, "peak_dbfs": -10.0, "clipping": false, "clipped_samples": 0, "speaking": false})
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
}

impl Connect for Socket {
    fn open<'a>(&'a self, _req: &'a WsRequest) -> BoxFuture<'a, Result<Conn, String>> {
        Box::pin(async move {
            let events = self.events.lock().unwrap().pop_front().unwrap_or_default();
            let (out_tx, mut out_rx) = mpsc::channel::<Outgoing>(64);
            let (inc_tx, inc_rx) = mpsc::channel::<Incoming>(64);
            tokio::spawn(async move {
                for e in events {
                    let _ = inc_tx.send(Incoming::Text(e.to_string())).await;
                }
                while out_rx.recv().await.is_some() {}
            });
            Ok(Conn { out: out_tx, inc: inc_rx })
        })
    }
}

struct Model;

impl Chat for Model {
    fn complete<'a>(&'a self, _url: &'a str, _key: &'a str, body: &'a Value) -> BoxFuture<'a, Result<Value, String>> {
        Box::pin(async move {
            let user: Value = serde_json::from_str(body["messages"][1]["content"].as_str().unwrap()).unwrap();
            let code = user["languages"].as_object().unwrap().keys().next().unwrap().clone();
            let text = format!("[{code}] {}", user["text"].as_str().unwrap());
            Ok(json!({"choices": [{"message": {"content": json!({code: text}).to_string()}}]}))
        })
    }
}

struct Site {
    dir: PathBuf,
    auth: Auth,
    router: Router,
    shutdown: relay_core::Shutdown,
    captions: Arc<Captions>,
}

impl Drop for Site {
    fn drop(&mut self) {
        self.shutdown.trigger();
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

struct Reply {
    status: StatusCode,
    body: Vec<u8>,
    headers: axum::http::HeaderMap,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_slice(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", String::from_utf8_lossy(&self.body)))
    }
}

async fn site(tag: &str, config: Value, events: Vec<Vec<Value>>) -> Site {
    let dir = std::env::temp_dir().join(format!("relay-captions-routes-{tag}-{}-{}", std::process::id(), relay_auth::now().replace(':', "")));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("config.json"), config.to_string()).unwrap();
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.unwrap();
    let app_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../app");
    let acc = Accounts::new(auth.clone(), Pages::new(&app_dir), dir.join("config.json"));
    let shutdown = relay_core::Shutdown::new();
    let hub = Hub::with(relay_captions::redact::factory(), Box::new(clock::wall));
    let cfg = Arc::new(relay_config::Settings::new(&dir.join("config.json")));
    let settings = Arc::new(ConfigSettings::new(Arc::clone(&cfg), dir.clone()));
    let captions = Captions::new(Parts {
        hub: Arc::clone(&hub),
        settings: settings.clone(),
        capture: Arc::new(Mic { consumers: Consumers::new(), open: AtomicBool::new(false) }),
        chat: Arc::new(Model),
        connect: Arc::new(Socket { events: Mutex::new(events.into()) }),
        outside: Arc::new(Alone),
        backends: Backends::builtin(),
        recorder: Recorder::new(clock::wall, clock::relay_zone),
    });
    let api = assemble(Assemble { acc, captions: Arc::clone(&captions), hub, config: cfg, settings, show: None, upstream: None, shutdown: shutdown.clone(), registry: None });
    Site { dir, auth, router: routes(api), shutdown, captions }
}

impl Site {
    async fn raw(&self, method: Method, uri: &str, cookie: &str, secure: bool, body: Option<Value>) -> axum::http::Response<Body> {
        let mut req = Request::builder().method(method).uri(uri);
        if !cookie.is_empty() {
            req = req.header("cookie", cookie);
        }
        let body = match body {
            Some(b) => {
                req = req.header(header::CONTENT_TYPE, "application/json");
                Body::from(b.to_string())
            }
            None => Body::empty(),
        };
        let mut req = req.body(body).unwrap();
        req.extensions_mut().insert(Peer { peer: LAN.parse::<SocketAddr>().unwrap(), local_port: if secure { 8443 } else { 8000 }, secure });
        self.router.clone().oneshot(req).await.unwrap()
    }

    async fn send(&self, method: Method, uri: &str, cookie: &str, secure: bool, body: Option<Value>) -> Reply {
        let (parts, body) = self.raw(method, uri, cookie, secure, body).await.into_parts();
        Reply { status: parts.status, headers: parts.headers, body: body.collect().await.unwrap().to_bytes().to_vec() }
    }

    async fn viewer(&self, uri: &str) -> Reply {
        self.send(Method::GET, uri, "", false, None).await
    }

    async fn admin(&self, method: Method, uri: &str, cookie: &str, body: Option<Value>) -> Reply {
        self.send(method, uri, cookie, true, body).await
    }

    async fn cookie(&self, username: &str, role: &str) -> String {
        let user = self.auth.create_user(NewUser { username, name: username, role, password: password(), temporary: false }, None).await.unwrap();
        format!("relay_admin={}", self.auth.start_session(&user, "password", None, "").await.unwrap().token)
    }

    async fn audited(&self, kind: &str) -> Vec<String> {
        let found = self.auth.search_audit(relay_auth::Search { kind: Some(kind), ..Default::default() }).await.unwrap();
        found.entries.iter().map(|e| e.action.clone()).collect()
    }
}

/// SSE frames off a stream until `done` says it has what it wants.
async fn read_frames(resp: axum::http::Response<Body>, mut done: impl FnMut(&[Value]) -> bool) -> Vec<Value> {
    let mut body = resp.into_body();
    let (mut text, mut out) = (String::new(), Vec::new());
    while !done(&out) {
        let chunk = tokio::time::timeout(Duration::from_secs(5), body.frame()).await.expect("a frame arrives").expect("the stream is open").unwrap();
        if let Some(data) = chunk.data_ref() {
            text.push_str(&String::from_utf8_lossy(data));
        }
        while let Some(end) = text.find("\n\n") {
            let frame: String = text.drain(..end + 2).collect();
            if let Some(json) = frame.lines().find_map(|l| l.strip_prefix("data: ")) {
                out.push(serde_json::from_str(json).unwrap());
            }
        }
    }
    out
}

fn config(dir_recording: bool) -> Value {
    json!({
        "vercel_api_key": "vk",
        "source_language": "ENGLISH",
        "targets": {"SPANISH": {"enabled": true}},
        "audio_lane": "1",
        "realtime": {"backend": "scribe"},
        "recording": {"enabled": dir_recording, "keep_runs": 5},
    })
}

fn say(text: &str) -> Vec<Value> {
    vec![json!({"type": "transcript-final", "text": text})]
}

async fn until(what: &str, mut ok: impl FnMut() -> bool) {
    for _ in 0..500 {
        if ok() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("never: {what}");
}

#[tokio::test]
async fn start_and_stop_need_the_right_role_and_are_audited() {
    let s = site("auth", config(false), vec![]).await;
    assert_eq!(s.admin(Method::POST, "/api/admin/start", "", None).await.status, StatusCode::UNAUTHORIZED);
    assert_eq!(s.admin(Method::GET, "/api/admin/status", "", None).await.status, StatusCode::UNAUTHORIZED);
    let viewer = s.cookie("view1", "viewer").await;
    assert_eq!(s.admin(Method::GET, "/api/admin/status", &viewer, None).await.status, StatusCode::OK, "a viewer may look");
    assert_eq!(s.admin(Method::POST, "/api/admin/start", &viewer, None).await.status, StatusCode::FORBIDDEN);
    let op = s.cookie("pat", "admin").await;
    let started = s.admin(Method::POST, "/api/admin/start", &op, None).await;
    assert_eq!(started.status, StatusCode::OK);
    let body = started.json();
    assert_eq!((body["running"].clone(), body["captions_running"].clone(), body["backend"]["id"].clone()), (json!(true), json!(true), json!("scribe")));
    assert_eq!(s.audited("session.start").await, ["session.start"]);
    let status = s.admin(Method::GET, "/api/admin/status", &viewer, None).await.json();
    assert_eq!(status["audio"]["running"], true);
    let stopped = s.admin(Method::POST, "/api/admin/stop", &op, None).await.json();
    assert_eq!((stopped["running"].clone(), stopped["captions_running"].clone()), (json!(false), json!(false)));
    assert_eq!(s.audited("session.stop").await, ["session.stop"]);
}

#[tokio::test]
async fn a_refused_start_is_a_400_with_the_sentence_that_names_the_key() {
    let mut cfg = config(false);
    cfg["vercel_api_key"] = json!("");
    let s = site("refused", cfg, vec![]).await;
    let op = s.cookie("pat", "admin").await;
    let r = s.admin(Method::POST, "/api/admin/start", &op, None).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    let body = r.json();
    assert!(body["error"].as_str().unwrap().starts_with("No Vercel AI Gateway API key set."), "{body}");
    assert_eq!(body["running"], false, "the status rides along");
    assert!(s.audited("session.start").await.is_empty(), "a refused press is not a change");
}

#[tokio::test]
async fn a_caption_line_reaches_the_viewers_in_both_languages_over_the_stream() {
    let s = site("feeds", config(false), vec![say("Good morning.")]).await;
    let op = s.cookie("pat", "admin").await;
    // Open the feeds first, so the line arrives as it is published, not as history.
    let source = s.raw(Method::GET, "/stream?stream=transcription", "", false, None).await;
    let spanish = s.raw(Method::GET, "/stream?stream=translation&lang=spanish", "", false, None).await;
    assert_eq!(s.admin(Method::POST, "/api/admin/start", &op, None).await.status, StatusCode::OK);
    let got = read_frames(source, |f| f.iter().any(|m| m["final"] == true)).await;
    assert_eq!(got[0]["type"], "snapshot");
    assert_eq!(got.iter().find(|m| m["final"] == true).unwrap()["text"], "Good morning.");
    let got = read_frames(spanish, |f| f.iter().any(|m| m["final"] == true)).await;
    assert_eq!(got.iter().find(|m| m["final"] == true).unwrap()["text"], "[es] Good morning.");
    assert_eq!(s.viewer("/stream?stream=translation&lang=klingon").await.status, StatusCode::BAD_REQUEST);
    assert_eq!(s.viewer("/stream?stream=nothing").await.status, StatusCode::BAD_REQUEST);
    s.captions.stop().await;
}

#[tokio::test]
async fn the_picker_lists_only_what_is_live_and_the_operator_switches_targets() {
    let s = site("targets", config(false), vec![]).await;
    let op = s.cookie("pat", "admin").await;
    let before = s.viewer("/api/targets").await.json();
    assert!(before.get("type").is_none());
    assert_eq!((before["live"].clone(), before["running"].clone()), (json!([]), json!(false)), "nothing is live until Start");
    s.admin(Method::POST, "/api/admin/start", &op, None).await;
    let live = s.viewer("/api/targets").await.json();
    assert_eq!(live["live"][0]["target"], "SPANISH");
    assert_eq!(live["running"], true);
    let r = s.admin(Method::POST, "/api/admin/target/french", &op, Some(json!({"enabled": true}))).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(s.viewer("/api/targets").await.json()["live"].as_array().unwrap().len(), 2);
    assert_eq!(s.admin(Method::POST, "/api/admin/target/klingon", &op, Some(json!({"enabled": true}))).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.admin(Method::POST, "/api/admin/target/french", &op, Some(json!([1]))).await.status, StatusCode::UNPROCESSABLE_ENTITY);
    let viewer = s.cookie("view1", "viewer").await;
    assert_eq!(s.admin(Method::POST, "/api/admin/target/french", &viewer, Some(json!({"enabled": false}))).await.status, StatusCode::FORBIDDEN);
    assert_eq!(s.audited("session.target").await, ["session.target"]);
    let saved: Value = serde_json::from_str(&std::fs::read_to_string(s.dir.join("config.json")).unwrap()).unwrap();
    assert_eq!(saved["targets"]["FRENCH"]["enabled"], true, "saved to config.json");
    s.captions.stop().await;
}

#[tokio::test]
async fn a_recorded_run_is_listed_downloaded_and_deleted() {
    let s = site("recordings", config(true), vec![say("Welcome home.")]).await;
    let op = s.cookie("pat", "admin").await;
    s.admin(Method::POST, "/api/admin/start", &op, None).await;
    let listed = || async { s.admin(Method::GET, "/api/admin/recordings", &op, None).await.json() };
    let mut run = String::new();
    until("two lines recorded", || {
        let runs = s.captions.recorder().lock().unwrap().list_runs();
        run = runs.first().and_then(|r| r["run_id"].as_str()).unwrap_or("").to_owned();
        runs.first().is_some_and(|r| r["lines"] == 2)
    })
    .await;
    let list = listed().await;
    assert_eq!((list["enabled"].clone(), list["keep_runs"].clone()), (json!(true), json!(5)));
    assert_eq!(list["runs"][0]["recording"], true);
    assert_eq!(s.admin(Method::DELETE, &format!("/api/admin/recordings/{run}"), &op, None).await.status, StatusCode::CONFLICT, "the run being written is refused");
    s.admin(Method::POST, "/api/admin/stop", &op, None).await;

    let one = s.admin(Method::GET, &format!("/api/admin/recordings/{run}"), &op, None).await.json();
    assert_eq!(one["run"]["lines"], 2);
    let names: Vec<&str> = one["files"].as_array().unwrap().iter().map(|f| f["name"].as_str().unwrap()).collect();
    assert!(names.contains(&"transcript-ENGLISH.txt") && names.contains(&"pairs-SPANISH.jsonl") && names.contains(&"manifest.txt"), "{names:?}");

    let zip = s.admin(Method::GET, &format!("/api/admin/recordings/{run}/export.zip"), &op, None).await;
    assert_eq!((zip.status, &zip.body[..2]), (StatusCode::OK, &b"PK"[..]));
    assert_eq!(zip.headers[header::CONTENT_TYPE], "application/zip");
    assert_eq!(zip.headers[header::CONTENT_DISPOSITION], format!("attachment; filename=\"relay-{run}.zip\""));
    let file = s.admin(Method::GET, &format!("/api/admin/recordings/{run}/file/transcript-ENGLISH.txt"), &op, None).await;
    assert_eq!(file.status, StatusCode::OK);
    assert!(String::from_utf8_lossy(&file.body).contains("Welcome home."));
    assert_eq!(file.headers[header::CONTENT_TYPE], "text/plain; charset=utf-8");
    assert_eq!(s.admin(Method::GET, &format!("/api/admin/recordings/{run}/file/nope.txt"), &op, None).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.admin(Method::GET, "/api/admin/recordings/..%2Fetc", &op, None).await.status, StatusCode::NOT_FOUND, "a request path is never a file path");
    assert_eq!(s.admin(Method::GET, "/api/admin/recordings/20260101-000000", &op, None).await.status, StatusCode::NOT_FOUND);

    let viewer = s.cookie("view1", "viewer").await;
    assert_eq!(s.admin(Method::GET, &format!("/api/admin/recordings/{run}"), &viewer, None).await.status, StatusCode::FORBIDDEN, "reading a transcript is the operator's");
    let operator = s.cookie("olly", "operator").await;
    assert_eq!(s.admin(Method::GET, &format!("/api/admin/recordings/{run}"), &operator, None).await.status, StatusCode::OK);
    assert_eq!(s.admin(Method::DELETE, &format!("/api/admin/recordings/{run}"), &operator, None).await.status, StatusCode::FORBIDDEN, "deleting is the admin's");
    let gone = s.admin(Method::DELETE, &format!("/api/admin/recordings/{run}"), &op, None).await;
    assert_eq!((gone.status, gone.json()["deleted"].clone(), gone.json()["runs"].clone()), (StatusCode::OK, json!(run), json!([])));
    assert_eq!(s.audited("recordings.delete").await, ["recordings.delete"]);
}

#[tokio::test]
async fn the_admin_status_stream_opens_with_the_status_then_the_meter() {
    let s = site("adminstream", config(false), vec![]).await;
    assert_eq!(s.admin(Method::GET, "/api/admin/status/stream", "", None).await.status, StatusCode::UNAUTHORIZED);
    let op = s.cookie("pat", "admin").await;
    let resp = s.raw(Method::GET, "/api/admin/status/stream", &op, true, None).await;
    assert_eq!(resp.status(), StatusCode::OK);
    let got = read_frames(resp, |f| f.len() >= 3).await;
    assert_eq!(got[0]["running"], false);
    assert!(got[0].get("blocklist_max").is_some(), "the first frame is the whole status");
    assert_eq!(got[1]["type"], "panels");
    assert_eq!(got[2]["type"], "alerts");
}
