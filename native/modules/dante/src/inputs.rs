//! Dante inputs in the audio input picker (#264) (`inputs.py`).
//!
//! Core asks `audio.input_labels` with the input devices; this answers for the ones that are Dante sound cards (Dante Virtual Soundcard, Via, AVIO), and
//! for the one running on this computer it says which Dante channel feeds each of its inputs, read from that device's subscriptions (#258). Where Relay
//! cannot read them the feed is empty: "no reading", never a guess. Core never names this module.

use std::collections::BTreeSet;

use serde_json::{json, Map, Value};

pub const DANTE_WORDS: [&str; 3] = ["dante", "dvs", "avio"];
pub const MAX_FEED_CHANNELS: usize = 64;

/// `"Dante"` or `""` from a sound card's name.
pub fn kind_of(name: &str) -> &'static str {
    let n = name.to_lowercase();
    if DANTE_WORDS.iter().any(|w| n.contains(w)) { "Dante" } else { "" }
}

/// For each receive channel of the Dante device that runs on this computer, the source it is subscribed to (`<channel>@<device>`), or `""` when it has
/// none. Empty when no device here is known or readable. `here` is this computer's addresses.
pub fn local_feeds(devices: &[Value], here: &BTreeSet<String>) -> Vec<String> {
    for d in devices {
        let subs = d["subscriptions"].as_array().filter(|s| !s.is_empty());
        let on_this_computer = d["ips"].as_array().into_iter().flatten().filter_map(Value::as_str).any(|ip| here.contains(ip));
        if let (Some(subs), true) = (subs, on_this_computer) {
            let top = usize::try_from(subs.iter().filter_map(|s| s["channel"].as_i64()).max().unwrap_or(0)).unwrap_or(0).min(MAX_FEED_CHANNELS);
            let mut feeds = vec![String::new(); top];
            for s in subs {
                let ch = usize::try_from(s["channel"].as_i64().unwrap_or(0)).unwrap_or(0);
                if (1..=top).contains(&ch) && s["status"] != json!("none") {
                    feeds[ch - 1] = s["source"].as_str().unwrap_or("").to_owned();
                }
            }
            return feeds;
        }
    }
    Vec::new()
}

/// `{device index: {"kind", "feeds"}}` for the input devices that are Dante. `known` is the Dante devices Relay has found.
pub fn labels(inputs: &[Value], known: &[Value], here: &BTreeSet<String>) -> Value {
    let feeds = local_feeds(known, here);
    let mut out = Map::new();
    for dev in inputs {
        let kind = kind_of(dev["name"].as_str().unwrap_or(""));
        if !kind.is_empty() {
            out.insert(dev["index"].to_string(), json!({"kind": kind, "feeds": feeds}));
        }
    }
    Value::Object(out)
}
