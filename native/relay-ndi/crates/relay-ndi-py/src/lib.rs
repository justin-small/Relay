//! Python binding for Relay's NDI engine: `relay_ndi.Connection`, `Sender`, `Router`, `find`.
//!
//! Every call that can wait (finding, connecting, reading the queue, a paced send) releases the GIL. The capture
//! loop itself is a Rust thread, so Python only ever collects what it has already queued.

use numpy::{PyArray1, PyArrayMethods, PyReadonlyArray2, PyUntypedArrayMethods};
use pyo3::pybacked::PyBackedBytes;
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use relay_ndi::find::Finder;
use relay_ndi::recv::{Event, Receiver};
use relay_ndi::route::Router as CoreRouter;
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
            }
            Event::Audio(a) => {
                let arr = PyArray1::from_vec(py, a.data).reshape([a.channels, a.samples])?;
                d.set_item("audio_db", a.peak_db)?;
                d.set_item("audio", arr)?;
                d.set_item("rate", a.rate)?;
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
    #[pyo3(signature = (name, width=1280, height=720, fps=30.0, fourcc="BGRA"))]
    fn new(py: Python<'_>, name: String, width: u32, height: u32, fps: f64, fourcc: &str) -> PyResult<Self> {
        let tx = py.detach(|| CoreSender::open(&name, width, height, fps, fourcc)).map_err(os_err)?;
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

#[pymodule]
fn _native(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<Connection>()?;
    m.add_class::<Sender>()?;
    m.add_class::<Router>()?;
    m.add_function(wrap_pyfunction!(find, m)?)?;
    m.add_function(wrap_pyfunction!(library, m)?)?;
    m.add_function(wrap_pyfunction!(set_search_dirs, m)?)?;
    Ok(())
}
