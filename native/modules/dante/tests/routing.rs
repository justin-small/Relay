//! Dante routing and presets (`tests/test_dante_routing.py` ported): the write packets byte for byte as Dante Controller sent them, fake devices over real
//! UDP sockets that read and write subscriptions, confirmation, who made a change, undo, locks, refusals, and presets saved, compared, applied and undone.

mod fake;

use std::sync::Arc;

use axum::http::StatusCode;
use fake::{record, rig, FakeDevice, Rig};
use relay_module_dante::arc::{self, Wrote};
use serde_json::{json, Value};

const SUB: &str = "/api/admin/dante/routing/subscribe";
const UNDO: &str = "/api/admin/dante/routing/undo";

fn hex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

struct Net {
    r: Rig,
    stage: Arc<FakeDevice>,
    booth: Arc<FakeDevice>,
    pa: Arc<FakeDevice>,
}

impl Net {
    async fn new() -> Self {
        let r = rig().await;
        let net = Self { r, stage: FakeDevice::start(&[(1, ""), (2, "")]).await, booth: FakeDevice::start(&[(1, "Lead Vox@STAGE"), (2, "Pastor@STAGE")]).await, pa: FakeDevice::start(&[(1, "Lead Vox@STAGE")]).await };
        net.scan().await;
        net
    }

    async fn scan(&self) {
        let devices = vec![record(&self.r, "STAGE", &self.stage, &["Lead Vox", "Pastor"], true).await, record(&self.r, "BOOTH", &self.booth, &["Mix"], true).await, record(&self.r, "PA", &self.pa, &[], true).await];
        self.r.service().record(devices);
    }

    async fn subscribe(&self, body: Value) -> (StatusCode, Value) {
        self.r.post(SUB, body).await
    }
}

#[tokio::test]
async fn the_write_packet_is_built_and_refusals_are_read() {
    // Packets Dante Controller sent to a real DT168 stage box on 2026-11-20 (sequence 0x39 and 0x3f), byte for byte.
    let mut real_sub = hex("272900c100393010000008010001");
    real_sub.extend(hex("00ac00af"));
    real_sub.extend(vec![0u8; 172 - 18]);
    real_sub.extend(b"16\0Stagebox-2-Bottom\0");
    let mut real_clear = hex("272900ac003f3010000008010001");
    real_clear.extend(hex("00000000"));
    real_clear.extend(vec![0u8; 172 - 18]);
    assert_eq!(arc::subscribe_request(1, "16@Stagebox-2-Bottom", 0x39), Some(real_sub), "a subscribe is what Dante Controller sent");
    assert_eq!(arc::subscribe_request(1, "", 0x3F), Some(real_clear), "a clear is what Dante Controller sent (0x3010 with no pointers, not 0x3014)");
    assert_eq!((arc::subscribe_request(300, "A@B", 1), arc::subscribe_request(1, "@B", 1)), (None, None), "a write the device cannot take is not built");
    assert!(arc::REPLY_PROTOCOLS.contains(&0x2729) && arc::REPLY_PROTOCOLS.contains(&0x2809), "answers under the protocols Dante Controller uses are accepted");

    let n = Net::new().await;
    let net = n.r.host.net();
    let w = std::time::Duration::from_millis(500);
    assert_eq!(arc::set_subscription(net, ("127.0.0.1", n.stage.port), 1, "Pastor@STAGE", 5, w, false).await.unwrap(), Wrote::Ok, "a fake device accepts");
    assert_eq!(n.stage.source(1), "Pastor@STAGE", "and shows it");
    assert_eq!(arc::set_subscription(net, ("127.0.0.1", n.stage.port), 1, "", 5, w, false).await.unwrap(), Wrote::Ok, "a clear is accepted");
    assert_eq!(n.stage.source(1), "", "and empties it");
    n.stage.lock().refuse = true;
    let got = arc::set_subscription(net, ("127.0.0.1", n.stage.port), 1, "Pastor@STAGE", 5, w, false).await.unwrap();
    assert!(matches!(&got, Wrote::Refused(why) if why.contains("Domain Manager")), "a refusal names Dante Domain Manager: {got:?}");
    n.stage.lock().refuse = false;
    n.stage.lock().up = false;
    assert!(matches!(arc::set_subscription(net, ("127.0.0.1", n.stage.port), 1, "Pastor@STAGE", 5, std::time::Duration::from_millis(100), false).await.unwrap(), Wrote::Silent(_)), "a device that says nothing is silent");
}

#[tokio::test]
async fn a_device_on_arc_2_8_or_newer_is_written_with_the_0x3410_page() {
    // Packets sent to a real ARC 2.8.12 device (Dante Virtual Soundcard) on 2026-11-21, both answered with result 1 and read back with 0x3000: receive
    // channel 2 to 01@StageBox-1-Top, then cleared.
    let mut real_set = hex(concat!("2809002e000a3410", "0000", "000000000000", "0800", "0101", "00020003", "001c001f"));
    real_set.extend(b"01\0StageBox-1-Top\0");
    let real_clear = hex(concat!("2809001c000b3410", "0000", "000000000000", "0800", "0101", "00020003", "00000000"));
    assert_eq!(arc::subscribe_page_request(2, "01@StageBox-1-Top", 0x0A), Some(real_set), "a subscribe page is what the real device accepted");
    assert_eq!(arc::subscribe_page_request(2, "", 0x0B), Some(real_clear), "a clear page is what the real device accepted");
    assert_eq!((arc::subscribe_page_request(0, "A@B", 1), arc::subscribe_page_request(1, "@B", 1)), (None, None), "a page the device cannot take is not built");
    for v in ["2.8.15", "2.8.9", "2.10.0", "3.0"] {
        assert!(arc::is_modern(v), "{v} is modern");
    }
    for v in ["2.7.41", "", "x.y", "2"] {
        assert!(!arc::is_modern(v), "{v:?} is not");
    }

    let n = Net::new().await;
    let modern = FakeDevice::start(&[(1, "Lead Vox@STAGE"), (2, "")]).await;
    let net = n.r.host.net();
    let w = std::time::Duration::from_millis(500);
    assert_eq!(arc::set_subscription(net, ("127.0.0.1", modern.port), 2, "Pastor@STAGE", 5, w, true).await.unwrap(), Wrote::Ok, "a modern write is accepted");
    assert_eq!((modern.source(2), modern.lock().pages), ("Pastor@STAGE".to_owned(), 1), "and shows on the device");
    assert_eq!(arc::set_subscription(net, ("127.0.0.1", modern.port), 2, "", 5, w, true).await.unwrap(), Wrote::Ok, "a modern clear is accepted");
    assert_eq!(modern.source(2), "", "and empties it");
    modern.lock().refuse = true;
    let got = arc::set_subscription(net, ("127.0.0.1", modern.port), 2, "Pastor@STAGE", 5, w, true).await.unwrap();
    assert!(matches!(&got, Wrote::Refused(why) if why.contains("Domain Manager")), "a modern refusal names Dante Domain Manager");
    modern.lock().refuse = false;

    let mut via = record(&n.r, "VIA", &modern, &[], true).await;
    via["arc_version"] = json!("2.8.15");
    n.r.service().record(vec![record(&n.r, "STAGE", &n.stage, &["Lead Vox", "Pastor"], true).await, via.clone()]);
    n.r.role("operator");
    let sent = modern.lock().pages;
    let (status, body) = n.subscribe(json!({"device": "VIA", "channel": 2, "source": "Pastor@STAGE", "confirm": true})).await;
    assert_eq!((status, body["result"]["result"].clone()), (StatusCode::OK, json!("ok")), "Relay subscribes a 2.8 device and reads it back");
    assert_eq!((modern.source(2), modern.lock().pages - sent), ("Pastor@STAGE".to_owned(), 1), "with a page, not the old packet");
    assert_eq!(n.r.post(UNDO, json!({"confirm": true})).await.0, StatusCode::OK, "and undo clears it the same way");
    assert_eq!(modern.source(2), "", "so the channel is empty again");
    via["arc_version"] = json!("2.7.41");
    n.r.service().record(vec![record(&n.r, "STAGE", &n.stage, &["Lead Vox", "Pastor"], true).await, via]);
    let before = modern.lock().pages;
    n.subscribe(json!({"device": "VIA", "channel": 2, "source": "Pastor@STAGE", "confirm": true})).await;
    assert_eq!(modern.lock().pages, before, "a 2.7 device is not sent a page");
}

#[tokio::test]
async fn permissions_a_viewer_reads_only_an_operator_changes() {
    let n = Net::new().await;
    n.r.role("viewer");
    assert_eq!(n.r.get("/api/admin/dante/routing").await.0, StatusCode::OK, "a viewer reads the matrix");
    assert_eq!(n.subscribe(json!({"device": "PA", "channel": 1, "source": "", "confirm": true})).await.0, StatusCode::FORBIDDEN, "a viewer's write is refused (403)");
    assert_eq!(n.pa.source(1), "Lead Vox@STAGE", "nothing changed");
    n.r.role("operator");
    assert_eq!(n.r.post("/api/admin/dante/routing/lock", json!({"device": "PA", "channel": 1, "on": true})).await.0, StatusCode::FORBIDDEN, "an operator cannot lock");
    assert_eq!(n.r.post("/api/admin/dante/presets", json!({"name": "x"})).await.0, StatusCode::FORBIDDEN, "an operator cannot save a preset");
}

#[tokio::test]
async fn subscribe_asks_for_confirmation_writes_reads_back_and_names_who() {
    let n = Net::new().await;
    n.r.role("operator");
    let body = json!({"device": "BOOTH", "channel": 1, "source": "Pastor@STAGE"});
    let (status, refused) = n.subscribe(body.clone()).await;
    assert_eq!((status, refused["detail"].clone()), (StatusCode::BAD_REQUEST, json!("Relay changes routing only after the operator confirms: send confirm true.")), "no confirm, no write");
    assert_eq!(n.booth.source(1), "Lead Vox@STAGE", "untouched");
    assert_eq!(n.subscribe(json!({"device": "BOOTH", "channel": 1})).await.0, StatusCode::BAD_REQUEST, "a body with no source is malformed");
    let mut unknown = body.clone();
    unknown["source"] = json!("Nope@STAGE");
    unknown["confirm"] = json!(true);
    assert_eq!(n.subscribe(unknown).await.0, StatusCode::CONFLICT, "a source Relay never saw is refused");
    let mut confirmed = body.clone();
    confirmed["confirm"] = json!(true);
    let (status, r) = n.subscribe(confirmed).await;
    assert_eq!(status, StatusCode::OK, "confirmed");
    assert_eq!((r["result"]["result"].clone(), r["result"]["from"].clone()), (json!("ok"), json!("Lead Vox@STAGE")), "the result names what it replaced");
    assert_eq!(n.booth.source(1), "Pastor@STAGE", "the device has it");
    let history = n.r.service().monitor.history(100).await;
    assert!(history.iter().any(|e| e["kind"] == json!("routing") && e["detail"].as_str().unwrap().starts_with("by op1:")), "history names the operator: {history:?}");
    assert_eq!(n.r.get("/api/admin/dante/routing").await.1["undo"]["count"], json!(1), "undo is offered");
    let notes = n.r.board.notes.lock().unwrap().clone();
    assert!(notes.iter().any(|x| x.0 == "dante.subscribe" && x.1 == "BOOTH channel 1: Pastor@STAGE (ok)"), "and it is in the audit log: {notes:?}");
}

#[tokio::test]
async fn undo_restores_the_previous_subscription() {
    let n = Net::new().await;
    n.r.role("operator");
    n.subscribe(json!({"device": "BOOTH", "channel": 1, "source": "Pastor@STAGE", "confirm": true})).await;
    assert_eq!(n.r.post(UNDO, json!({})).await.0, StatusCode::BAD_REQUEST, "undo needs confirm");
    let (status, r) = n.r.post(UNDO, json!({"confirm": true})).await;
    assert_eq!((status, n.booth.source(1)), (StatusCode::OK, "Lead Vox@STAGE".to_owned()), "undone");
    assert_eq!(r["undo"], Value::Null, "nothing left to undo");
    assert_eq!(n.r.post(UNDO, json!({"confirm": true})).await.0, StatusCode::CONFLICT, "a second undo is refused");
}

#[tokio::test]
async fn a_locked_channel_is_never_changed() {
    let n = Net::new().await;
    n.r.role("admin");
    assert_eq!(n.r.post("/api/admin/dante/routing/lock", json!({"device": "PA", "channel": 1, "on": true})).await.0, StatusCode::OK, "admin locks PA channel 1");
    assert_eq!(n.r.post("/api/admin/dante/routing/lock", json!({"device": "Nobody", "channel": 1, "on": true})).await.0, StatusCode::NOT_FOUND, "a device Relay never saw cannot be locked");
    n.r.role("operator");
    let (status, _) = n.subscribe(json!({"device": "PA", "channel": 1, "source": "", "confirm": true})).await;
    assert_eq!((status, n.pa.source(1)), (StatusCode::CONFLICT, "Lead Vox@STAGE".to_owned()), "refused");
    assert_eq!(n.r.get("/api/admin/dante/routing").await.1["locks"], json!([["PA", 1]]), "the lock shows in the matrix");
    n.r.role("admin");
    n.r.post("/api/admin/dante/routing/lock", json!({"device": "PA", "channel": 1, "on": false})).await;
    assert_eq!(n.r.get("/api/admin/dante/routing").await.1["locks"], json!([]), "and unlocking takes it off");
}

#[tokio::test]
async fn a_device_that_refuses_or_says_nothing_is_reported_plainly() {
    let n = Net::new().await;
    n.r.role("operator");
    n.booth.lock().refuse = true;
    let (_, r) = n.subscribe(json!({"device": "BOOTH", "channel": 2, "source": "", "confirm": true})).await;
    assert!(r["result"]["result"] == json!("refused") && r["result"]["message"].as_str().unwrap().contains("Domain Manager"), "refused: {r}");
    n.booth.lock().refuse = false;
    n.booth.lock().ignore_writes = true;
    let (_, r) = n.subscribe(json!({"device": "BOOTH", "channel": 2, "source": "", "confirm": true})).await;
    assert_eq!(r["result"]["result"], json!("unconfirmed"), "a change the device did not show is unconfirmed");
    n.booth.lock().ignore_writes = false;
    assert_eq!(n.r.get("/api/admin/dante/routing").await.1["undo"], Value::Null, "a failed change leaves nothing to undo");
}

#[tokio::test]
async fn presets_save_break_two_routes_compare_apply_undo() {
    let n = Net::new().await;
    n.r.role("admin");
    n.r.post("/api/admin/dante/routing/lock", json!({"device": "PA", "channel": 1, "on": true})).await;
    let (status, saved) = n.r.post("/api/admin/dante/presets", json!({"name": "Sunday"})).await;
    assert_eq!((status, saved["saved"]["count"].clone()), (StatusCode::OK, json!(5)), "saved");
    let pid = saved["saved"]["id"].as_i64().unwrap();
    let compare = format!("/api/admin/dante/presets/{pid}/compare");
    assert_eq!(n.r.get(&compare).await.1["changes"], json!([]), "nothing differs yet");
    n.booth.subscribe(1, "");
    n.booth.subscribe(2, "Lead Vox@STAGE"); // two routes broken
    n.pa.subscribe(1, ""); // and a locked one
    n.scan().await;
    n.r.role("viewer");
    let (_, c) = n.r.get(&compare).await;
    let mut diff: Vec<(String, i64, String, String)> = c["changes"].as_array().unwrap().iter().map(|e| (e["device"].as_str().unwrap().into(), e["channel"].as_i64().unwrap(), e["from"].as_str().unwrap().into(), e["to"].as_str().unwrap().into())).collect();
    diff.sort();
    assert_eq!(diff, [("BOOTH".into(), 1, String::new(), "Lead Vox@STAGE".into()), ("BOOTH".into(), 2, "Lead Vox@STAGE".into(), "Pastor@STAGE".into())], "the diff names both routes");
    let locked: Vec<(String, i64)> = c["locked"].as_array().unwrap().iter().map(|e| (e["device"].as_str().unwrap().into(), e["channel"].as_i64().unwrap())).collect();
    assert_eq!(locked, [("PA".to_owned(), 1)], "the locked one is listed, not changed");
    let apply = format!("/api/admin/dante/presets/{pid}/apply");
    assert_eq!(n.r.post(&apply, json!({"confirm": true})).await.0, StatusCode::FORBIDDEN, "a viewer cannot apply (403)");
    n.r.role("operator");
    assert_eq!(n.r.post(&apply, json!({})).await.0, StatusCode::BAD_REQUEST, "apply needs confirm");
    let (_, r) = n.r.post(&apply, json!({"confirm": true})).await;
    let mut results: Vec<(String, i64, String)> = r["results"].as_array().unwrap().iter().map(|x| (x["device"].as_str().unwrap().into(), x["channel"].as_i64().unwrap(), x["result"].as_str().unwrap().into())).collect();
    results.sort();
    assert_eq!(results, [("BOOTH".into(), 1, "ok".into()), ("BOOTH".into(), 2, "ok".into())], "each result reported");
    assert_eq!((n.booth.source(1), n.booth.source(2), n.pa.source(1)), ("Lead Vox@STAGE".into(), "Pastor@STAGE".into(), String::new()), "restored");
    assert_eq!(r["locked"][0]["device"], json!("PA"), "the locked channel stayed as it was");
    assert_eq!(r["undo"]["count"], json!(2), "undo covers the whole apply");
    assert_eq!(r["failed"], json!(0));
    n.r.post(UNDO, json!({"confirm": true})).await;
    assert_eq!((n.booth.source(1), n.booth.source(2)), (String::new(), "Lead Vox@STAGE".into()), "undo puts the broken routing back");
}

#[tokio::test]
async fn a_missing_device_or_source_is_listed_and_skipped() {
    let n = Net::new().await;
    n.r.role("admin");
    let (_, saved) = n.r.post("/api/admin/dante/presets", json!({"name": "Sunday", "devices": ["stage", "booth", "pa"]})).await;
    let pid = saved["saved"]["id"].as_i64().unwrap();
    assert_eq!(saved["saved"]["count"], json!(5), "a preset can name its devices in any case");
    n.r.service().record(vec![record(&n.r, "STAGE", &n.stage, &["Pastor"], true).await, record(&n.r, "PA", &n.pa, &[], true).await]); // BOOTH gone, Lead Vox gone
    let (_, c) = n.r.get(&format!("/api/admin/dante/presets/{pid}/compare")).await;
    assert!(!c["missing"].as_array().unwrap().is_empty() && c["missing"].as_array().unwrap().iter().all(|e| !e["why"].as_str().unwrap().is_empty()), "missing entries carry a reason");
    n.r.role("operator");
    n.booth.subscribe(1, "Lead Vox@STAGE");
    let (_, r) = n.r.post(&format!("/api/admin/dante/presets/{pid}/apply"), json!({"confirm": true})).await;
    assert_eq!((n.booth.source(1), n.booth.source(2)), ("Lead Vox@STAGE".into(), "Pastor@STAGE".into()), "nothing was written to the missing device");
    assert!(!r["missing"].as_array().unwrap().is_empty(), "they are reported");
    n.r.role("admin");
    assert_eq!(n.r.send_delete(&format!("/api/admin/dante/presets/{pid}")).await.1["presets"], json!([]), "a preset can be deleted by an admin");
    assert_eq!(n.r.post("/api/admin/dante/presets", json!({"name": ""})).await.0, StatusCode::BAD_REQUEST, "a bad preset name is refused");
    assert_eq!(n.r.post("/api/admin/dante/presets", json!({"name": "x", "devices": ["Nobody"]})).await.0, StatusCode::CONFLICT, "a device Relay never saw is refused");
    assert_eq!(n.r.get("/api/admin/dante/presets/999/compare").await.0, StatusCode::NOT_FOUND, "a preset that is not there");
    assert_eq!(n.r.send_delete("/api/admin/dante/presets/999").await.0, StatusCode::NOT_FOUND);
    assert_eq!(n.r.get("/api/admin/dante/presets/abc/compare").await.0, StatusCode::UNPROCESSABLE_ENTITY, "an id that is not a number");
}

#[tokio::test]
async fn saving_a_name_again_replaces_it() {
    let n = Net::new().await;
    n.r.role("admin");
    let (_, first) = n.r.post("/api/admin/dante/presets", json!({"name": "Sunday", "devices": ["PA"]})).await;
    let (_, second) = n.r.post("/api/admin/dante/presets", json!({"name": "Sunday"})).await;
    assert_eq!(first["saved"]["id"], second["saved"]["id"], "the same preset");
    assert_eq!((second["saved"]["count"].clone(), second["presets"].as_array().unwrap().len()), (json!(5), 1), "with its new routes");
}

#[test]
fn the_pure_rules() {
    use relay_module_dante::routing::{diff, routes_of, same_source, sources};
    use std::collections::BTreeSet;
    assert!(same_source("", "") && same_source("Lead Vox@STAGE", "lead vox@stage") && !same_source("", "A@B") && !same_source("A@B", "A@C"));
    let device = |name: &str, subs: Value, transmit: &[&str]| json!({"name": name, "subscriptions": subs, "transmit": transmit});
    let devices = vec![device("STAGE", json!([{"channel": 1, "source": "Lead Vox@STAGE", "status": "ok"}, {"channel": 2, "source": "x@y", "status": "none"}]), &["Lead Vox"]), device("BLIND", Value::Null, &[])];
    assert_eq!(sources(&devices), BTreeSet::from(["Lead Vox@STAGE".to_owned()]));
    assert_eq!(routes_of(&devices, &[]), vec![json!({"device": "STAGE", "channel": 1, "source": "Lead Vox@STAGE"}), json!({"device": "STAGE", "channel": 2, "source": ""})], "a channel with nothing subscribed has no source");
    assert_eq!(routes_of(&devices, &["blind".to_owned()]), Vec::<Value>::new(), "a device Relay cannot read has no routes");
    let routes = vec![json!({"device": "BLIND", "channel": 1, "source": ""}), json!({"device": "NOPE", "channel": 1, "source": ""}), json!({"device": "STAGE", "channel": 9, "source": ""}), json!({"device": "STAGE", "channel": 2, "source": "Gone@STAGE"})];
    let got: Vec<(String, String)> = diff(&routes, &devices, &BTreeSet::new()).iter().map(|e| (e["action"].as_str().unwrap().into(), e["why"].as_str().unwrap().into())).collect();
    assert_eq!(got, [("missing".to_owned(), "Relay cannot read that device's subscriptions".to_owned()), ("missing".into(), "that device is not on the network".into()), ("missing".into(), "the device has no receive channel 9".into()), ("missing".into(), "the source is not on the network".into())]);
}
