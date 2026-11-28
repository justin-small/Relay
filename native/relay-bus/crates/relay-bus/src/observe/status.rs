//! What `Engine::status()` returns. Plain data, so phase 2 can turn it into the dict the panel already reads.

use crate::health::GlitchCounters;
use crate::meter::MeterReading;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum StreamState {
    /// Running.
    Open,
    /// Failed or not available yet; `supervise()` is retrying with backoff.
    Waiting,
    Closed,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OutputKind {
    /// A device the backend opens.
    Device,
    /// A network sender: Python drains it with `pull`.
    Pull,
}

#[derive(Debug, Clone)]
pub struct LaneStatus {
    pub lane: usize,
    pub name: String,
    pub patched: bool,
    /// Id of the input feeding it.
    pub input: Option<String>,
    pub state: Option<StreamState>,
    pub error: Option<String>,
    /// Consumers on this lane's fan-out: outputs reading it plus taps.
    pub subscribers: usize,
    pub meter: MeterReading,
    /// Seconds since frames last arrived, to the resolution of the polling (`status()` and `supervise()` calls);
    /// None if none has arrived since the lane was patched.
    pub last_input_age_s: Option<f64>,
}

#[derive(Debug, Clone)]
pub struct InputStatus {
    pub id: String,
    pub source: String,
    pub device: Option<String>,
    pub state: StreamState,
    pub error: Option<String>,
    pub rate: Option<u32>,
    pub channels: Option<usize>,
    pub lanes: Vec<usize>,
    /// Frames lost because a consumer was too slow.
    pub dropped: u64,
}

#[derive(Debug, Clone)]
pub struct OutputStatus {
    pub id: String,
    pub name: String,
    pub kind: OutputKind,
    pub state: StreamState,
    pub error: Option<String>,
    pub channels: usize,
    /// The rate the device runs at (frames_out counts frames at this rate); None while it is not open.
    pub rate: Option<u32>,
    pub latency_ms: u32,
    /// Where each lane ring is held.
    pub target_ms: f32,
    pub underruns: u64,
    pub resyncs: u64,
    /// Smoothed ring fill, averaged over the lanes being read.
    pub queued_ms: f32,
    /// Smallest and largest instantaneous fill since the previous `status()` call.
    pub fill_min_ms: f32,
    pub fill_max_ms: f32,
    /// Mean resample ratio relative to nominal right now; wobbles a little around the settled value.
    pub ratio: f32,
    /// The settled clock offset the drift loop has learned, as a ratio: 1.003 means this output's clock runs 0.3%
    /// fast against the lanes it reads (and 1.0 means they agree).
    pub drift: f32,
    pub frames_out: u64,
    pub meters: Vec<MeterReading>,
}

#[derive(Debug, Clone)]
pub struct TapStatus {
    pub id: u64,
    pub lane: usize,
    pub rate: u32,
    pub dropped: u64,
}

#[derive(Debug, Clone)]
pub struct Status {
    pub running: bool,
    pub lane_rate: u32,
    pub lanes: Vec<LaneStatus>,
    pub inputs: Vec<InputStatus>,
    pub outputs: Vec<OutputStatus>,
    pub taps: Vec<TapStatus>,
    pub glitches: GlitchCounters,
}
