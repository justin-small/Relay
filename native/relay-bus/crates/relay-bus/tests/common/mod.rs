//! Helpers shared by the test binaries.
#![allow(dead_code)]

use relay_bus::fake::FakeBackend;
use relay_bus::*;
use std::sync::Arc;

// Every realtime callback the fake backend fires runs inside `assert_no_alloc` (the dev-dependency turns the
// feature on). That only bites if the allocator is installed, so every test binary installs it. Outside a guarded
// callback it behaves like the system allocator.
#[global_allocator]
static ALLOC: assert_no_alloc::AllocDisabler = assert_no_alloc::AllocDisabler;

pub struct Rig {
    pub fake: Arc<FakeBackend>,
    pub eng: Engine,
}

pub fn rig() -> Rig {
    let fake = FakeBackend::new();
    let eng = Engine::new(fake.clone());
    Rig { fake, eng }
}

pub fn lane(n: usize, source: &str, device: &str, channel: ChannelSel) -> LaneSpec {
    LaneSpec { lane: n, name: format!("lane {n}"), source: source.into(), device: Some(device.into()), channel }
}

pub fn device_lane(n: usize, device: &str, channel: ChannelSel) -> LaneSpec {
    lane(n, DEVICE_SOURCE, device, channel)
}

pub fn device_output(id: &str, device: &str, channels: usize, latency_ms: u32) -> OutputSpec {
    OutputSpec { id: id.into(), name: id.into(), output: DEVICE_SOURCE.into(), device: Some(device.into()), channels, latency_ms }
}

pub fn cell(lane: usize, out: &str, ch: usize, gain_db: f32) -> Cell {
    Cell { lane, out: out.into(), ch, gain_db }
}

/// The samples of channel `c` (0-based) of an interleaved capture, frames `from..to`.
pub fn channel(capture: &[f32], channels: usize, c: usize, from: usize, to: usize) -> Vec<f32> {
    capture.chunks_exact(channels).skip(from).take(to - from).map(|f| f[c]).collect()
}

pub fn mean(x: &[f32]) -> f32 {
    x.iter().sum::<f32>() / x.len() as f32
}

pub fn rms(x: &[f32]) -> f32 {
    (x.iter().map(|v| v * v).sum::<f32>() / x.len() as f32).sqrt()
}

pub fn near(a: f32, b: f32, tol: f32) -> bool {
    (a - b).abs() <= tol
}

pub fn output<'a>(st: &'a Status, id: &str) -> &'a OutputStatus {
    st.outputs.iter().find(|o| o.id == id).expect("output in status")
}
