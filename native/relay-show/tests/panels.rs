//! The panel registry (the rules of `tests/test_panels.py`), with a clock the test moves.

mod common;

use std::sync::{Arc, Mutex};

use common::*;
use relay_show::firmware::{self, Kind};
use relay_show::panels::{Cfg, PanelError, Registry, Update, MAX_PANELS};
use serde_json::{json, Value};

#[derive(Default)]
struct MemCfg {
    stored: Mutex<Value>,
    config: Mutex<Value>,
}

impl Cfg for MemCfg {
    fn panels(&self) -> Value {
        self.stored.lock().unwrap().clone()
    }
    fn save_panels(&self, stored: &Value) {
        *self.stored.lock().unwrap() = stored.clone();
    }
    fn config(&self) -> Value {
        self.config.lock().unwrap().clone()
    }
}

struct Rig {
    reg: Arc<Registry>,
    cfg: Arc<MemCfg>,
    clock: Arc<Manual>,
    dir: std::path::PathBuf,
}

fn rig() -> Rig {
    let cfg = Arc::new(MemCfg::default());
    *cfg.stored.lock().unwrap() = json!({});
    *cfg.config.lock().unwrap() = json!({"source_language": "ENGLISH", "targets": {"SPANISH": {"enabled": true}, "FRENCH": {"enabled": false}}});
    let clock = Manual::new();
    let dir = std::env::temp_dir().join(format!("relay-show-panels-{}-{}", std::process::id(), relay_show::alerts::token_hex(4)));
    std::fs::create_dir_all(&dir).unwrap();
    let fw = Arc::new(firmware::Store::new(&dir));
    Rig { reg: Registry::new(cfg.clone(), fw, clock.clone()), cfg, clock, dir }
}

impl Drop for Rig {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn body(n: u32) -> Value {
    json!({"id": pid(n), "name": "Hall 1", "version": "0.1.0", "scripts": ["latin"], "feeds": [{"stream": "translation", "lang": "SPANISH"}],
        "ip": "192.168.1.50", "uptime_s": 42, "rssi": -58})
}

fn with(mut b: Value, k: &str, v: Value) -> Value {
    b[k] = v;
    b
}

#[test]
fn check_in() {
    let r = rig();
    let reply = r.reg.checkin(&body(1), "10.0.0.9").unwrap();
    assert_eq!((reply["name"].clone(), reply["interval_s"].clone()), (json!("Hall 1"), json!(30)));
    let rows = r.reg.listing();
    assert_eq!(rows.iter().map(|p| (p["id"].clone(), p["online"].clone())).collect::<Vec<_>>(), vec![(json!(pid(1)), json!(true))]);
    let row = &rows[0];
    assert_eq!((row["version"].clone(), row["scripts"].clone(), row["feeds"].clone(), row["ip"].clone(), row["uptime_s"].clone(), row["rssi"].clone()),
        (json!("0.1.0"), json!(["latin"]), json!([{"stream": "translation", "lang": "SPANISH"}]), json!("192.168.1.50"), json!(42), json!(-58)));
    assert_eq!(r.cfg.panels(), json!({}), "a check-in alone writes nothing to config.json");
    assert_eq!(r.reg.checkin(&json!({"id": pid(2)}), "p").unwrap()["name"], "Panel 0000");
    r.reg.checkin(&with(with(with(body(3), "ip", json!("not an ip")), "rssi", json!("loud")), "scripts", json!(["latin", "<b>", 7])), "peer").unwrap();
    let row = r.reg.view(&pid(3));
    assert_eq!((row["ip"].clone(), row["rssi"].clone(), row["scripts"].clone()), (json!("peer"), Value::Null, json!(["latin"])));
    // what a check-in may not do
    for bad in [with(body(1), "id", json!("nope")), with(body(1), "id", json!("A".repeat(32))), json!([1])] {
        assert!(matches!(r.reg.checkin(&bad, "p"), Err(PanelError::Invalid(_))));
    }
    r.reg.checkin(&with(with(body(1), "name", json!("n".repeat(500))), "version", json!("v".repeat(500))), "p").unwrap();
    let row = r.reg.view(&pid(1));
    assert_eq!((row["reported_name"].as_str().unwrap().len(), row["version"].as_str().unwrap().len()), (64, 32));
}

#[test]
fn offline_after_90_seconds_and_the_cap() {
    let r = rig();
    r.reg.checkin(&body(1), "p").unwrap();
    r.clock.advance(89.0);
    assert!(r.reg.view(&pid(1))["online"].as_bool().unwrap());
    r.clock.advance(2.0);
    assert!(!r.reg.view(&pid(1))["online"].as_bool().unwrap());
    assert!(r.reg.listing().iter().any(|p| p["id"] == json!(pid(1))), "an offline panel stays listed");
    r.reg.checkin(&body(1), "p").unwrap();
    assert!(r.reg.view(&pid(1))["online"].as_bool().unwrap());
    for n in 100..100 + MAX_PANELS as u32 {
        let _ = r.reg.checkin(&body(n), "p");
    }
    assert_eq!(r.reg.checkin(&body(999), "p"), Err(PanelError::Full));
    assert!(!r.reg.has_checked_in(&pid(999)), "and holds no memory");
    r.clock.advance(200.0);
    assert!(r.reg.checkin(&body(999), "p").is_ok(), "once panels are offline, the stalest makes room");
}

impl PartialEq for Rig {
    fn eq(&self, _: &Self) -> bool {
        false
    }
}

#[test]
fn admin_control_and_feed_revisions() {
    let r = rig();
    r.reg.checkin(&body(1), "p").unwrap();
    let up = |u: Update| r.reg.update(&pid(1), &u);
    let row = up(Update { name: Some("Lobby".into()), ..Update::default() }).unwrap();
    assert_eq!(row["name"], "Lobby");
    assert_eq!(r.cfg.panels()[pid(1)]["name"], "Lobby");
    assert_eq!(r.reg.checkin(&body(1), "p").unwrap()["name"], "Lobby");
    let row = up(Update { feeds: Some(json!([{"stream": "translation", "lang": "french"}])), ..Update::default() }).unwrap();
    assert_eq!(row["set_feeds"], json!([{"stream": "translation", "lang": "FRENCH"}]));
    assert_eq!(r.cfg.panels()[pid(1)]["feed_rev"], 1);
    for (bad, why) in [
        (json!([{"stream": "translation", "lang": "KLINGON"}]), "unknown language"),
        (json!([{"stream": "nope"}]), "unknown stream"),
        (json!([]), "empty"),
        (json!([{"stream": "transcription"}, {"stream": "transcription"}, {"stream": "transcription"}]), "three feeds"),
        (json!("transcription"), "not a list"),
        (json!([{"stream": "translation", "lang": "JAPANESE"}]), "a script the panel cannot draw"),
    ] {
        assert!(matches!(up(Update { feeds: Some(bad), ..Update::default() }), Err(PanelError::Invalid(_))), "{why}");
    }
    assert_eq!(r.reg.update(&pid(77), &Update { name: Some("x".into()), ..Update::default() }), Err(PanelError::Unknown));
    assert_eq!(r.reg.update("short", &Update::default()), Err(PanelError::Unknown));
    let row = up(Update { feeds: Some(json!([{"stream": "transcription"}, {"stream": "translation", "lang": "SPANISH"}])), ..Update::default() }).unwrap();
    assert_eq!(row["set_feeds"].as_array().unwrap().len(), 2);
    assert_eq!(r.cfg.panels()[pid(1)]["feed_rev"], 2, "every change takes the next revision");

    // the feed reaches a panel that has not applied it
    let reply = r.reg.checkin(&body(1), "p").unwrap();
    assert_eq!(reply["set_feed"], json!({"type": "set_feed", "rev": 2, "feeds": r.cfg.panels()[pid(1)]["feeds"]}));
    assert!(r.reg.checkin(&with(body(1), "feed_rev", json!(2)), "p").unwrap().get("set_feed").is_none(), "stops once acknowledged");
    assert!(r.reg.checkin(&with(body(1), "feed_rev", json!(0)), "p").unwrap().get("set_feed").is_none(), "a rebooted panel keeps its screen choice");
    up(Update { feeds: Some(json!([{"stream": "transcription"}])), ..Update::default() }).unwrap();
    assert_eq!(r.reg.checkin(&with(body(1), "feed_rev", json!(2)), "p").unwrap()["set_feed"]["rev"], 3);
    up(Update { feeds: Some(Value::Null), ..Update::default() }).unwrap();
    assert!(r.reg.checkin(&body(1), "p").unwrap().get("set_feed").is_none());
    assert_eq!(r.cfg.panels()[pid(1)]["name"], "Lobby", "clearing keeps the name");

    // forget
    assert!(r.reg.forget(&pid(1)));
    assert!(r.cfg.panels().get(pid(1)).is_none());
    assert!(!r.reg.forget(&pid(1)));
}

#[test]
fn the_event_stream_primes_and_pushes() {
    let r = rig();
    r.reg.checkin(&body(1), "p").unwrap();
    r.reg.update(&pid(1), &Update { feeds: Some(json!([{"stream": "transcription"}])), rotation: Some(json!(90)), lock: Some(json!(true)), pin: Some(json!("1234")), service_mode: Some("control".into()), ..Update::default() }).unwrap();
    r.reg.set_alerts_for(Box::new(|_| vec![json!({"type": "alert", "id": "a1"})]));
    let mut a = r.reg.subscribe(&pid(1));
    let types: Vec<String> = drain(&mut a).iter().map(|m| m["type"].as_str().unwrap().to_owned()).collect();
    assert_eq!(types, vec!["set_feed", "service_mode", "set_rotation", "panel_policy", "alert"]);
    // a push reaches it, a third reader cuts the first
    assert_eq!(r.reg.push(&pid(1), &json!({"type": "reboot"})), 1);
    assert_eq!(drain(&mut a), vec![json!({"type": "reboot"})]);
    let _b = r.reg.subscribe(&pid(1));
    let _c = r.reg.subscribe(&pid(1));
    assert_eq!(r.reg.stream_count(), 2, "at most two streams per panel");
    assert!(matches!(a.try_recv(), Err(tokio::sync::mpsc::error::TryRecvError::Disconnected)), "the oldest is cut");
    assert!(r.reg.view(&pid(1))["can_reboot"].as_bool().unwrap());
    assert!(r.reg.reboot(&pid(1)).is_ok());
    assert!(matches!(r.reg.reboot(&pid(9)), Err(PanelError::Unknown)));
}

#[test]
fn lock_pin_service_and_return() {
    let r = rig();
    r.reg.checkin(&body(1), "p").unwrap();
    let up = |u: Update| r.reg.update(&pid(1), &u);
    let row = up(Update { lock: Some(json!(true)), pin: Some(json!("4821")), unlock_min: Some(json!(3)), ..Update::default() }).unwrap();
    assert_eq!((row["lock"].clone(), row["pin_set"].clone(), row["unlock_min"].clone()), (json!(true), json!(true), json!(3)));
    assert!(row.get("pin").is_none(), "the PIN itself is never listed");
    assert_eq!(r.reg.policy(&pid(1)), json!({"type": "panel_policy", "lock": true, "pin": "4821", "unlock_s": 180}));
    for bad in [Update { pin: Some(json!("12")), ..Update::default() }, Update { unlock_min: Some(json!(0)), ..Update::default() }, Update { lock: Some(json!("yes")), ..Update::default() }, Update { rotation: Some(json!(45)), ..Update::default() }, Update { service_mode: Some("loud".into()), ..Update::default() }] {
        assert!(matches!(up(bad), Err(PanelError::Invalid(_))));
    }
    // control mode gets a key, leaving it drops it
    up(Update { service_mode: Some("control".into()), ..Update::default() }).unwrap();
    let key = r.reg.control_key(&pid(1)).unwrap();
    assert_eq!(key.len(), 32);
    assert_eq!(r.reg.service(&pid(1))["key"], json!(key));
    up(Update { service_mode: Some("show".into()), ..Update::default() }).unwrap();
    assert!(r.reg.control_key(&pid(1)).is_none());
    // a Service feed needs a showing mode
    up(Update { service_mode: Some("off".into()), ..Update::default() }).unwrap();
    assert!(matches!(up(Update { feeds: Some(json!([{"stream": "service", "service_type": "12"}])), ..Update::default() }), Err(PanelError::Invalid(_))));

    // return to the operator's choice after the screen has been left alone
    up(Update { feeds: Some(json!([{"stream": "transcription"}])), return_min: Some(json!(2)), ..Update::default() }).unwrap();
    let rev = r.cfg.panels()[pid(1)]["feed_rev"].as_i64().unwrap();
    r.reg.checkin(&with(with(body(1), "feeds", json!([{"stream": "transcription"}])), "feed_rev", json!(rev)), "p").unwrap();
    // someone changes it at the screen
    r.reg.checkin(&with(with(with(body(1), "feeds", json!([{"stream": "translation", "lang": "SPANISH"}])), "feed_rev", json!(rev)), "idle_s", json!(10)), "p").unwrap();
    assert_eq!(r.reg.view(&pid(1))["feeds_changed_by"], "screen");
    let none = r.reg.checkin(&with(with(with(body(1), "feeds", json!([{"stream": "translation", "lang": "SPANISH"}])), "feed_rev", json!(rev)), "idle_s", json!(60)), "p").unwrap();
    assert!(none.get("set_feed").is_none(), "not idle long enough");
    let back = r.reg.checkin(&with(with(with(body(1), "feeds", json!([{"stream": "translation", "lang": "SPANISH"}])), "feed_rev", json!(rev)), "idle_s", json!(121)), "p").unwrap();
    assert_eq!(back["set_feed"]["feeds"], json!([{"stream": "transcription"}]));
    assert_eq!(back["set_feed"]["rev"], rev + 1);
}

#[test]
fn lyrics_captions_and_language_from_tags() {
    let r = rig();
    r.reg.checkin(&with(body(1), "feeds", json!([{"stream": "translation", "lang": "SPANISH"}])), "p").unwrap();
    let mut s = r.reg.subscribe(&pid(1));
    drain(&mut s);
    assert!(r.reg.show_lyrics().unwrap_err().contains("switched off"));
    r.reg.set_lyrics_offered(Box::new(|| true));
    assert_eq!(r.reg.show_lyrics().unwrap(), "Lyrics shown on 1 panel.");
    assert_eq!(drain(&mut s)[0]["feeds"], json!([{"stream": "lyrics"}]));
    assert_eq!(r.reg.show_lyrics().unwrap(), "Lyrics shown on 0 panels.", "already showing it");
    assert_eq!(r.reg.show_captions(), "Captions back on 1 panel.");
    assert_eq!(drain(&mut s)[0]["feeds"], json!([{"stream": "translation", "lang": "SPANISH"}]));
    assert_eq!(r.reg.show_captions(), "No panel was showing lyrics from a tag.");
    assert!(r.reg.show_language("KLINGON").unwrap_err().contains("not a language"));
    assert!(r.reg.show_language("FRENCH").unwrap_err().contains("not switched on"));
    assert_eq!(r.reg.show_language("ENGLISH").unwrap(), "English set on 1 panel.");
    assert_eq!(drain(&mut s)[0]["feeds"], json!([{"stream": "transcription"}]));
}

#[test]
fn timers_and_the_stored_block() {
    use relay_show::panels::{clean_stored, timer_fields};
    let (t, problem) = timer_fields(&json!({"amber_s": 90, "red_s": -1, "show": ["a", "a", "b c", 3]}));
    assert_eq!(Value::Object(t), json!({"amber_s": 90, "show": ["a"]}));
    assert!(problem.starts_with("The red threshold"), "{problem}");
    let stored = clean_stored(&json!({pid(1): {"name": " Lobby ", "feeds": [{"stream": "alert"}], "feed_rev": "3", "rotation": 180, "lock": true, "pin": "12", "unlock_min": 99,
        "service_mode": "control", "service_type": "5", "control_key": "k"}, "bad": {}}));
    assert_eq!(stored.len(), 1);
    let e = &stored[&pid(1)];
    assert_eq!(e["name"], "Lobby");
    assert_eq!(e["feed_rev"], 0, "a string revision is not a number");
    assert!(e.get("pin").is_none() && e.get("unlock_min").is_none() && e.get("control_key").is_none());
    assert_eq!((e["lock"].clone(), e["rotation"].clone(), e["service_mode"].clone(), e["service_type"].clone()), (json!(true), json!(180), json!("control"), json!("5")));
    let r = rig();
    r.reg.checkin(&body(1), "p").unwrap();
    let row = r.reg.update(&pid(1), &Update { timers: Some(json!({"amber_s": 30, "show": ["t1"]})), ..Update::default() }).unwrap();
    assert_eq!(row["set_timers"], json!({"amber_s": 30, "show": ["t1"]}));
    assert!(matches!(r.reg.update(&pid(1), &Update { timers: Some(json!({"amber_s": "x"})), ..Update::default() }), Err(PanelError::Invalid(_))));
    assert_eq!(r.reg.timer_settings(&pid(1)).unwrap()["amber_s"], 30);
}

#[test]
fn firmware_offers_and_reports() {
    let r = rig();
    let store = firmware::Store::new(&r.dir);
    // a stored image: the files a Python Relay wrote
    let folder = r.dir.join("panel-firmware").join("crowpanel-adv7");
    std::fs::create_dir_all(&folder).unwrap();
    std::fs::write(folder.join("panel.bin"), vec![0u8; 100]).unwrap();
    std::fs::write(folder.join("panel.json"), json!({"version": "0.6.0", "size": 100, "sha256": "ab", "uploaded_at": 1.0, "filename": "x.bin", "board": "crowpanel-adv7"}).to_string()).unwrap();
    let info = store.current("crowpanel-adv7", Kind::App).unwrap();
    assert_eq!((info.version.as_str(), info.size), ("0.6.0", 100));
    assert_eq!(info.image_url(), "/api/panels/firmware/crowpanel-adv7/0.6.0.bin");
    assert!(store.image_path_for("0.6.0", "crowpanel-adv7").is_some() && store.image_path_for("0.5.0", "crowpanel-adv7").is_none());

    r.reg.checkin(&with(body(1), "version", json!("0.5.0")), "p").unwrap();
    r.reg.checkin(&with(body(2), "version", json!("0.6.0")), "p").unwrap();
    assert!(r.reg.view(&pid(1))["update_available"].as_bool().unwrap());
    assert!(!r.reg.view(&pid(2))["update_available"].as_bool().unwrap());
    assert!(matches!(r.reg.request_update(&pid(2), false), Err(PanelError::Invalid(_))));
    assert_eq!(r.reg.request_update(&pid(9), false), Err(PanelError::Unknown));
    let mut s = r.reg.subscribe(&pid(1));
    drain(&mut s);
    let row = r.reg.request_update(&pid(1), true).unwrap();
    assert!(row["update_requested"].as_bool().unwrap());
    let pushed = drain(&mut s);
    assert_eq!((pushed[0]["type"].clone(), pushed[0]["version"].clone(), pushed[0]["now"].clone(), pushed[0]["path"].clone()), (json!("update_available"), json!("0.6.0"), json!(true), json!("/api/panels/firmware/crowpanel-adv7/0.6.0.bin")));
    assert_eq!(r.reg.checkin(&with(body(1), "version", json!("0.5.0")), "p").unwrap()["update"]["version"], "0.6.0", "the check-in carries the offer");
    // the panel's word
    assert!(r.reg.report_update(&pid(1), &json!({"state": "downloading", "progress": 40})));
    assert_eq!(r.reg.view(&pid(1))["update"]["progress"], 40);
    assert!(r.reg.report_update(&pid(1), &json!({"state": "nonsense"})));
    assert!(!r.reg.report_update(&pid(9), &json!({})));
    assert!(r.reg.report_update(&pid(1), &json!({"state": "failed", "error": "signature"})));
    let u = r.reg.view(&pid(1))["update"].clone();
    assert_eq!((u["state"].clone(), u["error_text"].clone()), (json!("failed"), json!("the image is not signed with the key this panel trusts")));
    assert!(!r.reg.view(&pid(1))["update_requested"].as_bool().unwrap(), "a failed update ends the request");
    // a check-in from the asked version finishes it
    r.reg.request_update(&pid(1), false).unwrap();
    r.reg.checkin(&with(body(1), "version", json!("0.6.0")), "p").unwrap();
    assert_eq!(r.reg.view(&pid(1))["update"]["state"], "installed");
    // replacing the image withdraws a request
    r.reg.checkin(&with(body(3), "version", json!("0.4.0")), "p").unwrap();
    r.reg.request_update(&pid(3), false).unwrap();
    r.reg.firmware_changed(Some("crowpanel-adv7"));
    assert!(!r.reg.view(&pid(3))["update_requested"].as_bool().unwrap());
    assert!(r.reg.view(&pid(3))["update"].is_null());
    // ask all: only online panels
    r.reg.checkin(&with(body(4), "version", json!("0.1.0")), "p").unwrap();
    r.clock.advance(100.0);
    r.reg.checkin(&with(body(5), "version", json!("0.1.0")), "p").unwrap();
    assert_eq!(r.reg.request_update_all(false, Some("crowpanel-adv7")), json!({"requested": 1, "offline": 2}));
}

#[test]
fn image_checks_say_what_is_wrong() {
    assert_eq!(firmware::parse_version("0.2.10"), Some((0, 2, 10)));
    assert_eq!(firmware::parse_version("1.2"), None);
    assert_eq!(firmware::parse_version("123456.0.0"), None);
    assert!(firmware::newer("0.10.0", "0.9.9") && !firmware::newer("0.9.9", "0.9.9") && !firmware::newer("x", "0.1.0"));
    assert_eq!(firmware::panel_board(""), "crowpanel-adv7");
    assert_eq!(firmware::panel_board("waveshare-p4-10.1"), "", "a board on hold takes none");
    assert_eq!(firmware::panel_board("mystery"), "");
    assert_eq!(firmware::inspect_board(&[], None).unwrap_err(), "The upload was empty.");
    assert!(firmware::inspect_board(&vec![0u8; 400], None).unwrap_err().starts_with("That file is not panel firmware"));
    assert!(firmware::inspect_board(&vec![0u8; firmware::SLOT_BYTES + 1], None).unwrap_err().contains("larger than a panel's application slot"));
    let mut serial = vec![0u8; 400];
    serial[0] = 0xE9;
    assert!(firmware::inspect_board(&serial, None).unwrap_err().contains("serial-flash image"));
    assert!(firmware::inspect_flash(&[], None).is_err());
    let mut app_alone = vec![0u8; 400];
    app_alone[0] = 0xE9;
    app_alone[32..36].copy_from_slice(&0xABCD_5432u32.to_le_bytes());
    assert!(firmware::inspect_flash(&app_alone, None).unwrap_err().contains("application alone"));
}
