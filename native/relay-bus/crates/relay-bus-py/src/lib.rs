//! Python binding for the Relay audio bus (issue 599 spike, phase 2): `relay_bus.Bus`.
//!
//! Every call that can block (opening devices, waiting on a tap, taking the engine lock) releases the GIL. No audio
//! callback calls Python: the callbacks are the engine's and the cpal backend's, which know nothing about Python.
//! A Rust thread calls `supervise()` every 0.5 s and at once when the device watcher reports a change, so Python
//! does not have to.

use numpy::{PyArray1, PyArrayMethods, PyReadonlyArrayDyn, PyUntypedArrayMethods};
use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use relay_bus::fake::{signal, DeviceSpec, FakeBackend};
use relay_bus::analysis::spl::CalibrateError;
use relay_bus::{Backend, Cell, ChannelSel, Config, Engine, Error, LaneSpec, MeterReading, OutputKind, OutputSpec, ProbeHub, ProbeReading, ProbeSpec, StreamState, TapOptions};
use relay_bus_cpal::{list_devices as cpal_list_devices, ChangeSignal, CpalBackend, DeviceWatcher};
use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

const SUPERVISE_EVERY: Duration = Duration::from_millis(500);
const FAKE_TICK: Duration = Duration::from_millis(5);

fn rt_err(e: Error) -> PyErr {
    match e {
        Error::Config(_) | Error::UnknownInput(_) | Error::UnknownOutput(_) | Error::UnknownTap(_) | Error::UnknownProbe(_) => PyValueError::new_err(e.to_string()),
        _ => PyRuntimeError::new_err(e.to_string()),
    }
}

fn bad(msg: String) -> PyErr {
    PyValueError::new_err(msg)
}

// -- the config dict ----------------------------------------------------------------------------------------------

fn field<'py, T: for<'a> FromPyObject<'a, 'py>>(d: &Bound<'py, PyDict>, key: &str, what: &str) -> PyResult<Option<T>>
where
    for<'a> <T as FromPyObject<'a, 'py>>::Error: Into<PyErr>,
{
    match d.get_item(key)? {
        None => Ok(None),
        Some(v) if v.is_none() => Ok(None),
        Some(v) => v.extract::<T>().map(Some).map_err(|e| bad(format!("{what}.{key}: {}", Into::<PyErr>::into(e)))),
    }
}

fn items<'py>(d: &Bound<'py, PyDict>, key: &str) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let Some(v) = d.get_item(key)? else { return Ok(Vec::new()) };
    if v.is_none() {
        return Ok(Vec::new());
    }
    let list = v.cast::<PyList>().map_err(|_| bad(format!("{key} must be a list")))?;
    list.iter().map(|i| i.cast_into::<PyDict>().map_err(|_| bad(format!("every entry of {key} must be a dict")))).collect()
}

/// The dict is the output of Python's `audiobus.normalize()`: `{enabled, lanes: [{lane, name, source, device,
/// channel}], outputs: [{id, name, output, device, channels, latency_ms}], matrix: [{in, out, ch, gain_db}]}`.
fn parse_config(d: &Bound<'_, PyDict>) -> PyResult<Config> {
    let mut cfg = Config { enabled: field::<bool>(d, "enabled", "config")?.unwrap_or(false), ..Default::default() };
    for l in items(d, "lanes")? {
        let channel = match l.get_item("channel")? {
            None => ChannelSel::Index(0),
            Some(v) if v.is_none() => ChannelSel::Index(0),
            Some(v) => {
                if let Ok(s) = v.extract::<String>() {
                    if s.trim().eq_ignore_ascii_case("mix") {
                        ChannelSel::Mix
                    } else {
                        return Err(bad(format!("lane.channel {s:?} is not an integer or \"mix\"")));
                    }
                } else {
                    ChannelSel::Index(v.extract::<usize>().map_err(|_| bad("lane.channel must be an integer or \"mix\"".into()))?)
                }
            }
        };
        cfg.lanes.push(LaneSpec {
            lane: field::<usize>(&l, "lane", "lane")?.ok_or_else(|| bad("lane.lane is missing".into()))?,
            name: field::<String>(&l, "name", "lane")?.unwrap_or_default(),
            source: field::<String>(&l, "source", "lane")?.unwrap_or_else(|| relay_bus::DEVICE_SOURCE.into()),
            device: field::<String>(&l, "device", "lane")?,
            channel,
        });
    }
    for o in items(d, "outputs")? {
        cfg.outputs.push(OutputSpec {
            id: field::<String>(&o, "id", "output")?.ok_or_else(|| bad("output.id is missing".into()))?,
            name: field::<String>(&o, "name", "output")?.unwrap_or_default(),
            output: field::<String>(&o, "output", "output")?.unwrap_or_else(|| relay_bus::DEVICE_SOURCE.into()),
            device: field::<String>(&o, "device", "output")?,
            channels: field::<usize>(&o, "channels", "output")?.unwrap_or(2),
            latency_ms: field::<u32>(&o, "latency_ms", "output")?.unwrap_or(60),
        });
    }
    for c in items(d, "matrix")? {
        cfg.matrix.push(Cell {
            lane: field::<usize>(&c, "in", "cell")?.ok_or_else(|| bad("cell.in is missing".into()))?,
            out: field::<String>(&c, "out", "cell")?.ok_or_else(|| bad("cell.out is missing".into()))?,
            ch: field::<usize>(&c, "ch", "cell")?.ok_or_else(|| bad("cell.ch is missing".into()))?,
            gain_db: field::<f32>(&c, "gain_db", "cell")?.unwrap_or(0.0),
        });
    }
    Ok(cfg)
}

// -- status dict ----------------------------------------------------------------------------------------------------

fn state_str(s: StreamState) -> &'static str {
    match s {
        StreamState::Open => "open",
        StreamState::Waiting => "waiting",
        StreamState::Closed => "closed",
    }
}

fn meter<'py>(py: Python<'py>, m: &MeterReading) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("rms", m.rms)?;
    d.set_item("peak", m.peak)?;
    d.set_item("clipping", m.clipping)?;
    d.set_item("clipped_samples", m.clipped_samples)?;
    Ok(d)
}

fn status_dict<'py>(py: Python<'py>, s: &relay_bus::Status, advisories: u64) -> PyResult<Bound<'py, PyDict>> {
    let out = PyDict::new(py);
    out.set_item("running", s.running)?;
    out.set_item("lane_rate", s.lane_rate)?;
    out.set_item("advisories", advisories)?;
    let lanes = PyList::empty(py);
    for l in &s.lanes {
        let d = PyDict::new(py);
        d.set_item("lane", l.lane)?;
        d.set_item("name", &l.name)?;
        d.set_item("patched", l.patched)?;
        d.set_item("input", &l.input)?;
        d.set_item("state", l.state.map(state_str))?;
        d.set_item("error", &l.error)?;
        d.set_item("subscribers", l.subscribers)?;
        d.set_item("meter", meter(py, &l.meter)?)?;
        d.set_item("last_input_age_s", l.last_input_age_s)?;
        lanes.append(d)?;
    }
    out.set_item("lanes", lanes)?;
    let inputs = PyList::empty(py);
    for i in &s.inputs {
        let d = PyDict::new(py);
        d.set_item("id", &i.id)?;
        d.set_item("source", &i.source)?;
        d.set_item("device", &i.device)?;
        d.set_item("state", state_str(i.state))?;
        d.set_item("error", &i.error)?;
        d.set_item("rate", i.rate)?;
        d.set_item("channels", i.channels)?;
        d.set_item("lanes", &i.lanes)?;
        d.set_item("dropped", i.dropped)?;
        inputs.append(d)?;
    }
    out.set_item("inputs", inputs)?;
    let outputs = PyList::empty(py);
    for o in &s.outputs {
        let d = PyDict::new(py);
        d.set_item("id", &o.id)?;
        d.set_item("name", &o.name)?;
        d.set_item("kind", if o.kind == OutputKind::Device { "device" } else { "pull" })?;
        d.set_item("state", state_str(o.state))?;
        d.set_item("error", &o.error)?;
        d.set_item("channels", o.channels)?;
        d.set_item("rate", o.rate)?;
        d.set_item("latency_ms", o.latency_ms)?;
        d.set_item("target_ms", o.target_ms)?;
        d.set_item("underruns", o.underruns)?;
        d.set_item("resyncs", o.resyncs)?;
        d.set_item("queued_ms", o.queued_ms)?;
        d.set_item("fill_min_ms", o.fill_min_ms)?;
        d.set_item("fill_max_ms", o.fill_max_ms)?;
        d.set_item("ratio", o.ratio)?;
        d.set_item("drift", o.drift)?;
        d.set_item("frames_out", o.frames_out)?;
        let ms = PyList::empty(py);
        for m in &o.meters {
            ms.append(meter(py, m)?)?;
        }
        d.set_item("meters", ms)?;
        outputs.append(d)?;
    }
    out.set_item("outputs", outputs)?;
    let taps = PyList::empty(py);
    for t in &s.taps {
        let d = PyDict::new(py);
        d.set_item("id", t.id)?;
        d.set_item("lane", t.lane)?;
        d.set_item("rate", t.rate)?;
        d.set_item("dropped", t.dropped)?;
        taps.append(d)?;
    }
    out.set_item("taps", taps)?;
    let g = PyDict::new(py);
    g.set_item("underruns", s.glitches.underruns)?;
    g.set_item("resyncs", s.glitches.resyncs)?;
    g.set_item("dropped_frames", s.glitches.dropped_frames)?;
    g.set_item("discard_overflow", s.glitches.discard_overflow)?;
    g.set_item("rejected", s.glitches.rejected)?;
    g.set_item("command_busy", s.glitches.command_busy)?;
    out.set_item("glitches", g)?;
    Ok(out)
}

// -- devices ----------------------------------------------------------------------------------------------------------

const FAKE_DEVICES: [(&str, usize, usize); 3] = [("default", 2, 2), ("Fake Mic", 2, 0), ("Fake Speaker", 0, 2)];

fn device_list<'py>(py: Python<'py>, fake: bool) -> PyResult<Bound<'py, PyList>> {
    let rows: Vec<relay_bus_cpal::DeviceInfo> = if fake {
        FAKE_DEVICES
            .iter()
            .map(|&(name, i, o)| relay_bus_cpal::DeviceInfo {
                name: name.into(),
                inputs: i,
                outputs: o,
                default_rate: 48000,
                is_default_input: name == "default" || name == "Fake Mic",
                is_default_output: name == "default" || name == "Fake Speaker",
            })
            .collect()
    } else {
        py.detach(cpal_list_devices)
    };
    let list = PyList::empty(py);
    for r in rows {
        let d = PyDict::new(py);
        d.set_item("name", r.name)?;
        d.set_item("inputs", r.inputs)?;
        d.set_item("outputs", r.outputs)?;
        d.set_item("default_rate", r.default_rate)?;
        d.set_item("is_default_input", r.is_default_input)?;
        d.set_item("is_default_output", r.is_default_output)?;
        list.append(d)?;
    }
    Ok(list)
}

/// The audio devices cpal can see: a list of `{name, inputs, outputs, default_rate, is_default_input,
/// is_default_output}` (`inputs` and `outputs` are the most channels the device offers).
#[pyfunction]
fn list_devices(py: Python<'_>) -> PyResult<Bound<'_, PyList>> {
    device_list(py, false)
}

// -- the Bus ------------------------------------------------------------------------------------------------------------

/// The 32-lane audio bus. `Bus("cpal")` opens real devices; `Bus("fake")` runs the deterministic fake backend on the
/// wall clock (a 2 channel 1 kHz sine input named "default" and "Fake Mic", outputs "default" and "Fake Speaker"),
/// which is handy for trying the API where there is no sound card.
#[pyclass(frozen, module = "relay_bus")]
struct Bus {
    engine: Arc<Engine>,
    probes: ProbeHub,
    fake: Option<Arc<FakeBackend>>,
    cpal: Option<Arc<CpalBackend>>,
    signal: Arc<ChangeSignal>,
    stop: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
    watcher: Mutex<Option<DeviceWatcher>>,
    watch_method: &'static str,
}

fn supervisor(engine: Arc<Engine>, fake: Option<Arc<FakeBackend>>, signal: Arc<ChangeSignal>, stop: Arc<AtomicBool>) {
    let start = Instant::now();
    let mut seen = signal.count();
    let mut last = Instant::now();
    while !stop.load(Relaxed) {
        let changed = signal.wait(&mut seen, if fake.is_some() { FAKE_TICK } else { SUPERVISE_EVERY });
        if let Some(f) = &fake {
            f.run_until(start.elapsed().as_secs_f64());
        }
        if changed {
            engine.retry_now();
        }
        if changed || last.elapsed() >= SUPERVISE_EVERY {
            last = Instant::now();
            engine.supervise();
        }
    }
}

fn to_vec(a: &PyReadonlyArrayDyn<'_, f32>) -> (Vec<f32>, usize) {
    let channels = if a.ndim() >= 2 { a.shape()[a.ndim() - 1] } else { 1 };
    (a.as_array().iter().copied().collect(), channels)
}

#[pymethods]
impl Bus {
    #[new]
    #[pyo3(signature = (backend = "cpal"))]
    fn new(backend: &str) -> PyResult<Self> {
        let signal = ChangeSignal::new();
        let (engine, fake, cpal, watcher, method): (Arc<Engine>, _, _, _, _) = match backend {
            "cpal" => {
                let b = Arc::new(CpalBackend::new());
                let w = DeviceWatcher::start(signal.clone());
                let m = w.method().as_str();
                (Arc::new(Engine::new(b.clone() as Arc<dyn Backend>)), None, Some(b), Some(w), m)
            }
            "fake" => {
                let f = FakeBackend::new();
                for name in ["default", "Fake Mic"] {
                    f.add_input(name, DeviceSpec::new(2, 48000, 480), signal::sine(1000.0, 48000, 0.25));
                }
                for name in ["default", "Fake Speaker"] {
                    f.add_output(name, DeviceSpec::new(2, 48000, 480));
                }
                (Arc::new(Engine::new(f.clone() as Arc<dyn Backend>)), Some(f), None, None, "none")
            }
            other => return Err(bad(format!("backend must be \"cpal\" or \"fake\", not {other:?}"))),
        };
        let stop = Arc::new(AtomicBool::new(false));
        let thread = {
            let (e, f, s, st) = (engine.clone(), fake.clone(), signal.clone(), stop.clone());
            std::thread::Builder::new()
                .name("relay-bus-supervisor".into())
                .spawn(move || supervisor(e, f, s, st))
                .map_err(|e| PyRuntimeError::new_err(e.to_string()))?
        };
        let probes = ProbeHub::new(engine.clone());
        Ok(Self { engine, probes, fake, cpal, signal, stop, thread: Mutex::new(Some(thread)), watcher: Mutex::new(watcher), watch_method: method })
    }

    /// How device changes are noticed: "coreaudio-listener", "polling-2s" or "none" (the fake backend).
    #[getter]
    fn watch_method(&self) -> &'static str {
        self.watch_method
    }

    /// Make the bus match `config` (the output of `audiobus.normalize()`), changing only what differs.
    fn apply(&self, py: Python<'_>, config: &Bound<'_, PyDict>) -> PyResult<()> {
        let cfg = parse_config(config)?;
        py.detach(|| self.engine.apply(&cfg)).map_err(rt_err)
    }

    /// Everything the panel shows: lanes with meters, inputs, outputs (state, underruns, resyncs, learned `drift`,
    /// meters per channel), taps and the global glitch counters.
    fn status<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let st = py.detach(|| self.engine.status());
        status_dict(py, &st, self.cpal.as_ref().map_or(0, |c| c.advisories()))
    }

    /// Check streams once now. A background thread already does this every 0.5 s and on device changes.
    fn supervise(&self, py: Python<'_>) {
        py.detach(|| self.engine.supervise());
    }

    /// Make waiting streams retry at the next supervise instead of after their backoff, and supervise now.
    fn retry_now(&self, py: Python<'_>) {
        py.detach(|| {
            self.engine.retry_now();
            self.engine.supervise();
        });
    }

    /// Close every stream. `apply()` with `enabled` starts them again.
    fn shutdown(&self, py: Python<'_>) {
        py.detach(|| self.engine.shutdown());
    }

    /// Shut down and stop the background threads. The Bus is unusable for devices afterwards.
    fn close(&self, py: Python<'_>) {
        py.detach(|| {
            self.stop.store(true, Relaxed);
            self.signal.notify();
            if let Some(t) = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take() {
                let _ = t.join();
            }
            self.watcher.lock().unwrap_or_else(|e| e.into_inner()).take();
            self.engine.shutdown();
        });
    }

    /// Feed an external input (a network source): `samples` float32, shape (frames,) or (frames, channels).
    fn push(&self, py: Python<'_>, input_id: &str, samples: PyReadonlyArrayDyn<'_, f32>, rate: u32) -> PyResult<()> {
        let (data, channels) = to_vec(&samples);
        py.detach(|| self.engine.push(input_id, &data, channels, rate)).map_err(rt_err)
    }

    /// `frames` frames of a pull output (a network sender) at 48 kHz: float32, shape (frames, channels).
    fn pull<'py>(&self, py: Python<'py>, output_id: &str, frames: usize) -> PyResult<Bound<'py, numpy::PyArray2<f32>>> {
        let v = py.detach(|| self.engine.pull(output_id, frames)).map_err(rt_err)?;
        let channels = v.len().checked_div(frames).unwrap_or(0);
        PyArray1::from_vec(py, v).reshape([frames, channels]).map_err(|e| PyRuntimeError::new_err(e.to_string()))
    }

    /// Mix a mono float32 clip at `rate` onto `channels` (counted from 0) of an output, heard at its next block.
    fn inject(&self, py: Python<'_>, output_id: &str, channels: Vec<usize>, samples: PyReadonlyArrayDyn<'_, f32>, rate: u32) -> PyResult<()> {
        let (data, _) = to_vec(&samples);
        py.detach(|| self.engine.inject(output_id, &channels, &data, rate)).map_err(rt_err)
    }

    /// Cut every clip playing on an output.
    fn stop_injects(&self, py: Python<'_>, output_id: &str) -> PyResult<()> {
        py.detach(|| self.engine.stop_injects(output_id)).map_err(rt_err)
    }

    /// A reader on lane `lane` (1..32) at `rate` Hz (default 48000). Returns the tap id.
    #[pyo3(signature = (lane, rate = None, capacity_ms = 4000))]
    fn open_tap(&self, py: Python<'_>, lane: usize, rate: Option<u32>, capacity_ms: u32) -> PyResult<u64> {
        py.detach(|| self.engine.open_tap(lane, TapOptions { rate, capacity_ms })).map_err(rt_err)
    }

    /// Up to `max_frames` mono float32 samples from a tap, waiting up to `timeout_s` for some. Returns
    /// `(samples, dropped)`: `dropped` is lane frames lost since the last read because the reader was too slow.
    #[pyo3(signature = (tap, max_frames, timeout_s = 0.0))]
    fn read_tap<'py>(&self, py: Python<'py>, tap: u64, max_frames: usize, timeout_s: f64) -> PyResult<(Bound<'py, PyArray1<f32>>, u64)> {
        let timeout = Duration::from_secs_f64(timeout_s.clamp(0.0, 3600.0));
        let r = py.detach(|| self.engine.read_tap(tap, max_frames, timeout)).map_err(rt_err)?;
        Ok((PyArray1::from_vec(py, r.samples), r.dropped))
    }

    fn close_tap(&self, py: Python<'_>, tap: u64) -> PyResult<()> {
        py.detach(|| self.engine.close_tap(tap)).map_err(rt_err)
    }

    // -- meters (issue 604) ----------------------------------------------------------------------------------
    /// Start a meter on the bus. `kind` is "spl" (`lanes` is one lane) or "loudness" (`lanes` is [left, right]).
    /// The analysis runs on a Rust thread, so the meter sees every sample whatever Python is doing. Returns its id.
    #[pyo3(signature = (kind, lanes, calibration_db = 110.0, silence_dbfs = -50.0))]
    fn open_probe(&self, py: Python<'_>, kind: &str, lanes: Vec<usize>, calibration_db: f64, silence_dbfs: f64) -> PyResult<u64> {
        let spec = match (kind, lanes.as_slice()) {
            ("spl", [lane]) => ProbeSpec::Spl { lane: *lane, calibration_db },
            ("loudness", [left, right]) => ProbeSpec::Loudness { left: *left, right: *right, silence_dbfs },
            ("spl" | "loudness", _) => return Err(bad("an spl meter reads one lane and a loudness meter two".into())),
            other => return Err(bad(format!("a meter kind is \"spl\" or \"loudness\", not {other:?}"))),
        };
        py.detach(|| self.probes.open(spec)).map_err(rt_err)
    }

    fn close_probe(&self, py: Python<'_>, probe: u64) -> PyResult<()> {
        py.detach(|| self.probes.close(probe)).map_err(rt_err)
    }

    /// The meter's newest reading: a dict with `kind`, the values, `dropped`, `idle_s` (None before any audio).
    fn probe_status<'py>(&self, py: Python<'py>, probe: u64) -> PyResult<Bound<'py, PyDict>> {
        let st = self.probes.status(probe).map_err(rt_err)?;
        let d = PyDict::new(py);
        d.set_item("dropped", st.dropped)?;
        d.set_item("idle_s", st.idle_s)?;
        d.set_item("calibration_db", st.calibration_db)?;
        match st.reading {
            ProbeReading::Spl(r) => {
                d.set_item("kind", "spl")?;
                d.set_item("a_fast", r.a_fast)?;
                d.set_item("a_slow", r.a_slow)?;
                d.set_item("c_fast", r.c_fast)?;
                d.set_item("c_slow", r.c_slow)?;
                d.set_item("leq_a", r.leq_a)?;
                d.set_item("bands", r.bands.to_vec())?;
            }
            ProbeReading::Loudness(r) => {
                d.set_item("kind", "loudness")?;
                d.set_item("momentary", r.momentary)?;
                d.set_item("short_term", r.short_term)?;
                d.set_item("integrated", r.integrated)?;
                d.set_item("range_lu", r.range_lu)?;
                d.set_item("true_peak_dbtp", r.true_peak_dbtp)?;
                d.set_item("sample_peak_dbfs", r.sample_peak_dbfs)?;
                d.set_item("correlation", r.correlation)?;
                d.set_item("octaves", r.octaves.to_vec())?;
                d.set_item("seconds", r.seconds)?;
            }
        }
        Ok(d)
    }

    /// The one-second rows a loudness meter wrote since the last call, oldest first.
    fn probe_rows<'py>(&self, py: Python<'py>, probe: u64) -> PyResult<Bound<'py, PyList>> {
        let rows = self.probes.take_rows(probe).map_err(rt_err)?;
        let out = PyList::empty(py);
        for r in rows {
            let d = PyDict::new(py);
            d.set_item("momentary", r.momentary)?;
            d.set_item("short_term", r.short_term)?;
            d.set_item("integrated", r.integrated)?;
            d.set_item("range_lu", r.range_lu)?;
            d.set_item("sample_peak_dbfs", r.sample_peak_dbfs)?;
            d.set_item("true_peak_dbtp", r.true_peak_dbtp)?;
            d.set_item("correlation", r.correlation)?;
            d.set_item("octaves", r.octaves.to_vec())?;
            d.set_item("sound", r.sound)?;
            out.append(d)?;
        }
        Ok(out)
    }

    fn reset_probe(&self, py: Python<'_>, probe: u64) -> PyResult<()> {
        py.detach(|| self.probes.reset(probe)).map_err(rt_err)
    }

    fn set_probe_calibration(&self, py: Python<'_>, probe: u64, calibration_db: f64) -> PyResult<()> {
        py.detach(|| self.probes.set_calibration(probe, calibration_db)).map_err(rt_err)
    }

    fn set_probe_silence(&self, py: Python<'_>, probe: u64, silence_dbfs: f64) -> PyResult<()> {
        py.detach(|| self.probes.set_silence(probe, silence_dbfs)).map_err(rt_err)
    }

    /// Calibrate an SPL meter so it now reads `reference_db`; returns the new calibration. Raises ValueError whose
    /// text is "reference" (not a level between 30 and 140) or "too_quiet" (nothing to read).
    fn calibrate_probe(&self, py: Python<'_>, probe: u64, reference_db: f64) -> PyResult<f64> {
        match py.detach(|| self.probes.calibrate(probe, reference_db)).map_err(rt_err)? {
            Ok(cal) => Ok(cal),
            Err(CalibrateError::Reference) => Err(bad("reference".into())),
            Err(CalibrateError::TooQuiet) => Err(bad("too_quiet".into())),
        }
    }

    /// Raise the device-change signal by hand, as the OS watcher does: waiting streams retry at once. Used to try the
    /// path without a device to unplug.
    fn notify_device_change(&self) {
        self.signal.notify();
    }

    /// Fake backend only: make a device appear to be unplugged (or plugged back in). Its streams fail at once.
    fn fake_unplug(&self, name: &str, unplugged: bool) -> PyResult<()> {
        let Some(f) = &self.fake else { return Err(PyRuntimeError::new_err("fake_unplug needs Bus(\"fake\")")) };
        f.set_unplugged(name, unplugged);
        Ok(())
    }

    /// Devices of this bus's backend (the same rows as the module's `list_devices()`).
    fn list_devices<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        device_list(py, self.fake.is_some())
    }
}

impl Drop for Bus {
    fn drop(&mut self) {
        self.stop.store(true, Relaxed);
        self.signal.notify();
        if let Some(t) = self.thread.get_mut().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = t.join();
        }
    }
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Bus>()?;
    m.add_function(wrap_pyfunction!(list_devices, m)?)?;
    Ok(())
}
