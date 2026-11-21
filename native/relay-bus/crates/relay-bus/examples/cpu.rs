//! CPU cost of the heavy configuration on the fake backend (callbacks run inline, so wall time over simulated
//! time is the core's share of one CPU): `cargo run --release --example cpu`.
use relay_bus::fake::{signal, DeviceSpec, FakeBackend};
use relay_bus::*;
use std::time::{Duration, Instant};

fn config(with_extra: bool) -> Config {
    let mut lanes = Vec::new();
    for l in 1..=32usize {
        let d = (l - 1) / 8;
        lanes.push(LaneSpec {
            lane: l,
            name: format!("lane {l}"),
            source: DEVICE_SOURCE.into(),
            device: Some(format!("in{d}")),
            channel: ChannelSel::Index((l - 1) % 8),
        });
    }
    let mut outputs = vec![OutputSpec {
        id: "spk".into(),
        name: "spk".into(),
        output: DEVICE_SOURCE.into(),
        device: Some("spk".into()),
        channels: 2,
        latency_ms: 60,
    }];
    for k in 1..=3 {
        outputs.push(OutputSpec {
            id: format!("o{k}"),
            name: format!("o{k}"),
            output: DEVICE_SOURCE.into(),
            device: Some(format!("o{k}")),
            channels: 8,
            latency_ms: 60,
        });
    }
    if !with_extra {
        outputs.truncate(3);
    }
    let mut matrix = Vec::new();
    for o in &outputs {
        for l in 1..=32 {
            for c in 1..=o.channels {
                matrix.push(Cell { lane: l, out: o.id.clone(), ch: c, gain_db: -40.0 });
            }
        }
    }
    Config { enabled: true, lanes, outputs, matrix }
}

fn main() {
    let fake = FakeBackend::new();
    for d in 0..4 {
        let rate = if d % 2 == 1 { 44_100 } else { 48_000 };
        fake.add_input(&format!("in{d}"), DeviceSpec::new(8, rate, 256), signal::sine(300.0 + d as f64 * 50.0, rate, 0.03));
    }
    fake.add_output("spk", DeviceSpec::new(2, 48_000, 256));
    for k in 1..=3 {
        fake.add_output(&format!("o{k}"), DeviceSpec::new(8, 48_000, 256));
    }
    let eng = Engine::new(fake.clone());
    let taps: Vec<u64> = (1..=4).map(|l| eng.open_tap(l, TapOptions { rate: Some(24_000), capacity_ms: 4000 }).unwrap()).collect();
    eng.apply(&config(true)).unwrap();
    let (mut t, start) = (0.0, Instant::now());
    while t < 30.0 {
        t += 0.1;
        fake.run_until(t);
        for &tap in &taps {
            let _ = eng.read_tap(tap, 48_000, Duration::ZERO);
        }
    }
    let wall = start.elapsed().as_secs_f64();
    println!("{:.1} s simulated in {:.2} s: {:.1}% of one core", t, wall, 100.0 * wall / t);
}
