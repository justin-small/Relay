//! Memory stays flat: a heavy configuration run for a long simulated time, with reconfigurations and tap reads,
//! holds the same amount of heap at the end as after the first lap. A counting allocator wraps the checking one
//! (so every realtime callback is still asserted allocation-free).

use assert_no_alloc::AllocDisabler;
use relay_bus::fake::{signal, DeviceSpec, FakeBackend};
use relay_bus::*;
use std::alloc::{GlobalAlloc, Layout};
use std::sync::atomic::{AtomicIsize, Ordering::Relaxed};
use std::time::Duration;

struct Counting(AllocDisabler);
static LIVE: AtomicIsize = AtomicIsize::new(0);

// SAFETY: forwards every call to the checking allocator, which forwards to the system one; only counts bytes.
unsafe impl GlobalAlloc for Counting {
    unsafe fn alloc(&self, l: Layout) -> *mut u8 {
        LIVE.fetch_add(l.size() as isize, Relaxed);
        unsafe { self.0.alloc(l) }
    }
    unsafe fn dealloc(&self, p: *mut u8, l: Layout) {
        LIVE.fetch_sub(l.size() as isize, Relaxed);
        unsafe { self.0.dealloc(p, l) }
    }
    unsafe fn alloc_zeroed(&self, l: Layout) -> *mut u8 {
        LIVE.fetch_add(l.size() as isize, Relaxed);
        unsafe { self.0.alloc_zeroed(l) }
    }
    unsafe fn realloc(&self, p: *mut u8, l: Layout, n: usize) -> *mut u8 {
        LIVE.fetch_add(n as isize - l.size() as isize, Relaxed);
        unsafe { self.0.realloc(p, l, n) }
    }
}

#[global_allocator]
static ALLOC: Counting = Counting(AllocDisabler);

/// The counter is process-wide, so the two tests take turns.
static SERIAL: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn live() -> isize {
    LIVE.load(Relaxed)
}

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

#[test]
fn heap_stays_flat_over_a_long_run_with_reconfigurations() {
    let _turn = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
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

    let mut t = 0.0;
    let mut lap = |full: bool, secs: f64| {
        eng.apply(&config(full)).unwrap();
        for _ in 0..(secs / 0.5) as usize {
            t += 0.5;
            fake.run_until(t);
            eng.supervise();
            for &tap in &taps {
                let _ = eng.read_tap(tap, 48_000, Duration::ZERO).unwrap();
            }
            let _ = eng.status();
        }
    };

    // Warm up: everything is built and every ring has wrapped at least once.
    lap(true, 10.0);
    lap(false, 5.0);
    lap(true, 10.0);
    let base = live();
    let mut worst = 0isize;
    // Ten minutes of simulated time, reconfiguring every 30 s.
    for i in 0..20 {
        lap(i % 2 == 0, 30.0);
        worst = worst.max(live() - base);
    }
    lap(true, 10.0);
    let end = live();
    eprintln!("heap after warm-up {} KB, worst growth {} KB, end growth {} KB", base / 1024, worst / 1024, (end - base) / 1024);
    assert!(worst < 256 * 1024, "heap grew by {} KB during the run", worst / 1024);
    assert!((end - base).abs() < 256 * 1024, "heap at the end differs from the start by {} KB", (end - base) / 1024);
    let st = eng.status();
    assert_eq!(st.glitches.discard_overflow, 0);
    eng.shutdown();
}

/// A caller may keep every `read_tap` result (the benchmark's continuity check does). Each must hold about its own
/// samples, not the ceiling it asked for: with a 24000-frame ceiling and a few hundred samples per read, the old
/// code held 96 KB per read and grew by 150 MB a minute.
#[test]
fn kept_tap_reads_hold_only_their_samples() {
    let _turn = SERIAL.lock().unwrap_or_else(|e| e.into_inner());
    let fake = FakeBackend::new();
    fake.add_input("in0", DeviceSpec::new(1, 48_000, 256), signal::sine(300.0, 48_000, 0.03));
    let eng = Engine::new(fake.clone());
    eng.apply(&Config {
        enabled: true,
        lanes: vec![LaneSpec { lane: 1, name: "l".into(), source: DEVICE_SOURCE.into(), device: Some("in0".into()), channel: ChannelSel::Index(0) }],
        outputs: vec![],
        matrix: vec![],
    })
    .unwrap();
    let tap = eng.open_tap(1, TapOptions { rate: Some(24_000), capacity_ms: 4000 }).unwrap();
    let before = live();
    let (mut kept, mut samples, mut t) = (Vec::new(), 0usize, 0.0);
    for _ in 0..600 {
        t += 0.02;
        fake.run_until(t);
        let r = eng.read_tap(tap, 24_000, Duration::ZERO).unwrap();
        samples += r.samples.len();
        kept.push(r);
    }
    let held = live() - before;
    eprintln!("kept {} reads, {} samples = {} KB, heap held {} KB", kept.len(), samples, samples * 4 / 1024, held / 1024);
    assert!(samples > 100_000);
    assert!(held < (samples * 4 * 2) as isize + 64 * 1024, "kept reads hold {held} bytes for {} bytes of samples", samples * 4);
    drop(kept);
    eng.shutdown();
}
