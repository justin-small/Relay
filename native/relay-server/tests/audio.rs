//! The audio bus, routes, channels and meter routes (#657) over HTTP, on a real database, a real config.json and the engine's fake
//! sound card: what `tests/test_audiobus.py`, `test_routing.py`, `test_channels.py` and `test_metering.py` check of the Python routes,
//! driven through the router the server serves.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::body::Body;
use axum::http::{header, Method, Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use relay_audio::{AudioBus, Backing, Channels, Metering, Routing};
use relay_auth::{Auth, Cost, NewUser};
use relay_server::accounts::{Accounts, Shared};
use relay_server::audio::{routes, AudioApi, ConfigStore};
use relay_server::internal;
use relay_server::forward::Conn;
use relay_server::pages::Pages;
use serde_json::{json, Value};
use tower::ServiceExt;

const LAN: &str = "192.168.1.50:5000";
const LOOPBACK: &str = "127.0.0.1:40000";
const TOKEN: &str = "front-secret";

fn password() -> &'static str {
    static P: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    P.get_or_init(|| format!("{:016x}{:016x}", rand::random::<u64>(), rand::random::<u64>()))
}

struct Site {
    dir: PathBuf,
    acc: Shared,
    api: Arc<AudioApi>,
    router: Router,
}

impl Drop for Site {
    fn drop(&mut self) {
        self.api.bus.close();
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

struct Reply {
    status: StatusCode,
    body: String,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", self.body))
    }
}

async fn site(tag: &str) -> Site {
    let dir = std::env::temp_dir().join(format!("relay-audio-routes-{tag}-{}-{}", std::process::id(), relay_auth::now().replace(':', "")));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("config.json"), "{}").unwrap();
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.unwrap();
    sqlx::raw_sql("create table gear (id integer primary key)").execute(auth.pool()).await.unwrap();
    sqlx::raw_sql(include_str!("../../../app/migrations/0014_channels.sql")).execute(auth.pool()).await.unwrap();
    sqlx::raw_sql(include_str!("../../../app/migrations/0015_routes.sql")).execute(auth.pool()).await.unwrap();
    let pool = auth.pool().clone();
    let app_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../app");
    let acc = Accounts::new(auth, Pages::new(&app_dir), dir.join("config.json"));
    let store = Arc::new(ConfigStore(Arc::new(relay_config::Settings::new(&dir.join("config.json")))));
    let channels = Channels::new(pool);
    let routing = Routing::new(channels.clone());
    let bus = AudioBus::new(Backing::fake(), store.clone(), channels.clone(), routing.clone());
    let metering = Metering::new(bus.clone(), store);
    let api = Arc::new(AudioApi { acc: acc.clone(), bus, routing, channels, metering, app: None });
    let router = routes(api.clone()).merge(internal::routes(api.clone(), TOKEN));
    Site { dir, acc, api, router }
}

impl Site {
    async fn send(&self, method: Method, uri: &str, cookie: &str, body: Option<String>) -> Reply {
        let mut req = Request::builder().method(method).uri(uri);
        if !cookie.is_empty() {
            req = req.header("cookie", cookie);
        }
        let body = match body {
            Some(b) => {
                req = req.header(header::CONTENT_TYPE, "application/json");
                Body::from(b)
            }
            None => Body::empty(),
        };
        let mut req = req.body(body).unwrap();
        req.extensions_mut().insert(Conn { peer: LAN.parse::<SocketAddr>().unwrap(), local_port: 8443, secure: true });
        let resp = self.router.clone().oneshot(req).await.unwrap();
        let (parts, body) = resp.into_parts();
        Reply { status: parts.status, body: String::from_utf8_lossy(&body.collect().await.unwrap().to_bytes()).into_owned() }
    }

    async fn get(&self, uri: &str, cookie: &str) -> Reply {
        self.send(Method::GET, uri, cookie, None).await
    }

    async fn post(&self, uri: &str, cookie: &str, body: Value) -> Reply {
        self.send(Method::POST, uri, cookie, Some(body.to_string())).await
    }

    /// A signed-in cookie for a new user of `role`.
    async fn cookie(&self, username: &str, role: &str) -> String {
        let user = self.acc.auth.create_user(NewUser { username, name: username, role, password: password(), temporary: false }, None).await.unwrap();
        format!("relay_admin={}", self.acc.auth.start_session(&user, "password", None, "").await.unwrap().token)
    }

    async fn audited(&self, kind: &str) -> Vec<String> {
        let found = self.acc.auth.search_audit(relay_auth::Search { kind: Some(kind), ..Default::default() }).await.unwrap();
        found.entries.iter().map(|e| e.action.clone()).collect()
    }

    fn config(&self) -> Value {
        serde_json::from_str(&std::fs::read_to_string(self.dir.join("config.json")).unwrap()).unwrap()
    }
}

fn fake_setup() -> Value {
    json!({"lanes": [{"lane": 1, "name": "Mic", "source": "portaudio", "device": "Fake Mic", "channel": "mix"}],
           "outputs": [{"id": "main", "name": "Main", "output": "portaudio", "device": "Fake Speaker", "channels": 2}],
           "matrix": [{"in": 1, "out": "main", "ch": 1, "gain_db": -3}, {"in": 1, "out": "main", "ch": 2}]})
}

#[tokio::test]
async fn every_route_needs_a_signed_in_user_with_the_permission() {
    let s = site("auth").await;
    for (method, uri) in [
        (Method::GET, "/api/admin/audiobus"),
        (Method::POST, "/api/admin/audiobus"),
        (Method::POST, "/api/admin/audiobus/test"),
        (Method::POST, "/api/admin/audiobus/setup"),
        (Method::GET, "/api/admin/metering"),
        (Method::POST, "/api/admin/metering"),
        (Method::POST, "/api/admin/metering/m1/calibrate"),
        (Method::GET, "/api/admin/routes"),
        (Method::POST, "/api/admin/routes"),
        (Method::POST, "/api/admin/routes/clear"),
        (Method::POST, "/api/admin/routes/salvos"),
        (Method::POST, "/api/admin/routes/salvos/1/take"),
        (Method::DELETE, "/api/admin/routes/salvos/1"),
        (Method::GET, "/api/admin/network/channels"),
        (Method::POST, "/api/admin/network/channels/merge"),
        (Method::DELETE, "/api/admin/network/channels/1"),
    ] {
        let r = s.send(method.clone(), uri, "", Some("{}".into())).await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED, "{method} {uri} answered {}", r.body);
        assert_eq!(r.json()["detail"], "Sign in required");
    }
    let member = s.cookie("mem", "member").await;
    let r = s.post("/api/admin/audiobus", &member, fake_setup()).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN, "{}", r.body);
    assert_eq!(r.json()["detail"], "Not allowed");
    assert!(s.get("/api/admin/network/channels", &member).await.status == StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn a_body_that_is_not_an_object_is_refused_before_anything_changes() {
    let s = site("body").await;
    let owner = s.cookie("boss", "owner").await;
    let r = s.send(Method::POST, "/api/admin/audiobus", &owner, Some("[1, 2]".into())).await;
    assert_eq!(r.status, StatusCode::UNPROCESSABLE_ENTITY);
    assert!(s.config()["audio_bus"].is_null(), "nothing was saved");
}

#[tokio::test]
async fn first_setup_saves_the_setting_patches_the_tones_and_is_audited() {
    let s = site("setup").await;
    let owner = s.cookie("boss", "owner").await;
    let page = s.get("/api/admin/audiobus", &owner).await.json();
    assert_eq!(page["setup"], json!({"input": true, "output": true}));
    assert_eq!(page["lanes"], 32);
    assert!(page["input_devices"].as_array().unwrap().iter().any(|d| d["label"] == "Fake Mic"));
    assert_eq!(page["sources"][0]["id"], "portaudio", "with no app behind the server the sound card is the one source");

    let r = s.post("/api/admin/audiobus/setup", &owner, json!({"input": "Fake Mic", "output": "Fake Speaker"})).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    let done = r.json();
    assert_eq!(done["setup"], json!({"input": false, "output": false}));
    assert_eq!(done["config"]["lanes"][0]["device"], "Fake Mic");
    assert_eq!(done["config"]["outputs"][0]["id"], "main");
    assert_eq!(s.config()["audio_bus"]["lanes"][0]["name"], "Main input", "the setting is in config.json, where Python reads it");
    assert!(s.config()["audio_bus"]["matrix"].as_array().unwrap().is_empty(), "the crosspoints are routes, not config");
    // the alert tones and the test tone reach both channels of the first output
    let played = s.post("/api/admin/audiobus/test", &owner, json!({})).await.json();
    assert_eq!(played["reached"], 2);
    assert_eq!(played["message"], "The test tone is playing.");
    assert_eq!(s.audited("audio").await.len(), 2, "setup and the test tone are each one entry");
}

#[tokio::test]
async fn the_test_tone_says_when_it_is_not_routed() {
    let s = site("tone").await;
    let owner = s.cookie("boss", "owner").await;
    s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    let r = s.post("/api/admin/audiobus/test", &owner, json!({})).await.json();
    assert_eq!(r["reached"], 0);
    assert_eq!(r["message"], "Test tone is not routed to any output yet. Route it to an output channel below, then try again.");
}

#[tokio::test]
async fn saving_the_bus_with_a_matrix_makes_routes_and_without_one_leaves_them() {
    let s = site("save").await;
    let owner = s.cookie("boss", "owner").await;
    let r = s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    assert_eq!(r.json()["config"]["matrix"].as_array().unwrap().len(), 2);
    assert_eq!(r.json()["status"]["running"], true);
    // a save with no "matrix" key (the Audio bus page) leaves the routes alone
    let mut again = fake_setup();
    again.as_object_mut().unwrap().remove("matrix");
    again["lanes"][0]["name"] = json!("Renamed");
    let r = s.post("/api/admin/audiobus", &owner, again).await.json();
    assert_eq!(r["config"]["matrix"].as_array().unwrap().len(), 2);
    assert_eq!(r["config"]["lanes"][0]["name"], "Renamed");
    let audit = s.acc.auth.search_audit(relay_auth::Search { kind: Some("audio.bus"), ..Default::default() }).await.unwrap();
    assert_eq!(audit.entries[0].detail["crosspoints"], 2);
    assert_eq!(audit.entries[0].summary, "Changed the audio bus");
}

#[tokio::test]
async fn a_route_change_shows_on_the_page_at_once_and_needs_the_right_permission() {
    let s = site("route").await;
    let owner = s.cookie("boss", "owner").await;
    s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    let routes = s.get("/api/admin/routes?provider=audiobus", &owner).await.json();
    let dests: Vec<&str> = routes["routes"].as_array().unwrap().iter().map(|r| r["name"].as_str().unwrap()).collect();
    assert_eq!(dests, ["Main 1", "Main 2"]);
    let second = routes["routes"][1]["destination"].as_str().unwrap().to_owned();
    assert_eq!(routes["routes"][1]["state"], "same", "what the engine mixes is what was asked");
    // an operator who may not manage the network cannot take a crosspoint off
    let member = s.cookie("mem", "member").await;
    let r = s.post("/api/admin/routes/clear", &member, json!({"destination": second})).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    let r = s.post("/api/admin/routes/clear", &owner, json!({"destination": second})).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    let page = s.get("/api/admin/audiobus", &owner).await.json();
    assert_eq!(page["config"]["matrix"].as_array().unwrap().len(), 1, "the page shows it straight away");
    assert_eq!(page["status"]["matrix"].as_array().unwrap().len(), 1, "and the engine runs it");
    assert_eq!(s.audited("routes").await, ["routes.clear"]);
    // setting it again, with a gain
    let lane = s.get("/api/admin/network/channels", &owner).await.json()["channels"].as_array().unwrap().iter().find(|c| c["shown"] == "Mic").unwrap()["ref"].clone();
    let r = s.post("/api/admin/routes", &owner, json!({"destination": second, "source": lane, "gain_db": -6})).await.json();
    assert_eq!(r["status"]["state"], "same");
    assert_eq!(r["problems"], json!([]));
    assert_eq!(s.get("/api/admin/audiobus", &owner).await.json()["config"]["matrix"].as_array().unwrap().len(), 2);
    // refusals are the fixed sentences with Python's statuses
    let r = s.post("/api/admin/routes", &owner, json!({"destination": second, "source": second})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::CONFLICT, "A channel cannot feed itself.".into()));
    let r = s.post("/api/admin/routes", &owner, json!({"destination": "channel:relay:999", "source": lane})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::NOT_FOUND, "That channel does not exist.".into()), "a network manager may route anything, so an unknown channel is simply not found");
}

#[tokio::test]
async fn a_preset_is_saved_taken_and_deleted() {
    let s = site("salvo").await;
    let owner = s.cookie("boss", "owner").await;
    s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    let routes = s.get("/api/admin/routes", &owner).await.json();
    let dests: Vec<Value> = routes["routes"].as_array().unwrap().iter().map(|r| r["destination"].clone()).collect();
    let r = s.post("/api/admin/routes/salvos", &owner, json!({"name": "Service", "destinations": dests, "scope": "audio"})).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    let id = r.json()["id"].as_i64().unwrap();
    let r = s.post("/api/admin/routes/salvos", &owner, json!({"name": "service", "destinations": dests, "scope": "audio"})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::CONFLICT, "There is already a preset with that name.".into()));
    let r = s.post("/api/admin/routes/salvos", &owner, json!({"name": "x", "destinations": "not a list"})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::BAD_REQUEST, "Choose the destinations to save.".into()));
    s.post("/api/admin/routes/clear", &owner, json!({"destination": dests[0]})).await;
    let r = s.post(&format!("/api/admin/routes/salvos/{id}/take"), &owner, json!({})).await;
    assert_eq!(r.json()["problems"], json!([]));
    assert_eq!(s.get("/api/admin/audiobus", &owner).await.json()["config"]["matrix"].as_array().unwrap().len(), 2, "taking the preset puts the crosspoint back");
    assert_eq!(s.send(Method::DELETE, &format!("/api/admin/routes/salvos/{id}"), &owner, None).await.status, StatusCode::OK);
    let r = s.send(Method::DELETE, &format!("/api/admin/routes/salvos/{id}"), &owner, None).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::NOT_FOUND, "That preset does not exist.".into()));
    assert_eq!(s.audited("routes").await.len(), 4, "save, clear, take and delete; a refused change is not an entry");
}

#[tokio::test]
async fn channels_are_listed_renamed_and_forgotten_with_fixed_sentences() {
    let s = site("channels").await;
    let owner = s.cookie("boss", "owner").await;
    s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    let view = s.get("/api/admin/network/channels", &owner).await.json();
    let chans = view["channels"].as_array().unwrap();
    let mic = chans.iter().find(|c| c["shown"] == "Mic").expect("the patched lane is a channel");
    assert_eq!(mic["kind"], "audio");
    assert_eq!(mic["feeds"].as_array().unwrap().len(), 2, "what it feeds is shown on every page");
    let id = mic["id"].as_i64().unwrap();
    let r = s.post(&format!("/api/admin/network/channels/{id}/rename"), &owner, json!({"name": "  Front  mic "})).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    assert!(r.json()["channels"].as_array().unwrap().iter().any(|c| c["shown"] == "Front mic"));
    let r = s.post(&format!("/api/admin/network/channels/{id}/rename"), &owner, json!({"name": "x".repeat(61)})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::BAD_REQUEST, "A name is up to 60 characters.".into()));
    let r = s.post(&format!("/api/admin/network/channels/{id}/rename"), &owner, json!({"name": "Front", "device": true})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::CONFLICT, "That channel's gear cannot be renamed from Relay.".into()));
    let r = s.post("/api/admin/network/channels/merge", &owner, json!({"keep": "nonsense", "other": 3})).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    let r = s.post("/api/admin/network/channels/merge", &owner, json!({"keep": "channel:relay:1", "other": "channel:relay:1"})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::CONFLICT, "A channel cannot be joined to itself.".into()));
    let r = s.send(Method::DELETE, &format!("/api/admin/network/channels/{id}"), &owner, None).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::CONFLICT, "That channel is still reported; only a missing one can be forgotten.".into()));
    let r = s.send(Method::DELETE, "/api/admin/network/channels/9999", &owner, None).await;
    assert_eq!(r.status, StatusCode::NOT_FOUND);
    assert!(s.audited("network").await.contains(&"network.channel_rename".to_owned()));
}

#[tokio::test]
async fn meters_are_saved_cleaned_calibrated_and_audited() {
    let s = site("meters").await;
    let owner = s.cookie("boss", "owner").await;
    s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    let r = s.post("/api/admin/metering", &owner, json!({"meters": [{"id": "m1", "name": " Room ", "lane": 1}, {"id": "m2", "lane": 99, "enabled": false}]})).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    let saved = r.json();
    assert_eq!(saved["config"]["meters"][0]["name"], "Room");
    assert_eq!(saved["config"]["meters"][1]["lane"], 32);
    assert_eq!(s.config()["metering"]["meters"].as_array().unwrap().len(), 2, "kept in config.json");
    let got = s.get("/api/admin/metering", &owner).await.json();
    assert_eq!(got["lanes"], 32);
    assert_eq!(got["status"][1]["state"], "off", "a meter that is off reads nothing");
    let r = s.post("/api/admin/metering/zz/calibrate", &owner, json!({"reference_db": 94})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::NOT_FOUND, "There is no such meter.".into()));
    let r = s.post("/api/admin/metering/m2/calibrate", &owner, json!({"reference_db": 94})).await;
    assert_eq!((r.status, r.json()["error"].as_str().unwrap().to_owned()), (StatusCode::CONFLICT, "Switch the meter on first.".into()));
    let end = Instant::now() + Duration::from_secs(5);
    loop {
        let st = s.get("/api/admin/metering", &owner).await.json();
        if st["status"][0]["state"] == "ok" && st["status"][0]["a_slow"].as_f64().unwrap_or(0.0) > 40.0 {
            break;
        }
        assert!(Instant::now() < end, "the meter never heard the lane: {st}");
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    let r = s.post("/api/admin/metering/m1/calibrate", &owner, json!({"reference_db": 94})).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    assert_eq!(r.json()["config"]["meters"][0]["calibrated"], true);
    assert_eq!(s.config()["metering"]["meters"][0]["calibrated"], true, "the calibration is saved");
    assert_eq!(s.audited("audio.metering").await.len(), 2);
}

// ------------------------------------------------------------------------------------------- the door for the Python app
impl Site {
    /// A request as the app behind the server makes it: from `peer`, with the front token if `token`.
    async fn app(&self, method: Method, uri: &str, peer: &str, token: bool, body: Vec<u8>) -> axum::response::Response {
        let mut req = Request::builder().method(method).uri(uri);
        if token {
            req = req.header("x-relay-front", TOKEN);
        }
        let mut req = req.header(header::CONTENT_TYPE, "application/json").body(Body::from(body)).unwrap();
        req.extensions_mut().insert(Conn { peer: peer.parse::<SocketAddr>().unwrap(), local_port: 8080, secure: false });
        self.router.clone().oneshot(req).await.unwrap()
    }

    async fn app_json(&self, method: Method, uri: &str, body: Value) -> Value {
        let resp = self.app(method, uri, LOOPBACK, true, body.to_string().into_bytes()).await;
        assert_eq!(resp.status(), StatusCode::OK, "{uri}");
        serde_json::from_slice(&resp.into_body().collect().await.unwrap().to_bytes()).unwrap()
    }
}

fn pcm(samples: &[f32]) -> Vec<u8> {
    samples.iter().flat_map(|s| s.to_le_bytes()).collect()
}

fn samples(bytes: &[u8]) -> Vec<f32> {
    bytes.chunks(4).map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]])).collect()
}

#[tokio::test]
async fn the_internal_door_opens_only_to_the_app_with_its_token() {
    let s = site("door").await;
    for (peer, token) in [(LAN, true), (LOOPBACK, false), (LAN, false)] {
        let r = s.app(Method::GET, "/internal/audio/status", peer, token, Vec::new()).await;
        assert_eq!(r.status(), StatusCode::NOT_FOUND, "from {peer} with token {token}: there is no such route");
    }
    assert_eq!(s.app_json(Method::GET, "/internal/audio/status", Value::Null).await["running"], false);
    // a signed-in browser is not the app either
    let owner = s.cookie("boss", "owner").await;
    assert_eq!(s.get("/internal/audio/status", &owner).await.status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn a_lane_tap_streams_the_lane_to_the_app() {
    let s = site("tap").await;
    let owner = s.cookie("boss", "owner").await;
    s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    let resp = s.app(Method::GET, "/internal/audio/tap/1", LOOPBACK, true, Vec::new()).await;
    assert_eq!(resp.status(), StatusCode::OK);
    let mut body = resp.into_body();
    let mut loud = 0.0f32;
    let end = Instant::now() + Duration::from_secs(5);
    while loud < 0.05 && Instant::now() < end {
        let Ok(Some(Ok(frame))) = tokio::time::timeout(Duration::from_secs(1), body.frame()).await else { continue };
        let Ok(data) = frame.into_data() else { continue };
        assert!(data.len() > 4, "a block is a length and its samples");
        let n = u32::from_le_bytes([data[0], data[1], data[2], data[3]]) as usize;
        assert_eq!(n, data.len() - 4, "the length says how many bytes follow");
        loud = samples(&data[4..]).iter().fold(0.0, |m, x| m.max(x.abs()));
    }
    assert!(loud >= 0.05, "the fake microphone's tone reached the tap, loudest {loud}");
    drop(body);
    let bad = s.app(Method::GET, "/internal/audio/tap/33", LOOPBACK, true, Vec::new()).await;
    assert_eq!(bad.status(), StatusCode::BAD_REQUEST);
}

#[tokio::test]
async fn the_app_feeds_a_network_source_and_drains_a_network_sender() {
    let s = site("network").await;
    let owner = s.cookie("boss", "owner").await;
    // an NDI source on lane 1 feeds a network output: Python opens the source and the sender, the engine mixes between them
    let setup = json!({"lanes": [{"lane": 1, "name": "NDI", "source": "ndi", "device": "Cam", "channel": 0}],
                       "outputs": [{"id": "aes", "output": "aes67", "device": null, "channels": 2}],
                       "matrix": [{"in": 1, "out": "aes", "ch": 1}, {"in": 1, "out": "aes", "ch": 2}]});
    assert_eq!(s.post("/api/admin/audiobus", &owner, setup).await.status, StatusCode::OK);
    let cfg = s.app_json(Method::GET, "/internal/audio/config", Value::Null).await;
    assert_eq!(cfg["running"], true);
    assert_eq!(cfg["config"]["lanes"][0]["source"], "ndi", "the app learns which network sources and senders to drive");
    let tone: Vec<f32> = (0..9600).map(|i| 0.5 * (i as f32 * 0.1).sin()).collect();
    let end = Instant::now() + Duration::from_secs(5);
    let mut heard = 0.0f32;
    while heard < 0.1 && Instant::now() < end {
        let ok = s.app_json(Method::POST, "/internal/audio/push?input=ndi:cam&channels=1&rate=48000", Value::Null).await;
        let _ = ok; // an empty block is refused quietly
        let resp = s.app(Method::POST, "/internal/audio/push?input=ndi:cam&channels=1&rate=48000", LOOPBACK, true, pcm(&tone)).await;
        assert_eq!(resp.status(), StatusCode::OK);
        let pulled = s.app(Method::GET, "/internal/audio/pull?output=aes&frames=960", LOOPBACK, true, Vec::new()).await;
        assert_eq!(pulled.status(), StatusCode::OK);
        let bytes = pulled.into_body().collect().await.unwrap().to_bytes();
        assert_eq!(bytes.len(), 960 * 2 * 4, "960 frames of two channels, as little-endian floats");
        heard = samples(&bytes).iter().fold(0.0, |m, x| m.max(x.abs()));
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert!(heard >= 0.1, "what the app pushed came out of the output it pulled, loudest {heard}");
    let gone = s.app(Method::GET, "/internal/audio/pull?output=nope&frames=10", LOOPBACK, true, Vec::new()).await;
    assert_eq!(gone.status(), StatusCode::GONE, "an output that is not on the bus");
    // the app reports how its sources and senders are doing, and the status shows it beside the engine's own
    s.app_json(Method::POST, "/internal/audio/external", json!({"inputs": {"ndi:cam": {"state": "waiting", "error": "The audio input stopped.", "label": "Cam"}}})).await;
    let st = s.app_json(Method::GET, "/internal/audio/status", Value::Null).await;
    assert_eq!(st["lanes"][0]["state"], "waiting");
    assert_eq!(st["lanes"][0]["error"], "The audio input stopped.");
}

#[tokio::test]
async fn the_app_plays_alert_tones_and_registers_a_destination() {
    let s = site("play").await;
    let owner = s.cookie("boss", "owner").await;
    s.post("/api/admin/audiobus/setup", &owner, json!({"input": "Fake Mic", "output": "Fake Speaker"})).await;
    let r = s.app(Method::POST, "/internal/audio/play?key=alerts&rate=48000", LOOPBACK, true, pcm(&[0.4; 960])).await;
    assert_eq!(serde_json::from_slice::<Value>(&r.into_body().collect().await.unwrap().to_bytes()).unwrap()["reached"], 2);
    let r = s.app(Method::POST, "/internal/audio/play?key=talkback&rate=48000", LOOPBACK, true, pcm(&[0.4; 960])).await;
    assert_eq!(serde_json::from_slice::<Value>(&r.into_body().collect().await.unwrap().to_bytes()).unwrap()["reached"], 0, "not patched yet");
    s.app_json(Method::POST, "/internal/audio/destinations", json!({"register": {"key": "talkback", "label": "Talkback"}})).await;
    let chans = s.get("/api/admin/network/channels", &owner).await.json();
    assert!(chans["channels"].as_array().unwrap().iter().any(|c| c["shown"] == "Talkback"), "a module's destination is a source channel you can route");
    let inj = s.app(Method::POST, "/internal/audio/inject?output=main&channels=0&rate=48000", LOOPBACK, true, pcm(&[0.2; 480])).await;
    assert_eq!(serde_json::from_slice::<Value>(&inj.into_body().collect().await.unwrap().to_bytes()).unwrap()["ok"], true);
    assert_eq!(s.app_json(Method::POST, "/internal/audio/stop_injects?output=main", Value::Null).await["ok"], true);
}

#[tokio::test]
async fn the_config_wait_returns_when_the_bus_changes() {
    let s = site("wait").await;
    let owner = s.cookie("boss", "owner").await;
    let first = s.app_json(Method::GET, "/internal/audio/config", Value::Null).await;
    let gen = first["gen"].as_u64().unwrap();
    let waiting = {
        let router = s.router.clone();
        tokio::spawn(async move {
            let mut req = Request::builder().method(Method::GET).uri(format!("/internal/audio/config?since={gen}")).header("x-relay-front", TOKEN).body(Body::empty()).unwrap();
            req.extensions_mut().insert(Conn { peer: LOOPBACK.parse::<SocketAddr>().unwrap(), local_port: 8080, secure: false });
            let resp = router.oneshot(req).await.unwrap();
            serde_json::from_slice::<Value>(&resp.into_body().collect().await.unwrap().to_bytes()).unwrap()
        })
    };
    tokio::time::sleep(Duration::from_millis(100)).await; // allow-sleep: shows the call is still waiting while nothing changed
    assert!(!waiting.is_finished(), "nothing changed, so the call waits");
    s.post("/api/admin/audiobus", &owner, fake_setup()).await;
    let got = tokio::time::timeout(Duration::from_secs(5), waiting).await.expect("the wait ended when the bus changed").unwrap();
    assert!(got["gen"].as_u64().unwrap() > gen);
    assert_eq!(got["config"]["lanes"][0]["name"], "Mic");
}

#[tokio::test]
async fn the_app_registers_a_sound_source_and_publishes_readings() {
    let s = site("sources").await;
    s.app_json(Method::POST, "/internal/metering", json!({"register": {"id": "osm", "label": "Open Sound Meter"}})).await;
    s.app_json(Method::POST, "/internal/metering", json!({"publish": {"id": "osm", "reading": {"a_fast": 70.5, "leq_a": 68}}})).await;
    let got = s.app_json(Method::GET, "/internal/metering", Value::Null).await;
    assert_eq!(got["public"]["sources"]["osm"]["a_fast"], 70.5);
    assert_eq!(got["choices"][0], json!(["osm", "Open Sound Meter"]));
}
