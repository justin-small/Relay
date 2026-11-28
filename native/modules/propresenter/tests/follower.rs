//! What `tests/test_propresenter.py` checks of the follower, the lyrics feed, view-only mode, the stage message, Alerts and the admin routes,
//! against a fake ProPresenter on 127.0.0.1 over real sockets (`tests/fake`).

mod fake;

use std::sync::Arc;
use std::time::Duration;

use axum::http::Method;
use fake::{rig, until, Fake};
use relay_core::caps::ALERT_STAGE;
use relay_core::{Module, Net, SettingsStore};
use relay_module_propresenter::client::{is_action_path, Client, Kind, Splitter};
use relay_module_propresenter::follower::STREAM_PATHS;
use relay_module_propresenter::settings;
use serde_json::{json, Value};

const URL: &str = "/api/admin/propresenter";

fn client(port: u16, view_only: bool) -> Client {
    Client::new(&Net::unrouted(), "127.0.0.1", port, Arc::new(move || view_only))
}

// -------------------------------------------------------------------------------------------------------------------- settings
#[test]
fn settings_default_off_and_clean_what_is_saved() {
    let d = settings::default_settings();
    assert_eq!((d["enabled"].clone(), d["host"].clone(), d["port"].clone(), d["view_only"].clone()), (json!(false), json!(""), json!(0), json!(false)), "off, no port assumed, view-only off");
    assert_eq!(d["lyrics"], json!({"viewer": false, "screen": true, "overlay": false, "panels": true}), "the public phone viewer's lyrics are off");
    assert_eq!(settings::clean_host(&json!("http://10.0.0.1:50001/")), "", "a URL is not a host");
    assert_eq!(settings::clean_host(&json!(" Studio-Mac.local ")), "Studio-Mac.local");
    assert_eq!((settings::clean_port(&json!(0)), settings::clean_port(&json!(70000)), settings::clean_port(&json!("50402"))), (0, 0, 50402), "a port outside 1-65535 is dropped");
    assert!(!settings::configured(&settings::clean_stored(&json!({}))), "nothing is followed until it is switched on and has an address");
    let fixed = [
        "status/slide", "presentation/current", "presentation/slide_index", "timers/current", "stage/message", "status/layers", "playlist/active", "look/current",
        "announcement/active", "announcement/slide_index", "status/screens", "status/audience_screens", "status/stage_screens",
    ];
    assert_eq!(STREAM_PATHS, fixed, "the stream's path list is the fixed one");
    assert!(STREAM_PATHS.iter().all(|p| fake::ACCEPTED.contains(p)), "and a live install accepts every name");

    // An install from before #483 had one computer in host and port: it becomes the first, main.
    let old = settings::clean_stored(&json!({"enabled": true, "host": "10.0.0.5", "port": 50001}));
    assert_eq!((old["computers"].clone(), old["primary"].clone(), old["host"].clone()), (json!([{"id": "main", "name": "ProPresenter", "host": "10.0.0.5", "port": 50001}]), json!("main"), json!("10.0.0.5")));
    // Anything malformed is dropped, not refused.
    let hand = settings::clean_stored(&json!({"timers": {"amber_s": "soon", "red_s": 500, "show": ["T1", "bad id!", "T1"]}, "lobby": {"interval_s": 1}, "macros_allowed": ["M2", "M1", 5, "M1"], "link": {"owned": {"uuid": "X", "name": "not ours"}}}));
    assert_eq!(hand["timers"], json!({"amber_s": 120, "red_s": 120, "show": ["T1"]}), "red cannot be later than amber");
    assert_eq!(hand["lobby"]["interval_s"], json!(8.0));
    assert_eq!(hand["macros_allowed"], json!(["M1", "M2"]));
    assert_eq!(hand["link"]["owned"], json!({}), "a playlist Relay did not make is not its own");
}

// ---------------------------------------------------------------------------------------------------------------------- framing
#[test]
fn messages_are_cut_at_the_blank_line_however_the_bytes_arrive() {
    let whole = b"{\"url\":\"status/slide\",\"data\":{\"current\":{\"text\":\"a\\nb\"}}}\r\n\r\n{\"url\":\"stage/message\",\"data\":\"\"}\r\n\r\n";
    let mut sp = Splitter::default();
    let mut got = Vec::new();
    for piece in whole.chunks(7) {
        got.extend(sp.feed(piece).unwrap()); // cut anywhere, including inside the blank line
    }
    assert_eq!(got.iter().map(|m| m["url"].as_str().unwrap()).collect::<Vec<_>>(), ["status/slide", "stage/message"]);
    assert_eq!(got[0]["data"]["current"]["text"], "a\nb", "data survives the cuts");
    let skipped = Splitter::default().feed(&[b"nope\r\n\r\n".as_slice(), whole].concat()).unwrap();
    assert_eq!(skipped.len(), 2, "a message that is not JSON is skipped");
    assert!(Splitter::default().feed(b"{\"url\":\"status/sl").unwrap().is_empty(), "an unfinished message waits");
}

// ---------------------------------------------------------------------------------------------------------------------- client
#[tokio::test]
async fn the_client_reads_refuses_and_names_what_went_wrong() {
    let fake = Fake::start().await;
    let c = client(fake.port, false);
    assert_eq!(c.version().await.unwrap()["host_description"], "ProPresenter 22");
    let e = c.read("/v1/nothing").await.unwrap_err();
    assert_eq!((e.kind, e.status, e.detail().contains("Network")), (Kind::Http, 404, true), "a 404 says to check Network");
    let e = c.open_stream(&["status/slide", "status/bogus"]).await.err().expect("one unknown path fails the whole stream request");
    assert_eq!((e.kind, e.status, e.detail().contains("status/bogus")), (Kind::Http, 400, true), "400 on an unknown stream path, and it says which");
    let e = client(9, false).version().await.unwrap_err();
    assert_eq!(e.kind, Kind::Network, "a closed port is a network error");
    assert!(!e.operator_message().contains("127.0.0.1"), "the page's sentence names no address");

    let acts: Vec<bool> = ["/v1/trigger/next", "/v1/clear/layer/slide", "/v1/timers/start", "/v1/timer/x/stop", "/v1/status/slide", "/v1/presentation/active/next/trigger", "/v1/stage/message", "/v1/version"].iter().map(|p| is_action_path(p)).collect();
    assert_eq!(acts, [true, true, true, true, false, true, false, false], "read() refuses a GET that acts");
    assert_eq!(c.read("/v1/trigger/next").await.unwrap_err().kind, Kind::Invalid);
    assert_eq!(c.action("/v1/trigger/next").await.unwrap(), Value::Null, "action() is how a trigger goes out");
    for never in ["/v1/timers/start", "/v1/timers/stop", "/v1/timers/reset", "/v1/capture/start"] {
        assert_eq!(c.action(never).await.unwrap_err().kind, Kind::Invalid, "{never} is never sent");
    }
}

#[tokio::test]
async fn view_only_refuses_every_write_before_a_socket_opens() {
    let fake = Fake::start().await;
    fake.lock().requests.clear();
    let vo = client(fake.port, true);
    assert_eq!(vo.put("/v1/stage/message", &json!("x")).await.unwrap_err().kind, Kind::ViewOnly);
    assert_eq!(vo.post("/v1/x", Some(&json!([]))).await.unwrap_err().kind, Kind::ViewOnly);
    assert_eq!(vo.delete("/v1/stage/message").await.unwrap_err().kind, Kind::ViewOnly);
    assert_eq!(vo.action("/v1/trigger/next").await.unwrap_err().kind, Kind::ViewOnly, "a GET that triggers is a write");
    assert!(fake.lock().requests.is_empty(), "nothing reached ProPresenter");
    assert_eq!(vo.version().await.unwrap()["api_version"], "v1", "reads still work in view-only mode");

    let switch = Arc::new(std::sync::atomic::AtomicBool::new(false));
    let s = Arc::clone(&switch);
    let live = Client::new(&Net::unrouted(), "127.0.0.1", fake.port, Arc::new(move || s.load(std::sync::atomic::Ordering::SeqCst)));
    live.put("/v1/stage/message", &json!("ok")).await.unwrap();
    switch.store(true, std::sync::atomic::Ordering::SeqCst);
    assert_eq!(live.put("/v1/stage/message", &json!("no")).await.unwrap_err().kind, Kind::ViewOnly, "the switch is read on every call");
    let puts = fake.requests("PUT");
    assert_eq!(puts, [("/v1/stage/message".to_owned(), b"\"ok\"".to_vec())], "the stage message is a bare JSON string on the wire");
}

// ------------------------------------------------------------------------------------------------------------------- follower
#[tokio::test]
async fn it_follows_the_slide_the_lyrics_and_the_stage_message() {
    let r = rig().await;
    r.fake.lock().default_song();
    r.configure(json!({}));
    let streams_before = r.fake.lock().stream_count;
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up()).await, "connects and is up");
    assert!(until(6.0, || !f.lyrics()["text"].as_str().unwrap_or("").is_empty()).await, "the first read of the slide arrives with the connection");
    let ly = f.lyrics();
    assert_eq!(ly["title"], "Great Song", "presentation name is trimmed");
    assert_eq!(ly["song"], true, "a presentation in the Songs library is a song");
    assert_eq!((ly["text"].clone(), ly["next"].clone()), (json!("line one\nline two"), json!("line three")));
    assert_eq!((ly["part"].clone(), ly["index"].clone()), (json!("Verse 1"), json!(0)), "the part the slide is in");
    {
        let inner = r.fake.lock();
        assert_eq!(inner.stream_count - streams_before, 1, "one stream");
        assert_eq!(inner.stream_bodies.last().unwrap(), &STREAM_PATHS.map(str::to_owned).to_vec(), "with the fixed path list");
    }

    // a slide change that is pushed
    r.fake.lock().index = Some(1);
    r.fake.push(&["status/slide", "presentation/slide_index"]);
    assert!(until(6.0, || f.lyrics()["text"] == "line three").await, "a pushed slide change shows");
    assert_eq!(f.lyrics()["part"], "Verse 1", "the part follows the slide");
    // one that is not pushed at all (the stream misses changes)
    r.fake.lock().index = Some(2);
    assert!(until(6.0, || f.lyrics()["text"] == "chorus a").await, "a slide change with no push is found by the re-read");
    assert!(until(6.0, || f.lyrics()["part"] == "Chorus").await, "the part changes at the group boundary");
    assert_eq!(f.lyrics()["next"], "", "the last slide has no next");

    // a feed frame: whole state
    let (_, mut rx) = r.ctx().lyrics.subscribe("screen");
    let first = rx.try_recv().expect("the first frame is queued");
    let first: Value = serde_json::from_str(std::str::from_utf8(&first).unwrap().trim_start_matches("data: ").trim()).unwrap();
    let mut keys: Vec<&str> = first.as_object().unwrap().keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(keys, ["enabled", "index", "live", "next", "part", "rev", "server_time", "song", "text", "title", "type"], "the feed's message shape");
    assert_eq!((first["type"].clone(), first["enabled"].clone(), first["live"].clone(), first["text"].clone()), (json!("lyrics"), json!(true), json!(true), json!("chorus a")));

    // the same text pushed twice does nothing
    let rev = f.rev();
    r.fake.push(&["status/slide", "status/slide", "presentation/slide_index"]);
    assert!(!until(0.7, || f.rev() != rev).await, "the same text again changes nothing (no new revision)"); // allow-sleep: shows no second push arrives

    // clear: presentation null, but status/slide keeps the old text
    r.fake.lock().clear();
    r.fake.push(&["presentation/current", "presentation/slide_index"]);
    assert!(until(6.0, || f.lyrics()["live"] == false).await, "clear: no presentation, no lyrics, whatever status/slide says");
    assert_eq!((f.lyrics()["text"].clone(), f.lyrics()["title"].clone()), (json!(""), json!("")));
    let mut last = None;
    assert!(until(6.0, || {
        while let Ok(b) = rx.try_recv() {
            last = Some(b);
        }
        last.is_some()
    })
    .await, "the screen was told");
    let last: Value = serde_json::from_str(std::str::from_utf8(&last.unwrap()).unwrap().trim_start_matches("data: ").trim()).unwrap();
    assert_eq!((last["live"].clone(), last["text"].clone()), (json!(false), json!("")), "the feed says not live");

    // a clear nobody pushed
    r.fake.lock().song("Second Song", "Songs", vec![("Verse 1", vec!["two a"])]);
    r.fake.push(&["presentation/current", "status/slide", "presentation/slide_index"]);
    assert!(until(6.0, || f.lyrics()["title"] == "Second Song").await, "a new presentation shows");
    r.fake.lock().clear();
    assert!(until(6.0, || f.lyrics()["live"] == false).await, "a clear with no push is found by the re-read");

    // not a song
    r.fake.lock().song("Sermon Slides", "Media", vec![("Slide", vec!["Point one", "Point two"])]);
    r.fake.push(&["presentation/current"]);
    assert!(until(6.0, || f.presentation().is_some_and(|p| p.name == "Sermon Slides") && f.current().text == "Point one").await);
    assert_eq!((f.is_song(), f.lyrics()["live"].clone(), f.lyrics()["text"].clone()), (false, json!(false), json!("")), "songs-only keeps its slides off the lyrics feed");
    r.configure(json!({"songs_only": false}));
    assert_eq!((f.lyrics()["live"].clone(), f.lyrics()["text"].clone()), (json!(true), json!("Point one")), "with songs-only off it shows");
    r.configure(json!({"songs_only": true, "songs_library": "Media"}));
    assert!(f.is_song(), "the library name is the operator's");
    r.configure(json!({}));
    r.fake.lock().song("Part Names Only", "Elsewhere", vec![("Verse 1", vec!["x"]), ("Chorus", vec!["y"])]);
    r.fake.push(&["presentation/current"]);
    assert!(until(6.0, || f.presentation().is_some_and(|p| p.name == "Part Names Only") && f.is_song()).await, "Verse / Chorus group names also mark a song");

    // the stage message, from ProPresenter's side and Relay's
    r.fake.lock().stage = "typed in Pro".into();
    r.fake.push(&["stage/message"]);
    assert!(until(6.0, || f.stage_message() == "typed in Pro").await, "the stage message is followed");
    r.fake.lock().requests.clear();
    f.set_stage_message("Sound desk needed", None).await.unwrap();
    assert_eq!(r.fake.requests("PUT"), [("/v1/stage/message".to_owned(), b"\"Sound desk needed\"".to_vec())], "PUT /v1/stage/message with a bare JSON string");
    assert_eq!(r.fake.lock().stage, "Sound desk needed", "and ProPresenter has it");
    f.clear_stage_message().await.unwrap();
    let deletes = r.fake.requests("DELETE").len();
    assert_eq!((r.fake.lock().stage.clone(), deletes), (String::new(), 1), "DELETE clears it");
    // view-only: switched on in config, takes effect on the live client at once
    r.amend(json!({"view_only": true}));
    r.fake.lock().requests.clear();
    assert_eq!(f.set_stage_message("nope", None).await.unwrap_err().kind, Kind::ViewOnly, "view-only refuses the stage message");
    assert_eq!(f.clear_stage_message().await.unwrap_err().kind, Kind::ViewOnly, "and the clear");
    assert!(r.fake.requests("PUT").is_empty() && r.fake.requests("DELETE").is_empty(), "and nothing was sent");
    assert!(f.is_up() && f.presentation().is_some(), "reading carries on in view-only mode");
    r.configure(json!({}));

    // the feed's switches
    let feed = &r.ctx().lyrics;
    assert!(feed.allowed("screen") && feed.allowed("panel"), "screen and panels allowed");
    assert!(!feed.allowed("viewer"), "the phone viewer is not");
    assert!(!feed.allowed("overlay"), "the overlay is opt-in");
    let mut off = feed.payload("viewer");
    off.as_object_mut().unwrap().remove("server_time");
    assert_eq!(off, json!({"type": "lyrics", "enabled": false, "live": false}), "a refused audience gets nothing but enabled:false");
    r.module.stop().await;
}

#[tokio::test]
async fn a_stalled_heartbeat_reconnects_and_two_misses_mark_it_down_once() {
    let r = rig().await;
    r.fake.lock().default_song();
    r.configure(json!({}));
    let before = r.fake.lock().stream_count;
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up()).await, "up");
    // the heartbeat stops: the connection is dropped and reopened
    r.fake.lock().beat = false;
    assert!(until(8.0, || r.fake.lock().stream_count >= before + 2).await, "a stalled heartbeat reconnects");
    assert!(f.is_up(), "one stall is one miss: still up");
    r.fake.lock().beat = true;
    assert!(until(6.0, || f.is_up() && f.state()["error"] == "").await, "and it carries on");
    // ProPresenter refuses: two misses make it down
    r.fake.lock().refuse = true;
    r.fake.kill_streams();
    assert!(until(8.0, || !f.is_up()).await, "two misses in a row mark it down");
    assert_eq!(f.lyrics()["live"], false, "and the lyrics are gone");
    r.fake.lock().refuse = false;
    assert!(until(8.0, || f.is_up()).await, "recovery");
    assert!(until(6.0, || f.lyrics()["live"] == true).await, "the slide is read again after it");
    // an address that does not answer: not up, with a reason
    r.configure(json!({"port": 9}));
    f.poke().await;
    assert!(until(8.0, || !f.is_up() && f.state()["error"].as_str().is_some_and(|e| e.contains("Could not reach"))).await, "a dead address is down with a reason");
    assert_eq!(r.module.health().state, relay_core::HealthState::Error);
    // switched off: quiet
    r.configure(json!({"enabled": false}));
    f.poke().await;
    assert!(until(6.0, || f.client().is_none() && !f.is_up()).await, "switched off: idle");
    assert_eq!(r.module.health().state, relay_core::HealthState::Warn);
    r.module.stop().await;
}

#[tokio::test]
async fn an_alert_shows_on_the_stage_display_and_only_its_own_end_takes_it_down() {
    let r = rig().await;
    r.fake.lock().default_song();
    r.configure(json!({}));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up()).await, "up");
    let alert = |id: &str, text: Option<&str>| r.host.announce(ALERT_STAGE, &json!({"id": id, "text": text}).to_string());
    alert("a1", Some("Sound desk (from Drums)"));
    assert!(until(6.0, || !r.fake.lock().stage.is_empty()).await, "an alert on a stage button shows on the stage display");
    assert_eq!(r.fake.lock().stage, "Sound desk (from Drums)");
    alert("a1", None);
    assert!(until(6.0, || r.fake.lock().stage.is_empty()).await, "the alert ending clears it");
    // an operator's own message is not wiped by an older alert ending
    alert("a2", Some("Sound desk (from Bass)"));
    assert!(until(6.0, || r.fake.lock().stage == "Sound desk (from Bass)").await);
    f.set_stage_message("Operator says hi", None).await.unwrap();
    alert("a2", None);
    assert!(!until(0.6, || r.fake.lock().stage != "Operator says hi").await, "an alert ending does not clear someone else's message"); // allow-sleep: shows nothing is sent
    f.clear_stage_message().await.unwrap();
    // view-only: the alert still flashes elsewhere; the stage display is just left alone
    r.amend(json!({"view_only": true}));
    r.fake.lock().requests.clear();
    alert("a3", Some("Keys"));
    assert!(!until(0.6, || !r.fake.requests("PUT").is_empty() || !r.fake.requests("DELETE").is_empty()).await, "view-only: the stage display is not touched"); // allow-sleep: shows nothing is sent
    r.module.stop().await;
}

// ------------------------------------------------------------------------------------------------------------------ the routes
#[tokio::test]
async fn the_admin_routes_save_test_and_send_the_stage_message() {
    let r = rig().await;
    let fake = &r.fake;
    let (st, body, _) = r.send(Method::POST, URL, Some(json!({"host": "http://10.0.0.5:50001/"}))).await;
    assert_eq!((st.as_u16(), body["error"].as_str().unwrap_or("").contains("no http://")), (400, true), "a URL as the host is refused with a sentence");
    r.store.replace("propresenter", &json!({})).unwrap();
    let (st, body, _) = r.send(Method::POST, URL, Some(json!({"enabled": true, "host": "10.0.0.5"}))).await;
    assert_eq!((st.as_u16(), body["error"].as_str().unwrap_or("").to_lowercase().contains("port")), (400, true), "switching on with no port is refused");
    assert_eq!(r.send(Method::POST, URL, Some(json!({"host": "10.0.0.5", "port": 70000}))).await.0.as_u16(), 400, "a bad port is refused");
    let (st, body, _) = r.send(Method::POST, &format!("{URL}/test"), Some(json!({"host": "127.0.0.1", "port": fake.port}))).await;
    assert_eq!((st.as_u16(), body["version"]["host_description"].clone()), (200, json!("ProPresenter 22")), "Test connection reads /version");
    let (st, body, _) = r.send(Method::POST, &format!("{URL}/test"), Some(json!({"host": "127.0.0.1", "port": 9}))).await;
    assert_eq!((st.as_u16(), body["error"].as_str().unwrap_or("").contains("Could not reach")), (502, true), "Test connection on a dead port says so");
    assert_eq!(r.send(Method::POST, &format!("{URL}/test"), Some(json!({"host": "", "port": 0}))).await.0.as_u16(), 400, "Test connection needs an address");

    let (st, body, _) = r.send(Method::POST, URL, Some(json!({"enabled": false, "host": "127.0.0.1", "port": fake.port, "lyrics": {"overlay": true}}))).await;
    assert_eq!((st.as_u16(), body["settings"]["host"].clone(), body["settings"]["port"].clone()), (200, json!("127.0.0.1"), json!(fake.port)), "save keeps the host and port");
    assert_eq!(body["settings"]["lyrics"], json!({"viewer": false, "screen": true, "overlay": true, "panels": true}), "a lyrics switch moves alone");
    assert_eq!(body["state"]["configured"], false, "while off, the state says not configured");
    // several computers (#483): one is primary and its address is the host and port the follower reads
    assert_eq!((body["settings"]["computers"].clone(), body["settings"]["primary"].clone()), (json!([{"id": "main", "name": "ProPresenter", "host": "127.0.0.1", "port": fake.port}]), json!("main")), "the one saved computer became the first, main");
    let (_, body, _) = r.send(Method::POST, URL, Some(json!({"computers": [
        {"id": "main", "name": "Sanctuary", "host": "127.0.0.1", "port": fake.port}, {"name": "Overflow", "host": "10.0.0.8", "port": 50002}, {"name": "No host", "host": ""}, "junk"]}))).await;
    let b = &body["settings"];
    let rows: Vec<(String, String, String)> = b["computers"].as_array().unwrap().iter().map(|c| (c["id"].as_str().unwrap().into(), c["name"].as_str().unwrap().into(), c["host"].as_str().unwrap().into())).collect();
    assert_eq!(rows, [("main".into(), "Sanctuary".into(), "127.0.0.1".into()), ("c2".into(), "Overflow".into(), "10.0.0.8".into())], "no host or junk is dropped, ids and names are made");
    assert_eq!((b["primary"].clone(), b["host"].clone(), b["port"].clone()), (json!("main"), json!("127.0.0.1"), json!(fake.port)));
    let (_, body, _) = r.send(Method::POST, URL, Some(json!({"primary": "c2"}))).await;
    assert_eq!((body["settings"]["host"].clone(), body["settings"]["port"].clone()), (json!("10.0.0.8"), json!(50002)), "another computer is made primary");
    let (_, body, _) = r.send(Method::POST, URL, Some(json!({"primary": "nope"}))).await;
    assert_eq!(body["settings"]["primary"], "main", "an unknown primary falls back to the first computer");
    let (_, body, _) = r.send(Method::POST, URL, Some(json!({"port": fake.port + 1}))).await;
    let ports: Vec<(String, i64)> = body["settings"]["computers"].as_array().unwrap().iter().map(|c| (c["id"].as_str().unwrap().into(), c["port"].as_i64().unwrap())).collect();
    assert_eq!(ports, [("main".into(), i64::from(fake.port) + 1), ("c2".into(), 50002)], "the old host and port body edits the primary");
    assert_eq!(r.send(Method::POST, URL, Some(json!({"computers": [{"id": "main", "host": "http://bad/"}]}))).await.0.as_u16(), 400, "a URL as a computer's host is refused");
    r.send(Method::POST, URL, Some(json!({"computers": [{"id": "main", "name": "ProPresenter", "host": "127.0.0.1", "port": fake.port}]}))).await;

    // the stream refuses what is switched off
    let module = &r.module;
    let query = |pairs: &[(&str, &str)]| pairs.iter().map(|(k, v)| ((*k).to_owned(), (*v).to_owned())).collect::<std::collections::BTreeMap<_, _>>();
    let stream = |q: &[(&str, &str)], name: &str| module.stream(name, &query(q), &axum::http::HeaderMap::new()).map(|res| res.status().as_u16());
    r.send(Method::POST, URL, Some(json!({"enabled": true}))).await;
    assert_eq!(stream(&[], "lyrics"), Some(403), "the phone viewer is refused by default");
    assert_eq!(stream(&[("for", "tv")], "lyrics"), Some(400), "so is an unknown audience");
    r.send(Method::POST, URL, Some(json!({"lyrics": {"screen": false, "overlay": false, "panels": false}}))).await;
    assert_eq!(["screen", "overlay", "panel"].map(|a| stream(&[("for", a)], "lyrics")), [Some(403), Some(403), Some(403)], "every audience can be switched off");
    assert_eq!(stream(&[("panel", "zzz")], "pptimers"), Some(400), "a timers reader names a panel by its id or none");
    assert_eq!(stream(&[("for", "tv")], "pptimers"), Some(400));
    r.send(Method::POST, URL, Some(json!({"enabled": false}))).await;
    assert_eq!(stream(&[("for", "screen")], "lyrics"), Some(403), "nothing is offered while ProPresenter is off");
    assert_eq!(stream(&[], "pptimers"), Some(403), "and no timers either");

    // stage routes: view-only maps to 409
    r.configure(json!({"view_only": true}));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up()).await);
    fake.lock().requests.clear();
    let (st, body, _) = r.send(Method::POST, &format!("{URL}/stage"), Some(json!({"text": "hello"}))).await;
    assert_eq!((st.as_u16(), body["error"].as_str().unwrap_or("").contains("View-only")), (409, true), "the admin stage message is refused in view-only mode");
    assert_eq!(r.send(Method::DELETE, &format!("{URL}/stage"), None).await.0.as_u16(), 409, "and so is the clear");
    assert!(fake.requests("PUT").is_empty() && fake.requests("DELETE").is_empty(), "nothing reached ProPresenter");
    r.configure(json!({"view_only": false}));
    let (st, _, _) = r.send(Method::POST, &format!("{URL}/stage"), Some(json!({"text": "  Sound   desk needed  "}))).await;
    assert_eq!((st.as_u16(), fake.lock().stage.clone()), (200, "Sound desk needed".to_owned()), "the admin stage message goes out tidied");
    assert_eq!(r.send(Method::POST, &format!("{URL}/stage"), Some(json!({"text": "  "}))).await.0.as_u16(), 400, "an empty message is refused");
    let (st, _, _) = r.send(Method::DELETE, &format!("{URL}/stage"), None).await;
    assert_eq!((st.as_u16(), fake.lock().stage.clone()), (200, String::new()), "the admin clear");

    // roles: reading needs view, the stage needs control, the connection needs manage
    r.gate.refuse.lock().unwrap().push("propresenter.manage");
    assert_eq!(r.send(Method::POST, URL, Some(json!({"enabled": false}))).await.0.as_u16(), 403);
    assert_eq!(r.send(Method::POST, &format!("{URL}/test"), Some(json!({}))).await.0.as_u16(), 403);
    assert_eq!(r.send(Method::GET, URL, None).await.0.as_u16(), 200, "an operator reads the page");
    r.gate.refuse.lock().unwrap().push("propresenter.control");
    assert_eq!(r.send(Method::POST, &format!("{URL}/stage"), Some(json!({"text": "x"}))).await.0.as_u16(), 403);
    r.module.stop().await;
    let _ = Duration::from_secs(0);
}
