//! Python binding for Relay's NDI engine: `relay_ndi.Connection`, `Sender`, `Router`, `find`.
//!
//! Every call that can wait (finding, connecting, reading the queue, a paced send) releases the GIL. The capture
//! loop itself is a Rust thread, so Python only ever collects what it has already queued.

use numpy::{PyArray1, PyArrayMethods, PyReadonlyArray2, PyReadonlyArray3, PyUntypedArrayMethods};
use pyo3::pybacked::PyBackedBytes;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict, PyList};
use relay_ndi::audio::{AudioMeter as CoreMeter, Snapshot};
use relay_ndi::find::Finder;
use relay_ndi::health::{self, Stats, SyncMeter as CoreSync, Timing as CoreTiming};
use std::sync::Mutex;
use relay_ndi::patterns::{self, Patch, Tone as CoreTone};
use relay_ndi::picture;
use relay_ndi::recv::{Event, Receiver};
use relay_ndi::route::Router as CoreRouter;
use relay_ndi::scopes::{self as sc, Frame, Grid, Pack};
use relay_ndi::send::Sender as CoreSender;
use std::path::PathBuf;
use std::time::Duration;

fn os_err(e: String) -> PyErr {
    PyOSError::new_err(e)
}

/// Folders to look for the NDI runtime in before its usual places. Call before the first use.
#[pyfunction]
fn set_search_dirs(dirs: Vec<String>) {
    relay_ndi::set_search_dirs(dirs.into_iter().map(PathBuf::from).collect());
}

/// The path of the NDI runtime in use; raises OSError saying why when there is none.
#[pyfunction]
fn library(py: Python<'_>) -> PyResult<String> {
    py.detach(relay_ndi::api).map(|a| a.path.display().to_string()).map_err(os_err)
}

/// Every source the SDK sees: [{"name", "address"}].
#[pyfunction]
#[pyo3(signature = (seconds=3.0))]
fn find<'py>(py: Python<'py>, seconds: f64) -> PyResult<Vec<Bound<'py, PyDict>>> {
    let found = py.detach(|| Finder::scan(seconds)).map_err(os_err)?;
    found
        .into_iter()
        .map(|s| {
            let d = PyDict::new(py);
            d.set_item("name", s.name)?;
            d.set_item("address", s.address)?;
            Ok(d)
        })
        .collect()
}

#[pyclass(frozen, module = "relay_ndi")]
struct Connection {
    rx: Receiver,
}

#[pymethods]
impl Connection {
    #[new]
    #[pyo3(signature = (name, low=false, wait=5.0))]
    fn new(py: Python<'_>, name: String, low: bool, wait: f64) -> PyResult<Self> {
        let rx = py.detach(|| Receiver::open(&name, low, wait)).map_err(os_err)?;
        Ok(Connection { rx })
    }

    /// The next thing that arrived, or None after `timeout_ms`. A picture is {"width", "height", "fps", "fourcc",
    /// "data", "age_ms"}; sound is {"audio_db", "audio" (float32, channels by samples), "rate"}; what the sender says
    /// about itself is {"meta": xml}.
    #[pyo3(signature = (timeout_ms=500))]
    fn receive<'py>(&self, py: Python<'py>, timeout_ms: u64) -> PyResult<Option<Bound<'py, PyDict>>> {
        let Some(ev) = py.detach(|| self.rx.next(Duration::from_millis(timeout_ms))) else { return Ok(None) };
        let d = PyDict::new(py);
        match ev {
            Event::Video(v, at) => {
                d.set_item("width", v.width)?;
                d.set_item("height", v.height)?;
                d.set_item("fps", (v.fps * 1000.0).round() / 1000.0)?;
                d.set_item("fourcc", v.fourcc)?;
                d.set_item("data", PyBytes::new(py, &v.data))?;
                d.set_item("age_ms", at.elapsed().as_secs_f64() * 1000.0)?;
                d.set_item("timecode", v.timecode)?;
                d.set_item("timestamp", v.timestamp)?;
                if !v.metadata.is_empty() {
                    d.set_item("frame_meta", v.metadata)?;
                }
            }
            Event::Audio(a) => {
                let arr = PyArray1::from_vec(py, a.data).reshape([a.channels, a.samples])?;
                d.set_item("audio_db", a.peak_db)?;
                d.set_item("audio", arr)?;
                d.set_item("rate", a.rate)?;
                d.set_item("timecode", a.timecode)?;
                d.set_item("timestamp", a.timestamp)?;
            }
            Event::Meta(xml) => d.set_item("meta", xml)?,
        }
        Ok(Some(d))
    }

    /// The SDK's frame counts, plus the pictures Relay's own queue had to drop.
    fn perf<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let c = self.rx.counts();
        let out = PyDict::new(py);
        for (key, total, dropped) in [("video", c.video_total, c.video_dropped), ("audio", c.audio_total, c.audio_dropped),
                                      ("metadata", c.meta_total, c.meta_dropped)] {
            let d = PyDict::new(py);
            d.set_item("frames_total", total)?;
            d.set_item("frames_dropped", dropped)?;
            out.set_item(key, d)?;
        }
        out.set_item("queue_overflow", c.overflow)?;
        Ok(out)
    }

    fn close(&self, py: Python<'_>) {
        py.detach(|| self.rx.close());
    }
}

#[pyclass(frozen, module = "relay_ndi")]
struct Sender {
    tx: CoreSender,
}

#[pymethods]
impl Sender {
    #[new]
    #[pyo3(signature = (name, width=1280, height=720, fps=30.0, fourcc="BGRA", audio_offset_ms=None))]
    fn new(py: Python<'_>, name: String, width: u32, height: u32, fps: f64, fourcc: &str, audio_offset_ms: Option<f64>) -> PyResult<Self> {
        let tx = py.detach(|| CoreSender::open_timed(&name, width, height, fps, fourcc, audio_offset_ms)).map_err(os_err)?;
        Ok(Sender { tx })
    }

    /// One picture (`width * height * 4` BGRA bytes) and optionally float32 audio shaped (channels, samples).
    #[pyo3(signature = (data, audio=None))]
    fn send(&self, py: Python<'_>, data: PyBackedBytes, audio: Option<PyReadonlyArray2<'_, f32>>) -> PyResult<()> {
        let bgra: &[u8] = &data;
        let planar = audio.map(|a| {
            let (ch, n) = (a.shape()[0], a.shape()[1]);
            (a.as_array().iter().copied().collect::<Vec<f32>>(), ch, n)
        });
        py.detach(|| self.tx.send(bgra, planar.as_ref().map(|(v, c, n)| (v.as_slice(), *c, *n)))).map_err(PyValueError::new_err)
    }

    fn close(&self, py: Python<'_>) {
        py.detach(|| self.tx.close());
    }
}

#[pyclass(frozen, module = "relay_ndi")]
struct Router {
    r: CoreRouter,
}

#[pymethods]
impl Router {
    #[new]
    fn new(py: Python<'_>, name: String) -> PyResult<Self> {
        let r = py.detach(|| CoreRouter::open(&name)).map_err(os_err)?;
        Ok(Router { r })
    }

    #[pyo3(signature = (source, address=""))]
    fn change(&self, py: Python<'_>, source: &str, address: &str) -> bool {
        py.detach(|| self.r.change(source, address))
    }

    fn clear(&self, py: Python<'_>) -> bool {
        py.detach(|| self.r.clear())
    }

    fn close(&self, py: Python<'_>) {
        py.detach(|| self.r.close());
    }
}

#[pyclass(frozen, module = "relay_ndi")]
struct AudioMeter {
    m: Mutex<CoreMeter>,
}

#[pymethods]
impl AudioMeter {
    #[new]
    fn new() -> Self {
        AudioMeter { m: Mutex::new(CoreMeter::new()) }
    }

    #[getter]
    fn rate(&self) -> u32 {
        self.m.lock().unwrap_or_else(|p| p.into_inner()).rate()
    }

    #[getter]
    fn ch(&self) -> usize {
        self.m.lock().unwrap_or_else(|p| p.into_inner()).channels()
    }

    /// `samples` is float32 shaped (channels, n); `now` is monotonic seconds.
    fn feed(&self, py: Python<'_>, samples: PyReadonlyArray2<'_, f32>, rate: u32, now: f64) {
        let (ch, n) = (samples.shape()[0], samples.shape()[1]);
        let flat: Vec<f32> = samples.as_array().iter().copied().collect();
        py.detach(|| self.m.lock().unwrap_or_else(|p| p.into_inner()).feed(&flat, ch, n, rate, now));
    }

    fn reset(&self) {
        self.m.lock().unwrap_or_else(|p| p.into_inner()).reset();
    }

    /// The same dict as Python's `AudioMeter.snapshot`, or None when no audio has arrived lately.
    fn snapshot<'py>(&self, py: Python<'py>, now: f64) -> PyResult<Option<Bound<'py, PyDict>>> {
        let snap: Option<Snapshot> = self.m.lock().unwrap_or_else(|p| p.into_inner()).snapshot(now);
        let Some(s) = snap else { return Ok(None) };
        let d = PyDict::new(py);
        let chans = PyList::empty(py);
        for c in &s.channels {
            let e = PyDict::new(py);
            e.set_item("rms", c.rms)?;
            e.set_item("peak", c.peak)?;
            e.set_item("hold", c.hold)?;
            e.set_item("true_peak", c.true_peak)?;
            e.set_item("dc", c.dc)?;
            e.set_item("mute", c.mute)?;
            chans.append(e)?;
        }
        d.set_item("rate", s.rate)?;
        d.set_item("channels", chans)?;
        d.set_item("clips", s.clips)?;
        d.set_item("clip_now", s.clip_now)?;
        d.set_item("momentary", s.momentary)?;
        d.set_item("short", s.short)?;
        d.set_item("integrated", s.integrated)?;
        d.set_item("lra", s.lra)?;
        d.set_item("correlation", s.correlation)?;
        d.set_item("spectrum", s.spectrum)?;
        let alarms: Vec<Vec<&str>> = s.alarms.iter().map(|(a, b)| vec![*a, *b]).collect();
        d.set_item("alarms", alarms)?;
        Ok(Some(d))
    }
}

/// The frame as `(width, height, rgb bytes)`, no wider than `max_width`; None for a pixel format not handled.
#[pyfunction]
fn to_rgb<'py>(py: Python<'py>, data: PyBackedBytes, width: usize, height: usize, fourcc: &str, max_width: usize)
               -> Option<(usize, usize, Bound<'py, PyBytes>)> {
    let bytes: &[u8] = &data;
    let img = py.detach(|| picture::to_rgb(bytes, width, height, fourcc, max_width))?;
    Some((img.width, img.height, PyBytes::new(py, &img.data)))
}

/// The frame as JPEG bytes, no wider than `max_width`; None for a pixel format not handled.
#[pyfunction]
fn jpeg<'py>(py: Python<'py>, data: PyBackedBytes, width: usize, height: usize, fourcc: &str, max_width: usize, quality: u8)
             -> Option<Bound<'py, PyBytes>> {
    let bytes: &[u8] = &data;
    let out = py.detach(|| picture::jpeg(bytes, width, height, fourcc, max_width, quality))?;
    Some(PyBytes::new(py, &out))
}

/// A test pattern's unchanging picture as BGRA bytes.
#[pyfunction]
fn base_picture<'py>(py: Python<'py>, pattern: &str, width: usize, height: usize) -> Bound<'py, PyBytes> {
    PyBytes::new(py, &py.detach(|| patterns::base_picture(pattern, width, height)))
}

/// One test-pattern frame (BGRA bytes): `base` (the BGRA picture shaped (height, width, 4)) plus the moving element and,
/// if `burn` is `(x, y, w, h, bgra bytes)`, the burned-in text patch.
#[pyfunction]
#[pyo3(signature = (pattern, base, now, frame, burn=None))]
fn draw_frame<'py>(py: Python<'py>, pattern: &str, base: PyReadonlyArray3<'_, u8>, now: f64, frame: u64,
                   burn: Option<(i64, i64, usize, usize, PyBackedBytes)>) -> PyResult<Bound<'py, PyBytes>> {
    let (height, width, depth) = (base.shape()[0], base.shape()[1], base.shape()[2]);
    let Ok(pixels) = base.as_slice() else { return Err(PyValueError::new_err("the base picture must be contiguous")) };
    if depth != 4 {
        return Err(PyValueError::new_err("the base picture must be BGRA, shaped (height, width, 4)"));
    }
    let out = py.detach(|| {
        let patch = burn.as_ref().map(|(x, y, w, h, b)| Patch { x: *x, y: *y, w: *w, h: *h, bgra: b });
        patterns::draw_frame(pattern, pixels, width, height, now, frame, patch.as_ref())
    });
    Ok(PyBytes::new(py, &out))
}

#[pyclass(frozen, module = "relay_ndi")]
struct Tone {
    t: Mutex<CoreTone>,
}

#[pymethods]
impl Tone {
    #[new]
    fn new(mode: &str, level: f64, fps: f64) -> Self {
        Tone { t: Mutex::new(CoreTone::new(mode, level, fps)) }
    }

    fn channel(&self) -> &'static str {
        self.t.lock().unwrap_or_else(|p| p.into_inner()).channel()
    }

    /// The audio for the next video frame: float32, shaped (2, samples).
    fn next<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyAny>> {
        let (data, n) = self.t.lock().unwrap_or_else(|p| p.into_inner()).next_frame();
        Ok(PyArray1::from_vec(py, data).reshape([2, n])?.into_any())
    }
}

type ScopeResult<'py> = (Option<Bound<'py, PyDict>>, Option<Bound<'py, PyAny>>);

fn pack_dict<'py>(py: Python<'py>, p: &Pack) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("w", p.w)?;
    d.set_item("h", p.h)?;
    d.set_item("b64", &p.b64)?;
    Ok(d)
}

/// The picture scopes and readouts for one frame (UYVY, BGRA, BGRX, RGBA or RGBX), and the grid to pass as `prev`
/// next time for the frame difference. `(None, prev)` when the pixel format is not handled. The same dict as
/// Python's `analysis.scopes`.
#[pyfunction]
#[pyo3(signature = (data, width, height, fourcc, prev=None))]
fn scopes<'py>(py: Python<'py>, data: PyBackedBytes, width: usize, height: usize, fourcc: &str,
               prev: Option<PyReadonlyArray2<'py, f32>>) -> PyResult<ScopeResult<'py>> {
    let prev_grid = prev.as_ref().map(|a| Grid { h: a.shape()[0], w: a.shape()[1], v: a.as_array().iter().copied().collect() });
    let bytes: &[u8] = &data;
    let out = py.detach(|| sc::scopes(&Frame { data: bytes, width, height, fourcc }, prev_grid.as_ref()));
    let Some((s, grid)) = out else {
        return Ok((None, prev.map(|a| a.as_any().clone())));
    };
    let d = PyDict::new(py);
    d.set_item("waveform", pack_dict(py, &s.waveform)?)?;
    let parade = PyList::new(py, s.parade.iter().map(|p| pack_dict(py, p)).collect::<PyResult<Vec<_>>>()?)?;
    d.set_item("parade", parade)?;
    d.set_item("vectorscope", pack_dict(py, &s.vectorscope)?)?;
    d.set_item("histogram", s.histogram)?;
    let r = PyDict::new(py);
    let rd = &s.read;
    r.set_item("luma_min", rd.luma_min)?;
    r.set_item("luma_max", rd.luma_max)?;
    r.set_item("luma_mean", rd.luma_mean)?;
    r.set_item("sub_black", rd.sub_black)?;
    r.set_item("super_white", rd.super_white)?;
    r.set_item("illegal_rgb", rd.illegal_rgb)?;
    r.set_item("frame_diff", rd.frame_diff)?;
    r.set_item("sharpness", rd.sharpness)?;
    r.set_item("letterbox", rd.letterbox.to_vec())?;
    r.set_item("pillarbox", rd.pillarbox.to_vec())?;
    r.set_item("aspect", rd.aspect)?;
    r.set_item("matrix", rd.matrix)?;
    r.set_item("width", rd.width)?;
    r.set_item("height", rd.height)?;
    d.set_item("read", r)?;
    let alarms: Vec<Vec<&str>> = s.alarms.iter().map(|(a, b)| vec![*a, *b]).collect();
    d.set_item("alarms", alarms)?;
    let g = PyArray1::from_vec(py, grid.v).reshape([grid.h, grid.w])?;
    Ok((Some(d), Some(g.into_any())))
}

fn stats_dict<'py>(py: Python<'py>, s: &Stats) -> PyResult<Bound<'py, PyDict>> {
    let d = PyDict::new(py);
    d.set_item("frames", s.frames)?;
    d.set_item("late", s.late)?;
    d.set_item("long_gaps", s.long_gaps)?;
    d.set_item("declared_fps", s.declared_fps)?;
    d.set_item("recent_late", s.recent_late)?;
    d.set_item("since_last_s", s.since_last_s)?;
    match &s.measured {
        None => {
            d.set_item("interval_ms", py.None())?;
            d.set_item("measured_fps", 0.0)?;
            d.set_item("jitter_ms", py.None())?;
            d.set_item("rate_mbps", 0.0)?;
            d.set_item("frame_bytes", py.None())?;
        }
        Some(m) => {
            let iv = PyDict::new(py);
            iv.set_item("min", m.iv_min)?;
            iv.set_item("avg", m.iv_avg)?;
            iv.set_item("max", m.iv_max)?;
            iv.set_item("sd", m.iv_sd)?;
            d.set_item("interval_ms", iv)?;
            d.set_item("measured_fps", m.measured_fps)?;
            let j = PyDict::new(py);
            j.set_item("mean", m.jitter_mean)?;
            j.set_item("peak", m.jitter_peak)?;
            d.set_item("jitter_ms", j)?;
            d.set_item("rate_mbps", m.rate_mbps)?;
            let b = PyDict::new(py);
            b.set_item("min", m.bytes_min)?;
            b.set_item("avg", m.bytes_avg)?;
            b.set_item("max", m.bytes_max)?;
            d.set_item("frame_bytes", b)?;
        }
    }
    Ok(d)
}

fn num(d: &Bound<'_, PyDict>, key: &str) -> f64 {
    d.get_item(key).ok().flatten().and_then(|v| v.extract::<f64>().ok()).unwrap_or(0.0)
}

fn sub(d: &Bound<'_, PyDict>, key: &str, field: &str) -> f64 {
    d.get_item(key).ok().flatten().and_then(|v| v.cast_into::<PyDict>().ok()).map(|x| num(&x, field)).unwrap_or(0.0)
}

fn stats_from(d: &Bound<'_, PyDict>) -> Stats {
    let measured = if d.get_item("interval_ms").ok().flatten().is_some_and(|v| !v.is_none()) {
        Some(health::Measured {
            iv_avg: sub(d, "interval_ms", "avg"),
            measured_fps: num(d, "measured_fps"),
            jitter_mean: sub(d, "jitter_ms", "mean"),
            ..Default::default()
        })
    } else {
        None
    };
    Stats {
        frames: num(d, "frames") as u64,
        declared_fps: num(d, "declared_fps"),
        recent_late: num(d, "recent_late") as usize,
        since_last_s: d.get_item("since_last_s").ok().flatten().and_then(|v| v.extract::<f64>().ok()),
        measured,
        ..Default::default()
    }
}

/// `(state, why)` from a `Timing.stats` dict and the SDK's dropped-frame count.
#[pyfunction]
#[pyo3(signature = (stats, dropped=0))]
fn health_of(stats: &Bound<'_, PyDict>, dropped: i64) -> (&'static str, String) {
    let (state, why) = health::health(&stats_from(stats), dropped);
    (state.name(), why)
}

/// Frame arrivals for one source: `record` on each picture, `stats` any time.
#[pyclass(frozen, module = "relay_ndi")]
struct Timing {
    t: Mutex<CoreTiming>,
}

#[pymethods]
impl Timing {
    #[new]
    fn new() -> Self {
        Timing { t: Mutex::new(CoreTiming::new()) }
    }

    /// "", "late" or "gap" for what this arrival was.
    #[pyo3(signature = (now, nbytes, fps=0.0))]
    fn record(&self, now: f64, nbytes: u64, fps: f64) -> &'static str {
        self.t.lock().unwrap_or_else(|p| p.into_inner()).record(now, nbytes, fps).name()
    }

    fn stats<'py>(&self, py: Python<'py>, now: f64) -> PyResult<Bound<'py, PyDict>> {
        let s = self.t.lock().unwrap_or_else(|p| p.into_inner()).stats(now);
        stats_dict(py, &s)
    }
}

/// How far the audio runs from the picture, by the senders' timestamps and timecodes.
#[pyclass(frozen, module = "relay_ndi")]
struct SyncMeter {
    m: Mutex<CoreSync>,
}

#[pymethods]
impl SyncMeter {
    #[new]
    fn new() -> Self {
        SyncMeter { m: Mutex::new(CoreSync::new()) }
    }

    fn video(&self, timecode: i64, timestamp: i64) {
        self.m.lock().unwrap_or_else(|p| p.into_inner()).video(timecode, timestamp);
    }

    fn audio(&self, timecode: i64, timestamp: i64) {
        self.m.lock().unwrap_or_else(|p| p.into_inner()).audio(timecode, timestamp);
    }

    fn reset(&self) {
        self.m.lock().unwrap_or_else(|p| p.into_inner()).reset();
    }

    fn snapshot<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyDict>> {
        let s = self.m.lock().unwrap_or_else(|p| p.into_inner()).snapshot();
        let d = PyDict::new(py);
        d.set_item("valid", s.valid)?;
        d.set_item("synthesized", s.synthesized)?;
        d.set_item("offset_ms", s.offset_ms)?;
        d.set_item("state", s.state)?;
        Ok(d)
    }
}

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Connection>()?;
    m.add_class::<Sender>()?;
    m.add_class::<Router>()?;
    m.add_class::<AudioMeter>()?;
    m.add_class::<Tone>()?;
    m.add_class::<Timing>()?;
    m.add_class::<SyncMeter>()?;
    m.add_function(wrap_pyfunction!(health_of, m)?)?;
    m.add_function(wrap_pyfunction!(find, m)?)?;
    m.add_function(wrap_pyfunction!(library, m)?)?;
    m.add_function(wrap_pyfunction!(scopes, m)?)?;
    m.add_function(wrap_pyfunction!(to_rgb, m)?)?;
    m.add_function(wrap_pyfunction!(jpeg, m)?)?;
    m.add_function(wrap_pyfunction!(base_picture, m)?)?;
    m.add_function(wrap_pyfunction!(draw_frame, m)?)?;
    m.add_function(wrap_pyfunction!(set_search_dirs, m)?)?;
    Ok(())
}
