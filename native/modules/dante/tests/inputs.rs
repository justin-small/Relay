//! Dante inputs named in the input picker (`tests/test_dante_inputs.py` ported): the name rules, the feeds from this computer's Dante device, and core's hook
//! with the Dante module on and off.

mod fake;

use std::collections::BTreeSet;

use fake::rig;
use relay_core::caps::{InputLabels, INPUT_LABELS};
use relay_core::Module;
use relay_module_dante::inputs::{kind_of, labels, local_feeds};
use serde_json::{json, Value};

fn dev(index: i64, name: &str) -> Value {
    json!({"index": index, "name": name, "label": format!("Core Audio: {name}"), "channels": 2, "hostapi": "x", "default_samplerate": 48000})
}

fn here() -> BTreeSet<String> {
    BTreeSet::from(["10.9.0.2".to_owned()])
}

fn subs() -> Value {
    json!([{"channel": 1, "status": "ok", "source": "Lead Vox@STAGE"}, {"channel": 2, "status": "none", "source": ""}, {"channel": 3, "status": "unresolved", "source": "Gone@BOX"}])
}

#[test]
fn names() {
    for (name, want) in [("Dante Virtual Soundcard (x64)", "Dante"), ("Dante Via", "Dante"), ("Dante AVIO USB", "Dante"), ("openaes67 Sound Card", ""), ("MacBook Pro Microphone", "")] {
        assert_eq!(kind_of(name), want, "{name}");
    }
}

#[test]
fn feeds_come_from_the_device_on_this_computer() {
    let mine = json!({"ips": ["10.9.0.2"], "subscriptions": subs()});
    let other = json!({"ips": ["203.0.113.9"], "subscriptions": subs()});
    assert_eq!(local_feeds(&[other.clone(), mine.clone()], &here()), ["Lead Vox@STAGE", "", "Gone@BOX"], "this computer's device");
    assert!(local_feeds(std::slice::from_ref(&other), &here()).is_empty(), "a device elsewhere is not it");
    assert!(local_feeds(&[json!({"ips": ["10.9.0.2"], "subscriptions": null})], &here()).is_empty(), "an unreadable device gives no feeds");
    assert!(local_feeds(&[json!({"ips": ["10.9.0.2"], "subscriptions": []})], &here()).is_empty(), "a device with no receive channels gives none");
    let out = labels(&[dev(0, "Dante Virtual Soundcard"), dev(1, "Built-in Line In"), dev(2, "Built-in Mic")], &[mine], &here());
    let kinds: Vec<(String, String)> = out.as_object().unwrap().iter().map(|(i, o)| (i.clone(), o["kind"].as_str().unwrap().to_owned())).collect();
    assert_eq!(kinds, [("0".to_owned(), "Dante".to_owned())], "only Dante is labelled");
    assert_eq!(out["0"]["feeds"], json!(["Lead Vox@STAGE", "", "Gone@BOX"]));
}

#[test]
fn feeds_are_capped_at_sixty_four_channels() {
    let many: Vec<Value> = (1..=100).map(|c| json!({"channel": c, "status": "ok", "source": format!("c{c}@X")})).collect();
    let feeds = local_feeds(&[json!({"ips": ["10.9.0.2"], "subscriptions": many})], &here());
    assert_eq!((feeds.len(), feeds[63].as_str()), (64, "c64@X"));
}

#[tokio::test]
async fn cores_hook_names_nothing_with_the_module_off_and_labels_with_it_on() {
    let r = rig().await;
    let devices = vec![dev(0, "Dante Virtual Soundcard"), dev(1, "Built-in Mic")];
    assert!(r.host.using::<InputLabels>(INPUT_LABELS).is_none(), "off: nothing is offered, so no kind");
    r.module.start(r.host.clone(), json!({})).await.unwrap();
    let hook = r.host.using::<InputLabels>(INPUT_LABELS).expect("on: the picker's hook is offered");
    let got = (hook.0)(&devices);
    assert_eq!(got["0"]["kind"], json!("Dante"));
    assert!(got.get("1").is_none(), "on: kinds, and only for the Dante card");
    assert_eq!(devices[0]["label"], json!("Core Audio: Dante Virtual Soundcard"), "the saved label is untouched");
    r.module.stop().await;
    assert!(r.host.using::<InputLabels>(INPUT_LABELS).is_none(), "off again");
}
