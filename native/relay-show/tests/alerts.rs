//! Alerts and stage messages (the rules of `tests/test_alerts.py`), with a clock the test moves and fakes for the panels.

mod common;

use std::sync::Arc;

use common::*;
use relay_show::alerts::{Alerts, OUTCOME_KEEP_S};
use serde_json::{json, Value};

struct Rig {
    alerts: Arc<Alerts>,
    env: Arc<FakeEnv>,
    clock: Arc<Manual>,
    history: Arc<MemHistory>,
    store: Arc<MemStore>,
}

fn rig() -> Rig {
    let env = Arc::new(FakeEnv::default());
    let clock = Manual::new();
    let store = Arc::new(MemStore::default());
    let history = Arc::new(MemHistory::default());
    let alerts = Alerts::new(env.clone(), store.clone(), history.clone(), clock.clone());
    for (n, name) in [(1, "Booth"), (2, "Wedge L"), (3, "Wedge R")] {
        env.names.lock().unwrap().insert(pid(n), name.into());
        env.open.lock().unwrap().push(pid(n));
    }
    Rig { alerts, env, clock, history, store }
}

fn button(r: &Rig, name: &str) -> Value {
    r.alerts.settings()["buttons"].as_array().unwrap().iter().find(|b| b["name"] == name).unwrap().clone()
}

fn saved(r: &Rig) -> (Value, Value) {
    let out = r
        .alerts
        .save(&json!({"auto_stop_s": 60, "buttons": [
            {"name": " FOH  booth ", "icon": "headphones", "panels": [pid(1)], "screens": ["Booth TV", "booth tv"]},
            {"name": "Everyone on stage", "icon": "people", "panels": [pid(2), pid(3)]},
        ]}))
        .unwrap();
    let _ = out;
    (button(r, "FOH booth"), button(r, "Everyone on stage"))
}

#[test]
fn defaults_and_refusals() {
    let r = rig();
    assert_eq!(
        r.alerts.settings(),
        json!({"auto_stop_s": 120, "buttons": [], "require_signin": false,
            "messages": {"presets": [], "free": {"panels": [], "screens": [], "level": "info"}, "message_s": 10, "banner_s": 300},
            "replies": []})
    );
    let bad: Vec<(Value, &str)> = vec![
        (json!({"auto_stop_s": 10, "buttons": []}), "auto-stop"),
        (json!({"auto_stop_s": 120, "buttons": [{"name": "", "panels": [pid(1)]}]}), "needs a name"),
        (json!({"auto_stop_s": 120, "buttons": [{"name": "A", "panels": [], "screens": []}]}), "flashes nothing"),
        (json!({"auto_stop_s": 120, "buttons": [{"name": "A", "panels": [pid(1)]}, {"name": "a", "panels": [pid(2)]}]}), "two buttons"),
        (json!({"auto_stop_s": 120, "buttons": [{"name": "A", "panels": ["nope"]}]}), "panel ids"),
        (json!({"auto_stop_s": 60, "buttons": [{"name": "A", "panels": [pid(1)], "icon": "trumpet"}]}), "icon"),
    ];
    for (body, words) in bad {
        let e = r.alerts.save(&body).unwrap_err();
        assert!(e.contains(words), "{words}: {e}");
    }
    let (foh, _) = saved(&r);
    assert_eq!(foh["name"], "FOH booth");
    assert_eq!(foh["screens"], json!(["Booth TV"]));
    assert_eq!(foh["icon"], "headphones");
    assert_eq!(foh["id"].as_str().unwrap().len(), 8);
    assert_eq!(r.store.read()["auto_stop_s"], 60);
}

use relay_show::alerts::Store;

#[test]
fn sending_and_acknowledging() {
    let r = rig();
    let (foh, stage) = saved(&r);
    let mut sender = r.alerts.subscribe_sender();
    let first = drain(&mut sender);
    assert_eq!(first[0]["buttons"].as_array().unwrap().iter().map(|b| b["state"].clone()).collect::<Vec<_>>(), vec![json!("idle"), json!("idle")]);
    let mut screen = r.alerts.subscribe_screen("booth TV").unwrap();
    drain(&mut screen);
    assert_eq!(r.alerts.screen_names(), vec!["booth TV"]);

    let state = r.alerts.press(foh["id"].as_str().unwrap(), "Drums\n\x07", None).unwrap();
    assert_eq!(row(&state, "FOH booth")["state"], "on");
    assert_eq!(row(&state, "FOH booth")["from"], "Drums");
    assert_eq!(row(&state, "FOH booth")["icon"], "headphones");
    assert_eq!(row(&state, "FOH booth")["ends_in_s"], 60);
    let msg = r.env.take(&pid(1));
    assert_eq!(msg.len(), 1);
    assert_eq!((msg[0]["type"].clone(), msg[0]["from"].clone(), msg[0]["to"].clone(), msg[0]["ends_in_s"].clone(), msg[0]["hz"].clone()),
        (json!("alert"), json!("Drums"), json!("FOH booth"), json!(60), json!(1)));
    let alert_id = msg[0]["id"].as_str().unwrap().to_owned();
    assert_eq!(drain(&mut screen).iter().map(|m| m["id"].clone()).collect::<Vec<_>>(), vec![json!(alert_id)]);
    assert!(r.env.take(&pid(2)).is_empty(), "a panel not on the button hears nothing");
    assert_eq!(row(drain(&mut sender).last().unwrap(), "FOH booth")["state"], "on");
    assert_eq!(r.alerts.press("ffffffff", "", None).unwrap_err().status, 404);

    // a panel that connects mid-alert
    assert!(r.alerts.active_for_panel(&pid(1)).iter().any(|m| m["type"] == "alert" && m["id"] == json!(alert_id)));

    // acknowledging
    assert_eq!(r.alerts.ack(&json!(alert_id), Some(&pid(2)), None, None).unwrap_err().status, 403);
    assert_eq!(r.alerts.ack(&json!(alert_id), None, Some("Lobby"), None).unwrap_err().status, 403);
    let ok = r.alerts.ack(&json!(alert_id), None, Some("BOOTH tv"), None).unwrap();
    assert_eq!(ok, json!({"ok": true, "state": "seen", "seen_on": "Booth TV", "reply": ""}));
    assert_eq!(r.env.take(&pid(1)), vec![json!({"type": "alert_end", "id": alert_id, "why": "seen"})]);
    assert_eq!(drain(&mut screen)[0]["why"], "seen");
    let st = row(drain(&mut sender).last().unwrap(), "FOH booth").clone();
    assert_eq!((st["state"].clone(), st["seen_on"].clone()), (json!("seen"), json!("Booth TV")));
    assert_eq!(r.alerts.ack(&json!(alert_id), Some(&pid(1)), None, None).unwrap()["seen_on"], "Booth TV", "a second tap changes nothing");
    assert_eq!(r.alerts.ack(&json!("x"), None, None, None).unwrap_err().status, 400);

    // a panel sends; the first tap of a group stops all
    let state = r.alerts.press(stage["id"].as_str().unwrap(), "Booth", None).unwrap();
    assert_eq!(row(&state, "Everyone on stage")["from"], "Booth");
    let w = r.env.take(&pid(2));
    let stage_id = w[0]["id"].as_str().unwrap().to_owned();
    r.alerts.ack(&json!(stage_id), Some(&pid(2)), None, None).unwrap();
    assert_eq!(row(&r.alerts.state(), "Everyone on stage")["seen_on"], "Wedge L");
    assert_eq!(r.env.take(&pid(2)).last().unwrap()["why"], "seen");

    // history: the seen alert was recorded with where and how long
    let rows = r.history.0.lock().unwrap();
    let seen = rows.iter().find(|h| h.name == "FOH booth").unwrap();
    assert_eq!((seen.outcome.as_str(), seen.seen_on.as_str(), seen.sender.as_str()), ("seen", "Booth TV", "Drums"));
    assert_eq!(seen.seen_after_s, Some(0));
}

#[test]
fn cancel_double_tap_and_auto_stop() {
    let r = rig();
    let (foh, _) = saved(&r);
    let id = foh["id"].as_str().unwrap();
    r.clock.advance(5.0);
    r.alerts.press(id, "Keys", None).unwrap();
    r.clock.advance(0.3);
    assert_eq!(row(&r.alerts.press(id, "Keys", None).unwrap(), "FOH booth")["state"], "on", "a double tap is one press");
    r.clock.advance(2.0);
    assert_eq!(row(&r.alerts.press(id, "Keys", None).unwrap(), "FOH booth")["state"], "cancelled");
    assert_eq!(r.env.take(&pid(1)).last().unwrap()["why"], "cancelled");
    r.alerts.press(id, "Keys", None).unwrap();
    assert_eq!(row(&r.alerts.cancel(id).unwrap(), "FOH booth")["state"], "cancelled");

    r.env.take(&pid(1));
    let mut sender = r.alerts.subscribe_sender();
    drain(&mut sender);
    r.alerts.press(id, "Bass", None).unwrap();
    r.clock.advance(59.0);
    r.alerts.tick();
    assert_eq!(row(&r.alerts.state(), "FOH booth")["state"], "on", "still on at 59 s");
    r.clock.advance(1.0);
    r.alerts.tick();
    assert_eq!(row(drain(&mut sender).last().unwrap(), "FOH booth")["state"], "missed");
    assert_eq!(r.env.take(&pid(1)).last().unwrap()["why"], "missed");
    r.clock.advance(OUTCOME_KEEP_S);
    r.alerts.tick();
    assert_eq!(row(&r.alerts.state(), "FOH booth")["state"], "idle", "the outcome is forgotten after a while");

    // removing a button stops its alert
    r.alerts.press(id, "Bass", None).unwrap();
    r.env.take(&pid(1));
    r.alerts.save(&json!({"auto_stop_s": 60, "buttons": [{"name": "Other", "panels": [pid(3)]}]})).unwrap();
    assert_eq!(r.env.take(&pid(1)).last().unwrap()["why"], "cancelled");
}

#[test]
fn levels_replies_and_places() {
    let r = rig();
    r.alerts
        .save(&json!({"auto_stop_s": 60, "replies": ["On my way", "Can't right now"], "buttons": [
            {"name": "Calm", "panels": [pid(1)], "level": "info"},
            {"name": "Room", "screens": ["place:relay:3", "Lobby TV"]}]}))
        .unwrap();
    r.env.places.lock().unwrap().insert(3, vec![("Foyer TV".into(), String::new()), ("Hall panel".into(), pid(3))]);
    let calm = button(&r, "Calm");
    let room = button(&r, "Room");
    r.alerts.press(calm["id"].as_str().unwrap(), "x", None).unwrap();
    let m = r.env.take(&pid(1));
    assert_eq!((m[0]["level"].clone(), m[0]["hz"].clone()), (json!("info"), json!(0)), "info is steady");
    assert_eq!(m[0]["replies"], json!(["On my way", "Can't right now"]));
    assert_eq!(r.alerts.press("zz", "", None).unwrap_err().key, "no_button");
    assert_eq!(r.alerts.press(calm["id"].as_str().unwrap(), "", Some(&json!("loud"))).unwrap_err().key, "bad_level");
    assert_eq!(r.alerts.targets_of(&room), (vec![pid(3)], vec!["Foyer TV".to_owned(), "Lobby TV".to_owned()]), "a place stands for its screens");
    r.alerts.press(room["id"].as_str().unwrap(), "Pastor", Some(&json!("attention"))).unwrap();
    let m = r.env.take(&pid(3));
    assert_eq!((m[0]["level"].clone(), m[0]["hz"].clone()), (json!("attention"), json!(1)));
    let id = m[0]["id"].clone();
    assert_eq!(r.alerts.ack(&id, Some(&pid(3)), None, Some(&json!("Nope"))).unwrap_err().key, "bad_reply");
    let ok = r.alerts.ack(&id, Some(&pid(3)), None, Some(&json!("on my WAY"))).unwrap();
    assert_eq!((ok["state"].clone(), ok["reply"].clone(), ok["seen_on"].clone()), (json!("seen"), json!("On my way"), json!("Wedge R")));
}

#[test]
fn raise_named_never_cancels() {
    let r = rig();
    r.alerts.save(&json!({"auto_stop_s": 60, "buttons": [{"name": "Stage", "panels": [pid(1)], "stage": true}]})).unwrap();
    let stages = Arc::new(std::sync::Mutex::new(Vec::<(String, Option<String>)>::new()));
    let s2 = stages.clone();
    r.alerts.set_on_stage(Some(Box::new(move |id, text| s2.lock().unwrap().push((id.to_owned(), text.map(str::to_owned))))));
    assert_eq!(r.alerts.raise_named("  stage ", "NDI").unwrap(), "Alert Stage raised.");
    r.clock.advance(5.0);
    assert_eq!(r.alerts.raise_named("STAGE", "NDI").unwrap(), "The Stage alert is already on.");
    assert_eq!(r.alerts.raise_named("Nobody", "NDI").unwrap_err().key, "no_button_named");
    let id = button(&r, "Stage")["id"].as_str().unwrap().to_owned();
    assert!(r.alerts.is_on(&id));
    {
        let s = stages.lock().unwrap();
        assert_eq!(s.len(), 1);
        assert_eq!(s[0].1.as_deref(), Some("Stage (from NDI)"));
    }
    r.alerts.cancel(&id).unwrap();
    assert_eq!(stages.lock().unwrap().last().unwrap().1, None, "any end clears the stage display");
}

#[test]
fn events_out_and_a_listener_that_is_removed() {
    let r = rig();
    r.alerts.save(&json!({"auto_stop_s": 60, "buttons": [{"name": "A", "panels": [pid(1)]}]})).unwrap();
    let got = Arc::new(std::sync::Mutex::new(Vec::<Value>::new()));
    let g = got.clone();
    r.alerts.add_listener("t", Box::new(move |e| g.lock().unwrap().push(e.clone())));
    let id = button(&r, "A")["id"].as_str().unwrap().to_owned();
    r.alerts.press(&id, "Pat", None).unwrap();
    let a = r.env.take(&pid(1))[0]["id"].clone();
    r.alerts.ack(&a, Some(&pid(1)), None, None).unwrap();
    let events: Vec<Value> = got.lock().unwrap().iter().map(|e| e["event"].clone()).collect();
    assert_eq!(events, vec![json!("alert.sent"), json!("alert.acknowledged")]);
    r.alerts.remove_listener("t");
    r.clock.advance(2.0);
    r.alerts.press(&id, "Pat", None).unwrap();
    assert_eq!(got.lock().unwrap().len(), 2);
}

#[test]
fn stage_messages() {
    let r = rig();
    r.alerts
        .save(&json!({"auto_stop_s": 60, "buttons": [{"name": "A", "panels": [pid(1)]}], "messages": {
            "presets": [{"id": "0a0a0a0a", "text": "Wrap up", "panels": [pid(1)], "level": "attention"},
                        {"text": "Welcome", "banner": true, "screens": ["Lobby"]}],
            "free": {"panels": [pid(2)]}, "message_s": 10, "banner_s": 60}}))
        .unwrap();
    let ms = r.alerts.message_state();
    assert_eq!(ms["free"], true);
    assert_eq!(ms["presets"].as_array().unwrap().len(), 2);
    let mut feed = r.alerts.subscribe_messages();
    assert_eq!(drain(&mut feed)[0]["type"], "messages");
    let shown = r.alerts.send_message("Pat", Some(&json!("0a0a0a0a")), None, None, None).unwrap();
    assert_eq!(shown["live"][0]["text"], "Wrap up");
    let w = r.env.take(&pid(1));
    assert_eq!((w[0]["type"].clone(), w[0]["level"].clone(), w[0]["hz"].clone(), w[0]["ends_in_s"].clone()), (json!("message"), json!("attention"), json!(1), json!(10)));
    assert_eq!(drain(&mut feed).last().unwrap()["live"][0]["from"], "Pat");
    // the same preset again replaces what is showing
    r.alerts.send_message("Pat", Some(&json!("0a0a0a0a")), None, None, None).unwrap();
    assert_eq!(r.alerts.message_state()["live"].as_array().unwrap().len(), 1);
    assert!(r.env.take(&pid(1)).iter().any(|m| m["type"] == "message_end"));
    // typed words go where the operator chose, and a bad request is refused with a fixed sentence
    assert_eq!(r.alerts.send_message("x", None, Some(&json!("  ")), None, None).unwrap_err().key, "no_words");
    assert_eq!(r.alerts.send_message("x", Some(&json!("nope")), None, None, None).unwrap_err().key, "no_preset");
    assert_eq!(r.alerts.send_message("x", None, Some(&json!("hi")), None, Some(&json!("loud"))).unwrap_err().key, "bad_level");
    r.alerts.send_message("x", None, Some(&json!("Hello\nthere")), Some(&json!(true)), None).unwrap();
    let w = r.env.take(&pid(2));
    assert_eq!((w[0]["text"].clone(), w[0]["banner"].clone(), w[0]["ends_in_s"].clone()), (json!("Hello there"), json!(true), json!(60)));
    // it ends on its own time
    r.clock.advance(11.0);
    r.alerts.tick();
    let live: Vec<Value> = r.alerts.message_state()["live"].as_array().unwrap().clone();
    assert_eq!(live.len(), 1, "the banner (60 s) is still up, the 10 s message is gone");
    r.alerts.clear_messages(None);
    assert_eq!(r.alerts.message_state()["live"], json!([]));
    // history rows for what was shown
    assert_eq!(r.history.0.lock().unwrap().iter().filter(|h| h.kind == "message").count(), 3);
}

#[test]
fn test_message_and_streams_are_bounded() {
    let r = rig();
    r.alerts.save(&json!({"auto_stop_s": 60, "buttons": [{"name": "A", "panels": [pid(1)]}]})).unwrap();
    assert_eq!(r.alerts.test(Some(&json!(pid(1))), None).unwrap(), 1);
    assert_eq!(r.env.take(&pid(1))[0]["text"], "Test from Relay");
    assert_eq!(r.alerts.test(None, Some(&json!("Nowhere TV"))).unwrap(), 0);
    assert_eq!(r.alerts.test(None, None).unwrap_err().key, "no_test_target");
    // no more than 32 screen names, and each name keeps at most 4 readers
    let mut keep = Vec::new();
    for n in 0..32 {
        keep.push(r.alerts.subscribe_screen(&format!("tv {n}")).unwrap());
    }
    assert_eq!(r.alerts.subscribe_screen("one too many").unwrap_err().status, 429);
    let mut same: Vec<_> = (0..5).map(|_| r.alerts.subscribe_screen("tv 0").unwrap()).collect();
    let mut oldest = same.remove(0);
    while oldest.try_recv().is_ok() {}
    assert!(matches!(oldest.try_recv(), Err(tokio::sync::mpsc::error::TryRecvError::Disconnected)), "the fifth reader of a name cuts the oldest");
    // a reader that stops reading is cut: its frames end instead of piling up
    let mut slow = r.alerts.subscribe_sender();
    for n in 0..40 {
        r.clock.advance(1.0);
        r.alerts.press(&button(&r, "A")["id"].as_str().unwrap().to_owned(), &format!("s{n}"), None).unwrap();
    }
    let mut got = 0;
    while slow.try_recv().is_ok() {
        got += 1;
    }
    assert!(got <= 17, "a stalled reader holds at most its queue: {got}");
}

#[test]
fn text_cleaners_follow_python() {
    use relay_show::text::*;
    assert_eq!(one_line("  Drums\n\x07 \u{200b}kit  ", 32), "Drums kit");
    assert_eq!(one_line(&"é".repeat(40), 32).chars().count(), 32);
    assert_eq!(py_int(&json!(" 42 ")), Some(42));
    assert_eq!(py_int(&json!(7.9)), Some(7));
    assert_eq!(py_int(&json!("x")), None);
    assert!(same_name("  Foo  Bar", "foo bar"));
    assert!(!same_name("", ""));
    assert_eq!(relay_show::stamp_of(1_700_000_000.0), "2023-11-14T22:13:20Z");
}
