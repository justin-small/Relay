//! Taps: non-realtime readers on a lane, with their own ring and an optional output rate.

mod common;
use common::*;
use relay_bus::fake::{signal, DeviceSpec};
use relay_bus::*;
use std::time::Duration;

fn tap_rig(in_rate: u32) -> Rig {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, in_rate, in_rate as usize / 100), signal::sine(1000.0, in_rate, 0.5));
    r.eng.apply(&Config { enabled: true, lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))], ..Default::default() }).unwrap();
    r
}

#[test]
fn a_24k_tap_gets_continuous_audio() {
    let r = tap_rig(48_000);
    let tap = r.eng.open_tap(1, TapOptions { rate: Some(24_000), capacity_ms: 4000 }).unwrap();
    let mut all: Vec<f32> = Vec::new();
    let mut dropped = 0;
    for step in 1..=300 {
        r.fake.run_until(step as f64 * 0.02); // 6 s of audio, read every 20 ms
        let got = r.eng.read_tap(tap, 100_000, Duration::ZERO).unwrap();
        dropped += got.dropped;
        all.extend(got.samples);
    }
    assert_eq!(dropped, 0);
    // 6 s at 24 kHz, less what the converter and the last partial blocks still hold.
    assert!((143_000..=144_000).contains(&all.len()), "got {} samples", all.len());
    // A 1 kHz sine at 24 kHz moves by at most 2*pi*1000/24000*0.5 = 0.131 per sample. A gap, a repeat or a click
    // would break that. Skip the converter's start-up.
    let body = &all[200..];
    let worst = body.windows(2).map(|w| (w[1] - w[0]).abs()).fold(0.0f32, f32::max);
    assert!(worst < 0.14, "continuous: biggest step {worst}");
    assert!(near(rms(body), 0.5 / 2f32.sqrt(), 0.005), "level {}", rms(body));
    let crossings = body.windows(2).filter(|w| w[0] <= 0.0 && w[1] > 0.0).count() as f32;
    let seconds = body.len() as f32 / 24_000.0;
    assert!(near(crossings / seconds, 1000.0, 2.0), "still 1 kHz: {}", crossings / seconds);
}

#[test]
fn a_tap_on_a_44k1_input_is_resampled_once_into_the_lane_and_once_out() {
    let r = tap_rig(44_100);
    let tap = r.eng.open_tap(1, TapOptions { rate: Some(16_000), capacity_ms: 4000 }).unwrap();
    r.fake.run_until(3.0);
    let got = r.eng.read_tap(tap, 1_000_000, Duration::ZERO).unwrap();
    assert!((47_000..=48_000).contains(&got.samples.len()), "3 s at 16 kHz, got {}", got.samples.len());
    assert!(near(rms(&got.samples[400..]), 0.5 / 2f32.sqrt(), 0.01));
}

#[test]
fn read_returns_at_most_what_was_asked_and_keeps_the_rest() {
    let r = tap_rig(48_000);
    let tap = r.eng.open_tap(1, TapOptions::default()).unwrap();
    r.fake.run_until(1.0);
    let a = r.eng.read_tap(tap, 1000, Duration::ZERO).unwrap();
    let b = r.eng.read_tap(tap, 1000, Duration::ZERO).unwrap();
    assert_eq!((a.samples.len(), b.samples.len()), (1000, 1000));
    // The two reads are consecutive stretches of the same sine: the step between them is a sine step.
    assert!((b.samples[0] - a.samples[999]).abs() < 0.07);
    let none = r.eng.read_tap(tap, 0, Duration::ZERO).unwrap();
    assert!(none.samples.is_empty());
    assert!(r.eng.read_tap(999, 10, Duration::ZERO).is_err());
}

#[test]
fn a_slow_reader_gets_a_drop_count_and_the_realtime_side_never_waits() {
    let r = tap_rig(48_000);
    // 200 ms of ring, and nobody reads for 3 s.
    let tap = r.eng.open_tap(1, TapOptions { rate: None, capacity_ms: 200 }).unwrap();
    let healthy = r.eng.open_tap(1, TapOptions { rate: None, capacity_ms: 4000 }).unwrap();
    r.fake.run_until(3.0);
    let st = r.eng.status();
    // The lane kept running at full speed (3 s of frames went through the input), and the other tap lost nothing.
    assert!((st.lanes[0].meter.rms - 0.5 / 2f32.sqrt()).abs() < 0.01);
    let lost = st.taps.iter().find(|t| t.id == tap).unwrap().dropped;
    assert!(lost > 100_000 && lost < 3 * 48_000, "most of 3 s was dropped, got {lost}");
    assert_eq!(st.taps.iter().find(|t| t.id == healthy).unwrap().dropped, 0);
    assert_eq!(st.inputs[0].dropped, lost);
    assert_eq!(st.glitches.dropped_frames, lost);
    // The reader sees the count on its next read, once, together with the 200 ms that was kept.
    let got = r.eng.read_tap(tap, 1_000_000, Duration::ZERO).unwrap();
    assert_eq!(got.dropped, lost);
    assert_eq!(got.samples.len(), 9600);
    assert_eq!(r.eng.read_tap(tap, 10, Duration::ZERO).unwrap().dropped, 0, "reported once");
    // And it recovers: audio that arrives after the reader catches up is not lost.
    r.fake.advance(0.1);
    let more = r.eng.read_tap(tap, 1_000_000, Duration::ZERO).unwrap();
    assert!(more.samples.len() > 4000 && more.dropped == 0);
    // The healthy tap got every frame.
    assert_eq!(r.eng.read_tap(healthy, 1_000_000, Duration::ZERO).unwrap().samples.len(), 3 * 48_000 + 4800);
}

#[test]
fn taps_come_and_go_without_touching_the_lane() {
    let r = tap_rig(48_000);
    assert_eq!(r.eng.status().lanes[0].subscribers, 0);
    let t1 = r.eng.open_tap(1, TapOptions::default()).unwrap();
    let t2 = r.eng.open_tap(1, TapOptions::default()).unwrap();
    assert_eq!(r.eng.status().lanes[0].subscribers, 2);
    r.fake.run_until(0.5);
    r.eng.close_tap(t1).unwrap();
    r.eng.supervise();
    assert_eq!(r.eng.status().lanes[0].subscribers, 1);
    r.fake.advance(0.5);
    assert!(r.eng.read_tap(t2, 100_000, Duration::ZERO).unwrap().samples.len() > 40_000);
    assert!(r.eng.close_tap(t1).is_err());
    assert!(r.eng.open_tap(0, TapOptions::default()).is_err());
    assert!(r.eng.open_tap(33, TapOptions::default()).is_err());
}

#[test]
fn a_tap_opened_before_the_lane_is_patched_starts_when_it_is() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 480), signal::dc(0.3));
    let tap = r.eng.open_tap(5, TapOptions::default()).unwrap();
    r.eng.apply(&Config { enabled: true, lanes: vec![device_lane(5, "mic", ChannelSel::Index(0))], ..Default::default() }).unwrap();
    r.fake.run_until(0.5);
    let got = r.eng.read_tap(tap, 100_000, Duration::ZERO).unwrap();
    assert!(got.samples.len() >= 23_000 && got.samples.iter().all(|&x| near(x, 0.3, 1e-6)));
    // Switching the engine off closes the lane; the tap stays registered and comes back with the next apply.
    r.eng.apply(&Config::default()).unwrap();
    r.fake.advance(0.5);
    assert!(r.eng.read_tap(tap, 100, Duration::ZERO).unwrap().samples.is_empty());
    r.eng.apply(&Config { enabled: true, lanes: vec![device_lane(5, "mic", ChannelSel::Index(0))], ..Default::default() }).unwrap();
    r.fake.advance(0.5);
    assert!(r.eng.read_tap(tap, 100_000, Duration::ZERO).unwrap().samples.len() > 20_000);
}
