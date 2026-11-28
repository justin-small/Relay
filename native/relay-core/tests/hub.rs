//! What the hub promises (#656), from `app/hub.py`: the frame format and sequence ids, the late-joiner snapshot, resume on reconnect,
//! the finals-only feed, a slow viewer dropped, the blocklist hook and the status stream.

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use relay_core::hub::{sse_frame, Hub, LineEvent, Redact, MAX_QUEUE, RESYNC_EVERY};
use serde_json::{json, Value};

/// A clock the test winds by hand, so every frame's `ts` is known.
fn hub_with_clock() -> (Arc<Hub>, Arc<AtomicU64>) {
    let ticks = Arc::new(AtomicU64::new(1_000));
    let t = Arc::clone(&ticks);
    let hub = Hub::with(Box::new(|_| Box::new(relay_core::hub::PassThrough)), Box::new(move || t.fetch_add(1, Ordering::SeqCst) as f64));
    (hub, ticks)
}

fn frames(sub: &mut relay_core::hub::Subscription) -> Vec<String> {
    let mut out = Vec::new();
    while let Ok(f) = sub.try_recv() {
        out.push(String::from_utf8(f.to_vec()).unwrap());
    }
    out
}

fn data(frame: &str) -> Value {
    let line = frame.lines().find(|l| l.starts_with("data: ")).unwrap();
    serde_json::from_str(&line["data: ".len()..]).unwrap()
}

fn id(frame: &str) -> Option<u64> {
    frame.lines().find_map(|l| l.strip_prefix("id: ")).map(|n| n.parse().unwrap())
}

#[test]
fn a_frame_is_id_then_data_then_a_blank_line() {
    assert_eq!(&sse_frame(&json!({"a": 1}), Some(7))[..], b"id: 7\ndata: {\"a\":1}\n\n");
    assert_eq!(&sse_frame(&json!({"a": 1}), None)[..], b"data: {\"a\":1}\n\n");
    assert_eq!(&sse_frame(&json!({"a": 1}), Some(0))[..], b"data: {\"a\":1}\n\n", "a zero seq has no id line");
    assert_eq!(&sse_frame(&json!({"t": "niño ✓"}), None)[..], "data: {\"t\":\"niño ✓\"}\n\n".as_bytes(), "UTF-8, not escapes");
}

#[test]
fn a_late_viewer_gets_a_snapshot_and_then_the_live_frames() {
    let (hub, _) = hub_with_clock();
    hub.publish_delta("translation", "SPANISH", "Hola ");
    hub.publish_delta("translation", "SPANISH", "amigos");
    assert_eq!(hub.publish_final("translation", "SPANISH", None), "Hola amigos");
    hub.publish_delta("translation", "SPANISH", "Buenos");
    let mut sub = hub.subscribe("translation", "SPANISH", None, false);
    let got = frames(&mut sub);
    assert_eq!(got.len(), 1);
    assert_eq!(id(&got[0]), None, "a snapshot has no id");
    let snap = data(&got[0]);
    assert_eq!(snap["type"], "snapshot");
    assert_eq!((snap["stream"].clone(), snap["lang"].clone()), (json!("translation"), json!("SPANISH")));
    assert_eq!(snap["lines"].as_array().unwrap().len(), 1);
    assert_eq!(snap["lines"][0]["text"], "Hola amigos");
    assert_eq!(snap["open"], "Buenos");

    hub.publish_delta("translation", "SPANISH", " dias");
    let live = frames(&mut sub);
    assert_eq!(live.len(), 1);
    let d = data(&live[0]);
    assert_eq!((d["delta"].clone(), d["final"].clone()), (json!(" dias"), json!(false)));
    assert_eq!(id(&live[0]), d["seq"].as_u64(), "the id is the seq");
    assert!(d.get("text").is_none(), "no full text on an ordinary delta");
}

#[test]
fn sequence_ids_rise_across_channels_and_a_final_carries_the_text() {
    let (hub, _) = hub_with_clock();
    let mut es = hub.subscribe("translation", "SPANISH", None, false);
    let mut fr = hub.subscribe("translation", "FRENCH", None, false);
    frames(&mut es);
    frames(&mut fr);
    hub.publish_delta("translation", "SPANISH", "uno");
    hub.publish_delta("translation", "FRENCH", "un");
    hub.publish_final("translation", "SPANISH", None);
    let a = frames(&mut es);
    let b = frames(&mut fr);
    assert_eq!((id(&a[0]), id(&b[0]), id(&a[1])), (Some(1), Some(2), Some(3)));
    let fin = data(&a[1]);
    assert_eq!((fin["final"].clone(), fin["delta"].clone(), fin["text"].clone()), (json!(true), json!(""), json!("uno")));
    assert_eq!(
        a[1],
        "id: 3\ndata: {\"stream\":\"translation\",\"lang\":\"SPANISH\",\"seq\":3,\"delta\":\"\",\"text\":\"uno\",\"final\":true,\"ts\":1004.0}\n\n",
        "the wire shape, byte for byte"
    );
}

#[test]
fn the_whole_open_text_rides_every_twentieth_delta() {
    let (hub, _) = hub_with_clock();
    let mut sub = hub.subscribe("transcription", "ENGLISH", None, false);
    frames(&mut sub);
    for i in 1..=RESYNC_EVERY * 2 {
        hub.publish_delta("transcription", "ENGLISH", "a");
        let f = frames(&mut sub);
        let has_text = data(&f[0]).get("text").is_some();
        assert_eq!(has_text, i.is_multiple_of(RESYNC_EVERY), "delta {i}");
    }
}

#[test]
fn an_empty_delta_and_an_empty_final_publish_nothing() {
    let (hub, _) = hub_with_clock();
    let mut sub = hub.subscribe("translation", "SPANISH", None, false);
    frames(&mut sub);
    hub.publish_delta("translation", "SPANISH", "");
    assert_eq!(hub.publish_final("translation", "SPANISH", None), "");
    assert_eq!(hub.publish_final("translation", "SPANISH", Some("   ")), "");
    assert!(frames(&mut sub).is_empty());
    assert_eq!(hub.publish_final("translation", "SPANISH", Some("  settled  ")), "settled", "the settled text wins and is trimmed");
}

#[test]
fn history_keeps_only_the_newest_lines() {
    let (hub, _) = hub_with_clock();
    hub.set_history_lines(3);
    for n in 1..=5 {
        hub.publish_delta("translation", "SPANISH", &format!("line {n}"));
        hub.publish_final("translation", "SPANISH", None);
    }
    let mut sub = hub.subscribe("translation", "SPANISH", None, false);
    let snap = data(&frames(&mut sub)[0]);
    let texts: Vec<&str> = snap["lines"].as_array().unwrap().iter().map(|l| l["text"].as_str().unwrap()).collect();
    assert_eq!(texts, ["line 3", "line 4", "line 5"]);
}

#[test]
fn a_reconnect_replays_only_what_was_missed() {
    let (hub, _) = hub_with_clock();
    hub.publish_delta("translation", "SPANISH", "uno");
    hub.publish_final("translation", "SPANISH", None); // seq 2
    hub.publish_delta("translation", "SPANISH", "dos");
    hub.publish_final("translation", "SPANISH", None); // seq 4
    hub.publish_delta("translation", "SPANISH", "tres"); // seq 5, still open
    let mut sub = hub.subscribe("translation", "SPANISH", Some(2), false);
    let got = frames(&mut sub);
    assert_eq!(got.iter().map(|f| id(f)).collect::<Vec<_>>(), [Some(4), Some(5)], "the line after 2, then the open segment");
    assert_eq!((data(&got[0])["final"].clone(), data(&got[0])["text"].clone()), (json!(true), json!("dos")));
    assert_eq!((data(&got[1])["final"].clone(), data(&got[1])["text"].clone()), (json!(false), json!("tres")));
    // caught up: nothing to replay, and not a snapshot either
    let mut caught = hub.subscribe("translation", "SPANISH", Some(5), false);
    assert!(frames(&mut caught).is_empty());
}

#[test]
fn a_resume_that_cannot_be_trusted_gets_a_snapshot() {
    let (hub, _) = hub_with_clock();
    hub.set_history_lines(2);
    for n in 1..=4 {
        hub.publish_delta("translation", "SPANISH", &format!("l{n}"));
        hub.publish_final("translation", "SPANISH", None); // seqs 2, 4, 6, 8
    }
    // fallen further behind than the ring buffer holds (it starts at seq 6)
    let mut behind = hub.subscribe("translation", "SPANISH", Some(2), false);
    assert_eq!(data(&frames(&mut behind)[0])["type"], "snapshot");
    // from a server that restarted: its id is ahead of anything this hub has issued
    let mut ahead = hub.subscribe("translation", "SPANISH", Some(9_999), false);
    assert_eq!(data(&frames(&mut ahead)[0])["type"], "snapshot");
    // from before the last reset
    hub.reset("translation", "SPANISH");
    let mut reset = hub.subscribe("translation", "SPANISH", Some(8), false);
    let got = frames(&mut reset);
    assert_eq!(data(&got[0])["type"], "snapshot");
    assert_eq!(data(&got[0])["lines"], json!([]));
    // from after it, replay works again
    hub.publish_delta("translation", "SPANISH", "new");
    hub.publish_final("translation", "SPANISH", None); // seqs 9, 10
    let mut after = hub.subscribe("translation", "SPANISH", Some(9), false);
    assert_eq!(frames(&mut after).iter().map(|f| id(f)).collect::<Vec<_>>(), [Some(10)]);
}

#[test]
fn a_reset_tells_every_viewer() {
    let (hub, _) = hub_with_clock();
    hub.publish_delta("translation", "SPANISH", "old");
    hub.publish_final("translation", "SPANISH", None);
    let mut all = hub.subscribe("translation", "SPANISH", None, false);
    let mut finals = hub.subscribe("translation", "SPANISH", None, true);
    frames(&mut all);
    frames(&mut finals);
    hub.reset("translation", "SPANISH");
    let a = frames(&mut all);
    assert_eq!((data(&a[0])["type"].clone(), data(&a[0])["lines"].clone(), data(&a[0])["open"].clone()), (json!("snapshot"), json!([]), json!("")));
    assert!(!frames(&mut finals).is_empty());
}

#[test]
fn the_finals_only_feed_skips_open_deltas() {
    let (hub, _) = hub_with_clock();
    let mut all = hub.subscribe("translation", "SPANISH", None, false);
    let mut finals = hub.subscribe("translation", "SPANISH", None, true);
    frames(&mut all);
    frames(&mut finals);
    hub.publish_delta("translation", "SPANISH", "he");
    hub.publish_delta("translation", "SPANISH", "llo");
    assert_eq!(frames(&mut all).len(), 2);
    assert!(frames(&mut finals).is_empty(), "a delta never reaches a finals-only viewer");
    hub.publish_final("translation", "SPANISH", None);
    assert_eq!(frames(&mut all).len(), 1);
    assert_eq!(frames(&mut finals).len(), 1);
    // and its snapshot shows no open text
    hub.publish_delta("translation", "SPANISH", "next");
    let mut late = hub.subscribe("translation", "SPANISH", None, true);
    assert_eq!(data(&frames(&mut late)[0])["open"], "");
}

#[test]
fn a_viewer_that_falls_behind_is_dropped_and_its_stream_ends() {
    let (hub, _) = hub_with_clock();
    let mut slow = hub.subscribe("translation", "SPANISH", None, false);
    let mut quick = hub.subscribe("translation", "SPANISH", None, false);
    frames(&mut quick);
    assert_eq!(hub.viewer_count(), 2);
    for _ in 0..MAX_QUEUE + 10 {
        hub.publish_delta("translation", "SPANISH", "x");
        frames(&mut quick);
    }
    assert_eq!(hub.viewer_count(), 1, "the slow one left");
    let mut got = 0;
    while slow.try_recv().is_ok() {
        got += 1;
    }
    assert!(got <= MAX_QUEUE + 1, "{got} frames queued");
    assert!(matches!(slow.try_recv(), Err(tokio::sync::mpsc::error::TryRecvError::Disconnected)), "the stream ends, so the browser reconnects");
    hub.publish_delta("translation", "SPANISH", "y");
    assert_eq!(frames(&mut quick).len(), 1, "the quick one carries on");
}

#[test]
fn a_dropped_subscription_unsubscribes() {
    let (hub, _) = hub_with_clock();
    let a = hub.subscribe("translation", "SPANISH", None, false);
    let b = hub.subscribe("transcription", "ENGLISH", None, true);
    assert_eq!(hub.viewer_count(), 2);
    drop(a);
    assert_eq!(hub.viewer_count(), 1);
    drop(b);
    assert_eq!(hub.viewer_count(), 0);
}

#[test]
fn a_control_message_reaches_everyone_without_an_id() {
    let (hub, _) = hub_with_clock();
    let mut es = hub.subscribe("translation", "SPANISH", None, false);
    let mut fr = hub.subscribe("translation", "FRENCH", None, true);
    frames(&mut es);
    frames(&mut fr);
    hub.broadcast_viewers(&json!({"type": "targets", "targets": ["SPANISH"]}));
    for sub in [&mut es, &mut fr] {
        let got = frames(sub);
        assert_eq!(got.len(), 1);
        assert_eq!(id(&got[0]), None);
        assert_eq!(data(&got[0])["type"], "targets");
    }
}

/// A small real blocklist: removes whole banned words, holding back a tail that could still grow into one.
struct Words {
    banned: Vec<String>,
    held: String,
}

impl Redact for Words {
    fn feed(&mut self, delta: &str) -> String {
        self.held.push_str(delta);
        let cut = self.held.rfind(' ').map(|i| i + 1).unwrap_or(0);
        let rest = self.held.split_off(cut);
        let ready = std::mem::replace(&mut self.held, rest);
        self.whole(&ready)
    }
    fn flush(&mut self) -> String {
        let rest = std::mem::take(&mut self.held);
        self.whole(&rest)
    }
    fn whole(&mut self, text: &str) -> String {
        text.split(' ').filter(|w| !self.banned.iter().any(|b| b.eq_ignore_ascii_case(w.trim_matches(|c: char| !c.is_alphanumeric())))).collect::<Vec<_>>().join(" ")
    }
    fn reset(&mut self) {
        self.held.clear();
    }
    fn set_terms(&mut self, terms: &[String]) {
        self.banned = terms.to_vec();
    }
}

#[test]
fn the_blocklist_is_applied_before_any_viewer_sees_text() {
    let hub = Hub::with(
        Box::new(|terms| Box::new(Words { banned: terms.to_vec(), held: String::new() })),
        Box::new(|| 1.0),
    );
    let mut sub = hub.subscribe("translation", "SPANISH", None, false);
    frames(&mut sub);
    hub.set_blocklist(&["darn".to_owned()]);
    hub.publish_delta("translation", "SPANISH", "oh da");
    hub.publish_delta("translation", "SPANISH", "rn that ");
    let seen: String = frames(&mut sub).iter().map(|f| data(f)["delta"].as_str().unwrap().to_owned()).collect();
    assert!(!seen.contains("darn"), "{seen:?}");
    assert_eq!(hub.publish_final("translation", "SPANISH", Some("so darn good")), "so good", "the settled text is redacted whole");
    assert_eq!(hub.blocklist(), ["darn"]);
}

#[test]
fn line_sinks_hear_every_line_and_a_failing_one_costs_nothing() {
    let (hub, _) = hub_with_clock();
    let heard: Arc<Mutex<Vec<LineEvent>>> = Arc::default();
    let h = Arc::clone(&heard);
    hub.add_line_sink("recorder", Arc::new(move |e| h.lock().unwrap().push(e.clone())));
    hub.add_line_sink("broken", Arc::new(|_| panic!("a module's sender failed")));
    let mut sub = hub.subscribe("translation", "SPANISH", None, false);
    frames(&mut sub);
    hub.publish_delta("translation", "SPANISH", "hello"); // ts 1001: the segment starts here
    let line = hub.publish_final("translation", "SPANISH", None);
    assert_eq!(line, "hello");
    assert_eq!(frames(&mut sub).len(), 2, "viewers still got the line");
    let got = heard.lock().unwrap().clone();
    assert_eq!(got.len(), 1);
    assert_eq!((got[0].text.as_str(), got[0].seq, got[0].lang.as_str()), ("hello", 2, "SPANISH"));
    assert!(got[0].started < got[0].ended, "a line spans its first delta to its commit");
    hub.remove_line_sink("recorder");
    hub.publish_delta("translation", "SPANISH", "again");
    hub.publish_final("translation", "SPANISH", None);
    assert_eq!(heard.lock().unwrap().len(), 1, "a removed sink hears nothing more");
}

#[test]
fn a_line_sink_may_use_the_hub() {
    let (hub, _) = hub_with_clock();
    let inner = Arc::clone(&hub);
    hub.add_line_sink("count", Arc::new(move |_| {
        let _ = inner.viewer_count();
        inner.publish_delta("echo", "ENGLISH", "x");
    }));
    hub.publish_delta("translation", "SPANISH", "hi");
    hub.publish_final("translation", "SPANISH", None); // would deadlock if sinks ran under the hub's lock
}

#[test]
fn the_status_stream_gets_every_push_and_drops_a_full_queue() {
    let (hub, _) = hub_with_clock();
    let mut a = hub.subscribe_status();
    let mut b = hub.subscribe_status();
    assert_eq!(hub.status_count(), 2);
    hub.push_status(json!({"type": "status", "n": 1}));
    assert_eq!(a.try_recv().unwrap()["n"], 1);
    for n in 2..=40 {
        hub.push_status(json!({"type": "status", "n": n}));
        if n < 34 {
            a.try_recv().unwrap();
        }
    }
    assert_eq!(hub.status_count(), 1, "b never read, so its queue filled and it was dropped");
    assert!(b.try_recv().is_ok());
    drop(a);
    assert_eq!(hub.status_count(), 0);
}
