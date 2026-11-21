//! Meter probes: the analysers of `spl` and `loudness`, attached to lanes of a running engine.
//!
//! A probe opens one tap per lane it reads (a tap is one more consumer in the lane's fan-out, with its own ring) and
//! the hub's worker thread drains the taps into the analysers. The thread belongs to Rust: it does not take the GIL
//! and does not wait for Python, so a meter sees every sample while Python is busy. If the thread ever falls behind,
//! the tap's ring drops frames and the probe counts them; it never slows the audio callbacks.

use super::loudness::{LoudnessMeter, LoudnessReading, Row};
use super::spl::{CalibrateError, SplAnalyzer, SplReading};
use crate::engine::Engine;
use crate::error::Error;
use crate::tap::TapOptions;
use crate::util::LANE_RATE;
use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// Most audio one `read_tap` takes per probe per round.
const READ_FRAMES: usize = 24_000;
/// A loudness probe whose two lanes drift apart (one unpatched) keeps at most this much of the one that runs ahead.
const MAX_PENDING: usize = 4 * LANE_RATE as usize;
const ROUND: Duration = Duration::from_millis(10);

#[derive(Debug, Clone, PartialEq)]
pub enum ProbeSpec {
    /// A sound-level meter on one lane.
    Spl { lane: usize, calibration_db: f64 },
    /// A stream-mix loudness meter on a stereo pair of lanes.
    Loudness { left: usize, right: usize, silence_dbfs: f64 },
}

#[derive(Debug, Clone, PartialEq)]
pub enum ProbeReading {
    Spl(SplReading),
    Loudness(LoudnessReading),
}

#[derive(Debug, Clone, PartialEq)]
pub struct ProbeStatus {
    pub reading: ProbeReading,
    /// Lane frames lost because the hub's thread was too slow.
    pub dropped: u64,
    /// Seconds since audio last arrived; `None` if none ever has.
    pub idle_s: Option<f64>,
    /// Calibration of an SPL probe (dB SPL for 0 dBFS RMS).
    pub calibration_db: Option<f64>,
}

enum Kind {
    Spl(Box<SplAnalyzer>),
    Loudness { meter: Box<LoudnessMeter>, left: Vec<f32>, right: Vec<f32> },
}

struct Probe {
    kind: Kind,
    taps: Vec<u64>,
    dropped: u64,
    last_data: Option<Instant>,
}

struct Inner {
    engine: Arc<Engine>,
    probes: Mutex<HashMap<u64, Arc<Mutex<Probe>>>>,
    next: AtomicU64,
    stop: AtomicBool,
}

pub struct ProbeHub {
    inner: Arc<Inner>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

fn lock<T>(m: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

impl Inner {
    fn probe(&self, id: u64) -> Result<Arc<Mutex<Probe>>, Error> {
        lock(&self.probes).get(&id).cloned().ok_or(Error::UnknownProbe(id))
    }

    fn pump(&self) {
        let all: Vec<Arc<Mutex<Probe>>> = lock(&self.probes).values().cloned().collect();
        for p in all {
            let mut p = lock(&p);
            let p = &mut *p;
            let mut got = Vec::with_capacity(p.taps.len());
            for &t in &p.taps {
                match self.engine.read_tap(t, READ_FRAMES, Duration::ZERO) {
                    Ok(r) => {
                        p.dropped += r.dropped;
                        got.push(r.samples);
                    }
                    Err(_) => got.push(Vec::new()),
                }
            }
            if got.iter().any(|g| !g.is_empty()) {
                p.last_data = Some(Instant::now());
            }
            match &mut p.kind {
                Kind::Spl(a) => {
                    if let Some(s) = got.first() {
                        a.feed(s);
                    }
                }
                Kind::Loudness { meter, left, right } => {
                    left.extend_from_slice(&got[0]);
                    right.extend_from_slice(&got[1]);
                    let n = left.len().min(right.len());
                    if n > 0 {
                        meter.feed(&left[..n], &right[..n]);
                        left.drain(..n);
                        right.drain(..n);
                    }
                    for side in [left, right] {
                        if side.len() > MAX_PENDING {
                            let cut = side.len() - MAX_PENDING;
                            side.drain(..cut);
                        }
                    }
                }
            }
        }
    }
}

impl ProbeHub {
    /// A hub whose worker thread pumps every 10 ms.
    pub fn new(engine: Arc<Engine>) -> Self {
        let hub = Self::manual(engine);
        let inner = hub.inner.clone();
        let handle = std::thread::Builder::new()
            .name("relay-probes".into())
            .spawn(move || {
                while !inner.stop.load(Relaxed) {
                    inner.pump();
                    std::thread::sleep(ROUND);
                }
            })
            .expect("spawn the probe thread");
        *lock(&hub.thread) = Some(handle);
        hub
    }

    /// A hub with no thread: the caller calls `pump`. The tests use this on the virtual clock.
    pub fn manual(engine: Arc<Engine>) -> Self {
        let inner = Arc::new(Inner { engine, probes: Mutex::new(HashMap::new()), next: AtomicU64::new(1), stop: AtomicBool::new(false) });
        Self { inner, thread: Mutex::new(None) }
    }

    /// Drain every probe's taps into its analyser once.
    pub fn pump(&self) {
        self.inner.pump();
    }

    pub fn open(&self, spec: ProbeSpec) -> Result<u64, Error> {
        let (lanes, kind) = match spec {
            ProbeSpec::Spl { lane, calibration_db } => (vec![lane], Kind::Spl(Box::new(SplAnalyzer::new(calibration_db)))),
            ProbeSpec::Loudness { left, right, silence_dbfs } => (
                vec![left, right],
                Kind::Loudness { meter: Box::new(LoudnessMeter::new(silence_dbfs)), left: Vec::new(), right: Vec::new() },
            ),
        };
        let mut taps = Vec::with_capacity(lanes.len());
        for lane in lanes {
            match self.inner.engine.open_tap(lane, TapOptions::default()) {
                Ok(t) => taps.push(t),
                Err(e) => {
                    for t in taps {
                        let _ = self.inner.engine.close_tap(t);
                    }
                    return Err(e);
                }
            }
        }
        let id = self.inner.next.fetch_add(1, Relaxed);
        lock(&self.inner.probes).insert(id, Arc::new(Mutex::new(Probe { kind, taps, dropped: 0, last_data: None })));
        Ok(id)
    }

    pub fn close(&self, id: u64) -> Result<(), Error> {
        let p = lock(&self.inner.probes).remove(&id).ok_or(Error::UnknownProbe(id))?;
        for t in lock(&p).taps.drain(..) {
            let _ = self.inner.engine.close_tap(t);
        }
        Ok(())
    }

    pub fn status(&self, id: u64) -> Result<ProbeStatus, Error> {
        let p = self.inner.probe(id)?;
        let p = lock(&p);
        let (reading, calibration_db) = match &p.kind {
            Kind::Spl(a) => (ProbeReading::Spl(a.reading()), Some(a.calibration_db)),
            Kind::Loudness { meter, .. } => (ProbeReading::Loudness(meter.reading().clone()), None),
        };
        Ok(ProbeStatus { reading, dropped: p.dropped, idle_s: p.last_data.map(|t| t.elapsed().as_secs_f64()), calibration_db })
    }

    /// Rows a loudness probe wrote since the last call (empty for an SPL probe).
    pub fn take_rows(&self, id: u64) -> Result<Vec<Row>, Error> {
        let p = self.inner.probe(id)?;
        let mut p = lock(&p);
        Ok(match &mut p.kind {
            Kind::Loudness { meter, .. } => meter.take_rows(),
            Kind::Spl(_) => Vec::new(),
        })
    }

    /// Start a loudness probe's programme over, or an SPL probe's averages.
    pub fn reset(&self, id: u64) -> Result<(), Error> {
        let p = self.inner.probe(id)?;
        match &mut lock(&p).kind {
            Kind::Spl(a) => a.reset(),
            Kind::Loudness { meter, .. } => meter.reset(),
        }
        Ok(())
    }

    pub fn set_calibration(&self, id: u64, calibration_db: f64) -> Result<(), Error> {
        let p = self.inner.probe(id)?;
        if let Kind::Spl(a) = &mut lock(&p).kind {
            a.calibration_db = calibration_db;
        }
        Ok(())
    }

    pub fn set_silence(&self, id: u64, silence_dbfs: f64) -> Result<(), Error> {
        let p = self.inner.probe(id)?;
        if let Kind::Loudness { meter, .. } = &mut lock(&p).kind {
            meter.set_silence_dbfs(silence_dbfs);
        }
        Ok(())
    }

    /// Calibrate an SPL probe against a reference level; the outer `Result` is for an unknown probe.
    pub fn calibrate(&self, id: u64, reference_db: f64) -> Result<Result<f64, CalibrateError>, Error> {
        let p = self.inner.probe(id)?;
        let mut p = lock(&p);
        Ok(match &mut p.kind {
            Kind::Spl(a) => a.calibrate(reference_db),
            Kind::Loudness { .. } => Err(CalibrateError::Reference),
        })
    }
}

impl Drop for ProbeHub {
    fn drop(&mut self) {
        self.inner.stop.store(true, Relaxed);
        if let Some(h) = lock(&self.thread).take() {
            let _ = h.join();
        }
        let ids: Vec<u64> = lock(&self.inner.probes).keys().copied().collect();
        for id in ids {
            let _ = self.close(id);
        }
    }
}
