//! `tests/test_realtime.py` and the session half of `tests/test_transcribe.py`: the connection loop against a scripted server, on
//! tokio's paused clock, so a backoff of fifteen seconds takes none.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine as _;
use relay_captions::session::{AudioFeed, AudioSender, Conn, Connect, Incoming, Outgoing, Session, WsRequest};
use relay_captions::transcribe::{Backends, SessionSpec, MAX_AUDIO_FRAME_BYTES};
use relay_core::future::BoxFuture;
use serde_json::{json, Value};
use tokio::sync::mpsc;

enum Step {
    /// The connect fails with this sentence.
    Fail(&'static str),
    /// A socket that sends these events, then hangs up (`Some(why)` is a failure) or stays open.
    Socket(Vec<Value>, Option<Option<&'static str>>),
}

struct Server {
    script: Mutex<VecDeque<Step>>,
    requests: Mutex<Vec<WsRequest>>,
    sent: Arc<Mutex<Vec<Vec<Outgoing>>>>,
}

impl Server {
    fn new(script: Vec<Step>) -> Arc<Self> {
        Arc::new(Self { script: Mutex::new(script.into()), requests: Mutex::new(Vec::new()), sent: Arc::new(Mutex::new(Vec::new())) })
    }

    fn connections(&self) -> usize {
        self.requests.lock().unwrap().len()
    }

    fn frames(&self, conn: usize) -> Vec<Outgoing> {
        let sent = self.sent.lock().unwrap();
        sent[conn].iter().map(|o| match o {
            Outgoing::Text(t) => Outgoing::Text(t.clone()),
            Outgoing::Binary(b) => Outgoing::Binary(b.clone()),
        }).collect()
    }

    fn texts(&self, conn: usize) -> Vec<Value> {
        self.frames(conn).into_iter().filter_map(|o| if let Outgoing::Text(t) = o { serde_json::from_str(&t).ok() } else { None }).collect()
    }
}

impl Connect for Server {
    fn open<'a>(&'a self, req: &'a WsRequest) -> BoxFuture<'a, Result<Conn, String>> {
        Box::pin(async move {
            self.requests.lock().unwrap().push(req.clone());
            let step = self.script.lock().unwrap().pop_front().unwrap_or(Step::Socket(Vec::new(), None));
            let (events, hang_up) = match step {
                Step::Fail(msg) => return Err(msg.to_owned()),
                Step::Socket(e, h) => (e, h),
            };
            let (out_tx, mut out_rx) = mpsc::channel::<Outgoing>(256);
            let (inc_tx, inc_rx) = mpsc::channel::<Incoming>(256);
            let slot = {
                let mut sent = self.sent.lock().unwrap();
                sent.push(Vec::new());
                sent.len() - 1
            };
            let sent = Arc::clone(&self.sent);
            tokio::spawn(async move {
                for e in events {
                    let _ = inc_tx.send(Incoming::Text(e.to_string())).await;
                    tokio::task::yield_now().await;
                }
                if let Some(why) = hang_up {
                    let _ = inc_tx.send(Incoming::Closed(why.map(str::to_owned))).await;
                }
                while let Some(o) = out_rx.recv().await {
                    sent.lock().unwrap()[slot].push(o);
                }
            });
            Ok(Conn { out: out_tx, inc: inc_rx })
        })
    }
}

#[derive(Default)]
struct Got {
    deltas: Mutex<Vec<String>>,
    finals: Mutex<Vec<String>>,
    states: Mutex<Vec<&'static str>>,
}

struct Rig {
    session: Arc<Session>,
    server: Arc<Server>,
    got: Arc<Got>,
    audio: AudioSender,
}

fn spec(backend: &str, cfg: Value) -> SessionSpec {
    let backends = Backends::builtin();
    let b = backends.get(backend).unwrap();
    (b.session)(&cfg, "sk-test", "ENGLISH")
}

fn rig(backend: &str, cfg: Value, script: Vec<Step>) -> Rig {
    let server = Server::new(script);
    let got = Arc::new(Got::default());
    let (g1, g2) = (Arc::clone(&got), Arc::clone(&got));
    let session = Arc::new(Session::new(
        "transcribe",
        spec(backend, cfg),
        server.clone(),
        Arc::new(move |t| g1.deltas.lock().unwrap().push(t.to_owned())),
        Arc::new(move |t| g2.finals.lock().unwrap().push(t.to_owned())),
        Arc::new(|| {}),
    ));
    let (audio, feed) = AudioFeed::channel(64);
    session.start(feed);
    Rig { session, server, got, audio }
}

/// Wait on the (paused) clock for `ok` to hold.
async fn until(what: &str, ok: impl Fn() -> bool) {
    for _ in 0..2000 {
        if ok() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    panic!("never: {what}");
}

fn delta(text: &str) -> Value {
    json!({"type": "conversation.item.input_audio_transcription.delta", "delta": text})
}

#[tokio::test(start_paused = true)]
async fn it_says_hello_first_then_delivers_deltas_and_a_final() {
    let done = json!({"type": "conversation.item.input_audio_transcription.completed", "transcript": "ignored"});
    let r = rig("openai", json!({"realtime": {"noise_reduction": "near_field"}}), vec![Step::Socket(vec![json!({"type": "session.created"}), delta("Hello "), delta("there."), done, delta("Next")], None)]);
    until("connected", || r.session.state() == "connected").await;
    until("events", || r.got.deltas.lock().unwrap().len() == 3).await;
    assert_eq!(*r.got.deltas.lock().unwrap(), ["Hello ", "there.", "Next"]);
    assert_eq!(*r.got.finals.lock().unwrap(), ["Hello there."], "the completed event commits what is in flight, once");
    let hello = &r.server.texts(0)[0];
    assert_eq!(hello["type"], "session.update");
    assert_eq!(hello["session"]["audio"]["input"]["noise_reduction"]["type"], "near_field");
    assert_eq!(hello["session"]["audio"]["input"]["transcription"]["languages"], json!(["en"]));
    let req = r.server.requests.lock().unwrap()[0].clone();
    assert_eq!(req.url, "wss://api.openai.com/v1/realtime?intent=transcription");
    assert_eq!(req.headers, [("Authorization".to_owned(), "Bearer sk-test".to_owned())]);
    r.session.stop().await;
    assert_eq!(r.session.state(), "idle");
    assert_eq!(*r.got.finals.lock().unwrap(), ["Hello there.", "Next"], "stopping commits the open line");
}

#[tokio::test(start_paused = true)]
async fn openai_audio_is_base64_and_a_pause_commits_the_turn() {
    let r = rig("openai", json!({}), vec![Step::Socket(vec![], None)]);
    until("connected", || r.session.state() == "connected").await;
    r.audio.push(vec![1, 2, 3, 4]);
    until("append", || r.server.texts(0).len() == 2).await;
    let append = &r.server.texts(0)[1];
    assert_eq!(append["type"], "input_audio_buffer.append");
    assert_eq!(base64::engine::general_purpose::STANDARD.decode(append["audio"].as_str().unwrap()).unwrap(), [1, 2, 3, 4]);
    // 0.6 s of quiet commits the turn.
    until("commit", || r.server.texts(0).iter().any(|t| t["type"] == "input_audio_buffer.commit")).await;
    assert_eq!(r.server.texts(0).len(), 3);
    // A turn that never pauses is committed after 15 s.
    for _ in 0..40 {
        r.audio.push(vec![0; 2]);
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
    let commits = r.server.texts(0).iter().filter(|t| t["type"] == "input_audio_buffer.commit").count();
    assert!(commits >= 2, "{commits}");
    assert!(r.session.health()["audio_s"].as_f64().unwrap() >= 0.0);
    r.session.stop().await;
}

#[tokio::test(start_paused = true)]
async fn scribe_audio_goes_as_binary_frames_no_bigger_than_the_gateway_takes() {
    let r = rig("scribe", json!({}), vec![Step::Socket(vec![], None)]);
    until("connected", || r.session.state() == "connected").await;
    let req = r.server.requests.lock().unwrap()[0].clone();
    assert_eq!(req.url, "wss://ai-gateway.vercel.sh/v4/ai/transcription-model?ai-model-id=elevenlabs%2Fscribe-v2-realtime");
    assert_eq!(req.subprotocols, ["ai-gateway-transcription.v1", "ai-gateway-auth.sk-test"]);
    r.audio.push(vec![7; MAX_AUDIO_FRAME_BYTES * 2 + 10]);
    until("frames", || r.server.frames(0).len() == 4).await;
    let sizes: Vec<usize> = r.server.frames(0).iter().skip(1).map(|f| if let Outgoing::Binary(b) = f { b.len() } else { 0 }).collect();
    assert_eq!(sizes, [MAX_AUDIO_FRAME_BYTES, MAX_AUDIO_FRAME_BYTES, 10]);
    assert_eq!(r.server.texts(0)[0]["type"], "transcription-stream.start");
    let h = r.session.health();
    assert_eq!((h["backend"].clone(), h["price_per_hour"].clone(), h["kind"].clone()), (json!("scribe"), json!(0.39), json!("transcribe")));
    assert!(h["cost_usd"].as_f64().is_some());
    r.session.stop().await;
}

#[tokio::test(start_paused = true)]
async fn a_refused_connection_is_an_error_not_fatal_and_it_reconnects_after_the_backoff() {
    let r = rig("openai", json!({}), vec![Step::Fail("The connection could not be made."), Step::Socket(vec![], None)]);
    until("error", || r.session.error().as_deref() == Some("The connection could not be made.")).await;
    assert!(!r.session.fatal());
    until("second connect", || r.server.connections() == 2).await;
    until("connected", || r.session.state() == "connected").await;
    assert_eq!(r.session.health()["reconnects"], 1);
    r.session.stop().await;
}

#[tokio::test(start_paused = true)]
async fn a_socket_that_hangs_up_mid_line_flushes_the_line_and_reconnects() {
    let r = rig("openai", json!({}), vec![Step::Socket(vec![delta("half a li")], Some(Some("ConnectionClosedError: lost"))), Step::Socket(vec![delta("back.")], Some(None)), Step::Socket(vec![], None)]);
    until("flush", || r.got.finals.lock().unwrap().first().is_some_and(|l| l == "half a li")).await;
    until("third connect", || r.server.connections() == 3).await;
    assert_eq!(*r.got.finals.lock().unwrap(), ["half a li", "back."]);
    r.session.stop().await;
}

#[tokio::test(start_paused = true)]
async fn a_rejected_key_stops_the_retries_and_keeps_the_apis_own_words() {
    let bad = json!({"type": "error", "error": {"message": "Incorrect API key provided: sk-xx."}});
    let r = rig("openai", json!({}), vec![Step::Socket(vec![bad], Some(Some("ConnectionClosedError: 1008")))]);
    until("fatal", || r.session.fatal()).await;
    until("error state", || r.session.state() == "error").await;
    assert_eq!(r.session.error().as_deref(), Some("Incorrect API key provided: sk-xx."));
    tokio::time::sleep(Duration::from_secs(60)).await;
    assert_eq!(r.server.connections(), 1, "no second try with a credential it refused");
}

#[tokio::test(start_paused = true)]
async fn a_401_on_connect_is_fatal_too() {
    let r = rig("openai", json!({}), vec![Step::Fail("HTTP 401")]);
    until("fatal", || r.session.fatal() && r.session.state() == "error").await;
    tokio::time::sleep(Duration::from_secs(60)).await;
    assert_eq!(r.server.connections(), 1);
}

#[tokio::test(start_paused = true)]
async fn a_failed_event_that_is_not_fatal_keeps_retrying() {
    let soft = json!({"type": "x.failed", "error": {"message": "overloaded"}});
    let r = rig("openai", json!({}), vec![Step::Socket(vec![soft], Some(None)), Step::Socket(vec![], None)]);
    until("retry", || r.server.connections() == 2).await;
    assert!(!r.session.fatal());
    r.session.stop().await;
}

#[tokio::test(start_paused = true)]
async fn backoff_doubles_to_a_cap_and_resets_after_a_good_connection() {
    let mut script: Vec<Step> = (0..6).map(|_| Step::Fail("The connection could not be made.")).collect();
    script.push(Step::Socket(vec![], Some(None)));
    script.push(Step::Fail("The connection could not be made."));
    let r = rig("openai", json!({}), script);
    let start = tokio::time::Instant::now();
    until("six failures", || r.server.connections() == 7).await;
    // 1 + 2 + 4 + 8 + 15 + 15 seconds between seven connects.
    let waited = start.elapsed().as_secs_f64();
    assert!((45.0..46.5).contains(&waited), "{waited}");
    until("the one after a good connect", || r.server.connections() == 9).await;
    let after = start.elapsed().as_secs_f64() - waited;
    assert!(after < 16.0, "the wait after a good connection starts again at one second, not fifteen: {after}");
    r.session.stop().await;
}

#[tokio::test(start_paused = true)]
async fn the_segmenter_commits_a_finished_sentence_once_it_goes_quiet() {
    let r = rig("openai", json!({"realtime": {"segment_idle_s": 0.4}}), vec![Step::Socket(vec![delta("Hello there.")], None)]);
    until("committed", || *r.got.finals.lock().unwrap() == ["Hello there."]).await;
    r.session.stop().await;
    assert_eq!(*r.got.finals.lock().unwrap(), ["Hello there."], "once");
}

#[tokio::test(start_paused = true)]
async fn scribe_commits_on_the_gateways_final_only() {
    let evts = vec![json!({"type": "transcript-partial", "text": "Amen"}), json!({"type": "transcript-final", "text": "Amen."})];
    let r = rig("scribe", json!({}), vec![Step::Socket(evts, None)]);
    until("final", || *r.got.finals.lock().unwrap() == ["Amen."]).await;
    assert_eq!(*r.got.deltas.lock().unwrap(), ["Amen"]);
    r.session.stop().await;
    assert_eq!(r.got.states.lock().unwrap().len(), 0);
}
