//! The Dante steps of the "No sound?" walk (`walk.py`): where the walk stops, and what it says.

use std::collections::BTreeSet;

use relay_module_dante::walk::{step, steps};
use serde_json::{json, Value};

fn here() -> BTreeSet<String> {
    BTreeSet::from(["10.9.0.2".to_owned()])
}

fn mine(subs: Value) -> Value {
    json!({"name": "DVS-MAC", "ips": ["10.9.0.2"], "subscriptions": subs, "online": true, "clock": null})
}

fn stage(online: bool, locked: bool) -> Value {
    json!({"name": "STAGE", "ips": ["10.9.0.21"], "subscriptions": [], "online": online, "clock": {"locked": locked}})
}

fn states(out: &[Value]) -> Vec<(String, String)> {
    out.iter().map(|s| (s["id"].as_str().unwrap().to_owned(), s["state"].as_str().unwrap().to_owned())).collect()
}

fn audio(channel: Value) -> Value {
    json!({"device": "Dante Virtual Soundcard", "channel": channel})
}

#[test]
fn it_adds_nothing_for_an_input_that_is_not_dante() {
    assert!(steps(&json!({"device": "Built-in Mic"}), &[], &here()).is_empty());
}

#[test]
fn a_step_is_cleaned_like_core_cleans_one() {
    let s = step("id", &"t".repeat(100), "weird", &format!("a\u{7}b{}", "x".repeat(300)), "");
    assert_eq!((s["title"].as_str().unwrap().len(), s["state"].clone(), s["checked"].as_str().unwrap().len()), (60, json!("unknown"), 200), "an unknown state is unknown, text is cut and control characters dropped");
    assert!(!s["checked"].as_str().unwrap().contains('\u{7}'));
}

#[test]
fn the_walk_stops_at_the_nearest_fault() {
    let none = steps(&audio(json!(0)), &[], &here());
    assert_eq!(states(&none), [("dante_here".to_owned(), "fail".to_owned())], "no Dante device on this computer");

    let unreadable = steps(&audio(json!(0)), &[mine(Value::Null)], &here());
    assert_eq!(states(&unreadable), [("dante_here".to_owned(), "ok".to_owned()), ("dante_sub".into(), "unknown".into())], "unreadable subscriptions are unknown, not a guess");

    let nothing = steps(&audio(json!(0)), &[mine(json!([{"channel": 1, "status": "none", "source": ""}]))], &here());
    assert_eq!(states(&nothing).last().unwrap(), &("dante_sub".to_owned(), "fail".to_owned()), "nothing is subscribed to the channel Relay listens to");
    assert!(nothing[1]["checked"].as_str().unwrap().contains("receive channel 1"));

    let broken = steps(&audio(json!(0)), &[mine(json!([{"channel": 1, "status": "unresolved", "source": "Lead Vox@STAGE"}]))], &here());
    assert!(broken[1]["checked"].as_str().unwrap().contains("cannot find Lead Vox@STAGE"), "{broken:?}");

    let ok = json!([{"channel": 1, "status": "ok", "source": "Lead Vox@STAGE"}]);
    let gone = steps(&audio(json!(0)), &[mine(ok.clone())], &here());
    assert_eq!(states(&gone).last().unwrap(), &("dante_source".to_owned(), "fail".to_owned()), "the device it listens to is not on the network");
    assert_eq!(gone.last().unwrap()["checked"], json!("Relay cannot see STAGE."));

    let silent = steps(&audio(json!(0)), &[mine(ok.clone()), stage(false, true)], &here());
    assert_eq!(silent.last().unwrap()["checked"], json!("STAGE does not answer."));

    let good = steps(&audio(json!(0)), &[mine(ok.clone()), stage(true, true)], &here());
    assert_eq!(states(&good).last().unwrap(), &("dante_clock".to_owned(), "ok".to_owned()), "every step passes");
    let unlocked = steps(&audio(json!(0)), &[mine(ok.clone()), stage(true, false)], &here());
    assert_eq!(unlocked.last().unwrap()["checked"], json!("STAGE's clock is not locked."));
    let no_clock = steps(&audio(Value::Null), &[mine(ok), json!({"name": "STAGE", "ips": ["10.9.0.21"], "subscriptions": [], "online": true, "clock": null})], &here());
    assert_eq!(states(&no_clock).last().unwrap(), &("dante_clock".to_owned(), "unknown".to_owned()), "no device reports its clock: unknown, and no channel means every receive channel is looked at");
}
