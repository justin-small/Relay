//! The OBS module against a fake obs-websocket 5 server (`tests/test_obs.py` ported): the password handshake, scenes and events, the health rule, outputs
//! that follow Start and Stop, captions into the stream, tally, alerts, the preview, the admin routes and the scene screen.

mod fake;

use std::sync::Arc;

use axum::http::{Method, StatusCode};
use fake::{rig, until, JPEG};
use relay_core::caps::{STREAM_STARTED, STREAM_STOPPED, TAG_ACTION, TagAction};
use relay_core::HealthState;
use relay_module_obs::proto::METERS;
use relay_module_obs::routes::test_machine;
use relay_module_obs::settings::{caption_lines, clean_settings, passwords, public};
use serde_json::json;

const A: &str = "/api/admin/obs";

#[test]
fn settings_are_cleaned_and_secrets_kept_out_of_a_read() {
    let s = clean_settings(&json!({"machines": [
        {"id": "a1", "name": "  Stream   PC ", "host": "10.0.0.5", "port": 99999, "captions": true, "caption_lang": "SPANISH", "key": "f".repeat(32), "screen_on": true},
        {"id": "a1", "host": "bad host!", "caption_lang": "KLINGON"}, {}, {}, {}, {}, {}],
        "passwords": json!({"a1": "pw", "zz": "gone"}).to_string()}));
    let m = s["machines"].as_array().unwrap();
    assert_eq!(m.len(), 4, "at most four computers");
    assert_eq!((m[0]["name"].clone(), m[0]["port"].clone(), m[0]["caption_lang"].clone()), (json!("Stream PC"), json!(4455), json!("SPANISH")));
    assert_ne!(m[1]["id"], json!("a1"), "a repeated id gets a new one");
    assert_eq!((m[1]["host"].clone(), m[1]["caption_lang"].clone()), (json!(""), json!("source")));
    assert_eq!(serde_json::Value::Object(passwords(&s["passwords"])), json!({"a1": "pw"}), "a password for a computer that is gone is dropped");
    let p = public(&s);
    assert!(p.get("passwords").is_none() && p["machines"].as_array().unwrap().iter().all(|m| m.get("key").is_none()));
    assert_eq!((p["machines"][0]["has_password"].clone(), p["machines"][0]["has_key"].clone()), (json!(true), json!(true)));
}

#[test]
fn captions_split_into_608_sized_lines() {
    assert_eq!(caption_lines("Hello there", 32), ["Hello there"]);
    let text = "The grace of the Lord Jesus Christ be with you all, now and forevermore. Amen.";
    let lines = caption_lines(text, 32);
    assert!(lines.iter().all(|l| l.chars().count() <= 32), "{lines:?}");
    assert_eq!(lines.join(" "), text, "nothing is lost");
    assert_eq!(caption_lines(&"x".repeat(70), 32), ["x".repeat(32), "x".repeat(32), "x".repeat(6)], "a long word is cut");
}

#[tokio::test]
async fn the_password_handshake_and_an_unreachable_port() {
    let r = rig("hunter2").await;
    let cfg = json!({"name": "Stream PC", "host": "127.0.0.1", "port": r.fake.port});
    let t = test_machine(&r.host, &cfg, "wrong").await;
    assert!(t["ok"] == json!(false) && t["detail"].as_str().unwrap().contains("refused the password"), "{t}");
    let t = test_machine(&r.host, &cfg, "").await;
    assert!(t["detail"].as_str().unwrap().contains("asks for a password"), "{t}");
    let t = test_machine(&r.host, &cfg, "hunter2").await;
    assert!(t["ok"] == json!(true) && t["detail"].as_str().unwrap().contains("3 scenes"), "{t}");
    let t = test_machine(&r.host, &json!({"name": "X", "host": "127.0.0.1", "port": 1}), "").await;
    assert!(t["detail"].as_str().unwrap().contains("Could not reach OBS"), "{t}");
}

#[tokio::test]
async fn follows_obs_switches_scenes_and_judges_health() {
    let r = rig("hunter2").await;
    r.configure(json!({}), "hunter2");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    assert_eq!(m.scenes(), ["Walk-in", "Worship", "Message"], "OBS's own order");
    assert_eq!(m.health().0, "idle");
    let e = r.board.streams.lock().unwrap().get("obs_m1").cloned().expect("published");
    assert_eq!((e.label.as_str(), e.on_air, e.health.as_str()), ("Stream PC", Some(false), "idle"));

    m.set_scene("Worship").await.unwrap();
    assert_eq!(r.fake.lock().program, "Worship");
    r.fake.lock().program = "Message".into();
    r.fake.event("CurrentProgramSceneChanged", json!({"sceneName": "Message"}));
    assert!(until(4.0, || m.program() == "Message").await, "a switch made in OBS is followed");
    assert!(m.set_scene("Nope").await.is_err());

    {
        let mut f = r.fake.lock();
        f.stream["outputActive"] = json!(true);
        f.stream["outputDuration"] = json!(65000);
        f.stream["outputTotalFrames"] = json!(1000);
    }
    r.fake.event("StreamStateChanged", json!({"outputActive": true, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"}));
    assert!(until(6.0, || m.health().0 == "ok").await, "{:?}", m.health());
    let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_secs_f64();
    assert!(until(6.0, || r.board.streams.lock().unwrap()["obs_m1"].live_since.is_some_and(|s| (now - 65.0 - s).abs() < 5.0)).await, "live since about 65 s ago");
    {
        let mut f = r.fake.lock();
        f.stream["outputTotalFrames"] = json!(2000);
        f.stream["outputSkippedFrames"] = json!(50);
    }
    assert!(until(6.0, || m.health().0 == "warn").await, "over 1% dropped: {:?}", m.health());
    assert!(m.health().1.contains("dropped (network)"));
    {
        let mut f = r.fake.lock();
        f.stream["outputTotalFrames"] = json!(10);
        f.stream["outputSkippedFrames"] = json!(0);
    }
    assert!(until(6.0, || m.health().0 == "ok").await, "a reset starts the minute again: {:?}", m.health());
    r.fake.lock().stream["outputReconnecting"] = json!(true);
    assert!(until(6.0, || m.health().1.contains("reconnecting")).await);
    r.fake.lock().stream["outputReconnecting"] = json!(false);

    // An unexpected stop is an error; an operator's stop is not.
    r.fake.lock().stream["outputActive"] = json!(false);
    r.fake.event("StreamStateChanged", json!({"outputActive": false, "outputState": "OBS_WEBSOCKET_OUTPUT_STOPPED"}));
    assert!(until(4.0, || m.health().0 == "error").await);
    assert!(m.health().1.contains("stopped by itself"));
    r.fake.lock().stream["outputActive"] = json!(true);
    r.fake.event("StreamStateChanged", json!({"outputActive": true, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"}));
    assert!(until(6.0, || m.health().0 != "error").await, "starting again clears it");
    r.fake.event("StreamStateChanged", json!({"outputActive": true, "outputState": "OBS_WEBSOCKET_OUTPUT_STOPPING"}));
    r.fake.lock().stream["outputActive"] = json!(false);
    r.fake.event("StreamStateChanged", json!({"outputActive": false, "outputState": "OBS_WEBSOCKET_OUTPUT_STOPPED"}));
    assert!(until(6.0, || m.health().0 == "idle").await, "{:?}", m.health());

    assert_eq!(m.screenshot().await.unwrap(), JPEG);
    let snap = m.snapshot();
    assert_eq!((snap["service"].clone(), snap["youtube"]["id"].clone()), (json!("YouTube - RTMPS"), json!("dQw4w9WgXcQ")));
    assert_eq!(snap["youtube"]["studio"], "https://studio.youtube.com/video/dQw4w9WgXcQ/livestreaming");
    let all = snap.to_string();
    assert!(!all.contains("SECRET-KEY") && !all.contains("rtmps://"), "the key and server never reach the snapshot");
    assert_eq!(snap["inputs"], json!(["Pastor mic", "Music"]), "audio inputs only");
    assert_eq!(r.fake.lock().subscriptions & METERS, 0, "no meters without watched audio");
    r.stop().await;
    assert!(r.board.streams.lock().unwrap().is_empty(), "stopping clears the stream status");
}

#[tokio::test]
async fn outputs_follow_start_and_stop_only_when_asked() {
    let r = rig("").await;
    r.configure(json!({}), "");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    r.host.announce(STREAM_STARTED, "");
    assert!(!until(0.5, || r.fake.lock().record["outputActive"] == json!(true)).await, "nothing starts while it is not asked");
    r.stop().await;

    r.configure(json!({"start_record": true}), "");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    r.fake.lock().stream["outputActive"] = json!(true);
    r.host.announce(STREAM_STARTED, "");
    assert!(until(6.0, || r.fake.lock().record["outputActive"] == json!(true)).await, "Start begins the recording");
    assert!(r.board.audit.lock().unwrap().iter().any(|a| a.0 == "obs.start"));
    r.host.announce(STREAM_STOPPED, "");
    assert!(until(6.0, || r.fake.lock().record["outputActive"] == json!(false)).await, "Stop ends the recording it began");
    assert_eq!(r.fake.lock().stream["outputActive"], json!(true), "and leaves a stream nobody asked it to touch");
    r.fake.lock().record["outputActive"] = json!(true);
    r.host.announce(STREAM_STOPPED, "");
    assert!(!until(0.5, || r.fake.lock().record["outputActive"] == json!(false)).await, "an output the operator started by hand is left running");
    r.stop().await;
}

#[tokio::test]
async fn captions_go_into_the_stream_in_short_lines() {
    let r = rig("").await;
    r.configure(json!({}), "");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    r.fake.lock().stream["outputActive"] = json!(true);
    r.fake.event("StreamStateChanged", json!({"outputActive": true, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"}));
    assert!(until(4.0, || m.streaming()).await);
    let line = "This line is for the people who watch at home today.";
    r.hub.publish_final("transcription", "ENGLISH", Some(line));
    assert!(!until(0.5, || !r.fake.lock().captions.is_empty()).await, "off by default: nothing sent");
    r.stop().await;

    r.configure(json!({"captions": true}), "");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    r.fake.lock().stream["outputActive"] = json!(true);
    r.fake.event("StreamStateChanged", json!({"outputActive": true, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"}));
    assert!(until(4.0, || m.streaming()).await);
    r.hub.publish_final("transcription", "ENGLISH", Some(line));
    assert!(until(4.0, || r.fake.lock().captions.len() >= 2).await, "sent in short lines");
    assert!(r.fake.lock().captions.iter().all(|c| c.chars().count() <= 32));
    let n = r.fake.lock().captions.len();
    r.hub.publish_final("translation", "SPANISH", Some("Hola"));
    assert!(!until(0.5, || r.fake.lock().captions.len() > n).await, "another language is not sent");
    r.stop().await;
    r.hub.publish_final("transcription", "ENGLISH", Some("After stop"));
    assert!(!until(0.3, || r.fake.lock().captions.iter().any(|c| c == "After stop")).await, "stop removes the sink");
}

#[tokio::test]
async fn audio_tally_studio_alerts_and_outputs() {
    let r = rig("").await;
    r.configure(json!({"audio": [{"name": "Pastor mic", "warn_muted": true}], "tally": [{"n": 1, "scene": "Walk-in"}, {"n": 2, "scene": "Worship"}], "alert_error": "bad", "alert_warn": "meh"}), "");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    assert_ne!(r.fake.lock().subscriptions & METERS, 0, "meters asked for when audio is watched");
    r.fake.event("InputVolumeMeters", json!({"inputs": [{"inputName": "Pastor mic", "inputLevelsMul": [[0.1, 0.5, 0.5], [0.1, 0.25, 0.25]]}, {"inputName": "Music", "inputLevelsMul": [[1.0, 1.0, 1.0]]}]}));
    assert!(until(4.0, || m.audio()[0]["db"] == json!(-6.0)).await, "the loudest channel, in dB: {:?}", m.audio());
    assert_eq!(m.audio().len(), 1, "only watched inputs");

    let cams = |r: &fake::Rig| -> Vec<(i64, bool, bool)> { r.board.tally.lock().unwrap().get("obs_m1").map(|c| c.iter().map(|x| (x["n"].as_i64().unwrap(), x["program"] == json!(true), x["preview"] == json!(true))).collect()).unwrap_or_default() };
    assert!(until(4.0, || cams(&r) == [(1, true, false), (2, false, false)]).await, "{:?}", cams(&r));
    m.set_studio(true).await.unwrap();
    m.set_preview("Worship").await.unwrap();
    assert!(until(4.0, || cams(&r)[1] == (2, false, true)).await, "preview shows on tally");
    m.take().await.unwrap();
    assert!(until(4.0, || cams(&r)[1] == (2, true, false)).await, "the tally follows a take");
    m.set_studio(false).await.unwrap();
    assert!(m.set_preview("Message").await.is_err(), "preview needs studio mode");

    m.output("stream", "start").await.unwrap();
    assert!(until(6.0, || m.health().0 == "ok").await, "{:?}", m.health());
    m.set_mute("Pastor mic", true).await.unwrap();
    assert!(until(4.0, || m.health().0 == "warn").await);
    assert!(m.health().1.contains("Pastor mic is muted while live"));
    assert_eq!(*r.board.presses.lock().unwrap(), [("meh".to_owned(), "OBS (Stream PC)".to_owned())], "the warn alert is raised once");
    m.publish();
    m.publish();
    assert_eq!(r.board.presses.lock().unwrap().len(), 1, "not again while it lasts");
    m.set_mute("Pastor mic", false).await.unwrap();
    assert!(until(6.0, || m.health().0 == "ok").await);

    m.output("stream", "stop").await.unwrap();
    assert!(until(6.0, || m.health().0 == "idle").await, "an operator's stop is not an error");
    assert!(r.board.presses.lock().unwrap().iter().all(|p| p.0 != "bad"));

    m.output("record", "start").await.unwrap();
    assert!(until(4.0, || m.recording()).await);
    r.fake.lock().can_pause = false;
    assert!(m.output("record", "pause").await.unwrap_err().0.contains("did not pause"), "a pause OBS ignores is reported");
    r.fake.lock().can_pause = true;
    m.output("record", "pause").await.unwrap();
    m.output("record", "stop").await.unwrap();
    assert!(m.output("record", "explode").await.is_err());

    m.output("replay", "start").await.unwrap();
    m.output("replay", "save").await.unwrap();
    assert!(until(4.0, || m.snapshot()["replay"]["last"] == json!("Replay 1.mkv")).await, "the saved file's name only");

    // Connection lost: an error, and one alert for it.
    r.fake.drop_clients();
    assert!(until(6.0, || m.health().0 == "error").await || m.health().0 == "error");
    assert!(until(6.0, || r.board.presses.lock().unwrap().iter().any(|p| p.0 == "bad")).await, "raised once after a lost connection");
    r.stop().await;
    assert!(r.board.tally.lock().unwrap().is_empty(), "stopping clears the tally");
}

#[tokio::test]
async fn a_slide_tag_switches_scenes_and_the_module_withdraws_it() {
    let r = rig("").await;
    r.configure(json!({}), "");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    let tag = r.host.using::<TagAction>(&format!("{TAG_ACTION}obs")).expect("offered while on");
    assert_eq!((tag.0)(Some("  message ".into())).await, Ok("OBS switched to message.".to_owned()));
    assert_eq!(r.fake.lock().program, "Message", "any case, the computer that has it");
    let err = (tag.0)(Some("Nope".into())).await.unwrap_err();
    assert!(err.contains("Nope"), "{err}");
    assert!((tag.0)(None).await.unwrap_err().contains("Name the scene"));
    assert!(r.board.audit.lock().unwrap().iter().any(|a| a.1.contains("(from a ProPresenter slide tag)")));
    assert_eq!(r.module_health(), HealthState::Idle);
    r.stop().await;
    assert!(r.host.using::<TagAction>(&format!("{TAG_ACTION}obs")).is_none(), "off: withdrawn");
}

#[tokio::test]
async fn the_settings_route_keeps_passwords_secret_and_the_operator_routes_refuse_what_is_wrong() {
    let r = rig("").await;
    r.start().await;
    let (st, body, text) = r.send(Method::PUT, &format!("{A}/settings"), Some(json!({"machines": [{"name": "Stream PC", "password": "pw1"}]}))).await;
    assert_eq!(st, StatusCode::OK, "{text}");
    let mid = body["machines"][0]["id"].as_str().unwrap().to_owned();
    assert!(!text.contains("pw1") && body["machines"][0]["has_password"] == json!(true));
    let pw = |r: &fake::Rig| passwords(&clean_settings(&r.store_obs())["passwords"]);
    assert_eq!(serde_json::Value::Object(pw(&r)), json!({mid.clone(): "pw1"}));
    r.send(Method::PUT, &format!("{A}/settings"), Some(json!({"machines": [{"id": mid, "name": "Stream PC", "password": ""}]}))).await;
    assert_eq!(serde_json::Value::Object(pw(&r)), json!({mid.clone(): "pw1"}), "a blank password keeps it");
    r.send(Method::PUT, &format!("{A}/settings"), Some(json!({"machines": [{"id": mid, "name": "Stream PC", "clear_password": true}]}))).await;
    assert!(pw(&r).is_empty(), "forget clears it");
    assert_eq!(r.send(Method::PUT, &format!("{A}/settings"), Some(json!({"machines": [{"host": "not a host!"}]}))).await.0, StatusCode::BAD_REQUEST);
    assert_eq!(r.send(Method::PUT, &format!("{A}/settings"), Some(json!({"machines": [{}, {}, {}, {}, {}]}))).await.0, StatusCode::BAD_REQUEST);
    assert!(r.board.notes.lock().unwrap().iter().any(|n| n.0 == "obs.settings"));

    r.board.refuse.lock().unwrap().push("obs.control");
    let body = json!({"machine": mid, "output": "stream", "action": "stop"});
    assert_eq!(r.send(Method::POST, &format!("{A}/output"), Some(body.clone())).await.0, StatusCode::FORBIDDEN);
    r.board.refuse.lock().unwrap().clear();
    assert_eq!(r.send(Method::POST, &format!("{A}/output"), Some(json!({"machine": "zz", "output": "stream", "action": "stop"}))).await.0, StatusCode::NOT_FOUND);
    let (st, b, _) = r.send(Method::POST, &format!("{A}/output"), Some(body)).await;
    assert!(st == StatusCode::BAD_GATEWAY && b["error"].as_str().unwrap().contains("not connected"));
    assert_eq!(r.send(Method::POST, &format!("{A}/studio"), Some(json!({"machine": mid}))).await.0, StatusCode::BAD_REQUEST);
    assert_eq!(r.send(Method::POST, &format!("{A}/mute"), Some(json!({"machine": mid, "input": "Mic"}))).await.0, StatusCode::BAD_REQUEST);
    assert_eq!(r.send(Method::GET, &format!("{A}/audio/{mid}"), None).await.1, json!({"audio": []}));
    assert_eq!(r.send(Method::GET, &format!("{A}/preview/{mid}"), None).await.0, StatusCode::NOT_FOUND, "the preview is off by default");
    let (_, state, _) = r.send(Method::GET, A, None).await;
    assert_eq!(state["alert_buttons"], json!([{"id": "bad", "name": "Bad"}, {"id": "meh", "name": "Meh"}]));
    r.stop().await;
}

#[tokio::test]
async fn the_scene_screen_needs_its_key_and_only_switches_scenes() {
    let r = rig("").await;
    r.configure(json!({}), "");
    r.start().await;
    let m = r.machine();
    assert!(until(6.0, || m.is_connected()).await);
    let off = format!("/obs/screen?obs=m1&key={}", "0".repeat(32));
    assert_eq!(r.send(Method::GET, &off, None).await.0, StatusCode::NOT_FOUND, "off: the same plain 404");
    let (_, on, _) = r.send(Method::PUT, &format!("{A}/screen/m1"), Some(json!({"on": true}))).await;
    let link = on["link"].as_str().unwrap().to_owned();
    let key = link.split("key=").nth(1).unwrap().to_owned();
    assert!(on["on"] == json!(true) && key.len() == 32 && link.starts_with("https://relay.local:8443/obs/screen?obs=m1"), "{on}");
    let (_, state, text) = r.send(Method::GET, A, None).await;
    assert!(!text.contains(&key) && state["machines"][0]["has_key"] == json!(true), "the key never reaches a read");
    let (st, _, page) = r.send(Method::GET, &format!("/obs/screen?obs=m1&key={key}"), None).await;
    assert!(st == StatusCode::OK && page.contains("Stream PC: scenes"));
    assert_eq!(r.send(Method::GET, &format!("/obs/screen?obs=m1&key={}", "1".repeat(32)), None).await.0, StatusCode::NOT_FOUND);
    let (_, s, _) = r.send(Method::GET, &format!("/obs/_/state/m1?key={key}"), None).await;
    assert_eq!((s["scenes"].clone(), s["program"].clone(), s["connected"].clone()), (json!(["Walk-in", "Worship", "Message"]), json!("Walk-in"), json!(true)));
    let (st, _, _) = r.send(Method::POST, &format!("/obs/_/scene/m1?key={key}"), Some(json!({"scene": "Message"}))).await;
    assert_eq!((st, r.fake.lock().program.clone()), (StatusCode::OK, "Message".to_owned()));
    assert!(r.board.audit.lock().unwrap().iter().any(|a| a.1.contains("from the scene screen")));
    assert_eq!(r.send(Method::POST, &format!("/obs/_/scene/m1?key={key}"), Some(json!({"scene": "Zzz"}))).await.0, StatusCode::NOT_FOUND, "a scene OBS lacks is refused");
    assert_eq!(r.send(Method::POST, &format!("/obs/_/scene/m1?key={}", "1".repeat(32)), Some(json!({"scene": "Walk-in"}))).await.0, StatusCode::NOT_FOUND, "a wrong key cannot switch");
    r.send(Method::PUT, &format!("{A}/screen/m1"), Some(json!({"on": true, "rotate": true}))).await;
    assert_eq!(r.send(Method::GET, &format!("/obs/_/state/m1?key={key}"), None).await.0, StatusCode::NOT_FOUND, "a new link stops the old one");
    // Eight wrong keys lock the client out, even for the right key.
    let g = &r.module.ctx().unwrap().guard;
    for _ in 0..g.max_bad_keys {
        r.send(Method::GET, &format!("/obs/_/state/m1?key={}", "2".repeat(32)), None).await;
    }
    let fresh = r.send(Method::GET, A, None).await.1["machines"][0]["has_key"].clone();
    assert_eq!(fresh, json!(true));
    let now_key = r.store_key();
    assert_eq!(r.send(Method::GET, &format!("/obs/_/state/m1?key={now_key}"), None).await.0, StatusCode::TOO_MANY_REQUESTS);
    let _ = Arc::strong_count(g);
    r.stop().await;
}
