//! The 32-lane audio bus as a module that drives a network source or sender sees it (#599, #658). The engine is core's; a module that reads a
//! network source (NDI sound) hands each block to [`LaneBus::push`], and one that sends a mix drains an output with [`LaneBus::pull`], paced by
//! its own clock. The module also says how those streams are doing with [`LaneBus::set_status`], so the Audio bus page shows them next to the
//! engine's own.
//!
//! The server provides this (`Host::provide`, `"core.lanes"`) when it runs the bus itself (`--rust-audio`).

use std::sync::Arc;

use serde_json::Value;

pub trait LaneBus: Send + Sync + 'static {
    /// The setting the bus runs: `{"lanes": [{"lane", "source", "device", ...}], "outputs": [{"id", "output", "device", "channels",
    /// "latency_ms", ...}], "matrix": [...]}`.
    fn config(&self) -> Value;
    fn running(&self) -> bool;
    /// Hand a block of a network source to the engine (`channels` interleaved at `rate`). `input_id` is `source:device`, lower-cased.
    /// False when the engine has no such input (it is being reconfigured).
    fn push(&self, input_id: &str, data: &[f32], channels: usize, rate: u32) -> bool;
    /// Take `frames` of a network sender's mix (interleaved, 48 kHz), or `None` when the output is gone.
    fn pull(&self, output_id: &str, frames: usize) -> Option<Vec<f32>>;
    /// How `driver`'s inputs and outputs are doing: `{id: {state, error, rate, channels, lanes, dropped}}` and `{id: {state, error, underruns}}`.
    fn set_status(&self, driver: &str, inputs: Value, outputs: Value);
}

/// What the server provides under [`LANES`], boxed so a module can name the type.
pub struct LanesCap(pub Arc<dyn LaneBus>);

/// The capability name (`Host::provide`, `Host::using`).
pub const LANES: &str = "core.lanes";

/// The engine's name for an input: `source:device`, lower-cased.
pub fn input_id(source: &str, device: &str) -> String {
    format!("{}:{}", source.trim().to_lowercase(), device.trim().to_lowercase())
}
