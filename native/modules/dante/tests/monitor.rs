//! Dante monitoring (`tests/test_dante_monitor.py` ported): the request layouts, a fake Dante device over real UDP sockets on loopback, problems that raise
//! once and clear, the history that survives a restart, and the stage alert button named Dante.

mod fake;

use std::collections::BTreeSet;
use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::http::StatusCode;
use fake::{record, rig, FakeDevice, Rig};
use relay_module_dante::arc;
use relay_module_dante::heartbeat;
use relay_module_dante::monitor::{stage_alert, Monitor};
use serde_json::{json, Value};

/// A monitor that keeps its history in the rig's database and flashes the rig's stage button.
fn monitor(r: &Rig) -> Monitor {
    let m = Monitor::new();
    m.use_database(Some(r.pool.clone()));
    let host = r.host.clone();
    m.set_stage(Some(Arc::new(move |_why: &str, on: bool| stage_alert(&host, on))));
    m
}

fn keys(m: &Monitor) -> Vec<String> {
    m.current().iter().map(|p| p["key"].as_str().unwrap().to_owned()).collect()
}

async fn kinds(m: &Monitor) -> Vec<String> {
    m.history(100).await.iter().map(|e| e["kind"].as_str().unwrap().to_owned()).collect()
}

#[test]
fn the_layouts_read_what_a_device_sends_and_refuse_the_rest() {
    let mut clock = vec![3u8, 0, 0, 2];
    clock.extend_from_slice(&500u32.to_be_bytes());
    clock.extend_from_slice(&[0, 1]);
    assert_eq!(arc::parse_clock(Some(&clock)), Some(json!({"leader": true, "locked": true, "follower_errors": 2, "latency_us": 500, "interface_errors": 1})), "clock");
    assert_eq!(arc::parse_clock(Some(&[0, 1])), None, "a short clock answer is no reading");
    let mut runs_off = vec![16u8, 3];
    runs_off.extend_from_slice(&[0; 20]);
    assert_eq!(arc::parse_rx_page(Some(&runs_off)), None, "a receive page that runs off the end is no reading");
    let (page, _) = arc::rx_page(&[(1, "Lead Vox@STAGE".into(), 10), (2, String::new(), 0), (3, "Gone@NOPE".into(), 4)], false);
    assert_eq!(
        arc::parse_rx_page(Some(&page)),
        Some(vec![
            json!({"channel": 1, "source": "Lead Vox@STAGE", "status": "ok"}),
            json!({"channel": 2, "source": "", "status": "none"}),
            json!({"channel": 3, "source": "Gone@NOPE", "status": "unresolved"}),
        ])
    );
    assert_eq!(arc::body(&arc::reply(arc::CMD_CLOCK, 5, &[], 1, arc::PROTOCOL), arc::CMD_SUBSCRIPTIONS, 5), None, "an answer to another request is ignored");
}

#[tokio::test]
async fn a_fake_device_is_read_over_real_sockets() {
    let r = rig().await;
    let boxed = FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await;
    let d = record(&r, "STAGE", &boxed, &["Lead Vox", "Pastor", "Spare"], true).await;
    assert_eq!((d["clock"]["leader"].clone(), d["clock"]["locked"].clone()), (json!(true), json!(true)), "clock");
    assert_eq!(d["subscriptions"], json!([{"channel": 1, "source": "Lead Vox@STAGE", "status": "ok"}]), "subscriptions");
    boxed.lock().up = false;
    let got = arc::ask(r.host.net(), "127.0.0.1", boxed.port, arc::CMD_CLOCK, 9, Duration::from_millis(300)).await.unwrap();
    assert!(got.is_none(), "a silent device leaves no reading");
}

#[tokio::test]
async fn one_alert_when_a_subscription_breaks_one_clear_on_recovery_with_the_stage_button() {
    let r = rig().await;
    let boxed = FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await;
    let m = monitor(&r);
    m.observe(&[record(&r, "STAGE", &boxed, &["Lead Vox", "Pastor", "Spare"], true).await]).await;
    assert!(m.current().is_empty(), "nothing wrong");
    boxed.subscribe_as(1, "Lead Vox@STAGE", 4);
    m.observe(&[record(&r, "STAGE", &boxed, &["Lead Vox", "Pastor", "Spare"], true).await]).await;
    assert_eq!(keys(&m), ["unresolved:stage:1"], "one problem");
    assert_eq!(m.current()[0]["text"], json!("STAGE receive channel 1 cannot find Lead Vox@STAGE"));
    assert!(r.board.on.lock().unwrap().contains(&"dante".to_owned()), "the stage alert is on");
    m.observe(&[record(&r, "STAGE", &boxed, &["Lead Vox", "Pastor", "Spare"], true).await]).await;
    assert_eq!(m.current().len(), 1, "still one problem, no second alert");
    assert_eq!(r.board.presses.lock().unwrap().len(), 1, "the button was pressed once");
    assert_eq!(kinds(&m).await.iter().filter(|k| *k == "problem").count(), 1, "one problem line in the history");
    boxed.subscribe_as(1, "Lead Vox@STAGE", 10);
    m.observe(&[record(&r, "STAGE", &boxed, &["Lead Vox", "Pastor", "Spare"], true).await]).await;
    assert!(m.current().is_empty(), "cleared");
    assert!(!r.board.on.lock().unwrap().contains(&"dante".to_owned()), "the stage alert is off");
    assert_eq!(m.history(1).await[0]["kind"], json!("recovered"), "history has the recovery");
}

#[tokio::test]
async fn no_button_named_dante_means_only_the_status_card() {
    let r = rig().await;
    r.board.buttons.lock().unwrap().clear();
    let boxed = FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await;
    let m = monitor(&r);
    boxed.subscribe_as(1, "Lead Vox@STAGE", 4);
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    assert_eq!(m.current().len(), 1, "the problem is still shown");
    assert!(r.board.presses.lock().unwrap().is_empty(), "and nothing flashes");
}

#[tokio::test]
async fn unplugging_a_device_raises_one_alert_after_two_missed_scans() {
    let r = rig().await;
    let boxed = FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await;
    let m = monitor(&r);
    let here = record(&r, "STAGE", &boxed, &["Lead Vox"], true).await;
    m.observe(std::slice::from_ref(&here)).await;
    m.observe(&[]).await;
    assert!(m.current().is_empty(), "one missed scan is not an outage");
    m.observe(&[]).await;
    assert_eq!(keys(&m), ["offline:stage"], "two are");
    m.observe(&[]).await;
    assert_eq!(m.current().len(), 1, "still one");
    m.observe(std::slice::from_ref(&here)).await;
    assert!(m.current().is_empty(), "back on the network clears it");
    let got: BTreeSet<String> = kinds(&m).await.into_iter().collect();
    assert!(["vanished", "appeared", "problem", "recovered"].iter().all(|k| got.contains(*k)), "vanished, appeared and the pair of problem lines are kept: {got:?}");
}

#[tokio::test]
async fn a_device_that_stops_answering_its_control_port() {
    let r = rig().await;
    let boxed = FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await;
    let m = monitor(&r);
    m.observe(&[record(&r, "STAGE", &boxed, &[], false).await]).await;
    assert_eq!(keys(&m), ["offline:stage"], "offline");
    assert_eq!(m.current()[0]["text"], json!("STAGE does not answer on its control port"));
}

#[tokio::test]
async fn clock_unlocked_errors_climbing_and_the_leader_changing() {
    let r = rig().await;
    let boxed = FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await;
    let m = monitor(&r);
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    boxed.lock().errors = 3;
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    assert_eq!(keys(&m), ["clock:stage"], "errors that grew");
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    assert!(m.current().is_empty(), "errors that stopped growing clear");
    boxed.lock().locked = false;
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    assert_eq!(keys(&m), ["clock:stage"], "unlocked");
    boxed.lock().locked = true;
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    boxed.lock().leader = false;
    let other = FakeDevice::start(&[]).await;
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await, record(&r, "BOOTH", &other, &[], true).await]).await;
    let moved: Vec<String> = m.history(100).await.iter().filter(|e| e["kind"] == json!("clock_leader")).map(|e| e["detail"].as_str().unwrap().to_owned()).collect();
    assert_eq!(moved, ["clock leader changed from STAGE"], "the leader moved");
}

#[tokio::test]
async fn subscription_changes_name_who_made_them() {
    let r = rig().await;
    let boxed = FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await;
    let m = monitor(&r);
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    boxed.subscribe(1, "Pastor@STAGE");
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    let first = m.history(100).await.into_iter().find(|e| e["kind"] == json!("subscription")).unwrap();
    assert!(first["detail"].as_str().unwrap().contains("Dante Controller"), "by someone else: {first}");
    m.relay_wrote("stage");
    boxed.subscribe(1, "Spare@STAGE");
    m.observe(&[record(&r, "STAGE", &boxed, &[], true).await]).await;
    let latest = m.history(1).await;
    assert!(latest[0]["detail"].as_str().unwrap().ends_with("by Relay"), "by Relay when routing says so: {latest:?}");
    assert_eq!(latest[0]["detail"], json!("receive channel 1: Spare@STAGE (was Pastor@STAGE), by Relay"));
}

#[tokio::test]
async fn history_survives_a_restart_and_so_do_stars() {
    let r = rig().await;
    let m = monitor(&r);
    m.event("STAGE", "vanished", "test").await;
    m.star("STAGE", "Lead Vox", true).await.unwrap();
    let again = monitor(&r);
    assert_eq!(again.history(100).await[0]["kind"], json!("vanished"), "a new Monitor reads the same history");
    assert_eq!(again.starred().await, BTreeSet::from([("STAGE".to_owned(), "Lead Vox".to_owned())]), "and the stars");
    again.star("STAGE", "Lead Vox", false).await.unwrap();
    assert!(again.starred().await.is_empty(), "unstarred");
    let at = again.history(100).await[0]["at"].as_str().unwrap().to_owned();
    assert!(at.len() == 20 && at.ends_with('Z') && at.as_bytes()[10] == b'T', "times are written as Python wrote them: {at}");
}

#[tokio::test]
async fn history_is_kept_in_memory_while_the_database_is_down_and_pruned_at_a_thousand() {
    let m = Monitor::new();
    m.event("STAGE", "vanished", "no database").await;
    assert_eq!(m.history(10).await[0]["detail"], json!("no database"), "with no database the history is in memory");
    assert!(m.starred().await.is_empty());
    assert!(m.star("STAGE", "x", true).await.is_err(), "a star needs the database");
    let r = rig().await;
    let m = monitor(&r);
    for i in 0..1005 {
        m.event("STAGE", "routing", &format!("n{i}")).await;
    }
    let all = m.history(5000).await;
    assert_eq!((all.len(), all[0]["detail"].clone()), (1000, json!("n1004")), "the newest thousand are kept");
}

#[tokio::test]
async fn levels_for_starred_channels() {
    let r = rig().await;
    let s = r.service();
    let mut d = relay_module_dante::service::device("STAGE", &["127.0.0.1"], None, &["Lead Vox", "Pastor", "Spare"]);
    d["reachable"] = json!(true);
    s.record(vec![d]);
    s.monitor.star("STAGE", "Pastor", true).await.unwrap();
    let want = |keys: &[usize]| std::collections::BTreeMap::from([("stage".to_owned(), keys.iter().copied().collect::<BTreeSet<usize>>())]);
    assert_eq!(s.meter_targets().await, want(&[1]), "only the starred channel of another computer's device is watched");
    s.read_levels().await;
    assert!(s.snapshot()["levels"].as_array().unwrap().is_empty(), "no heartbeat is no reading");
    let beat = heartbeat::packet(&[0x00, 0x1d, 0xc1, 0xff, 0xfe, 0x19, 0x86, 0x2a], Some(&[0, 40, 255]), Some(&[9]), Some(-120), 0, 0);
    assert!(s.heartbeats.feed(&beat, "127.0.0.1", Instant::now()), "a heartbeat is kept");
    s.read_levels().await;
    let levels = s.levels_of("stage").unwrap();
    let got: Vec<(String, i64)> = levels["channels"].as_array().unwrap().iter().map(|c| (c["channel"].as_str().unwrap().to_owned(), c["level"].as_i64().unwrap())).collect();
    assert_eq!(got, [("Pastor".to_owned(), -20)], "levels by channel name");
    // A device on this computer is watched whole.
    r.board.local.lock().unwrap()[0].addresses = vec!["127.0.0.1".into()];
    assert_eq!(s.meter_targets().await, want(&[0, 1, 2]), "a device on this computer is watched whole");
    s.heartbeats.stop();
    s.read_levels().await;
    assert!(s.snapshot()["levels"].as_array().unwrap().is_empty(), "a heartbeat that has gone is no reading again");
}

#[tokio::test]
async fn a_level_count_that_disagrees_with_the_channel_names_gets_numbered_channels() {
    let r = rig().await;
    let s = r.service();
    let mut d = relay_module_dante::service::device("STAGE", &["127.0.0.1"], None, &["A", "B"]);
    d["reachable"] = json!(true);
    s.record(vec![d]);
    s.monitor.star("STAGE", "B", true).await.unwrap();
    s.heartbeats.feed(&heartbeat::packet(&[1; 8], Some(&[2, 4, 6]), None, None, 0, 0), "127.0.0.1", Instant::now());
    s.read_levels().await;
    let channels = s.levels_of("stage").unwrap()["channels"].clone();
    assert_eq!(channels, json!([{"channel": "Channel 2", "level": -2}]), "the order cannot be trusted, so no name is guessed");
}

#[tokio::test]
async fn the_star_and_history_routes() {
    let r = rig().await;
    let mut d = relay_module_dante::service::device("STAGE", &["10.9.0.2"], None, &["Lead Vox"]);
    d["reachable"] = json!(true);
    r.service().record(vec![d]);
    let star = json!({"device": "STAGE", "channel": "Lead Vox", "on": true});
    r.role("viewer");
    assert_eq!(r.post("/api/admin/dante/star", star.clone()).await.0, StatusCode::FORBIDDEN, "a viewer cannot star");
    r.role("admin");
    let mut unknown = star.clone();
    unknown["channel"] = json!("No");
    assert_eq!(r.post("/api/admin/dante/star", unknown).await.0, StatusCode::NOT_FOUND, "an unknown channel is refused");
    let (status, body) = r.post("/api/admin/dante/star", json!({"device": "STAGE"})).await;
    assert_eq!((status, body["detail"].clone()), (StatusCode::BAD_REQUEST, json!("Send the device, the channel and on true or false.")), "a malformed body is refused");
    assert_eq!(r.post("/api/admin/dante/star", star).await.1["starred"], json!([["STAGE", "Lead Vox"]]), "star");
    assert!(r.board.notes.lock().unwrap().iter().any(|n| n.0 == "dante.star" && n.1 == "Starred Lead Vox on STAGE for live levels"));
    r.role("viewer");
    assert_eq!(r.get("/api/admin/dante/history").await.0, StatusCode::OK, "a viewer reads history");
    let state: Value = r.get("/api/admin/dante").await.1;
    for k in ["problems", "levels", "leader"] {
        assert!(state.get(k).is_some(), "state carries {k}");
    }
    r.service().monitor.event("STAGE", "vanished", "x").await;
    let (_, history) = r.get("/api/admin/dante/history?limit=1").await;
    assert_eq!(history["history"].as_array().unwrap().len(), 1, "the limit is read");
    assert_eq!(r.get("/api/admin/dante/history?limit=abc").await.0, StatusCode::UNPROCESSABLE_ENTITY);
}
