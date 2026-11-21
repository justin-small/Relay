//! The engine: the non-realtime half. It owns configuration, opens and supervises streams, builds everything the
//! realtime callbacks will use (rings, resamplers, taps), and sends it to them through the command queues in
//! `rt.rs`. Nothing here runs on a realtime thread.
//!
//! Wiring model. A lane is fed by one input (a device stream or an external feed). Its consumers (the outputs
//! whose matrix uses it, and any taps) each have a `Wire`: a ring whose producer end sits in the input's tap for
//! that lane and whose consumer end sits in the consumer. `rewire` brings the real wires in line with what the
//! configuration and the running streams call for, and is safe to call at any time: it only adds what is missing
//! and removes what is stale. A wire is stale if its input or output has been reopened since (each reopening
//! gets a new generation number), so a stream that dies and comes back is rewired without special cases.

use crate::backend::{Backend, DeviceFormat, Stream};
use crate::config::{db_to_gain, ChannelSel, Config, OutputSpec, DEVICE_SOURCE};
use crate::error::Error;
use crate::health::Glitches;
use crate::input::{InCmd, InputCore, InputShared, Sink, Tap};
use crate::meter::MeterShared;
use crate::output::{new_ring, target_frames, Clip, LaneRx, OutCmd, OutputCore, OutputShared, RING_FRAMES};
use crate::resamp::resample_offline;
use crate::rt::{command_queue, discard_queue, CmdTx, DiscardRx};
use crate::status::{InputStatus, LaneStatus, OutputKind, OutputStatus, Status, StreamState, TapStatus};
use crate::tap::{TapOptions, TapRead, TapReader};
use crate::util::{LANE_RATE, MAX_LANES};
use std::collections::{BTreeMap, HashMap};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

/// An input that delivers no frames for this long is treated as unplugged (the Python bus uses 2 s too).
const LOST_AFTER_S: f64 = 2.0;
const RETRY_FIRST_S: f64 = 0.5;
const RETRY_MAX_S: f64 = 8.0;
/// How long an output keeps a lane's ring after the matrix stops using it (plenty of blocks for the ramp).
const WIRE_GRACE_S: f64 = 0.25;
pub const LOST_SENTENCE: &str = "The audio input stopped. Check the device is plugged in; Relay reconnects on its own.";

#[derive(Clone, PartialEq, Eq, Debug)]
enum Who {
    Output(String),
    Tap(u64),
}

struct Wire {
    who: Who,
    /// Generation of the consumer when the wire was made (0 for a tap, which outlives its rings).
    who_gen: u64,
    sink_id: u64,
    /// When the matrix stopped asking for this wire. An output's ring is kept for a moment after that so the
    /// gain can ramp to zero on real audio instead of the lane being cut off.
    stale_at: Option<f64>,
}

struct LaneRec {
    shared: Arc<MeterShared>,
    /// The input (id, generation) whose core holds this lane's tap.
    tap_at: Option<(String, u64)>,
    channel_sent: ChannelSel,
    wires: Vec<Wire>,
    seen_frames: u64,
    seen_at: Option<f64>,
}

struct InputRt {
    source: String,
    device: Option<String>,
    external: bool,
    /// 0 when nothing is running.
    gen: u64,
    state: StreamState,
    error: Option<String>,
    next_try: f64,
    backoff: f64,
    stream: Option<Box<dyn Stream>>,
    cmd: Option<CmdTx<InCmd>>,
    discards: Option<DiscardRx>,
    /// External inputs: the core lives here and runs on the pusher's thread.
    ext: Option<Arc<Mutex<InputCore>>>,
    shared: Arc<InputShared>,
    format: Option<DeviceFormat>,
    seen_frames: u64,
    seen_at: f64,
}

struct OutputRt {
    spec: OutputSpec,
    gen: u64,
    state: StreamState,
    error: Option<String>,
    next_try: f64,
    backoff: f64,
    stream: Option<Box<dyn Stream>>,
    cmd: Option<CmdTx<OutCmd>>,
    discards: Option<DiscardRx>,
    /// Pull outputs: the core lives here and runs on the puller's thread.
    pull: Option<Arc<Mutex<OutputCore>>>,
    shared: Arc<OutputShared>,
    format: Option<DeviceFormat>,
    /// The matrix last sent (dense, lane-major), so an unchanged matrix is not sent again.
    matrix_sent: Option<Vec<f32>>,
}

struct TapRec {
    lane: usize, // 1-based
    reader: Arc<TapReader>,
}

struct Inner {
    backend: Arc<dyn Backend>,
    glitches: Arc<Glitches>,
    cfg: Config,
    running: bool,
    lanes: Vec<LaneRec>,
    inputs: HashMap<String, InputRt>,
    outputs: BTreeMap<String, OutputRt>,
    taps: HashMap<u64, TapRec>,
    next_id: u64,
    next_gen: u64,
    /// The backend's clock when the current call began.
    now: f64,
}

pub struct Engine {
    backend: Arc<dyn Backend>,
    glitches: Arc<Glitches>,
    inner: Mutex<Inner>,
}

impl Engine {
    pub fn new(backend: Arc<dyn Backend>) -> Self {
        let glitches = Arc::new(Glitches::default());
        let lanes = (0..MAX_LANES)
            .map(|_| LaneRec {
                shared: Arc::new(MeterShared::default()),
                tap_at: None,
                channel_sent: ChannelSel::Index(0),
                wires: Vec::new(),
                seen_frames: 0,
                seen_at: None,
            })
            .collect();
        let inner = Inner {
            backend: backend.clone(),
            glitches: glitches.clone(),
            cfg: Config::default(),
            running: false,
            lanes,
            inputs: HashMap::new(),
            outputs: BTreeMap::new(),
            taps: HashMap::new(),
            next_id: 1,
            next_gen: 1,
            now: 0.0,
        };
        Self { backend, glitches, inner: Mutex::new(inner) }
    }

    fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(|e| e.into_inner())
    }

    // -- configuration ------------------------------------------------------------------------------------
    /// Make the engine match `cfg`, touching only what changed. A matrix-only change sends new gains to the
    /// running outputs (ramped over one block) and adds or removes the lane rings that change needs; nothing is
    /// reopened and no output's drift state is reset. A changed device, channel count or latency reopens just
    /// that input or output.
    pub fn apply(&self, cfg: &Config) -> Result<(), Error> {
        cfg.validate()?;
        let now = self.backend.now();
        let mut g = self.lock();
        g.now = now;
        g.cfg = cfg.clone();
        if !cfg.enabled {
            g.stop_all();
            return Ok(());
        }
        g.running = true;
        g.sync_inputs(now);
        g.sync_outputs(now);
        g.send_matrices();
        g.rewire();
        g.reap();
        Ok(())
    }

    /// Close everything. Taps stay registered and reconnect on the next `apply`.
    pub fn shutdown(&self) {
        self.lock().stop_all();
    }

    /// Call every half second or so from a non-realtime thread: notices failed streams, reopens them with
    /// backoff, rewires lanes after a reopening, and drops what the realtime side has retired.
    pub fn supervise(&self) {
        let now = self.backend.now();
        let mut g = self.lock();
        g.now = now;
        if !g.running {
            return;
        }
        let keys: Vec<String> = g.inputs.keys().cloned().collect();
        for k in keys {
            if let Some(mut rt) = g.inputs.remove(&k) {
                g.check_input(&mut rt, now);
                g.inputs.insert(k, rt);
            }
        }
        let ids: Vec<String> = g.outputs.keys().cloned().collect();
        for id in ids {
            if let Some(mut rt) = g.outputs.remove(&id) {
                g.check_output(&mut rt, now);
                g.outputs.insert(id, rt);
            }
        }
        g.send_matrices();
        g.rewire();
        g.reap();
    }

    /// A device was plugged or unplugged, or the default changed: make every stream that is waiting try again at
    /// the next `supervise()` instead of after its backoff. Streams that are open are not touched.
    pub fn retry_now(&self) {
        let mut g = self.lock();
        for i in g.inputs.values_mut().filter(|i| i.state == StreamState::Waiting) {
            i.next_try = 0.0;
            i.backoff = RETRY_FIRST_S;
        }
        for o in g.outputs.values_mut().filter(|o| o.state == StreamState::Waiting) {
            o.next_try = 0.0;
            o.backoff = RETRY_FIRST_S;
        }
    }

    // -- external inputs and pull outputs ------------------------------------------------------------------
    /// Feed an external input (a network source Python reads). `input_id` is `source:device` lower-cased (see
    /// `config::input_id`); `data` is interleaved `channels` x frames at `rate`. Runs the same path as a device
    /// callback, on the calling thread.
    pub fn push(&self, input_id: &str, data: &[f32], channels: usize, rate: u32) -> Result<(), Error> {
        let core = {
            let g = self.lock();
            match g.inputs.get(input_id).and_then(|i| i.ext.clone()) {
                Some(c) => c,
                None => return Err(Error::UnknownInput(input_id.to_string())),
            }
        };
        let mut core = core.lock().unwrap_or_else(|e| e.into_inner());
        core.ensure_format(channels, rate);
        core.process(data);
        Ok(())
    }

    /// Frames of a pull output (a network sender), interleaved at 48 kHz, paced by the caller's clock.
    pub fn pull(&self, output_id: &str, frames: usize) -> Result<Vec<f32>, Error> {
        let (core, ch) = self.pull_core(output_id)?;
        let mut out = vec![0.0; frames * ch];
        core.lock().unwrap_or_else(|e| e.into_inner()).render(&mut out);
        Ok(out)
    }

    /// The same without allocating: fills `out` (a whole number of frames of the output's channel count).
    pub fn pull_into(&self, output_id: &str, out: &mut [f32]) -> Result<(), Error> {
        let (core, _) = self.pull_core(output_id)?;
        core.lock().unwrap_or_else(|e| e.into_inner()).render(out);
        Ok(())
    }

    fn pull_core(&self, output_id: &str) -> Result<(Arc<Mutex<OutputCore>>, usize), Error> {
        let g = self.lock();
        match g.outputs.get(output_id) {
            Some(o) => match &o.pull {
                Some(core) => Ok((core.clone(), o.spec.channels)),
                None => Err(Error::UnknownOutput(output_id.to_string())),
            },
            None => Err(Error::UnknownOutput(output_id.to_string())),
        }
    }

    // -- clips -----------------------------------------------------------------------------------------------
    /// Mix a mono clip at `rate` onto `channels` (counted from 0) of an output, starting with the output's next
    /// block. It is added after the latency buffer, so an alert is heard within a block, not after the buffer.
    pub fn inject(&self, output_id: &str, channels: &[usize], samples: &[f32], rate: u32) -> Result<(), Error> {
        let g = self.lock();
        let Some(o) = g.outputs.get(output_id) else { return Err(Error::UnknownOutput(output_id.to_string())) };
        let (Some(cmd), Some(fmt)) = (&o.cmd, o.format) else {
            return Err(Error::NotRunning(format!("output {output_id}")));
        };
        let mask = channels.iter().filter(|&&c| c < o.spec.channels && c < 32).fold(0u32, |m, &c| m | (1 << c));
        if mask == 0 || samples.is_empty() {
            return Ok(());
        }
        let clip = Clip { samples: resample_offline(samples, rate, fmt.rate).into_boxed_slice(), mask, pos: 0 };
        cmd.send(OutCmd::AddClip(Box::new(clip)), &g.glitches)
    }

    /// Cut every clip playing on an output, effective at its next block.
    pub fn stop_injects(&self, output_id: &str) -> Result<(), Error> {
        let g = self.lock();
        let Some(o) = g.outputs.get(output_id) else { return Err(Error::UnknownOutput(output_id.to_string())) };
        match &o.cmd {
            Some(cmd) => cmd.send(OutCmd::StopClips, &g.glitches),
            None => Ok(()),
        }
    }

    // -- taps ------------------------------------------------------------------------------------------------
    /// A non-realtime reader on lane `lane` (1..=32). Works before the lane is patched; audio arrives once it is.
    pub fn open_tap(&self, lane: usize, opts: TapOptions) -> Result<u64, Error> {
        if !(1..=MAX_LANES).contains(&lane) {
            return Err(Error::Config(format!("lane {lane} is not in 1..={MAX_LANES}")));
        }
        let now = self.backend.now();
        let mut g = self.lock();
        g.now = now;
        let id = g.take_id();
        g.taps.insert(id, TapRec { lane, reader: Arc::new(TapReader::new(opts)) });
        g.rewire_lane(lane - 1);
        Ok(id)
    }

    pub fn close_tap(&self, tap: u64) -> Result<(), Error> {
        let now = self.backend.now();
        let mut g = self.lock();
        g.now = now;
        let Some(rec) = g.taps.remove(&tap) else { return Err(Error::UnknownTap(tap)) };
        g.rewire_lane(rec.lane - 1);
        g.reap();
        Ok(())
    }

    /// Up to `max_frames` mono samples from a tap, at the tap's rate. Waits up to `timeout` (real time) if none
    /// are ready; with a zero timeout it returns what is there. `dropped` counts lane frames lost since the last
    /// read because the reader was too slow.
    pub fn read_tap(&self, tap: u64, max_frames: usize, timeout: Duration) -> Result<TapRead, Error> {
        let reader = self.lock().taps.get(&tap).map(|t| t.reader.clone()).ok_or(Error::UnknownTap(tap))?;
        Ok(reader.read(max_frames, timeout))
    }

    // -- status ------------------------------------------------------------------------------------------------
    pub fn status(&self) -> Status {
        let now = self.backend.now();
        let mut g = self.lock();
        g.refresh_ages(now);
        let patch: HashMap<usize, _> = g.cfg.lanes.iter().map(|l| (l.lane, l)).collect();
        let lanes = (0..MAX_LANES)
            .map(|n| {
                let spec = patch.get(&(n + 1));
                let rec = &g.lanes[n];
                let input = spec.and_then(|s| g.inputs.get(&s.input_id()));
                LaneStatus {
                    lane: n + 1,
                    name: spec.map(|s| s.name.clone()).unwrap_or_default(),
                    patched: spec.is_some(),
                    input: spec.map(|s| s.input_id()),
                    state: spec.map(|_| input.map_or(StreamState::Closed, |i| g.input_state(i, now))),
                    error: input.and_then(|i| g.input_error(i, now)),
                    subscribers: rec.wires.iter().filter(|w| w.stale_at.is_none()).count(),
                    meter: rec.shared.reading(),
                    last_input_age_s: if spec.is_some() { rec.seen_at.map(|t| now - t) } else { None },
                }
            })
            .collect();
        let mut inputs: Vec<InputStatus> = g
            .inputs
            .iter()
            .map(|(id, i)| InputStatus {
                id: id.clone(),
                source: i.source.clone(),
                device: i.device.clone(),
                state: g.input_state(i, now),
                error: g.input_error(i, now),
                rate: i.format.map(|f| f.rate),
                channels: i.format.map(|f| f.channels),
                lanes: g.cfg.lanes.iter().filter(|l| &l.input_id() == id).map(|l| l.lane).collect(),
                dropped: i.shared.dropped.load(Relaxed),
            })
            .collect();
        inputs.sort_by(|a, b| a.id.cmp(&b.id));
        let outputs = g
            .outputs
            .values()
            .map(|o| {
                let sh = &o.shared;
                let per_ms = LANE_RATE as f32 / 1000.0;
                let queued = sh.fill.get() / per_ms;
                let (lo, hi) = (sh.fill_min.swap(f32::MAX), sh.fill_max.swap(0.0));
                OutputStatus {
                    id: o.spec.id.clone(),
                    name: o.spec.name.clone(),
                    kind: if o.spec.is_device() { OutputKind::Device } else { OutputKind::Pull },
                    state: o.state,
                    error: o.error.clone(),
                    channels: o.spec.channels,
                    rate: o.format.map(|f| f.rate),
                    latency_ms: o.spec.latency_ms,
                    target_ms: target_frames(o.spec.latency_ms) as f32 / per_ms,
                    underruns: sh.underruns.load(Relaxed),
                    resyncs: sh.resyncs.load(Relaxed),
                    queued_ms: queued,
                    fill_min_ms: if lo == f32::MAX { queued } else { lo / per_ms },
                    fill_max_ms: if hi == 0.0 { queued } else { hi / per_ms },
                    ratio: sh.ratio.get(),
                    drift: sh.drift.get(),
                    frames_out: sh.frames_out.load(Relaxed),
                    meters: sh.meters.iter().map(|m| m.reading()).collect(),
                }
            })
            .collect();
        let taps = g
            .taps
            .iter()
            .map(|(&id, t)| TapStatus { id, lane: t.lane, rate: t.reader.rate, dropped: t.reader.dropped.load(Relaxed) })
            .collect();
        Status { running: g.running, lane_rate: LANE_RATE, lanes, inputs, outputs, taps, glitches: self.glitches.snapshot() }
    }
}

impl Drop for Engine {
    fn drop(&mut self) {
        self.lock().stop_all();
    }
}

// -- the non-realtime machinery -------------------------------------------------------------------------------
impl Inner {
    fn take_id(&mut self) -> u64 {
        self.next_id += 1;
        self.next_id
    }

    fn take_gen(&mut self) -> u64 {
        self.next_gen += 1;
        self.next_gen
    }

    fn stop_all(&mut self) {
        self.inputs.clear();
        self.outputs.clear();
        for rec in &mut self.lanes {
            rec.wires.clear();
            rec.tap_at = None;
            rec.seen_at = None;
            rec.seen_frames = rec.shared.frames.load(Relaxed);
            rec.shared.reset();
        }
        for t in self.taps.values() {
            t.reader.set_rx(None);
        }
        self.running = false;
    }

    fn reap(&mut self) {
        for i in self.inputs.values_mut() {
            if let Some(d) = i.discards.as_mut() {
                d.reap();
            }
        }
        for o in self.outputs.values_mut() {
            if let Some(d) = o.discards.as_mut() {
                d.reap();
            }
        }
    }

    // inputs ---------------------------------------------------------------------------------------------------
    fn sync_inputs(&mut self, now: f64) {
        let mut wanted: HashMap<String, (String, Option<String>)> = HashMap::new();
        for l in &self.cfg.lanes {
            wanted.entry(l.input_id()).or_insert_with(|| (l.source.clone(), l.device.clone()));
        }
        self.inputs.retain(|k, _| wanted.contains_key(k));
        for (key, (source, device)) in wanted {
            if self.inputs.contains_key(&key) {
                continue;
            }
            let mut rt = InputRt {
                external: source != DEVICE_SOURCE,
                source,
                device,
                gen: 0,
                state: StreamState::Closed,
                error: None,
                next_try: 0.0,
                backoff: RETRY_FIRST_S,
                stream: None,
                cmd: None,
                discards: None,
                ext: None,
                shared: Arc::new(InputShared::default()),
                format: None,
                seen_frames: 0,
                seen_at: now,
            };
            self.open_input(&mut rt, now);
            self.inputs.insert(key, rt);
        }
    }

    fn open_input(&mut self, rt: &mut InputRt, now: f64) {
        let (cmd_tx, cmd_rx) = command_queue();
        let (disc_tx, disc_rx) = discard_queue(self.glitches.clone());
        if rt.external {
            let core = InputCore::new(1, LANE_RATE, cmd_rx, disc_tx, rt.shared.clone());
            rt.ext = Some(Arc::new(Mutex::new(core)));
        } else {
            let opened = self.backend.input_format(rt.device.as_deref()).and_then(|fmt| {
                let mut core = InputCore::new(fmt.channels, fmt.rate, cmd_rx, disc_tx, rt.shared.clone());
                let stream = self.backend.open_input(rt.device.as_deref(), fmt, Box::new(move |d| core.process(d)))?;
                Ok((fmt, stream))
            });
            match opened {
                Ok((fmt, stream)) => {
                    rt.stream = Some(stream);
                    rt.format = Some(fmt);
                }
                Err(e) => {
                    rt.state = StreamState::Waiting;
                    rt.error = Some(e);
                    schedule_retry(&mut rt.next_try, &mut rt.backoff, now);
                    return;
                }
            }
        }
        rt.cmd = Some(cmd_tx);
        rt.discards = Some(disc_rx);
        rt.gen = self.take_gen();
        rt.state = StreamState::Open;
        rt.error = None;
        rt.backoff = RETRY_FIRST_S;
        rt.seen_frames = rt.shared.frames_in.load(Relaxed);
        rt.seen_at = now;
    }

    fn check_input(&mut self, rt: &mut InputRt, now: f64) {
        let frames = rt.shared.frames_in.load(Relaxed);
        if frames != rt.seen_frames {
            rt.seen_frames = frames;
            rt.seen_at = now;
        }
        if rt.external {
            return;
        }
        if let Some(stream) = &rt.stream {
            let failure = stream.error().or_else(|| (now - rt.seen_at > LOST_AFTER_S).then(|| LOST_SENTENCE.to_string()));
            if let Some(msg) = failure {
                rt.stream = None;
                rt.cmd = None;
                rt.discards = None;
                rt.gen = 0;
                rt.state = StreamState::Waiting;
                rt.error = Some(msg);
                schedule_retry(&mut rt.next_try, &mut rt.backoff, now);
            }
        } else if rt.state == StreamState::Waiting && now >= rt.next_try {
            self.open_input(rt, now);
        }
    }

    fn input_state(&self, i: &InputRt, now: f64) -> StreamState {
        if i.external && i.gen != 0 {
            let live = i.shared.frames_in.load(Relaxed) > 0 && now - i.seen_at <= LOST_AFTER_S;
            if live { StreamState::Open } else { StreamState::Waiting }
        } else {
            i.state
        }
    }

    fn input_error(&self, i: &InputRt, now: f64) -> Option<String> {
        if i.external && i.gen != 0 && self.input_state(i, now) == StreamState::Waiting {
            Some("No audio has arrived from this source yet.".to_string())
        } else {
            i.error.clone()
        }
    }

    fn refresh_ages(&mut self, now: f64) {
        for rec in &mut self.lanes {
            let f = rec.shared.frames.load(Relaxed);
            if f != rec.seen_frames {
                rec.seen_frames = f;
                rec.seen_at = Some(now);
            }
        }
        for i in self.inputs.values_mut() {
            let f = i.shared.frames_in.load(Relaxed);
            if f != i.seen_frames {
                i.seen_frames = f;
                i.seen_at = now;
            }
        }
    }

    // outputs --------------------------------------------------------------------------------------------------
    fn sync_outputs(&mut self, now: f64) {
        let wanted: Vec<OutputSpec> = self.cfg.outputs.clone();
        self.outputs.retain(|id, o| wanted.iter().any(|w| &w.id == id && w == &o.spec));
        for spec in wanted {
            if self.outputs.contains_key(&spec.id) {
                continue;
            }
            let mut rt = OutputRt {
                shared: Arc::new(OutputShared::new(spec.channels)),
                spec,
                gen: 0,
                state: StreamState::Closed,
                error: None,
                next_try: 0.0,
                backoff: RETRY_FIRST_S,
                stream: None,
                cmd: None,
                discards: None,
                pull: None,
                format: None,
                matrix_sent: None,
            };
            self.open_output(&mut rt, now);
            self.outputs.insert(rt.spec.id.clone(), rt);
        }
    }

    fn open_output(&mut self, rt: &mut OutputRt, now: f64) {
        let (cmd_tx, cmd_rx) = command_queue();
        let (disc_tx, disc_rx) = discard_queue(self.glitches.clone());
        let ch = rt.spec.channels;
        if rt.spec.is_device() {
            let opened = self.backend.output_format(rt.spec.device.as_deref(), ch).and_then(|fmt| {
                let mut core = OutputCore::new(ch, fmt.rate, cmd_rx, disc_tx, rt.shared.clone(), self.glitches.clone());
                let stream = self.backend.open_output(rt.spec.device.as_deref(), fmt, Box::new(move |b| core.render(b)))?;
                Ok((fmt, stream))
            });
            match opened {
                Ok((fmt, stream)) => {
                    rt.stream = Some(stream);
                    rt.format = Some(fmt);
                }
                Err(e) => {
                    rt.state = StreamState::Waiting;
                    rt.error = Some(e);
                    schedule_retry(&mut rt.next_try, &mut rt.backoff, now);
                    return;
                }
            }
        } else {
            let core = OutputCore::new(ch, LANE_RATE, cmd_rx, disc_tx, rt.shared.clone(), self.glitches.clone());
            rt.pull = Some(Arc::new(Mutex::new(core)));
            rt.format = Some(DeviceFormat { channels: ch, rate: LANE_RATE });
        }
        rt.cmd = Some(cmd_tx);
        rt.discards = Some(disc_rx);
        rt.gen = self.take_gen();
        rt.state = StreamState::Open;
        rt.error = None;
        rt.backoff = RETRY_FIRST_S;
        rt.matrix_sent = None;
    }

    fn check_output(&mut self, rt: &mut OutputRt, now: f64) {
        if let Some(stream) = &rt.stream {
            if let Some(msg) = stream.error() {
                rt.stream = None;
                rt.cmd = None;
                rt.discards = None;
                rt.gen = 0;
                rt.state = StreamState::Waiting;
                rt.error = Some(msg);
                schedule_retry(&mut rt.next_try, &mut rt.backoff, now);
            }
        } else if rt.state == StreamState::Waiting && now >= rt.next_try {
            self.open_output(rt, now);
        }
    }

    /// Send each running output the gains the configuration says, if they differ from what it already has.
    fn send_matrices(&mut self) {
        for (id, o) in self.outputs.iter_mut() {
            let Some(cmd) = &o.cmd else { continue };
            let ch = o.spec.channels;
            let mut dense = vec![0.0f32; MAX_LANES * ch];
            for c in self.cfg.matrix.iter().filter(|c| &c.out == id) {
                dense[(c.lane - 1) * ch + (c.ch - 1)] = db_to_gain(c.gain_db);
            }
            if o.matrix_sent.as_ref() != Some(&dense) && cmd.send(OutCmd::SetMatrix(dense.clone().into_boxed_slice()), &self.glitches).is_ok() {
                o.matrix_sent = Some(dense);
            }
        }
    }

    // wiring ---------------------------------------------------------------------------------------------------
    fn rewire(&mut self) {
        for n in 0..MAX_LANES {
            self.rewire_lane(n);
        }
    }

    /// Send a command to input `key` if it is still the generation `gen` that the sender had in mind.
    fn send_in(&self, key: &str, gen: u64, cmd: InCmd) -> bool {
        match self.inputs.get(key) {
            Some(i) if i.gen == gen => i.cmd.as_ref().is_some_and(|c| c.send(cmd, &self.glitches).is_ok()),
            _ => false,
        }
    }

    /// Take a wire out of service: tell the input to forget its ring (if the input is still the one it was made
    /// for) and the consumer to let go of the other end.
    fn drop_wire(&self, lane: usize, w: &Wire, feeder: &Option<(String, u64)>) {
        if let Some((k, g)) = feeder {
            self.send_in(k, *g, InCmd::RemoveSink(lane, w.sink_id));
        }
        match &w.who {
            Who::Output(id) => {
                if let Some(o) = self.outputs.get(id) {
                    if o.gen == w.who_gen {
                        if let Some(c) = &o.cmd {
                            let _ = c.send(OutCmd::SetLane(lane, None), &self.glitches);
                        }
                    }
                }
            }
            Who::Tap(id) => {
                if let Some(t) = self.taps.get(id) {
                    t.reader.set_rx(None);
                }
            }
        }
    }

    /// The consumers lane `n` should have right now.
    fn desired_consumers(&self, n: usize) -> Vec<(Who, u64)> {
        let mut out = Vec::new();
        for (id, o) in &self.outputs {
            if o.gen != 0 && self.cfg.matrix.iter().any(|c| c.lane == n + 1 && &c.out == id) {
                out.push((Who::Output(id.clone()), o.gen));
            }
        }
        for (&id, t) in &self.taps {
            if t.lane == n + 1 {
                out.push((Who::Tap(id), 0));
            }
        }
        out
    }

    fn rewire_lane(&mut self, n: usize) {
        if !self.running {
            return;
        }
        let spec = self.cfg.lanes.iter().find(|l| l.lane == n + 1).cloned();
        let live: Option<(String, u64)> = spec.as_ref().and_then(|s| {
            let k = s.input_id();
            self.inputs.get(&k).filter(|i| i.gen != 0).map(|i| (k, i.gen))
        });
        let mut wires = std::mem::take(&mut self.lanes[n].wires);
        let mut tap_at = self.lanes[n].tap_at.take();
        let mut channel_sent = self.lanes[n].channel_sent;

        // The tap this lane had is stale if its input is gone, was reopened, or the lane moved.
        if let Some((k, g)) = tap_at.clone() {
            if live.as_ref() != Some(&(k.clone(), g)) {
                self.send_in(&k, g, InCmd::RemoveTap(n));
                for w in wires.drain(..) {
                    self.drop_wire(n, &w, &None);
                }
                tap_at = None;
            }
        }

        if let (Some((k, g)), Some(spec)) = (&live, &spec) {
            if tap_at.is_none() {
                let tap = Tap::new(n, spec.channel, self.lanes[n].shared.clone());
                if self.send_in(k, *g, InCmd::AddTap(Box::new(tap))) {
                    tap_at = Some((k.clone(), *g));
                    channel_sent = spec.channel;
                }
            } else if channel_sent != spec.channel && self.send_in(k, *g, InCmd::SetChannel(n, spec.channel)) {
                channel_sent = spec.channel;
            }
        }

        let desired = self.desired_consumers(n);
        let now = self.now;
        wires.retain_mut(|w| {
            let wanted = desired.iter().any(|(who, g)| *who == w.who && *g == w.who_gen);
            let keep = if tap_at.is_none() {
                false
            } else if wanted {
                w.stale_at = None;
                true
            } else if matches!(w.who, Who::Output(_)) {
                // Not asked for any more: let the gain ramp down on live audio before the ring goes.
                let since = *w.stale_at.get_or_insert(now);
                now - since < WIRE_GRACE_S
            } else {
                false
            };
            if !keep {
                self.drop_wire(n, w, &tap_at);
            }
            keep
        });
        if let Some((k, g)) = tap_at.clone() {
            for (who, who_gen) in desired {
                if wires.iter().any(|w| w.who == who && w.who_gen == who_gen) {
                    continue;
                }
                if let Some(w) = self.make_wire(n, &k, g, who, who_gen) {
                    wires.push(w);
                }
            }
        }
        self.lanes[n].wires = wires;
        self.lanes[n].tap_at = tap_at;
        self.lanes[n].channel_sent = channel_sent;
    }

    /// Build a ring for one consumer, put its producer end in the input's tap and its consumer end where it reads.
    fn make_wire(&mut self, n: usize, key: &str, gen: u64, who: Who, who_gen: u64) -> Option<Wire> {
        let id = self.take_id();
        let input_dropped = self.inputs.get(key)?.shared.dropped.clone();
        match &who {
            Who::Output(oid) => {
                let o = self.outputs.get(oid)?;
                let cmd = o.cmd.as_ref()?;
                let (tx, rx) = new_ring(RING_FRAMES);
                let sink = Sink { id, tx, input_dropped, own_dropped: o.shared.dropped.clone(), glitches: self.glitches.clone() };
                let lane_rx = LaneRx::new(rx, target_frames(o.spec.latency_ms), o.shared.drift.get());
                // Consumer end first, so the ring is never fed with nobody to read it.
                cmd.send(OutCmd::SetLane(n, Some(Box::new(lane_rx))), &self.glitches).ok()?;
                if !self.send_in(key, gen, InCmd::AddSink(n, Box::new(sink))) {
                    let _ = cmd.send(OutCmd::SetLane(n, None), &self.glitches);
                    return None;
                }
            }
            Who::Tap(tid) => {
                let t = self.taps.get(tid)?;
                let (tx, rx) = new_ring(t.reader.capacity);
                let sink = Sink { id, tx, input_dropped, own_dropped: t.reader.dropped.clone(), glitches: self.glitches.clone() };
                t.reader.set_rx(Some(rx));
                if !self.send_in(key, gen, InCmd::AddSink(n, Box::new(sink))) {
                    t.reader.set_rx(None);
                    return None;
                }
            }
        }
        Some(Wire { who, who_gen, sink_id: id, stale_at: None })
    }
}

fn schedule_retry(next_try: &mut f64, backoff: &mut f64, now: f64) {
    *next_try = now + *backoff;
    *backoff = (*backoff * 2.0).min(RETRY_MAX_S);
}
