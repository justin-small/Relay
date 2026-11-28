//! What `tests/test_propresenter.py` checks of control (#138 stage 2): the routes' roles, next/previous, the slide grid and its thumbnails, playlists,
//! macros and the operator-safe selection, looks, timers, messages, the media transport, screens, view-only refusal and ProPresenter being away.
//! A fake ProPresenter over real sockets (`tests/fake`); the follower runs against it, so the control GETs are told apart from the follower's own reads
//! by the paths that act.

mod fake;

use std::sync::Arc;

use axum::http::{Method, StatusCode};
use fake::{anonymous, rig, send_to, until, Rig};
use relay_core::Module;
use relay_module_propresenter::client::is_action_path;
use serde_json::{json, Value};

const B: &str = "/api/admin/propresenter";

/// The paths of the GETs that act (trigger, clear, timer operations), in order.
fn acts(r: &Rig) -> Vec<String> {
    r.fake.lock().requests.iter().filter(|q| q.0 == "GET" && is_action_path(&q.1)).map(|q| q.1.clone()).collect()
}

fn writes(r: &Rig, method: &str) -> Vec<(String, Value)> {
    r.fake.requests(method).into_iter().map(|(p, b)| (p, serde_json::from_slice(&b).unwrap_or(Value::Null))).collect()
}

async fn up(r: &Rig) {
    {
        let mut f = r.fake.lock();
        f.song("Great Song  ", "Songs", vec![("Verse 1", vec!["v1 a", "v1 b"]), ("Chorus", vec!["chorus a"])]);
        f.presentation.as_mut().unwrap().arrangement = Some(["Verse 1", "Chorus", "Verse 1", "Chorus"].map(str::to_owned).to_vec());
        f.index = Some(2);
    }
    r.configure(json!({"view_only": false, "macros_mode": "selected", "macros_allowed": ["M1"]}));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up() && f.presentation().is_some()).await, "connects");
}

async fn post(r: &Rig, path: &str, body: Value) -> (StatusCode, Value) {
    let (s, v, _) = r.send(Method::POST, &format!("{B}{path}"), Some(body)).await;
    (s, v)
}

#[tokio::test]
async fn nobody_signed_in_reaches_nothing_and_roles_hold() {
    let r = rig().await;
    up(&r).await;
    let anon = anonymous();
    let routes = [
        ("GET", "/slides"), ("GET", "/thumbnail/P-Grea/0"), ("POST", "/step"), ("POST", "/next-presentation"), ("POST", "/slide"), ("POST", "/clear"),
        ("GET", "/playlists"), ("GET", "/playlists/PL1"), ("POST", "/playlists/PL1/trigger"), ("GET", "/macros"), ("POST", "/macros/M1/trigger"),
        ("GET", "/looks"), ("POST", "/looks/L1/trigger"), ("GET", "/timers"), ("POST", "/timers/T1/start"), ("GET", "/messages"),
        ("POST", "/messages/MSG1/trigger"), ("POST", "/messages/MSG1/clear"), ("POST", "/transport"), ("POST", "/item"), ("POST", "/screens"),
        ("GET", "/playlists/PL3/diagnostics"), ("GET", "/playlists/PL3/items/1/cues"), ("GET", "/playlists/PL3/thumbnail/1/0"), ("GET", "/lobby"),
        ("POST", "/lobby/start"), ("GET", "/live"), ("GET", "/live/image/current?v=x"),
    ];
    r.fake.lock().requests.clear();
    for (m, p) in routes {
        let method = if m == "GET" { Method::GET } else { Method::POST };
        let (st, _, _) = send_to(&anon, method, &format!("{B}{p}"), if m == "POST" { Some(json!({})) } else { None }).await;
        assert_eq!(st, StatusCode::UNAUTHORIZED, "{m} {p} answers nobody who has not signed in");
    }
    assert!(acts(&r).is_empty() && r.fake.requests("POST").is_empty(), "and none reached ProPresenter");

    // roles: an operator controls, a viewer reads, only an admin changes the setup
    r.gate.refuse.lock().unwrap().push("propresenter.manage");
    assert_eq!(post(&r, "/step", json!({"direction": "next"})).await.0, StatusCode::OK, "an operator steps the slides");
    for p in ["/macros/M1/trigger", "/looks/L1/trigger", "/messages/MSG1/clear"] {
        assert_eq!(post(&r, p, json!({})).await.0, StatusCode::OK, "{p}");
    }
    for p in ["/playlists", "/slides"] {
        assert_eq!(r.send(Method::GET, &format!("{B}{p}"), None).await.0, StatusCode::OK, "an operator reads {p}");
    }
    assert_eq!(r.send(Method::POST, B, Some(json!({"view_only": true}))).await.0, StatusCode::FORBIDDEN, "an operator may not change the setup");
    for p in ["/test", "/discover"] {
        assert_eq!(post(&r, p, json!({})).await.0, StatusCode::FORBIDDEN, "nor {p}");
    }
    assert_eq!(r.send(Method::GET, &format!("{B}/macros?all=true"), None).await.0, StatusCode::FORBIDDEN, "nor list every macro");
    assert_eq!(r.ctx().follower.settings()["view_only"], false, "and the setup is unchanged");
    r.gate.refuse.lock().unwrap().push("propresenter.control");
    r.fake.lock().requests.clear();
    for p in ["", "/slides", "/playlists", "/macros", "/live"] {
        assert_eq!(r.send(Method::GET, &format!("{B}{p}"), None).await.0, StatusCode::OK, "a viewer reads {p}");
    }
    for p in ["/step", "/clear", "/stage", "/playlists/PL1/trigger", "/timers/T1/start", "/lobby/start", "/transport"] {
        assert_eq!(post(&r, p, json!({})).await.0, StatusCode::FORBIDDEN, "a viewer may not control {p}");
    }
    assert_eq!(r.send(Method::DELETE, &format!("{B}/stage"), None).await.0, StatusCode::FORBIDDEN);
    assert!(acts(&r).is_empty(), "a refused request never reached ProPresenter");
    r.module.stop().await;
}

#[tokio::test]
async fn steps_slides_clears_the_grid_and_thumbnails() {
    let r = rig().await;
    up(&r).await;
    r.fake.lock().requests.clear();
    assert_eq!(post(&r, "/step", json!({"direction": "next"})).await.0, StatusCode::OK);
    post(&r, "/step", json!({"direction": "previous"})).await;
    post(&r, "/next-presentation", json!({})).await;
    assert_eq!(acts(&r), ["/v1/trigger/next", "/v1/trigger/previous", "/v1/playlist/focused/next/trigger"], "next, previous and the next item hit the right paths");
    assert_eq!(post(&r, "/step", json!({"direction": "sideways"})).await.0, StatusCode::BAD_REQUEST, "a bad direction is refused");
    r.fake.lock().requests.clear();
    post(&r, "/slide", json!({"uuid": "P-Grea", "index": 3})).await;
    assert_eq!(acts(&r), ["/v1/presentation/P-Grea/3/trigger"], "a slide is triggered by presentation and index");
    assert_eq!(post(&r, "/slide", json!({"uuid": "../clear/layer/slide", "index": 0})).await.0, StatusCode::BAD_REQUEST, "a path-shaped uuid is refused");
    assert_eq!(post(&r, "/slide", json!({"uuid": "P-Grea", "index": "x"})).await.0, StatusCode::BAD_REQUEST, "so is a bad index");
    assert_eq!(post(&r, "/slide", json!({"uuid": "P-Grea", "index": -1})).await.0, StatusCode::BAD_REQUEST, "and a negative one");
    r.fake.lock().requests.clear();
    let layers = ["slide", "media", "props", "messages", "audio", "video_input", "announcements"];
    for l in layers {
        post(&r, "/clear", json!({"layer": l})).await;
    }
    assert_eq!(acts(&r), layers.map(|l| format!("/v1/clear/layer/{l}")), "each layer clears by its own path");
    assert_eq!(post(&r, "/clear", json!({"layer": "everything"})).await.0, StatusCode::BAD_REQUEST, "an unknown layer is refused");

    // the slide grid follows the arrangement, with group names and colours
    let (_, grid, _) = r.send(Method::GET, &format!("{B}/slides"), None).await;
    let cues: Vec<(i64, String)> = grid["cues"].as_array().unwrap().iter().map(|c| (c["index"].as_i64().unwrap(), c["group"].as_str().unwrap().to_owned())).collect();
    assert_eq!(cues, [(0, "Verse 1"), (1, "Verse 1"), (2, "Chorus"), (3, "Verse 1"), (4, "Verse 1"), (5, "Chorus")].map(|(i, g)| (i, g.to_owned())), "a cue for every play of a repeated group");
    assert_eq!(grid["cues"][0]["color"], "#ff0080", "each cue carries its group's colour");
    let rev = grid["rev"].as_str().unwrap().to_owned();
    r.fake.lock().presentation.as_mut().unwrap().groups[0] = ("Verse 1".into(), vec!["v1 a".into(), "v1 CHANGED".into()]);
    assert_ne!(r.send(Method::GET, &format!("{B}/slides"), None).await.1["rev"], rev.as_str(), "the change marker moves when a slide changes");
    r.fake.lock().presentation.as_mut().unwrap().groups[0] = ("Verse 1".into(), vec!["v1 a".into(), "v1 b".into()]);
    assert_eq!(r.send(Method::GET, &format!("{B}/slides"), None).await.1["rev"], rev.as_str(), "and returns when it is put back");

    // thumbnails: proxied, cached by presentation + index + change marker
    r.fake.lock().thumb_calls = 0;
    let (st, ctype, bytes) = r.fetch(&format!("{B}/thumbnail/P-Grea/2?v={rev}")).await;
    assert_eq!((st, ctype.as_str(), &bytes[..2]), (StatusCode::OK, "image/jpeg", &b"\xff\xd8"[..]), "a thumbnail is served as an image");
    let (_, _, again) = r.fetch(&format!("{B}/thumbnail/P-Grea/2?v={rev}")).await;
    assert_eq!((again == bytes, r.fake.lock().thumb_calls), (true, 1), "asking again is a cache hit: ProPresenter was asked once");
    r.fetch(&format!("{B}/thumbnail/P-Grea/3?v={rev}")).await;
    assert_eq!(r.fake.lock().thumb_calls, 2, "another index is another fetch");
    r.fetch(&format!("{B}/thumbnail/P-Grea/2?v=other")).await;
    assert_eq!(r.fake.lock().thumb_calls, 3, "a new change marker fetches again");
    assert!(r.fake.lock().requests.iter().any(|q| q.1.starts_with("/v1/presentation/P-Grea/thumbnail/2?quality=320")), "ProPresenter's own thumbnail path is Relay's to ask");
    assert!(matches!(r.fetch(&format!("{B}/thumbnail/..%2Fx/0")).await.0.as_u16(), 400 | 404), "a bad thumbnail id is refused");
    r.module.stop().await;
}

#[tokio::test]
async fn playlists_macros_looks_timers_messages_transport_and_screens() {
    let r = rig().await;
    up(&r).await;
    // playlists
    let (_, tree, _) = r.send(Method::GET, &format!("{B}/playlists"), None).await;
    let rows: Vec<(String, String, Vec<String>)> = tree["playlists"].as_array().unwrap().iter().map(|p| (p["name"].as_str().unwrap().into(), p["type"].as_str().unwrap().into(), p["children"].as_array().unwrap().iter().map(|c| c["name"].as_str().unwrap().to_owned()).collect())).collect();
    assert_eq!(rows, [("Sunday".to_owned(), "playlist".to_owned(), vec![]), ("Folder".to_owned(), "group".to_owned(), vec!["Youth".to_owned()])], "playlists come as a tree");
    let (_, pl, _) = r.send(Method::GET, &format!("{B}/playlists/PL1"), None).await;
    let items: Vec<(i64, &str, &str, &str)> = pl["items"].as_array().unwrap().iter().map(|i| (i["index"].as_i64().unwrap(), i["name"].as_str().unwrap(), i["type"].as_str().unwrap(), i["presentation"].as_str().unwrap())).collect();
    assert_eq!(items, [(0, "Welcome", "header", ""), (1, "Great Song", "presentation", "P-Grea")], "a playlist lists its items");
    r.fake.lock().requests.clear();
    post(&r, "/playlists/PL1/trigger", json!({"index": 1})).await;
    assert_eq!(acts(&r), ["/v1/playlist/PL1/1/trigger"], "an item is triggered by playlist and index");

    // macros: the operator-safe selection is enforced on the server
    let (_, m, _) = r.send(Method::GET, &format!("{B}/macros"), None).await;
    assert_eq!(m["macros"].as_array().unwrap().iter().map(|x| x["name"].as_str().unwrap()).collect::<Vec<_>>(), ["Lights up"], "only the selected macros are listed");
    let (_, all, _) = r.send(Method::GET, &format!("{B}/macros?all=true"), None).await;
    let picked: Vec<(&str, bool)> = all["macros"].as_array().unwrap().iter().map(|x| (x["name"].as_str().unwrap(), x["selected"].as_bool().unwrap())).collect();
    assert_eq!(picked, [("Lights up", true), ("Reboot", false)], "the picker lists them all");
    r.fake.lock().requests.clear();
    assert_eq!(post(&r, "/macros/M1/trigger", json!({})).await.0, StatusCode::OK, "a selected macro triggers by its ID");
    assert_eq!(post(&r, "/macros/M2/trigger", json!({})).await.0, StatusCode::FORBIDDEN, "one outside the selection is refused");
    assert_eq!(acts(&r), ["/v1/macro/M1/trigger"], "and never reached ProPresenter");
    r.send(Method::POST, B, Some(json!({"macros_mode": "all"}))).await;
    assert_eq!(r.send(Method::GET, &format!("{B}/macros"), None).await.1["macros"].as_array().unwrap().len(), 2, "with 'all' every macro is listed");
    assert_eq!(post(&r, "/macros/M2/trigger", json!({})).await.0, StatusCode::OK, "and may be triggered");
    let (_, saved, _) = r.send(Method::POST, B, Some(json!({"macros_mode": "bogus", "macros_allowed": ["M1", "../x", 5]}))).await;
    assert_eq!((saved["settings"]["macros_mode"].clone(), saved["settings"]["macros_allowed"].clone()), (json!("selected"), json!(["M1"])), "a bad mode and bad IDs are cleaned on save");

    // looks
    let (_, looks, _) = r.send(Method::GET, &format!("{B}/looks"), None).await;
    assert_eq!(looks["looks"].as_array().unwrap().iter().map(|l| l["name"].as_str().unwrap()).collect::<Vec<_>>(), ["Default", "Sermon"]);
    r.fake.lock().requests.clear();
    post(&r, "/looks/L2/trigger", json!({})).await;
    assert_eq!(acts(&r), ["/v1/look/L2/trigger"], "a look is triggered by ID");

    // timers: one at a time, never the bulk call
    let (_, t, _) = r.send(Method::GET, &format!("{B}/timers"), None).await;
    assert_eq!((t["timers"][0]["name"].clone(), t["timers"][0]["kind"].clone()), (json!("Segment"), json!("countdown")));
    r.fake.lock().requests.clear();
    for op in ["start", "stop", "reset"] {
        post(&r, &format!("/timers/T1/{op}"), json!({})).await;
    }
    assert_eq!(acts(&r), ["/v1/timer/T1/start", "/v1/timer/T1/stop", "/v1/timer/T1/reset"], "each operation goes to the one timer");
    assert_eq!(post(&r, "/timers/T1/explode", json!({})).await.0, StatusCode::BAD_REQUEST, "an unknown operation is refused");
    assert!(r.fake.lock().requests.iter().all(|q| !q.1.starts_with("/v1/timers/")), "no bulk timer path was sent");

    // messages
    let (_, msgs, _) = r.send(Method::GET, &format!("{B}/messages"), None).await;
    assert_eq!(msgs["messages"][0]["tokens"], json!([{"name": "room", "kind": "text", "text": "3"}]), "messages list with their tokens");
    assert_eq!(msgs["messages"][0]["active"], false);
    r.fake.lock().requests.clear();
    let (st, _) = post(&r, "/messages/MSG1/trigger", json!({"tokens": [{"name": "room", "text": "  Room   12 "}]})).await;
    assert_eq!((st, writes(&r, "POST")), (StatusCode::OK, vec![("/v1/message/MSG1/trigger".to_owned(), json!([{"name": "room", "text": {"text": "Room 12"}}]))]), "a message is triggered with its text tokens");
    assert_eq!(r.send(Method::GET, &format!("{B}/messages"), None).await.1["messages"][0]["active"], true, "it then reads as active");
    r.fake.lock().requests.clear();
    post(&r, "/messages/MSG1/clear", json!({})).await;
    assert_eq!(acts(&r), ["/v1/message/MSG1/clear"], "and clears with a GET to its clear path");
    assert_eq!(post(&r, "/messages/MSG1/trigger", json!({"tokens": [{"text": "x"}]})).await.0, StatusCode::BAD_REQUEST, "a token with no name is refused");

    // transport
    r.fake.lock().requests.clear();
    let (st, _) = post(&r, "/transport", json!({"seconds": 12.5})).await;
    assert_eq!((st, writes(&r, "PUT")), (StatusCode::OK, vec![("/v1/transport/presentation/time".to_owned(), json!(12.5))]), "go to a time is a PUT of a bare number");
    for v in [json!("x"), json!(-1), Value::Null] {
        assert_eq!(post(&r, "/transport", json!({"seconds": v})).await.0, StatusCode::BAD_REQUEST, "a bad time is refused");
    }

    // next / previous playlist ITEM: Relay triggered PL1 above, so that is the one it steps through
    assert_eq!(post(&r, "/item", json!({"direction": "../x"})).await.0, StatusCode::BAD_REQUEST, "a bad direction for an item is refused");
    // ProPresenter 22 leaves playlist/active empty after an item is triggered through the API (checked live), so the playlist Relay itself just triggered
    // is the one Next item steps through.
    post(&r, "/playlists/PL9/trigger", json!({"index": 2})).await;
    r.fake.lock().requests.clear();
    post(&r, "/item", json!({"direction": "next"})).await;
    post(&r, "/item", json!({"direction": "previous"})).await;
    assert_eq!(acts(&r), ["/v1/playlist/PL9/next/trigger", "/v1/playlist/PL9/previous/trigger"], "with no active playlist, they go to the playlist Relay last triggered");

    // screens: a bare true or false, through the one client
    r.fake.lock().requests.clear();
    let (st, _) = post(&r, "/screens", json!({"kind": "audience", "on": true})).await;
    assert_eq!((st, writes(&r, "PUT")), (StatusCode::OK, vec![("/v1/status/audience_screens".to_owned(), json!(true))]), "audience screens on is a PUT of a bare true");
    post(&r, "/screens", json!({"kind": "stage", "on": false})).await;
    assert_eq!(writes(&r, "PUT").last().cloned(), Some(("/v1/status/stage_screens".to_owned(), json!(false))), "stage screens off is a PUT of a bare false");
    let (audience, stage) = {
        let f = r.fake.lock();
        (f.screens_audience, f.screens_stage)
    };
    assert_eq!((audience, stage), (true, false));
    r.fake.lock().screens_audience = false;
    r.fake.lock().requests.clear();
    for b in [json!({"kind": "lobby", "on": true}), json!({"kind": "audience", "on": "yes"}), json!({"kind": "audience", "on": 1}), json!({"kind": "audience"})] {
        assert_eq!(post(&r, "/screens", b).await.0, StatusCode::BAD_REQUEST, "an unknown screen, or a non-boolean, is refused");
    }
    assert!(r.fake.requests("PUT").is_empty(), "and nothing was sent");
    r.module.stop().await;
}

#[tokio::test]
async fn playlist_items_cues_thumbnails_and_diagnostics() {
    let r = rig().await;
    up(&r).await;
    let (_, pl, _) = r.send(Method::GET, &format!("{B}/playlists/PL3"), None).await;
    let rows: Vec<(i64, &str, &str, bool, bool)> = pl["items"].as_array().unwrap().iter().map(|i| (i["index"].as_i64().unwrap(), i["type"].as_str().unwrap(), i["name"].as_str().unwrap(), i["hidden"].as_bool().unwrap(), i["pco"].as_bool().unwrap())).collect();
    assert_eq!(rows, [(0, "header", "Worship", false, false), (1, "presentation", "Great Song", false, true), (2, "presentation", "Gone Song", true, false), (3, "placeholder", "Empty slot", false, false), (4, "presentation", "Unlinked", false, false), (5, "header", "Sermon", false, false)], "every item is listed, headers and placeholders included");
    assert_eq!((pl["items"][0]["color"].clone(), pl["items"][1]["arrangement"].clone(), pl["items"][1]["arrangement_uuid"].clone()), (json!("#0000ff"), json!("Master"), json!("ARR")), "a header keeps its colour, a presentation its arrangement");
    let (_, cues, _) = r.send(Method::GET, &format!("{B}/playlists/PL3/items/1/cues"), None).await;
    let strip: Vec<(i64, &str)> = cues["cues"].as_array().unwrap().iter().map(|c| (c["index"].as_i64().unwrap(), c["group"].as_str().unwrap())).collect();
    assert_eq!(strip, [(0, "Verse 1"), (1, "Verse 1"), (2, "Chorus"), (3, "Verse 1"), (4, "Verse 1"), (5, "Chorus")], "an item's cues follow its arrangement");
    assert_eq!((cues["index"].clone(), cues["name"].clone(), cues["presentation"].clone(), cues["arrangement"].clone()), (json!(1), json!("Great Song"), json!("P-Grea"), json!("Master")), "the strip is for that item");
    let marker = cues["rev"].as_str().unwrap().to_owned();
    for i in [0, 3, 99] {
        assert_eq!(r.send(Method::GET, &format!("{B}/playlists/PL3/items/{i}/cues"), None).await.0, StatusCode::BAD_REQUEST, "item {i} has no slides");
    }
    assert!(matches!(r.send(Method::GET, &format!("{B}/playlists/..%2Fclear/items/1/cues"), None).await.0.as_u16(), 400 | 404), "a path-shaped playlist id is refused");

    // playlist thumbnails: proxied and cached exactly like the presentation ones
    r.fake.lock().thumb_calls = 0;
    r.fake.lock().requests.clear();
    let (st, ctype, bytes) = r.fetch(&format!("{B}/playlists/PL3/thumbnail/1/2?v={marker}")).await;
    assert_eq!((st, ctype.as_str(), &bytes[..2]), (StatusCode::OK, "image/jpeg", &b"\xff\xd8"[..]), "a playlist thumbnail is served as an image");
    assert!(r.fake.lock().requests.iter().any(|q| q.1 == "/v1/playlist/PL3/1/thumbnail/2?quality=320&thumbnail_type=jpeg"), "ProPresenter was asked at the playlist item's path, for that cue");
    let (_, _, again) = r.fetch(&format!("{B}/playlists/PL3/thumbnail/1/2?v={marker}")).await;
    assert_eq!((again == bytes, r.fake.lock().thumb_calls), (true, 1), "asking again is a cache hit");
    r.fetch(&format!("{B}/playlists/PL3/thumbnail/1/3?v={marker}")).await;
    assert_eq!(r.fake.lock().thumb_calls, 2, "the second chorus, at its own cue, is its own thumbnail");
    r.fetch(&format!("{B}/playlists/PL3/thumbnail/1/2?v=other")).await;
    r.fetch(&format!("{B}/playlists/PL3/thumbnail/2/2?v={marker}")).await;
    assert_eq!(r.fake.lock().thumb_calls, 4, "a new marker and another item are each a fetch");
    r.fetch(&format!("{B}/thumbnail/PL3/1?v={marker}")).await;
    assert_eq!(r.fake.lock().thumb_calls, 5, "a presentation thumbnail with the same numbers is not mistaken for it");
    assert_eq!(r.fetch(&format!("{B}/playlists/PL3/thumbnail/x/0")).await.0.as_u16(), 422);
    assert_eq!(r.fetch(&format!("{B}/playlists/PL3/thumbnail/1/-1")).await.0.as_u16(), 400);

    // playlist diagnostics
    r.fake.lock().missing.insert("P-GONE".into());
    r.fake.lock().requests.clear();
    let (_, d, _) = r.send(Method::GET, &format!("{B}/playlists/PL3/diagnostics"), None).await;
    assert_eq!((d["count"].clone(), d["types"].clone(), d["hidden"].clone()), (json!(6), json!({"header": 2, "placeholder": 1, "presentation": 3}), json!(1)), "the diagnostics count the items and their kinds");
    let status: Vec<(i64, &str)> = d["items"].as_array().unwrap().iter().map(|i| (i["index"].as_i64().unwrap(), i["status"].as_str().unwrap())).collect();
    assert_eq!(status, [(0, ""), (1, "ok"), (2, "missing"), (3, ""), (4, "none"), (5, "")], "each presentation item says whether its presentation exists");
    assert_eq!(d["problems"], 2, "items with no matching presentation are counted");
    let mut looked: Vec<String> = r.fake.lock().requests.iter().filter(|q| q.0 == "GET" && q.1.starts_with("/v1/presentation/P-") && !q.1.contains("thumbnail")).map(|q| q.1.clone()).collect();
    looked.sort();
    looked.dedup();
    assert_eq!(looked, ["/v1/presentation/P-GONE", "/v1/presentation/P-Grea"], "each presentation was looked up, and an empty one not at all");
    r.fake.lock().missing.clear();
    r.fake.lock().forced.push(("/v1/presentation/P-Grea".into(), 503, "busy".into()));
    let (_, d, _) = r.send(Method::GET, &format!("{B}/playlists/PL3/diagnostics"), None).await;
    assert_eq!(d["items"][1]["status"], "unknown", "a stalled lookup is 'unknown', never 'missing'");
    r.module.stop().await;
}

#[tokio::test]
async fn view_only_refuses_every_write_and_a_dead_proppresenter_is_said_plainly() {
    let r = rig().await;
    up(&r).await;
    r.amend(json!({"view_only": true, "macros_mode": "all"}));
    r.fake.lock().requests.clear();
    let writes_list = [
        ("/step", json!({"direction": "next"})), ("/next-presentation", json!({})), ("/slide", json!({"uuid": "P-Grea", "index": 0})), ("/clear", json!({"layer": "slide"})),
        ("/playlists/PL1/trigger", json!({"index": 1})), ("/macros/M1/trigger", json!({})), ("/looks/L1/trigger", json!({})), ("/timers/T1/start", json!({})),
        ("/messages/MSG1/trigger", json!({"tokens": []})), ("/messages/MSG1/clear", json!({})), ("/transport", json!({"seconds": 1})), ("/item", json!({"direction": "next"})),
        ("/screens", json!({"kind": "audience", "on": true})),
    ];
    for (p, b) in writes_list {
        assert_eq!(post(&r, p, b).await.0, StatusCode::CONFLICT, "view-only refuses {p} with 409");
    }
    assert!(acts(&r).is_empty() && r.fake.requests("PUT").is_empty() && r.fake.requests("POST").is_empty(), "and nothing reached ProPresenter");
    for p in ["/playlists", "/slides", "/thumbnail/P-Grea/0?v=ro", "/playlists/PL3/diagnostics", "/playlists/PL3/thumbnail/1/0?v=ro"] {
        assert_eq!(r.fetch(&format!("{B}{p}")).await.0, StatusCode::OK, "reads still work in view-only mode: {p}");
    }
    r.amend(json!({"view_only": false}));

    // ProPresenter down: a clear sentence, and no exception text
    r.configure(json!({"port": 1}));
    r.ctx().follower.poke().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || !f.is_up() && f.client().is_some_and(|c| c.port == 1)).await, "the follower is pointed at the dead address");
    let (st, body) = post(&r, "/step", json!({"direction": "next"})).await;
    assert_eq!((st, body["error"].clone()), (StatusCode::BAD_GATEWAY, json!("Could not reach ProPresenter. Check the address and port, and that ProPresenter is running.")), "with ProPresenter unreachable a control says so, without exception text");
    r.configure(json!({"enabled": false}));
    f.poke().await;
    assert!(until(6.0, || f.client().is_none()).await);
    assert_eq!(post(&r, "/step", json!({"direction": "next"})).await.0, StatusCode::BAD_GATEWAY, "with ProPresenter not set up there is an error, not a crash");
    r.module.stop().await;
}

#[tokio::test]
async fn next_item_goes_to_the_focused_playlist_until_relay_triggers_one() {
    let r = rig().await;
    up(&r).await;
    r.fake.lock().requests.clear();
    post(&r, "/item", json!({"direction": "next"})).await;
    post(&r, "/item", json!({"direction": "previous"})).await;
    assert_eq!(acts(&r), ["/v1/playlist/focused/next/trigger", "/v1/playlist/focused/previous/trigger"], "with no active playlist, next and previous item go to the focused playlist (never the slide-level trigger)");
    r.fake.lock().no_focus = true;
    let (st, body) = post(&r, "/item", json!({"direction": "next"})).await;
    assert_eq!((st, body["error"].clone()), (StatusCode::NOT_FOUND, json!("No playlist is active or focused in ProPresenter. Trigger an item first.")), "with no playlist focused, ProPresenter's 404 becomes a plain sentence");
    r.fake.lock().no_focus = false;
    r.module.stop().await;
}
