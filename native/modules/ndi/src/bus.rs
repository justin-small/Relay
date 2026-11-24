//! NDI sound on the 32-lane audio bus (`bus.py`, #301, #599): an NDI source's sound as bus lanes, and bus lanes as an NDI sound source.
//!
//! Everything Relay plays or hears goes through the bus, so NDI joins it the same way every network audio module does:
//!
//! * **Input** `ndi`: a lane patched to the NDI source named in `device` (as the NDI page lists it, "MACHINE (Source)"). All of the source's
//!   channels (up to 32) become lanes. The bus resamples whatever rate the sender uses.
//! * **Output** `ndi`: an audio-only NDI source named "Relay - <device>" that the lanes routed to it are written to, so a spoken-translation
//!   destination or any lane can be picked up by a mixer, a recorder or another Relay. It sends at 48 kHz.
//!
//! Neither opens a sound device. The receiver is the shared pool's (one per source however many listen), the sender is `relay-ndi`'s audio
//! sink, and the blocks cross to the engine through core's [`LaneBus`]. The driver looks at the bus's setting every half second, as the
//! Python bus's supervisor does: it opens what is patched, closes what is not, and reopens what was lost.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU32, AtomicU64, AtomicUsize, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use relay_core::lanes::{input_id, LaneBus};
use relay_ndi::recv::Audio;
use serde_json::{json, Value};

use crate::outputs::{AudioOut, Maker};
use crate::pool::OnAudio;
use crate::settings::squeeze;

pub const ID: &str = "ndi";
pub const MAX_CHANNELS: usize = 32;
pub const RATE: u32 = 48_000;
pub const PREFIX: &str = "Relay - ";
/// How long opening an input waits for the source's first sound.
pub const FIRST_WAIT: Duration = Duration::from_secs(4);
/// No sound for this long: the input is lost and the driver reconnects it.
pub const LOST_AFTER: Duration = Duration::from_secs(2);
pub const RETRY_EVERY: Duration = Duration::from_secs(2);
pub const TICK: Duration = Duration::from_millis(5);
/// The most an output produces in one go (20 ms).
pub const CHUNK_FRAMES: usize = 960;
pub const LOST_SENTENCE: &str = "The audio input stopped. Check the device is plugged in; Relay reconnects on its own.";
pub const OUT_LOST_SENTENCE: &str = "The audio output stopped. Check the device is plugged in; Relay reconnects on its own.";

/// What holds a source's receiver open: letting go of it gives the receiver back.
pub type Guard = Box<dyn Send + Sync>;
/// Hold `source`'s sound: call the callback with each chunk until the guard is dropped. `None` when it cannot be received.
pub type Listen = dyn Fn(&str, OnAudio) -> Option<Guard> + Send + Sync;

/// The NDI name of a sound output: "Relay - <device>".
pub fn sink_name(device: &str) -> String {
    let name: String = squeeze(device).chars().take(60).collect();
    format!("{PREFIX}{}", if name.is_empty() { "Sound" } else { &name })
}

// ------------------------------------------------------------------------------------------------- inputs
#[derive(Default)]
struct Flow {
    channels: AtomicUsize,
    rate: AtomicU32,
    blocks: AtomicU64,
    last: Mutex<Option<Instant>>,
    first: (Mutex<bool>, Condvar),
}

struct Intake {
    id: String,
    device: String,
    guard: Option<Guard>,
    flow: Arc<Flow>,
    error: Option<String>,
    next_try: Instant,
    lanes: Vec<i64>,
}

impl Intake {
    fn state(&self) -> &'static str {
        if self.guard.is_some() && self.error.is_none() {
            "open"
        } else if self.error.is_some() {
            "waiting"
        } else {
            "closed"
        }
    }
}

/// One chunk of sound (planar) as interleaved blocks of the channel count the first chunk set: a chunk with more channels loses the extra, one
/// with fewer leaves the rest silent.
fn interleave(a: &Audio, channels: usize) -> Vec<f32> {
    let n = a.samples;
    let mut out = vec![0.0f32; n * channels];
    for c in 0..channels.min(a.channels) {
        for (i, s) in a.data[c * n..(c + 1) * n].iter().enumerate() {
            out[i * channels + c] = *s;
        }
    }
    out
}

// ------------------------------------------------------------------------------------------------- outputs
struct OutRun {
    spec: Value,
    state: Arc<OutState>,
    stop: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

#[derive(Default)]
struct OutState {
    sink: Mutex<Option<Arc<dyn AudioOut>>>,
    error: Mutex<Option<String>>,
    next_try: Mutex<Option<Instant>>,
    underruns: AtomicU64,
}

/// How long the driver waits at each step; a test shortens them.
#[derive(Clone, Copy, Debug)]
pub struct Timing {
    pub first_wait: Duration,
    pub lost_after: Duration,
    pub retry_every: Duration,
}

impl Default for Timing {
    fn default() -> Self {
        Timing { first_wait: FIRST_WAIT, lost_after: LOST_AFTER, retry_every: RETRY_EVERY }
    }
}

pub struct Driver {
    timing: Timing,
    bus: Arc<dyn LaneBus>,
    maker: Arc<dyn Maker>,
    listen: Arc<Listen>,
    intakes: Mutex<HashMap<String, Intake>>,
    outputs: Mutex<HashMap<String, OutRun>>,
}

impl Driver {
    pub fn new(bus: Arc<dyn LaneBus>, maker: Arc<dyn Maker>, listen: Arc<Listen>) -> Arc<Driver> {
        Self::with_timing(bus, maker, listen, Timing::default())
    }

    pub fn with_timing(bus: Arc<dyn LaneBus>, maker: Arc<dyn Maker>, listen: Arc<Listen>, timing: Timing) -> Arc<Driver> {
        Arc::new(Driver { timing, bus, maker, listen, intakes: Mutex::default(), outputs: Mutex::default() })
    }

    /// Look at the bus's setting and make the inputs and outputs match it; reopen what was lost; say how they are doing. Blocking (opening an
    /// input waits for its first sound): run it on a worker thread.
    pub fn reconcile(self: &Arc<Self>) {
        let cfg = self.bus.config();
        if !self.bus.running() {
            self.close_all();
            self.report();
            return;
        }
        self.reconcile_inputs(&cfg);
        self.reconcile_outputs(&cfg);
        self.report();
    }

    fn reconcile_inputs(&self, cfg: &Value) {
        let mut wanted: HashMap<String, (String, Vec<i64>)> = HashMap::new();
        for spec in cfg["lanes"].as_array().into_iter().flatten().filter(|s| s["source"] == ID) {
            let device = spec["device"].as_str().unwrap_or("").to_owned();
            let e = wanted.entry(input_id(ID, &device)).or_insert_with(|| (device, Vec::new()));
            if let Some(l) = spec["lane"].as_i64() {
                e.1.push(l);
            }
        }
        let gone: Vec<Intake> = {
            let Ok(mut intakes) = self.intakes.lock() else { return };
            let ids: Vec<String> = intakes.keys().filter(|k| !wanted.contains_key(*k)).cloned().collect();
            ids.iter().filter_map(|k| intakes.remove(k)).collect()
        };
        drop(gone);
        let now = Instant::now();
        for (id, (device, lanes)) in wanted {
            let mut intake = {
                let Ok(mut intakes) = self.intakes.lock() else { return };
                intakes.remove(&id).unwrap_or_else(|| Intake { id: id.clone(), device: device.clone(), guard: None, flow: Arc::new(Flow::default()), error: None, next_try: now, lanes: vec![] })
            };
            intake.lanes = lanes;
            if intake.guard.is_some() {
                let last = intake.flow.last.lock().ok().and_then(|l| *l);
                if last.is_some_and(|l| now.duration_since(l) > self.timing.lost_after) {
                    tracing::warn!("Audio input {} lost; waiting for it to come back", intake.device);
                    intake.guard = None;
                    intake.flow = Arc::new(Flow::default());
                    intake.error = Some(LOST_SENTENCE.to_owned());
                    intake.next_try = now + self.timing.retry_every;
                }
            } else if intake.error.is_none() || now >= intake.next_try {
                let had = intake.error.clone();
                if !self.open(&mut intake) {
                    if had.as_deref() == Some(LOST_SENTENCE) {
                        intake.error = had;
                    }
                    intake.next_try = now + self.timing.retry_every;
                }
            }
            if let Ok(mut intakes) = self.intakes.lock() {
                intakes.insert(id, intake);
            }
        }
    }

    /// Open the source's sound: hold its receiver and wait for the first chunk. False (with `error` set) when it cannot.
    fn open(&self, intake: &mut Intake) -> bool {
        let device = intake.device.trim().to_owned();
        if device.is_empty() {
            intake.error = Some("Name the NDI source to take sound from.".to_owned());
            return false;
        }
        let flow = Arc::new(Flow::default());
        let (bus, id, f2) = (Arc::clone(&self.bus), intake.id.clone(), Arc::clone(&flow));
        let deliver: OnAudio = Arc::new(move |a: &Audio| {
            if a.samples == 0 || a.channels == 0 || a.data.len() < a.channels * a.samples {
                return;
            }
            if f2.channels.load(Ordering::SeqCst) == 0 {
                f2.channels.store(a.channels.min(MAX_CHANNELS), Ordering::SeqCst);
                f2.rate.store(if a.rate == 0 { RATE } else { a.rate }, Ordering::SeqCst);
            }
            let channels = f2.channels.load(Ordering::SeqCst);
            let block = interleave(a, channels);
            if let Ok(mut l) = f2.last.lock() {
                *l = Some(Instant::now());
            }
            f2.blocks.fetch_add(1, Ordering::SeqCst);
            bus.push(&id, &block, channels, f2.rate.load(Ordering::SeqCst));
            if let Ok(mut first) = f2.first.0.lock() {
                *first = true;
                f2.first.1.notify_all();
            }
        });
        let Some(guard) = (self.listen)(&device, deliver) else {
            intake.error = Some("NDI is not running, or cannot receive that source.".to_owned());
            return false;
        };
        let got = flow.first.0.lock().ok().and_then(|g| flow.first.1.wait_timeout_while(g, self.timing.first_wait, |first| !*first).ok()).is_some_and(|(g, _)| *g);
        if !got {
            drop(guard);
            intake.error = Some("That NDI source is not sending any sound.".to_owned());
            return false;
        }
        intake.guard = Some(guard);
        intake.flow = flow;
        intake.error = None;
        true
    }

    fn reconcile_outputs(&self, cfg: &Value) {
        let wanted: HashMap<String, Value> = cfg["outputs"].as_array().into_iter().flatten().filter(|o| o["output"] == ID).filter_map(|o| Some((o["id"].as_str()?.to_owned(), o.clone()))).collect();
        let gone: Vec<OutRun> = {
            let Ok(mut outs) = self.outputs.lock() else { return };
            let ids: Vec<String> = outs.iter().filter(|(k, v)| wanted.get(*k) != Some(&v.spec)).map(|(k, _)| k.clone()).collect();
            ids.iter().filter_map(|k| outs.remove(k)).collect()
        };
        gone.iter().for_each(Self::stop_out);
        let now = Instant::now();
        for (id, spec) in wanted {
            let existing = self.outputs.lock().ok().and_then(|o| o.get(&id).map(|r| Arc::clone(&r.state)));
            match existing {
                None => {
                    let run = self.start_out(&id, spec);
                    if let Ok(mut outs) = self.outputs.lock() {
                        outs.insert(id, run);
                    }
                }
                Some(st) => {
                    let due = st.next_try.lock().ok().and_then(|n| *n).is_some_and(|n| now >= n);
                    if st.sink.lock().is_ok_and(|s| s.is_none()) && due {
                        let spec = self.outputs.lock().ok().and_then(|o| o.get(&id).map(|r| r.spec.clone())).unwrap_or(Value::Null);
                        self.open_sink(&spec, &st);
                    }
                }
            }
        }
    }

    fn channels_of(spec: &Value) -> usize {
        (spec["channels"].as_u64().unwrap_or(2) as usize).clamp(1, MAX_CHANNELS)
    }

    fn open_sink(&self, spec: &Value, st: &OutState) -> bool {
        let name = sink_name(spec["device"].as_str().unwrap_or(""));
        match self.maker.audio_out(&name, Self::channels_of(spec), RATE) {
            Some(s) => {
                if let Ok(mut g) = st.sink.lock() {
                    *g = Some(s);
                }
                if let Ok(mut e) = st.error.lock() {
                    *e = None;
                }
                true
            }
            None => {
                if let Ok(mut e) = st.error.lock() {
                    *e = Some("NDI would not create that sound output.".to_owned());
                }
                if let Ok(mut n) = st.next_try.lock() {
                    *n = Some(Instant::now() + self.timing.retry_every);
                }
                false
            }
        }
    }

    fn start_out(&self, id: &str, spec: Value) -> OutRun {
        let state = Arc::new(OutState::default());
        self.open_sink(&spec, &state);
        let stop = Arc::new(AtomicBool::new(false));
        let (bus, st, sp, id2) = (Arc::clone(&self.bus), Arc::clone(&state), Arc::clone(&stop), id.to_owned());
        let channels = Self::channels_of(&spec);
        let latency = spec["latency_ms"].as_u64().unwrap_or(60) as usize;
        let thread = std::thread::Builder::new().name("relay-ndi-lane-out".into()).spawn(move || pump(&*bus, &id2, channels, latency, &st, &sp));
        OutRun { spec, state, stop, thread: Mutex::new(thread.ok()) }
    }

    fn stop_out(run: &OutRun) {
        run.stop.store(true, Ordering::SeqCst);
        let t = run.thread.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = t {
            let _ = t.join();
        }
        let sink = run.state.sink.lock().ok().and_then(|mut s| s.take());
        if let Some(s) = sink {
            run.state.underruns.fetch_add(s.underruns(), Ordering::SeqCst);
            s.close();
        }
    }

    /// Say how every input and output is doing (the Audio bus page shows it next to the engine's own).
    pub fn report(&self) {
        let mut inputs = serde_json::Map::new();
        for (id, i) in self.intakes.lock().map(|g| g.iter().map(|(k, v)| (k.clone(), (v.state(), v.error.clone(), v.device.clone(), v.lanes.clone(), Arc::clone(&v.flow)))).collect::<Vec<_>>()).unwrap_or_default() {
            let (state, error, device, lanes, flow) = i;
            let open = state == "open";
            inputs.insert(id, json!({"source": ID, "device": device, "label": device, "state": state, "error": error, "rate": open.then(|| flow.rate.load(Ordering::SeqCst)),
                                     "channels": open.then(|| flow.channels.load(Ordering::SeqCst)), "lanes": lanes, "dropped": 0}));
        }
        let mut outputs = serde_json::Map::new();
        for (id, o) in self.outputs.lock().map(|g| g.iter().map(|(k, v)| (k.clone(), Arc::clone(&v.state))).collect::<Vec<_>>()).unwrap_or_default() {
            let sink = o.sink.lock().ok().and_then(|s| s.clone());
            let error = o.error.lock().ok().and_then(|e| e.clone());
            let state = if sink.is_some() { "open" } else if error.is_some() { "waiting" } else { "closed" };
            let underruns = o.underruns.load(Ordering::SeqCst) + sink.map_or(0, |s| s.underruns());
            outputs.insert(id, json!({"state": state, "error": error, "underruns": underruns}));
        }
        self.bus.set_status(ID, Value::Object(inputs), Value::Object(outputs));
    }

    fn close_all(&self) {
        let ins: Vec<Intake> = self.intakes.lock().map(|mut i| i.drain().map(|(_, v)| v).collect()).unwrap_or_default();
        drop(ins);
        let outs: Vec<OutRun> = self.outputs.lock().map(|mut o| o.drain().map(|(_, v)| v).collect()).unwrap_or_default();
        outs.iter().for_each(Self::stop_out);
    }

    pub fn stop(&self) {
        self.close_all();
        self.bus.set_status(ID, json!({}), json!({}));
    }

    pub fn input_states(&self) -> HashMap<String, &'static str> {
        self.intakes.lock().map(|i| i.iter().map(|(k, v)| (k.clone(), v.state())).collect()).unwrap_or_default()
    }

    pub fn output_ids(&self) -> Vec<String> {
        let mut v: Vec<String> = self.outputs.lock().map(|o| o.keys().cloned().collect()).unwrap_or_default();
        v.sort();
        v
    }
}

/// Keep a sink topped up to a target fill with the audio the engine mixes for it, until told to stop.
fn pump(bus: &dyn LaneBus, id: &str, channels: usize, latency_ms: usize, st: &OutState, stop: &AtomicBool) {
    let mut pending: Vec<f32> = Vec::new();
    while !stop.load(Ordering::SeqCst) {
        std::thread::sleep(TICK);
        let Some(sink) = st.sink.lock().ok().and_then(|s| s.clone()) else { continue };
        if !pending.is_empty() {
            let took = sink.write(&pending, pending.len() / channels);
            pending.drain(..took * channels);
            if !pending.is_empty() {
                continue;
            }
        }
        let target = sink.rate() as usize * latency_ms / 2000;
        let want = target.saturating_sub(sink.queued()).min(sink.free());
        if want < 1 {
            continue;
        }
        let n = want.min(CHUNK_FRAMES);
        let Some(block) = bus.pull(id, n) else { continue };
        let took = sink.write(&block, n);
        if took < n {
            pending = block[took * channels..].to_vec();
        }
    }
}
