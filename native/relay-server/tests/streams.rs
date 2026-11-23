//! The event streams and websockets (#656) over real sockets: what `tests/test_stream*.py`, `test_streamstatus.py` and the alert and
//! status checks hold of the Python server, plus 500 held-open viewers as `tools/stress.py sse` does. Each test serves the router on
//! a loopback port and talks to it with a plain TCP client, so the bytes checked are the bytes a browser would read.

use std::net::SocketAddr;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::extract::ws::WebSocketUpgrade;
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::Router;
use futures_util::{SinkExt, StreamExt};
use relay_config::{SecretBox, Settings, Store};
use relay_core::hub::Hub;
use relay_core::Shutdown;
use relay_server::status::{self, Publish, Show};
use relay_server::streams::{self, Streams};
use relay_server::ws::{self, Inbound, Options};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};

/// Long enough for a slow CI machine, short of the harness's limit.
const PATIENCE: Duration = Duration::from_secs(4);

struct Folder(PathBuf);

impl Folder {
    fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("relay-streams-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Folder(dir)
    }
}

impl Drop for Folder {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn settings(dir: &Folder) -> Arc<Settings> {
    let path = dir.0.join("config.json");
    Arc::new(Settings::with_secretbox(&path, SecretBox::with_store(&dir.0, Store::File)))
}

/// A show a test can change.
#[derive(Default)]
struct Room {
    running: AtomicBool,
    started: AtomicU64,
    meters: AtomicU64,
}

impl Show for Room {
    fn running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }
    fn started_at(&self) -> Option<f64> {
        Some(self.started.load(Ordering::SeqCst) as f64)
    }
    fn audio(&self) -> Value {
        json!({"running": self.running(), "rms_dbfs": -20.4})
    }
    fn live_languages(&self) -> Vec<String> {
        vec!["Spanish".into(), "French".into()]
    }
    fn meter(&self) -> Value {
        json!({"type": "meter", "n": self.meters.fetch_add(1, Ordering::SeqCst)})
    }
}

async fn serve(router: Router) -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

/// An HTTP/1.0 client: the server streams the body and closes at the end, with no chunking to undo.
struct Client {
    sock: TcpStream,
    buf: Vec<u8>,
    pub status: u16,
    pub head: String,
}

impl Client {
    async fn get(addr: SocketAddr, target: &str, extra: &[(&str, &str)]) -> Self {
        let mut sock = TcpStream::connect(addr).await.unwrap();
        let mut req = format!("GET {target} HTTP/1.0\r\nHost: test\r\n");
        for (k, v) in extra {
            req.push_str(&format!("{k}: {v}\r\n"));
        }
        req.push_str("\r\n");
        sock.write_all(req.as_bytes()).await.unwrap();
        let mut buf = Vec::new();
        let end = loop {
            let mut chunk = [0u8; 4096];
            let n = tokio::time::timeout(PATIENCE, sock.read(&mut chunk)).await.expect("no response head").unwrap();
            assert!(n > 0, "closed before the head");
            buf.extend_from_slice(&chunk[..n]);
            if let Some(i) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break i + 4;
            }
        };
        let head = String::from_utf8_lossy(&buf[..end]).into_owned();
        let status = head.split_whitespace().nth(1).unwrap().parse().unwrap();
        let rest = buf.split_off(end);
        Client { sock, buf: rest, status, head }
    }

    fn header(&self, name: &str) -> Option<String> {
        self.head.lines().skip(1).find_map(|l| {
            let (k, v) = l.split_once(':')?;
            k.eq_ignore_ascii_case(name).then(|| v.trim().to_owned())
        })
    }

    /// The next whole event (everything up to a blank line), or `None` at the end of the stream.
    async fn event(&mut self) -> Option<String> {
        self.event_within(PATIENCE).await
    }

    async fn event_within(&mut self, wait: Duration) -> Option<String> {
        let started = Instant::now();
        loop {
            if let Some(i) = self.buf.windows(2).position(|w| w == b"\n\n") {
                let ev: Vec<u8> = self.buf.drain(..i + 2).collect();
                return Some(String::from_utf8(ev).unwrap());
            }
            let left = wait.checked_sub(started.elapsed()).unwrap_or_default();
            let mut chunk = [0u8; 8192];
            match tokio::time::timeout(left, self.sock.read(&mut chunk)).await {
                Err(_) => panic!("no event within {wait:?}; have {:?}", String::from_utf8_lossy(&self.buf)),
                Ok(Ok(0)) | Ok(Err(_)) => return None,
                Ok(Ok(n)) => self.buf.extend_from_slice(&chunk[..n]),
            }
        }
    }

    async fn body(mut self) -> String {
        let mut rest = Vec::new();
        let _ = tokio::time::timeout(PATIENCE, self.sock.read_to_end(&mut rest)).await;
        self.buf.extend(rest);
        String::from_utf8_lossy(&self.buf).into_owned()
    }
}

fn data(event: &str) -> Value {
    let line = event.lines().find_map(|l| l.strip_prefix("data: ")).unwrap_or_else(|| panic!("no data line in {event:?}"));
    serde_json::from_str(line).unwrap()
}

fn id(event: &str) -> Option<u64> {
    event.lines().find_map(|l| l.strip_prefix("id: ")).and_then(|n| n.parse().ok())
}

struct Site {
    addr: SocketAddr,
    hub: Arc<Hub>,
    streams: Arc<Streams>,
    room: Arc<Room>,
    shutdown: Shutdown,
    _dir: Folder,
}

async fn site(name: &str, tune: impl FnOnce(Streams) -> Streams) -> Site {
    let dir = Folder::new(name);
    let hub = Hub::new();
    let room = Arc::new(Room::default());
    let shutdown = Shutdown::new();
    let streams = Arc::new(tune(Streams::new(Arc::clone(&hub), room.clone(), settings(&dir), shutdown.clone())));
    let addr = serve(streams::routes(Arc::clone(&streams))).await;
    Site { addr, hub, streams, room, shutdown, _dir: dir }
}

/// Wait until `cond` holds (a task has noticed something), without a fixed sleep.
async fn until(what: &str, mut cond: impl FnMut() -> bool) {
    let started = Instant::now();
    while !cond() {
        assert!(started.elapsed() < PATIENCE, "gave up waiting for {what}");
        tokio::time::sleep(Duration::from_millis(5)).await; // allow-sleep: the poll interval of a wait_for
    }
}

// ---- the caption feeds ------------------------------------------------------------------------------------------------------

#[tokio::test]
async fn a_caption_feed_is_an_event_stream_that_starts_with_a_snapshot() {
    let s = site("feed", |s| s).await;
    s.hub.publish_delta("translation", "SPANISH", "Hola");
    s.hub.publish_final("translation", "SPANISH", None);
    let mut c = Client::get(s.addr, "/stream?stream=translation&lang=spanish", &[]).await;
    assert_eq!(c.status, 200);
    assert_eq!(c.header("content-type").as_deref(), Some("text/event-stream"));
    assert_eq!(c.header("cache-control").as_deref(), Some("no-cache, no-transform"));
    assert_eq!(c.header("x-accel-buffering").as_deref(), Some("no"), "so a proxy does not hold the frames back");
    let snap = c.event().await.unwrap();
    assert_eq!(id(&snap), None);
    assert_eq!(data(&snap)["type"], "snapshot");
    assert_eq!(data(&snap)["lines"][0]["text"], "Hola");
    until("the viewer to be counted", || s.hub.viewer_count() == 1).await;
    s.hub.publish_delta("translation", "SPANISH", " amigos");
    let live = c.event().await.unwrap();
    assert_eq!((id(&live), data(&live)["delta"].clone()), (Some(3), json!(" amigos")));
}

#[tokio::test]
async fn the_default_stream_is_translation_and_transcription_follows_the_source_language() {
    let s = site("defaults", |s| s).await;
    let mut a = Client::get(s.addr, "/stream?lang=FRENCH", &[]).await;
    assert_eq!(data(&a.event().await.unwrap())["stream"], "translation");
    let mut b = Client::get(s.addr, "/stream?stream=transcription", &[]).await;
    let snap = data(&b.event().await.unwrap());
    assert_eq!((snap["stream"].clone(), snap["lang"].clone()), (json!("transcription"), json!("ENGLISH")));
}

#[tokio::test]
async fn an_unknown_stream_or_language_is_refused_with_python_s_words() {
    let s = site("refuse", |s| s).await;
    for (target, sentence) in [
        ("/stream?stream=nonsense", "unknown stream"),
        ("/stream?stream=translation&lang=KLINGON", "unknown language"),
        ("/stream?stream=translation", "unknown language"),
    ] {
        let c = Client::get(s.addr, target, &[]).await;
        assert_eq!(c.status, StatusCode::BAD_REQUEST.as_u16(), "{target}");
        let body = c.body().await;
        assert_eq!(serde_json::from_str::<Value>(&body).unwrap(), json!({"detail": sentence}), "{target}");
    }
}

#[tokio::test]
async fn a_reconnect_with_last_event_id_replays_only_what_was_missed() {
    let s = site("resume", |s| s).await;
    for word in ["one", "two", "three"] {
        s.hub.publish_delta("translation", "SPANISH", word);
        s.hub.publish_final("translation", "SPANISH", None); // seqs 2, 4, 6
    }
    let mut c = Client::get(s.addr, "/stream?stream=translation&lang=SPANISH", &[("Last-Event-ID", "2")]).await;
    let a = c.event().await.unwrap();
    let b = c.event().await.unwrap();
    assert_eq!((id(&a), data(&a)["text"].clone()), (Some(4), json!("two")));
    assert_eq!((id(&b), data(&b)["text"].clone()), (Some(6), json!("three")));
    // by hand, with ?last_id=
    let mut d = Client::get(s.addr, "/stream?stream=translation&lang=SPANISH&last_id=4", &[]).await;
    assert_eq!(id(&d.event().await.unwrap()), Some(6));
    // a number that is not one is a fresh start
    let mut e = Client::get(s.addr, "/stream?stream=translation&lang=SPANISH", &[("Last-Event-ID", "abc")]).await;
    assert_eq!(data(&e.event().await.unwrap())["type"], "snapshot");
}

#[tokio::test]
async fn the_finals_feed_sees_lines_and_not_the_words_on_the_way() {
    let s = site("finals", |s| s).await;
    let mut c = Client::get(s.addr, "/stream?stream=translation&lang=SPANISH&finals=1", &[]).await;
    c.event().await.unwrap();
    until("the viewer", || s.hub.viewer_count() == 1).await;
    s.hub.publish_delta("translation", "SPANISH", "hel");
    s.hub.publish_delta("translation", "SPANISH", "lo");
    s.hub.publish_final("translation", "SPANISH", None);
    let line = c.event().await.unwrap();
    assert_eq!((id(&line), data(&line)["final"].clone()), (Some(3), json!(true)), "the first thing it saw was the line");
}

#[tokio::test]
async fn a_quiet_stream_gets_a_heartbeat_comment() {
    let s = site("beat", |s| s.with_timing(Duration::from_millis(80), Duration::from_secs(1))).await;
    let mut c = Client::get(s.addr, "/stream?stream=translation&lang=SPANISH", &[]).await;
    c.event().await.unwrap(); // the snapshot
    let beat = c.event().await.unwrap();
    assert_eq!(beat, ": ping\n\n");
    s.hub.publish_delta("translation", "SPANISH", "x");
    let mut next = c.event().await.unwrap();
    while next == ": ping\n\n" {
        next = c.event().await.unwrap();
    }
    assert_eq!(data(&next)["delta"], "x");
}

#[tokio::test]
async fn a_viewer_who_leaves_is_forgotten_and_a_slow_one_is_ended() {
    let s = site("leave", |s| s).await;
    let mut c = Client::get(s.addr, "/stream?stream=translation&lang=SPANISH", &[]).await;
    c.event().await.unwrap();
    until("the viewer", || s.hub.viewer_count() == 1).await;
    drop(c);
    // the task notices when its next write fails or the socket reads closed
    until("the hub to forget the viewer", || {
        s.hub.publish_delta("translation", "SPANISH", "x");
        s.hub.viewer_count() == 0
    })
    .await;
}

#[tokio::test]
async fn shutdown_ends_every_stream() {
    let s = site("shutdown", |s| s).await;
    let mut c = Client::get(s.addr, "/stream?stream=translation&lang=SPANISH", &[]).await;
    c.event().await.unwrap();
    s.shutdown.trigger();
    assert_eq!(c.event().await, None, "the stream ends instead of hanging");
}

#[tokio::test]
async fn a_module_serves_its_own_stream_while_it_is_on() {
    let s = site("provider", |s| s).await;
    let on = Arc::new(AtomicBool::new(true));
    let flag = Arc::clone(&on);
    s.streams.provide(
        "lyrics",
        Arc::new(move |q, _| {
            flag.load(Ordering::SeqCst).then(|| {
                let lang = q.get("lang").unwrap_or("").to_owned();
                streams::event_stream(move |tx| async move {
                    let _ = tx.send(relay_core::hub::sse_frame(&json!({"lyrics": lang}), None)).await;
                })
            })
        }),
    );
    let mut c = Client::get(s.addr, "/stream?stream=lyrics&lang=en", &[]).await;
    assert_eq!(data(&c.event().await.unwrap()), json!({"lyrics": "en"}));
    on.store(false, Ordering::SeqCst);
    assert_eq!(Client::get(s.addr, "/stream?stream=lyrics", &[]).await.status, 400, "off, it is an unknown stream");
    s.streams.withdraw("lyrics");
}

// ---- 500 viewers held open --------------------------------------------------------------------------------------------------

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
async fn five_hundred_held_open_viewers_all_keep_up() {
    let s = site("crowd", |s| s).await;
    let viewers = 500;
    let mut clients = Vec::new();
    for _ in 0..viewers {
        clients.push(Client::get(s.addr, "/stream?stream=translation&lang=SPANISH", &[]).await);
    }
    for c in &mut clients {
        assert_eq!(data(&c.event().await.unwrap())["type"], "snapshot");
    }
    until("every viewer to be counted", || s.hub.viewer_count() == viewers).await;
    let lines = 20;
    for n in 0..lines {
        s.hub.publish_delta("translation", "SPANISH", &format!("word{n} "));
        s.hub.publish_final("translation", "SPANISH", None);
    }
    let mut longest = Duration::ZERO;
    for (i, c) in clients.iter_mut().enumerate() {
        let started = Instant::now();
        for n in 0..lines * 2 {
            let ev = c.event().await.unwrap_or_else(|| panic!("viewer {i} was dropped at frame {n}"));
            assert_eq!(id(&ev), Some(n as u64 + 1), "viewer {i} saw frames in order");
        }
        longest = longest.max(started.elapsed());
    }
    assert_eq!(s.hub.viewer_count(), viewers, "nobody was dropped");
    assert!(longest < PATIENCE, "the slowest viewer waited {longest:?}");
}

// ---- the status feed --------------------------------------------------------------------------------------------------------

#[tokio::test]
async fn the_status_feed_is_the_whole_state_and_moves_when_something_shown_changes() {
    let s = site("status", |s| s).await;
    let mut c = Client::get(s.addr, "/stream?stream=status", &[]).await;
    assert_eq!(c.status, 200);
    let first = data(&c.event().await.unwrap());
    assert_eq!(first["type"], "status");
    assert_eq!(first["running"], false);
    assert_eq!(first["input"], json!({"state": "off", "dbfs": -20}));
    assert_eq!(first["languages"], json!(["Spanish", "French"]));
    assert_eq!(first["streams"], json!([]));
    assert_eq!(first["on_air"], Value::Null, "nothing says either way");
    assert!(first["server_time"].is_f64() && first.get("sound").is_none());
    assert_eq!(first["started_at"], Value::Null);

    s.room.running.store(true, Ordering::SeqCst);
    s.room.started.store(1234, Ordering::SeqCst);
    let next = data(&c.event_within(Duration::from_millis(2500)).await.unwrap());
    assert_eq!((next["running"].clone(), next["input"]["state"].clone(), next["started_at"].clone()), (json!(true), json!("ok"), json!(1234.0)));

    s.streams.status.publish("obs", Publish { label: "OBS", on_air: Some(true), health: "ok", live_since: Some(99.0), viewers: Some(3), detail: "secret reason" }).unwrap();
    let next = data(&c.event_within(Duration::from_millis(2500)).await.unwrap());
    assert_eq!(next["on_air"], true);
    assert_eq!(next["streams"][0], json!({"source": "obs", "label": "OBS", "on_air": true, "health": "ok", "live_since": 99.0, "viewers": 3}));
    assert!(!next.to_string().contains("secret reason"), "the viewer port never carries the reason");
}

#[test]
fn stream_status_validates_and_clears_like_python() {
    let st = status::StreamStatus::new();
    let p = |label: &'static str| Publish { label, on_air: None, health: "idle", live_since: Some(5.0), viewers: Some(-4), detail: "d" };
    assert!(st.publish("Bad Name", p("x")).is_err());
    assert!(st.publish("obs", Publish { health: "great", ..p("x") }).is_err());
    st.publish("obs", p("  OBS\u{7}  ")).unwrap();
    let e = st.entries(true);
    assert_eq!(e[0]["label"], "OBS", "unprintable characters go");
    assert_eq!(e[0]["live_since"], Value::Null, "no start time unless on air");
    assert_eq!(e[0]["viewers"], 0, "a negative count is zero");
    assert_eq!(e[0]["detail"], "d");
    assert!(st.entries(false)[0].get("detail").is_none());
    for n in 0..status::MAX_SOURCES {
        let _ = st.publish(&format!("m{n}"), p("m"));
    }
    assert!(st.publish("extra", p("e")).is_err(), "at most eight sources");
    assert!(st.publish("obs", p("again")).is_ok(), "an existing one may be replaced");
    st.clear("obs");
    assert!(st.entries(true).iter().all(|e| e["source"] != "obs"));
}

#[test]
fn the_input_state_follows_the_capture() {
    let ok = |audio: Value, running| status::input_state(&audio, running);
    assert_eq!(ok(json!({"running": true, "rms_dbfs": -20.0}), true), json!({"state": "ok", "dbfs": -20}));
    assert_eq!(ok(json!({"running": true, "rms_dbfs": -20.0}), false)["state"], "off");
    assert_eq!(ok(json!({"running": false}), true)["state"], "off");
    assert_eq!(ok(json!({"running": true, "reconnecting": true}), true)["state"], "lost");
    assert_eq!(ok(json!({"running": true, "error": "gone"}), true)["state"], "lost");
    assert_eq!(ok(json!({"running": true, "clipping": true, "rms_dbfs": -1.0}), true)["state"], "clipping");
    assert_eq!(ok(json!({"running": true, "rms_dbfs": -55.0}), true), json!({"state": "silent", "dbfs": -55}));
    assert_eq!(ok(json!({"running": true, "rms_dbfs": 7.0}), true)["dbfs"], 0, "clamped");
}

#[test]
fn a_change_is_everything_but_the_clock_and_the_exact_level() {
    let hub = Hub::new();
    let room = Room::default();
    let st = status::StreamStatus::new();
    let a = status::payload(&room, &hub, &st, 1.0, false);
    let b = status::payload(&room, &hub, &st, 2.0, false);
    assert_eq!(status::signature(&a), status::signature(&b));
    let mut louder = a.clone();
    louder["input"]["dbfs"] = json!(-10);
    assert_eq!(status::signature(&a), status::signature(&louder), "the dB moves every poll; only the state counts");
    let mut other = a.clone();
    other["viewers"] = json!(9);
    assert_ne!(status::signature(&a), status::signature(&other));
}

// ---- the admin status stream ------------------------------------------------------------------------------------------------

#[tokio::test]
async fn the_admin_status_stream_opens_with_the_full_state_then_meters_and_pushes() {
    let s = site("admin", |s| s.with_timing(Duration::from_secs(15), Duration::from_millis(60))).await;
    let mut c = Client::get(s.addr, "/api/admin/status/stream", &[]).await;
    assert_eq!(c.status, 200);
    assert_eq!(c.header("content-type").as_deref(), Some("text/event-stream"));
    let first = data(&c.event().await.unwrap());
    assert_eq!(first["running"], false, "the engine's status first");
    assert_eq!(data(&c.event().await.unwrap())["type"], "panels");
    assert_eq!(data(&c.event().await.unwrap())["type"], "alerts");
    let m = data(&c.event().await.unwrap());
    assert_eq!(m["type"], "meter", "between changes only the small meter frame is sent");
    s.hub.push_status(json!({"type": "status", "running": true}));
    let mut seen_push = false;
    for _ in 0..50 {
        let m = data(&c.event().await.unwrap());
        if m["type"] == "status" {
            assert_eq!(m["running"], true);
            seen_push = true;
            break;
        }
    }
    assert!(seen_push, "a pushed change reaches the stream");
    drop(c);
    until("the stream to unsubscribe", || s.hub.status_count() == 0).await;
}

#[tokio::test]
async fn the_admin_status_stream_needs_the_guard_to_pass() {
    let s = site("guard", |s| {
        s.guarded(|headers, _| async move {
            if headers.contains_key("x-allowed") {
                Ok(())
            } else {
                Err((StatusCode::UNAUTHORIZED, axum::Json(json!({"detail": "Sign in required"}))).into_response())
            }
        })
    })
    .await;
    let refused = Client::get(s.addr, "/api/admin/status/stream", &[]).await;
    assert_eq!(refused.status, 401);
    assert_eq!(s.hub.status_count(), 0, "nothing was subscribed");
    let mut ok = Client::get(s.addr, "/api/admin/status/stream", &[("x-allowed", "1")]).await;
    assert_eq!(ok.status, 200);
    assert_eq!(data(&ok.event().await.unwrap())["running"], false);
}

// ---- websockets -------------------------------------------------------------------------------------------------------------

async fn ws_site(shutdown: Shutdown, opts: Options) -> SocketAddr {
    let router = Router::new().route(
        "/ws/echo",
        get(move |upgrade: WebSocketUpgrade| async move {
            ws::serve(upgrade, shutdown, opts, |mut peer| async move {
                while let Some(msg) = peer.recv().await {
                    match msg {
                        Inbound::Text(t) if t == "bye" => {
                            peer.close(4000, "done").await;
                            return;
                        }
                        Inbound::Text(t) => {
                            if !peer.send_text(format!("echo {t}")).await {
                                return;
                            }
                        }
                        Inbound::Binary(b) => {
                            if !peer.send_binary(b).await {
                                return;
                            }
                        }
                    }
                }
            })
        }),
    );
    serve(router).await
}

type Client2 = tokio_tungstenite::WebSocketStream<tokio_tungstenite::MaybeTlsStream<TcpStream>>;

async fn connect(addr: SocketAddr) -> Client2 {
    tokio_tungstenite::connect_async(format!("ws://{addr}/ws/echo")).await.unwrap().0
}

use tokio_tungstenite::tungstenite::Message;

async fn next(c: &mut Client2) -> Option<Message> {
    tokio::time::timeout(PATIENCE, c.next()).await.expect("nothing from the server").and_then(Result::ok)
}

fn quick() -> Options {
    Options { ping: Duration::from_millis(60), idle: Duration::from_secs(3), max_message: 1024 }
}

#[tokio::test]
async fn a_websocket_carries_text_and_binary_both_ways() {
    let addr = ws_site(Shutdown::new(), Options::default()).await;
    let mut c = connect(addr).await;
    c.send(Message::text("hello")).await.unwrap();
    assert_eq!(next(&mut c).await, Some(Message::text("echo hello")));
    c.send(Message::binary(vec![1u8, 2, 3])).await.unwrap();
    assert_eq!(next(&mut c).await, Some(Message::binary(vec![1u8, 2, 3])));
}

#[tokio::test]
async fn a_quiet_websocket_is_pinged() {
    let addr = ws_site(Shutdown::new(), quick()).await;
    let mut c = connect(addr).await;
    assert!(matches!(next(&mut c).await, Some(Message::Ping(_))), "the server keeps a quiet socket alive");
}

#[tokio::test]
async fn a_websocket_that_says_nothing_is_closed_as_idle() {
    let opts = Options { ping: Duration::from_millis(40), idle: Duration::from_millis(250), max_message: 1024 };
    let addr = ws_site(Shutdown::new(), opts).await;
    let mut c = connect(addr).await;
    // not reading means no pong goes back: the client library only answers a ping while it is polled
    tokio::time::sleep(Duration::from_millis(600)).await; // allow-sleep: the client must stay silent for longer than the idle limit
    let mut closed_idle = false;
    while let Some(Ok(m)) = c.next().await {
        if let Message::Close(Some(f)) = m {
            closed_idle = u16::from(f.code) == ws::POLICY;
            break;
        }
    }
    assert!(closed_idle, "closed with the policy code");
}

#[tokio::test]
async fn a_message_over_the_limit_ends_the_socket() {
    let addr = ws_site(Shutdown::new(), quick()).await;
    let mut c = connect(addr).await;
    c.send(Message::text("x".repeat(5000))).await.unwrap();
    let mut got_echo = false;
    while let Some(Ok(m)) = tokio::time::timeout(PATIENCE, c.next()).await.unwrap() {
        if matches!(m, Message::Text(_)) {
            got_echo = true;
        }
        if matches!(m, Message::Close(_)) {
            break;
        }
    }
    assert!(!got_echo, "the oversized message never reached the handler");
}

#[tokio::test]
async fn the_handler_can_close_with_its_own_code() {
    let addr = ws_site(Shutdown::new(), Options::default()).await;
    let mut c = connect(addr).await;
    c.send(Message::text("bye")).await.unwrap();
    match next(&mut c).await {
        Some(Message::Close(Some(f))) => assert_eq!((u16::from(f.code), f.reason.to_string()), (4000, "done".to_owned())),
        other => panic!("expected a close, got {other:?}"),
    }
}

#[tokio::test]
async fn shutdown_closes_a_websocket_as_going_away() {
    let shutdown = Shutdown::new();
    let addr = ws_site(shutdown.clone(), Options::default()).await;
    let mut c = connect(addr).await;
    c.send(Message::text("hi")).await.unwrap();
    assert_eq!(next(&mut c).await, Some(Message::text("echo hi")));
    shutdown.trigger();
    match next(&mut c).await {
        Some(Message::Close(Some(f))) => assert_eq!(u16::from(f.code), ws::GOING_AWAY),
        other => panic!("expected a close, got {other:?}"),
    }
}

#[tokio::test]
async fn a_client_that_hangs_up_ends_the_handler() {
    let addr = ws_site(Shutdown::new(), Options::default()).await;
    let mut c = connect(addr).await;
    c.send(Message::text("hi")).await.unwrap();
    next(&mut c).await;
    c.close(None).await.unwrap();
    // a second connection still works: nothing was left stuck
    let mut d = connect(addr).await;
    d.send(Message::text("again")).await.unwrap();
    assert_eq!(next(&mut d).await, Some(Message::text("echo again")));
}
