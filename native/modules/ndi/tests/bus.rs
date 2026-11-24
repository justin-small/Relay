use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use relay_core::lanes::LaneBus;
use relay_module_ndi::bus::{sink_name, Driver, Guard, Listen, Timing, LOST_SENTENCE};
use relay_module_ndi::outputs::{AudioOut, Frames, Maker, Sink};
use relay_module_ndi::pool::OnAudio;
use relay_ndi::recv::Audio;
use serde_json::{json, Value};

/// input id, channels, rate, interleaved samples.
type Pushed = (String, usize, u32, Vec<f32>);

#[derive(Default)]
struct FakeBus {
    cfg: Mutex<Value>,
    running: AtomicBool,
    pushed: Mutex<Vec<Pushed>>,
    status: Mutex<Option<(Value, Value)>>,
    pulls: AtomicUsize,
}

impl LaneBus for FakeBus {
    fn config(&self) -> Value {
        self.cfg.lock().expect("lock").clone()
    }
    fn running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }
    fn push(&self, id: &str, data: &[f32], channels: usize, rate: u32) -> bool {
        self.pushed.lock().expect("lock").push((id.to_owned(), channels, rate, data.to_vec()));
        true
    }
    fn pull(&self, _id: &str, frames: usize) -> Option<Vec<f32>> {
        self.pulls.fetch_add(1, Ordering::SeqCst);
        Some(vec![0.5; frames * 2])
    }
    fn set_status(&self, _driver: &str, inputs: Value, outputs: Value) {
        *self.status.lock().expect("lock") = Some((inputs, outputs));
    }
}

fn bus(cfg: Value) -> Arc<FakeBus> {
    let b = Arc::new(FakeBus::default());
    *b.cfg.lock().expect("lock") = cfg;
    b.running.store(true, Ordering::SeqCst);
    b
}

#[derive(Default)]
struct Sources {
    callbacks: Mutex<HashMap<String, OnAudio>>,
    dropped: AtomicUsize,
    refuse: AtomicBool,
}

struct Held(Arc<Sources>, String);

impl Drop for Held {
    fn drop(&mut self) {
        self.0.callbacks.lock().expect("lock").remove(&self.1);
        self.0.dropped.fetch_add(1, Ordering::SeqCst);
    }
}

fn listen(s: &Arc<Sources>) -> Arc<Listen> {
    let s = Arc::clone(s);
    Arc::new(move |source: &str, cb: OnAudio| -> Option<Guard> {
        if s.refuse.load(Ordering::SeqCst) {
            return None;
        }
        s.callbacks.lock().expect("lock").insert(source.to_owned(), cb);
        Some(Box::new(Held(Arc::clone(&s), source.to_owned())))
    })
}

fn chunk(ch: usize, n: usize) -> Audio {
    // planar: channel c holds the value c + 1, with the first sample of each channel marked
    let mut data = Vec::new();
    for c in 0..ch {
        for i in 0..n {
            data.push((c + 1) as f32 + if i == 0 { 0.5 } else { 0.0 });
        }
    }
    Audio { rate: 44100, channels: ch, samples: n, data, peak_db: -6.0, levels: vec![], timecode: i64::MAX, timestamp: 0 }
}

fn deliver(s: &Sources, source: &str, a: &Audio) {
    let cb = s.callbacks.lock().expect("lock").get(source).cloned().expect("listening");
    cb(a);
}

struct Out(Arc<Log>);

#[derive(Default)]
struct Log {
    names: Mutex<Vec<(String, usize, u32)>>,
    frames: AtomicUsize,
    blocks: Mutex<Vec<usize>>,
    closed: AtomicUsize,
    refuse: AtomicBool,
}

impl AudioOut for Out {
    fn rate(&self) -> u32 {
        48000
    }
    fn free(&self) -> usize {
        9600
    }
    fn queued(&self) -> usize {
        0
    }
    fn write(&self, block: &[f32], frames: usize) -> usize {
        self.0.frames.fetch_add(frames, Ordering::SeqCst);
        self.0.blocks.lock().expect("lock").push(block.len());
        frames
    }
    fn underruns(&self) -> u64 {
        2
    }
    fn close(&self) {
        self.0.closed.fetch_add(1, Ordering::SeqCst);
    }
}

struct M(Arc<Log>);

impl Maker for M {
    fn frames<'a>(&'a self, _: &'a str, _: usize, _: usize, _: bool) -> relay_core::module::BoxFuture<'a, Result<Box<dyn Frames>, String>> {
        Box::pin(async { Err("no".to_owned()) })
    }
    fn sink(&self, _: &str, _: usize, _: usize, _: f64) -> Option<Arc<dyn Sink>> {
        None
    }
    fn available(&self) -> String {
        String::new()
    }
    fn audio_out(&self, name: &str, channels: usize, rate: u32) -> Option<Arc<dyn AudioOut>> {
        if self.0.refuse.load(Ordering::SeqCst) {
            return None;
        }
        self.0.names.lock().expect("lock").push((name.to_owned(), channels, rate));
        Some(Arc::new(Out(Arc::clone(&self.0))))
    }
}

fn fast() -> Timing {
    Timing { first_wait: Duration::from_millis(150), lost_after: Duration::from_millis(150), retry_every: Duration::from_millis(100) }
}

fn wait(what: &str, mut f: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(5);
    while !f() {
        assert!(Instant::now() < end, "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(5)); // allow-sleep: polls a counter a thread bumps
    }
}

fn input_state(b: &FakeBus, id: &str) -> String {
    b.status.lock().expect("lock").as_ref().and_then(|s| s.0[id]["state"].as_str().map(str::to_owned)).unwrap_or_default()
}

/// Open the input on a thread (it waits for the first sound) and give it that sound once it is listening.
fn open_with_sound(d: &Arc<Driver>, s: &Arc<Sources>, source: &str, a: &Audio) {
    let d2 = Arc::clone(d);
    let t = std::thread::spawn(move || d2.reconcile());
    wait("the driver to listen", || s.callbacks.lock().expect("lock").contains_key(source));
    deliver(s, source, a);
    t.join().expect("reconcile");
}

#[test]
fn a_patched_ndi_source_becomes_lanes() {
    let b = bus(json!({"lanes": [{"lane": 3, "source": "ndi", "device": "MIC (Desk)"}, {"lane": 4, "source": "ndi", "device": "mic (desk)"}, {"lane": 1, "source": "portaudio", "device": "x"}], "outputs": []}));
    let s = Arc::new(Sources::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::default())), listen(&s), fast());
    open_with_sound(&d, &s, "MIC (Desk)", &chunk(2, 4));
    assert_eq!(d.input_states().get("ndi:mic (desk)"), Some(&"open"), "two lanes of one source share one input; a sound card is not ours");
    let (id, ch, rate, data) = b.pushed.lock().expect("lock")[0].clone();
    assert_eq!((id.as_str(), ch, rate), ("ndi:mic (desk)", 2, 44100));
    assert_eq!(&data[..4], &[1.5, 2.5, 1.0, 2.0], "planar sound reaches the bus interleaved: L0 R0 L1 R1");
    let st = b.status.lock().expect("lock").clone().expect("status");
    assert_eq!((st.0["ndi:mic (desk)"]["state"].clone(), st.0["ndi:mic (desk)"]["channels"].clone(), st.0["ndi:mic (desk)"]["lanes"].clone()), (json!("open"), json!(2), json!([3, 4])));
    d.stop();
    assert_eq!(s.dropped.load(Ordering::SeqCst), 1, "stopping gives the receiver back");
}

#[test]
fn a_chunk_with_other_channels_keeps_the_count_the_first_one_set() {
    let b = bus(json!({"lanes": [{"lane": 1, "source": "ndi", "device": "S"}], "outputs": []}));
    let s = Arc::new(Sources::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::default())), listen(&s), fast());
    open_with_sound(&d, &s, "S", &chunk(2, 2));
    deliver(&s, "S", &chunk(1, 2));
    deliver(&s, "S", &chunk(4, 2));
    let pushed = b.pushed.lock().expect("lock").clone();
    assert!(pushed.iter().all(|p| p.1 == 2 && p.3.len() == 4), "always two channels");
    assert_eq!(pushed[1].3, vec![1.5, 0.0, 1.0, 0.0], "a missing channel is silent");
    d.stop();
}

#[test]
fn a_source_that_sends_no_sound_says_so_and_is_tried_again() {
    let b = bus(json!({"lanes": [{"lane": 1, "source": "ndi", "device": "S"}], "outputs": []}));
    let s = Arc::new(Sources::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::default())), listen(&s), fast());
    d.reconcile();
    assert_eq!(input_state(&b, "ndi:s"), "waiting");
    let err = b.status.lock().expect("lock").as_ref().and_then(|x| x.0["ndi:s"]["error"].as_str().map(str::to_owned));
    assert_eq!(err.as_deref(), Some("That NDI source is not sending any sound."));
    assert_eq!(s.dropped.load(Ordering::SeqCst), 1, "the receiver was given back");
    std::thread::sleep(Duration::from_millis(120)); // allow-sleep: lets the retry time pass
    open_with_sound(&d, &s, "S", &chunk(2, 2));
    assert_eq!(input_state(&b, "ndi:s"), "open", "it came back");
    d.stop();
}

#[test]
fn an_input_that_goes_quiet_is_lost_then_reconnected() {
    let b = bus(json!({"lanes": [{"lane": 1, "source": "ndi", "device": "S"}], "outputs": []}));
    let s = Arc::new(Sources::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::default())), listen(&s), fast());
    open_with_sound(&d, &s, "S", &chunk(2, 2));
    std::thread::sleep(Duration::from_millis(200)); // allow-sleep: no sound for longer than lost_after
    d.reconcile();
    let err = b.status.lock().expect("lock").as_ref().and_then(|x| x.0["ndi:s"]["error"].as_str().map(str::to_owned));
    assert_eq!((input_state(&b, "ndi:s").as_str(), err.as_deref()), ("waiting", Some(LOST_SENTENCE)));
    std::thread::sleep(Duration::from_millis(120)); // allow-sleep: lets the retry time pass
    open_with_sound(&d, &s, "S", &chunk(2, 2));
    assert_eq!(input_state(&b, "ndi:s"), "open");
    d.stop();
}

#[test]
fn an_unpatched_or_unnamed_input_is_closed_and_a_missing_ndi_says_so() {
    let b = bus(json!({"lanes": [{"lane": 1, "source": "ndi", "device": "  "}], "outputs": []}));
    let s = Arc::new(Sources::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::default())), listen(&s), fast());
    d.reconcile();
    let err = |b: &FakeBus, id: &str| b.status.lock().expect("lock").as_ref().and_then(|x| x.0[id]["error"].as_str().map(str::to_owned));
    assert_eq!(err(&b, "ndi:").as_deref(), Some("Name the NDI source to take sound from."));
    *b.cfg.lock().expect("lock") = json!({"lanes": [{"lane": 1, "source": "ndi", "device": "S"}], "outputs": []});
    s.refuse.store(true, Ordering::SeqCst);
    d.reconcile();
    assert_eq!(err(&b, "ndi:s").as_deref(), Some("NDI is not running, or cannot receive that source."));
    s.refuse.store(false, Ordering::SeqCst);
    std::thread::sleep(Duration::from_millis(120)); // allow-sleep: lets the retry time pass
    open_with_sound(&d, &s, "S", &chunk(2, 2));
    *b.cfg.lock().expect("lock") = json!({"lanes": [], "outputs": []});
    d.reconcile();
    assert!(d.input_states().is_empty(), "nothing patched, nothing held");
    assert_eq!(s.dropped.load(Ordering::SeqCst), 1);
}

#[test]
fn a_stopped_bus_closes_everything() {
    let b = bus(json!({"lanes": [{"lane": 1, "source": "ndi", "device": "S"}], "outputs": []}));
    let s = Arc::new(Sources::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::default())), listen(&s), fast());
    open_with_sound(&d, &s, "S", &chunk(2, 2));
    b.running.store(false, Ordering::SeqCst);
    d.reconcile();
    assert!(d.input_states().is_empty());
    assert_eq!(s.dropped.load(Ordering::SeqCst), 1);
}

fn out_cfg(device: &str, channels: u64) -> Value {
    json!({"lanes": [], "outputs": [{"id": "o1", "output": "ndi", "device": device, "channels": channels, "latency_ms": 60}, {"id": "o2", "output": "portaudio", "device": "x", "channels": 2, "latency_ms": 60}]})
}

#[test]
fn lanes_routed_to_an_ndi_output_are_sent_as_sound() {
    assert_eq!(sink_name(" Spanish   mix "), "Relay - Spanish mix");
    assert_eq!(sink_name(""), "Relay - Sound");
    let b = bus(out_cfg("Spanish", 2));
    let log = Arc::new(Log::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::clone(&log))), listen(&Arc::default()), fast());
    d.reconcile();
    assert_eq!(log.names.lock().expect("lock").as_slice(), [("Relay - Spanish".to_owned(), 2, 48000)], "only the ndi output is ours, at 48 kHz");
    assert_eq!(d.output_ids(), ["o1".to_owned()]);
    wait("sound to be written", || log.frames.load(Ordering::SeqCst) >= 1920);
    assert!(log.blocks.lock().expect("lock").iter().all(|n| n % 2 == 0 && *n <= 960 * 2), "whole stereo frames, at most 20 ms at a time");
    assert!(b.pulls.load(Ordering::SeqCst) > 0);
    d.report();
    let st = b.status.lock().expect("lock").clone().expect("status");
    assert_eq!((st.1["o1"]["state"].clone(), st.1["o1"]["underruns"].clone()), (json!("open"), json!(2)));
    d.stop();
    assert_eq!(log.closed.load(Ordering::SeqCst), 1);
}

#[test]
fn changing_an_output_restarts_it_and_removing_it_closes_it() {
    let b = bus(out_cfg("A", 2));
    let log = Arc::new(Log::default());
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::clone(&log))), listen(&Arc::default()), fast());
    d.reconcile();
    d.reconcile();
    assert_eq!(log.names.lock().expect("lock").len(), 1, "the same output keeps running");
    *b.cfg.lock().expect("lock") = out_cfg("A", 8);
    d.reconcile();
    assert_eq!(log.names.lock().expect("lock").iter().map(|n| n.1).collect::<Vec<_>>(), [2, 8]);
    assert_eq!(log.closed.load(Ordering::SeqCst), 1, "the old one was closed");
    *b.cfg.lock().expect("lock") = json!({"lanes": [], "outputs": []});
    d.reconcile();
    assert_eq!(log.closed.load(Ordering::SeqCst), 2);
    assert!(d.output_ids().is_empty());
}

#[test]
fn an_output_ndi_will_not_create_waits_and_is_tried_again() {
    let b = bus(out_cfg("A", 2));
    let log = Arc::new(Log::default());
    log.refuse.store(true, Ordering::SeqCst);
    let d = Driver::with_timing(b.clone(), Arc::new(M(Arc::clone(&log))), listen(&Arc::default()), fast());
    d.reconcile();
    let st = b.status.lock().expect("lock").clone().expect("status");
    assert_eq!((st.1["o1"]["state"].clone(), st.1["o1"]["error"].clone()), (json!("waiting"), json!("NDI would not create that sound output.")));
    log.refuse.store(false, Ordering::SeqCst);
    std::thread::sleep(Duration::from_millis(120)); // allow-sleep: lets the retry time pass
    d.reconcile();
    assert_eq!(b.status.lock().expect("lock").clone().expect("status").1["o1"]["state"], "open");
    wait("sound to flow", || log.frames.load(Ordering::SeqCst) > 0);
    d.stop();
}
