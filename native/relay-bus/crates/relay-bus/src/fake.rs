//! A deterministic backend for tests: no devices, no threads, no wall clock.
//!
//! Devices are described up front (channels, nominal rate, a clock error in ppm, a block size). Time is virtual and
//! moves only when the test calls `run_until` / `run_blocks`; each open stream's callback fires at the instant its
//! device's own clock says a block is due, in time order. An output clock 0.3% fast is `error_ppm: 3000.0`: its
//! callbacks come 0.3% closer together than the nominal rate implies.
//!
//! With the `no-alloc-check` feature every callback runs inside `assert_no_alloc`, so a test binary that installs
//! the checking allocator (see tests/common/mod.rs) aborts if the realtime code allocates or frees.

use crate::backend::{Backend, DeviceFormat, InputCallback, OutputCallback, Stream};
use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

/// Fills one frame (`out.len()` = channels) of an input device; the first argument is the frame number since the
/// stream was opened.
pub type Signal = Box<dyn FnMut(u64, &mut [f32]) + Send>;

#[derive(Debug, Clone, Copy)]
pub struct DeviceSpec {
    pub channels: usize,
    /// The rate the device reports (what the engine believes).
    pub rate: u32,
    /// How far the device's real clock is from that rate, in parts per million. +3000 runs 0.3% fast.
    pub error_ppm: f64,
    /// Frames per callback.
    pub block: usize,
}

impl DeviceSpec {
    pub fn new(channels: usize, rate: u32, block: usize) -> Self {
        Self { channels, rate, error_ppm: 0.0, block }
    }
    pub fn with_error_ppm(mut self, ppm: f64) -> Self {
        self.error_ppm = ppm;
        self
    }
}

/// Ready-made input signals.
pub mod signal {
    use super::Signal;

    pub fn silence() -> Signal {
        Box::new(|_, f| f.fill(0.0))
    }
    /// The same constant on every channel.
    pub fn dc(v: f32) -> Signal {
        Box::new(move |_, f| f.fill(v))
    }
    /// A constant per channel.
    pub fn dc_per_channel(v: Vec<f32>) -> Signal {
        Box::new(move |_, f| {
            for (o, x) in f.iter_mut().zip(&v) {
                *o = *x;
            }
        })
    }
    /// A sine of `freq` Hz sampled at `rate` Hz, on every channel.
    pub fn sine(freq: f64, rate: u32, amp: f32) -> Signal {
        Box::new(move |n, f| {
            let s = (2.0 * std::f64::consts::PI * freq * n as f64 / rate as f64).sin() as f32 * amp;
            f.fill(s);
        })
    }
}

struct InDev {
    spec: DeviceSpec,
    signal: Signal,
    unplugged: bool,
    /// Open but delivering nothing: a device that hangs without reporting an error.
    stalled: bool,
}

struct OutDev {
    spec: DeviceSpec,
    unplugged: bool,
    capture: Option<Vec<f32>>,
    blocks: u64,
}

struct Active<C> {
    id: u64,
    dev: String,
    cb: C,
    block: usize,
    channels: usize,
    rate_actual: f64,
    t0: f64,
    frames_done: u64,
    buf: Vec<f32>,
    failure: Arc<Mutex<Option<String>>>,
}

impl<C> Active<C> {
    fn failed(&self) -> bool {
        self.failure.lock().unwrap().is_some()
    }
}

#[derive(Default)]
struct Sim {
    now: f64,
    next_id: u64,
    in_dev: HashMap<String, InDev>,
    out_dev: HashMap<String, OutDev>,
    inputs: Vec<Active<InputCallback>>,
    outputs: Vec<Active<OutputCallback>>,
}

#[cfg(feature = "no-alloc-check")]
fn realtime<R>(f: impl FnOnce() -> R) -> R {
    assert_no_alloc::assert_no_alloc(f)
}
#[cfg(not(feature = "no-alloc-check"))]
fn realtime<R>(f: impl FnOnce() -> R) -> R {
    f()
}

impl Sim {
    /// Run every callback due up to and including virtual time `t`.
    fn run_until(&mut self, t: f64) {
        loop {
            // The earliest due callback: an input block is due when its last frame has been captured, an output
            // block when the device asks for it.
            let mut best: Option<(f64, bool, usize)> = None;
            for (i, s) in self.inputs.iter().enumerate() {
                if s.failed() {
                    continue;
                }
                let due = s.t0 + (s.frames_done + s.block as u64) as f64 / s.rate_actual;
                if best.is_none_or(|(b, _, _)| due < b) {
                    best = Some((due, true, i));
                }
            }
            for (i, s) in self.outputs.iter().enumerate() {
                if s.failed() {
                    continue;
                }
                let due = s.t0 + s.frames_done as f64 / s.rate_actual;
                if best.is_none_or(|(b, _, _)| due < b) {
                    best = Some((due, false, i));
                }
            }
            let Some((due, is_input, i)) = best else { break };
            if due > t {
                break;
            }
            self.now = self.now.max(due);
            if is_input {
                self.fire_input(i);
            } else {
                self.fire_output(i);
            }
        }
        self.now = self.now.max(t);
    }

    fn fire_input(&mut self, i: usize) {
        let s = &mut self.inputs[i];
        let dev = self.in_dev.get_mut(&s.dev).expect("device exists while a stream is open");
        for f in 0..s.block {
            let frame = &mut s.buf[f * s.channels..(f + 1) * s.channels];
            (dev.signal)(s.frames_done + f as u64, frame);
        }
        s.frames_done += s.block as u64;
        if dev.stalled {
            return;
        }
        let buf = &s.buf[..];
        realtime(|| (s.cb)(buf));
    }

    fn fire_output(&mut self, i: usize) {
        let s = &mut self.outputs[i];
        s.buf.fill(0.0);
        let buf = &mut s.buf[..];
        realtime(|| (s.cb)(buf));
        s.frames_done += s.block as u64;
        let dev = self.out_dev.get_mut(&s.dev).expect("device exists while a stream is open");
        dev.blocks += 1;
        if let Some(cap) = dev.capture.as_mut() {
            cap.extend_from_slice(&s.buf);
        }
    }
}

/// The fake backend. Clone the `Arc` to keep driving it while the engine holds another.
pub struct FakeBackend {
    sim: Arc<Mutex<Sim>>,
}

impl FakeBackend {
    pub fn new() -> Arc<Self> {
        Arc::new(Self { sim: Arc::new(Mutex::new(Sim::default())) })
    }

    fn sim(&self) -> MutexGuard<'_, Sim> {
        self.sim.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn add_input(&self, name: &str, spec: DeviceSpec, signal: Signal) {
        self.sim().in_dev.insert(name.to_string(), InDev { spec, signal, unplugged: false, stalled: false });
    }

    pub fn add_output(&self, name: &str, spec: DeviceSpec) {
        self.sim().out_dev.insert(name.to_string(), OutDev { spec, unplugged: false, capture: None, blocks: 0 });
    }

    /// Change what an input device plays from now on.
    pub fn set_signal(&self, name: &str, signal: Signal) {
        if let Some(d) = self.sim().in_dev.get_mut(name) {
            d.signal = signal;
        }
    }

    /// Start (or stop) keeping everything an output device is sent, interleaved.
    pub fn capture_output(&self, name: &str, on: bool) {
        if let Some(d) = self.sim().out_dev.get_mut(name) {
            d.capture = on.then(Vec::new);
        }
    }

    /// Take what has been captured so far.
    pub fn take_output(&self, name: &str) -> Vec<f32> {
        self.sim().out_dev.get_mut(name).and_then(|d| d.capture.as_mut().map(std::mem::take)).unwrap_or_default()
    }

    /// Callbacks an output device has had since the start.
    pub fn output_blocks(&self, name: &str) -> u64 {
        self.sim().out_dev.get(name).map_or(0, |d| d.blocks)
    }

    /// Unplug or replug a device: opens fail while it is unplugged, and open streams on it fail at once.
    pub fn set_unplugged(&self, name: &str, unplugged: bool) {
        let mut sim = self.sim();
        let msg = format!("device {name} was unplugged");
        if let Some(d) = sim.in_dev.get_mut(name) {
            d.unplugged = unplugged;
        }
        if let Some(d) = sim.out_dev.get_mut(name) {
            d.unplugged = unplugged;
        }
        if unplugged {
            for s in sim.inputs.iter().filter(|s| s.dev == name) {
                *s.failure.lock().unwrap() = Some(msg.clone());
            }
            for s in sim.outputs.iter().filter(|s| s.dev == name) {
                *s.failure.lock().unwrap() = Some(msg.clone());
            }
        }
    }

    /// Make an open input device stop delivering blocks without reporting an error, or start again.
    pub fn stall_input(&self, name: &str, stalled: bool) {
        if let Some(d) = self.sim().in_dev.get_mut(name) {
            d.stalled = stalled;
        }
    }

    /// Make the open stream(s) on a device report an error without unplugging it (a driver fault: reopening works).
    pub fn fail_streams(&self, name: &str, message: &str) {
        let sim = self.sim();
        for s in sim.inputs.iter().filter(|s| s.dev == name) {
            *s.failure.lock().unwrap() = Some(message.to_string());
        }
        for s in sim.outputs.iter().filter(|s| s.dev == name) {
            *s.failure.lock().unwrap() = Some(message.to_string());
        }
    }

    /// Advance virtual time to `t` seconds, running every callback that comes due.
    pub fn run_until(&self, t: f64) {
        self.sim().run_until(t);
    }

    pub fn advance(&self, seconds: f64) {
        let mut sim = self.sim();
        let t = sim.now + seconds;
        sim.run_until(t);
    }

    /// Run until output device `name` has had `n` more callbacks (other devices run as their clocks dictate).
    pub fn run_output_blocks(&self, name: &str, n: u64) {
        let mut sim = self.sim();
        let want = sim.out_dev.get(name).map_or(0, |d| d.blocks) + n;
        // Step in small slices of virtual time until the device has produced them.
        while sim.out_dev.get(name).map_or(want, |d| d.blocks) < want {
            let t = sim.now + 0.0005;
            sim.run_until(t);
            if sim.outputs.iter().all(|s| s.dev != name) {
                break;
            }
        }
    }
}

struct FakeStream {
    sim: Arc<Mutex<Sim>>,
    id: u64,
    failure: Arc<Mutex<Option<String>>>,
}

impl Stream for FakeStream {
    fn error(&self) -> Option<String> {
        self.failure.lock().unwrap().clone()
    }
}

impl Drop for FakeStream {
    fn drop(&mut self) {
        let mut sim = self.sim.lock().unwrap_or_else(|e| e.into_inner());
        sim.inputs.retain(|s| s.id != self.id);
        sim.outputs.retain(|s| s.id != self.id);
    }
}

impl Backend for FakeBackend {
    fn now(&self) -> f64 {
        self.sim().now
    }

    fn input_format(&self, device: Option<&str>) -> Result<DeviceFormat, String> {
        let sim = self.sim();
        let name = device.unwrap_or("default");
        match sim.in_dev.get(name) {
            Some(d) if !d.unplugged => Ok(DeviceFormat { channels: d.spec.channels, rate: d.spec.rate }),
            Some(_) => Err(format!("device {name} is not connected")),
            None => Err(format!("no input device {name}")),
        }
    }

    fn open_input(&self, device: Option<&str>, format: DeviceFormat, callback: InputCallback) -> Result<Box<dyn Stream>, String> {
        let mut sim = self.sim();
        let name = device.unwrap_or("default").to_string();
        let Some(d) = sim.in_dev.get(&name) else { return Err(format!("no input device {name}")) };
        if d.unplugged {
            return Err(format!("device {name} is not connected"));
        }
        let (block, ppm) = (d.spec.block, d.spec.error_ppm);
        let id = sim.next_id;
        sim.next_id += 1;
        let failure = Arc::new(Mutex::new(None));
        let t0 = sim.now;
        sim.inputs.push(Active {
            id,
            dev: name,
            cb: callback,
            block,
            channels: format.channels,
            rate_actual: format.rate as f64 * (1.0 + ppm / 1e6),
            t0,
            frames_done: 0,
            buf: vec![0.0; block * format.channels],
            failure: failure.clone(),
        });
        Ok(Box::new(FakeStream { sim: self.sim.clone(), id, failure }))
    }

    fn output_format(&self, device: Option<&str>, channels: usize) -> Result<DeviceFormat, String> {
        let sim = self.sim();
        let name = device.unwrap_or("default");
        match sim.out_dev.get(name) {
            Some(d) if !d.unplugged => {
                if channels > d.spec.channels {
                    Err(format!("device {name} has {} channels, {channels} wanted", d.spec.channels))
                } else {
                    Ok(DeviceFormat { channels, rate: d.spec.rate })
                }
            }
            Some(_) => Err(format!("device {name} is not connected")),
            None => Err(format!("no output device {name}")),
        }
    }

    fn open_output(&self, device: Option<&str>, format: DeviceFormat, callback: OutputCallback) -> Result<Box<dyn Stream>, String> {
        let mut sim = self.sim();
        let name = device.unwrap_or("default").to_string();
        let Some(d) = sim.out_dev.get(&name) else { return Err(format!("no output device {name}")) };
        if d.unplugged {
            return Err(format!("device {name} is not connected"));
        }
        let (block, ppm) = (d.spec.block, d.spec.error_ppm);
        let id = sim.next_id;
        sim.next_id += 1;
        let failure = Arc::new(Mutex::new(None));
        let t0 = sim.now;
        sim.outputs.push(Active {
            id,
            dev: name,
            cb: callback,
            block,
            channels: format.channels,
            rate_actual: format.rate as f64 * (1.0 + ppm / 1e6),
            t0,
            frames_done: 0,
            buf: vec![0.0; block * format.channels],
            failure: failure.clone(),
        });
        Ok(Box::new(FakeStream { sim: self.sim.clone(), id, failure }))
    }
}
