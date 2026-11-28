//! The Dante steps of the "No sound?" walk (#263), from what monitoring (#258) already knows (`walk.py`). Sends nothing.
//!
//! Core asks `nosound.dante` with the input's status. When the input is not a Dante sound card this adds nothing. When it is, it walks upstream from this
//! computer's Dante device: is it there, is the channel that feeds Relay subscribed, is the device it listens to on the network, and is the clock locked.
//! A reading Relay does not have is "unknown", never a guess.

use std::collections::BTreeSet;

use serde_json::{json, Value};

use crate::inputs::kind_of;

pub const TEXT_MAX: usize = 200;
pub const TITLE_MAX: usize = 60;

fn clean(text: &str, limit: usize) -> String {
    text.chars().filter(|c| !c.is_control()).collect::<String>().trim().chars().take(limit).collect()
}

/// One step of the walk (`nosound.step`): `checked` says what Relay looked at and found; `look` what the person should do next (shown when it failed).
pub fn step(id: &str, title: &str, state: &str, checked: &str, look: &str) -> Value {
    let state = if ["ok", "fail", "unknown", "skipped"].contains(&state) { state } else { "unknown" };
    json!({"id": clean(id, 40), "title": clean(title, TITLE_MAX), "state": state, "checked": clean(checked, TEXT_MAX), "look": clean(look, TEXT_MAX)})
}

fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}

fn local<'a>(devices: &'a [Value], here: &BTreeSet<String>) -> Option<&'a Value> {
    devices.iter().find(|d| d["ips"].as_array().into_iter().flatten().filter_map(Value::as_str).any(|ip| here.contains(ip)))
}

/// The device of a `<channel>@<device>` source.
fn source_device(source: &str) -> &str {
    source.rsplit_once('@').map_or(source, |(_, d)| d).trim()
}

pub fn steps(audio: &Value, devices: &[Value], here: &BTreeSet<String>) -> Vec<Value> {
    if kind_of(text(&audio["device"])).is_empty() {
        return Vec::new();
    }
    let Some(mine) = local(devices, here) else {
        return vec![step("dante_here", "Dante on this computer", "fail", "Relay cannot see a Dante device on this computer.", "Open Dante Virtual Soundcard (or Dante Via) on the computer running Relay and check it is started.")];
    };
    let mine_name = text(&mine["name"]);
    let mut out = vec![step("dante_here", "Dante on this computer", "ok", &format!("{mine_name} is on the network."), "")];
    let Some(subs) = mine["subscriptions"].as_array() else {
        out.push(step("dante_sub", "What feeds Relay", "unknown", "Relay could not read this computer's Dante subscriptions.", "Open Dante Controller and check that the channel Relay listens to is subscribed."));
        return out;
    };
    let channel = audio["channel"].as_i64().filter(|_| !audio["channel"].is_boolean());
    let wanted: Vec<&Value> = match channel {
        Some(c) => subs.iter().filter(|s| s["channel"].as_i64() == Some(c + 1)).take(1).collect(),
        None => subs.iter().collect(),
    };
    let subscribed: Vec<&Value> = wanted.into_iter().filter(|s| s["status"] != json!("none")).collect();
    let label = channel.map_or_else(|| "its receive channels".to_owned(), |c| format!("receive channel {}", c + 1));
    if subscribed.is_empty() {
        out.push(step("dante_sub", "What feeds Relay", "fail", &format!("{mine_name} {label} has nothing subscribed to it."), "In Dante Controller, subscribe that channel to the microphone or desk channel it should hear."));
        return out;
    }
    if let Some(broken) = subscribed.iter().find(|s| s["status"] != json!("ok")) {
        let source = if text(&broken["source"]).is_empty() { "its source" } else { text(&broken["source"]) };
        out.push(step("dante_sub", "What feeds Relay", "fail", &format!("{mine_name} receive channel {} cannot find {source}.", broken["channel"]), "Check that device is switched on and connected, or subscribe the channel again."));
        return out;
    }
    let sources: Vec<&str> = subscribed.iter().take(3).map(|s| text(&s["source"])).collect();
    out.push(step("dante_sub", "What feeds Relay", "ok", &format!("Subscribed to {}.", sources.join(", ")), ""));
    for s in subscribed.iter().take(3) {
        let name = source_device(text(&s["source"]));
        let title = format!("{name} is on the network");
        let src = devices.iter().find(|d| text(&d["name"]).to_lowercase() == name.to_lowercase());
        let Some(src) = src else {
            out.push(step("dante_source", &title, "fail", &format!("Relay cannot see {name}."), &format!("Check {name} is switched on and its network cable is in.")));
            return out;
        };
        if src["online"] != json!(true) {
            out.push(step("dante_source", &title, "fail", &format!("{name} does not answer."), &format!("Check {name} is switched on and its network cable is in.")));
            return out;
        }
        out.push(step("dante_source", &title, "ok", &format!("{name} answers."), ""));
    }
    if let Some(unlocked) = devices.iter().find(|d| d["clock"].is_object() && d["clock"]["locked"] != json!(true)) {
        out.push(step("dante_clock", "Dante clock", "fail", &format!("{}'s clock is not locked.", text(&unlocked["name"])), "Open Dante Controller and check the clock leader is on and every device follows it."));
    } else if devices.iter().any(|d| d["clock"].is_object()) {
        out.push(step("dante_clock", "Dante clock", "ok", "Every device that reports its clock is locked.", ""));
    } else {
        out.push(step("dante_clock", "Dante clock", "unknown", "Relay could not read any device's clock.", ""));
    }
    out
}
