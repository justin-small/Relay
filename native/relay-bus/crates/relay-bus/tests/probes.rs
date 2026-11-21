//! Meter probes (issue 604): the sound-level and stream-mix meters read known signals off the bus and give the values
//! the standards give. The tests drive the fake backend on its virtual clock and call `pump` between steps, so they
//! are exact and take no wall time; one test runs the hub's own thread.

mod common;
use common::*;
use relay_bus::fake::{signal, DeviceSpec, FakeBackend, Signal};
use relay_bus::*;
use std::sync::Arc;
use std::time::{Duration, Instant};

const TAU: f64 = std::f64::consts::TAU;

fn sine_lr(freq: f64, left: f32, right: f32) -> Signal {
    Box::new(move |n, f| {
        let s = (TAU * freq * n as f64 / 48_000.0).sin() as f32;
        f[0] = s * left;
        f[1] = s * right;
    })
}

fn rig2(signal: Signal) -> (Arc<FakeBackend>, Arc<Engine>) {
    let fake = FakeBackend::new();
    fake.add_input("mic", DeviceSpec::new(2, 48_000, 480), signal);
    let eng = Arc::new(Engine::new(fake.clone()));
    eng.apply(&Config {
        enabled: true,
        lanes: vec![device_lane(1, "mic", ChannelSel::Index(0)), device_lane(2, "mic", ChannelSel::Index(1))],
        ..Default::default()
    })
    .unwrap();
    (fake, eng)
}

/// Run `seconds` of audio, pumping every 20 ms.
fn run(fake: &FakeBackend, hub: &ProbeHub, from: f64, seconds: f64) -> f64 {
    let mut t = from;
    while t < from + seconds {
        t += 0.02;
        fake.run_until(t);
        hub.pump();
    }
    t
}

fn spl(hub: &ProbeHub, id: u64) -> analysis::spl::SplReading {
    match hub.status(id).unwrap().reading {
        ProbeReading::Spl(r) => r,
        other => panic!("expected an SPL reading, got {other:?}"),
    }
}

fn loud(hub: &ProbeHub, id: u64) -> analysis::loudness::LoudnessReading {
    match hub.status(id).unwrap().reading {
        ProbeReading::Loudness(r) => r,
        other => panic!("expected a loudness reading, got {other:?}"),
    }
}

fn within(what: &str, got: f64, want: f64, tol: f64) {
    assert!((got - want).abs() <= tol, "{what}: got {got:.2}, want {want:.2} +/- {tol}");
}

#[test]
fn a_1k_tone_reads_its_calibrated_level_and_lands_in_its_band() {
    let (fake, eng) = rig2(signal::sine(1000.0, 48_000, 0.5));
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Spl { lane: 1, calibration_db: 110.0 }).unwrap();
    run(&fake, &hub, 0.0, 6.0);
    // RMS 0.5/sqrt(2) = -9.03 dBFS, so 110 - 9.03 = 100.97 dB SPL; A and C weighting are both ~0 dB at 1 kHz.
    let r = spl(&hub, id);
    within("A slow", r.a_slow, 100.97, 0.3);
    within("A fast", r.a_fast, 100.97, 0.3);
    within("C slow", r.c_slow, 100.97, 0.3);
    within("Leq A", r.leq_a, 100.97, 0.3);
    // The 1 kHz third-octave band (index 17) holds the tone; its neighbours two bands away are 20+ dB down.
    within("1 kHz band", r.bands[17], 100.97, 0.6);
    assert!(r.bands[15] < r.bands[17] - 20.0 && r.bands[19] < r.bands[17] - 20.0, "{:?}", r.bands);
}

#[test]
fn a_100_hz_tone_reads_lower_on_a_than_on_c() {
    let (fake, eng) = rig2(signal::sine(100.0, 48_000, 0.5));
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Spl { lane: 1, calibration_db: 110.0 }).unwrap();
    run(&fake, &hub, 0.0, 6.0);
    let r = spl(&hub, id);
    // IEC 61672: A is -19.1 dB and C -0.3 dB at 100 Hz.
    within("A slow", r.a_slow, 100.97 - 19.1, 0.4);
    within("C slow", r.c_slow, 100.97 - 0.3, 0.4);
}

#[test]
fn fast_follows_a_step_sooner_than_slow() {
    let (fake, eng) = rig2(signal::silence());
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Spl { lane: 1, calibration_db: 110.0 }).unwrap();
    let t = run(&fake, &hub, 0.0, 2.0);
    fake.set_signal("mic", signal::sine(1000.0, 48_000, 0.5));
    run(&fake, &hub, t, 0.3);
    let r = spl(&hub, id);
    assert!(r.a_fast > r.a_slow + 5.0, "fast {} slow {}", r.a_fast, r.a_slow);
}

#[test]
fn calibrating_against_a_reference_sets_the_offset() {
    let (fake, eng) = rig2(signal::sine(1000.0, 48_000, 0.5));
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Spl { lane: 1, calibration_db: 110.0 }).unwrap();
    assert_eq!(hub.calibrate(id, 94.0).unwrap(), Err(analysis::spl::CalibrateError::TooQuiet), "nothing has arrived yet");
    run(&fake, &hub, 0.0, 6.0);
    assert_eq!(hub.calibrate(id, 200.0).unwrap(), Err(analysis::spl::CalibrateError::Reference));
    let cal = hub.calibrate(id, 94.0).unwrap().unwrap();
    within("calibration", cal, 94.0 + 9.03, 0.3);
    run(&fake, &hub, 6.0, 0.1);
    within("A slow after", spl(&hub, id).a_slow, 94.0, 0.3);
}

#[test]
fn a_minus_23_dbfs_stereo_tone_reads_minus_23_lufs() {
    // EBU Tech 3341: a 997 Hz stereo sine at -23 dBFS peak per channel is -23.0 LUFS in every window.
    let amp = 10f32.powf(-23.0 / 20.0);
    let (fake, eng) = rig2(sine_lr(997.0, amp, amp));
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Loudness { left: 1, right: 2, silence_dbfs: -50.0 }).unwrap();
    run(&fake, &hub, 0.0, 8.0);
    let r = loud(&hub, id);
    within("momentary", r.momentary.unwrap(), -23.0, 0.2);
    within("short-term", r.short_term.unwrap(), -23.0, 0.2);
    within("integrated", r.integrated.unwrap(), -23.0, 0.2);
    assert!(r.range_lu < 0.5, "a steady tone has no loudness range, got {}", r.range_lu);
    // The tone's peak is -23 dBFS; the 4x oversampled peak of a sine sampled this densely is within a few hundredths.
    within("sample peak", r.sample_peak_dbfs, -23.0, 0.1);
    within("true peak", r.true_peak_dbtp, -23.0, 0.2);
    within("correlation", r.correlation.unwrap(), 1.0, 0.01);
    // The mono mix has RMS amp/sqrt(2) = -26.0 dBFS, all in the 1 kHz octave (index 5).
    within("1 kHz octave", r.octaves[5], -26.0, 0.6);
    assert!(r.octaves[3] < -50.0 && r.octaves[8] < -50.0, "{:?}", r.octaves);
    assert_eq!(r.seconds, 8);
}

#[test]
fn opposite_channels_read_minus_one_and_cancel_in_the_mono_mix() {
    let (fake, eng) = rig2(sine_lr(997.0, 0.5, -0.5));
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Loudness { left: 1, right: 2, silence_dbfs: -50.0 }).unwrap();
    run(&fake, &hub, 0.0, 3.0);
    within("correlation", loud(&hub, id).correlation.unwrap(), -1.0, 0.01);
    // The mono mix of a pair that cancels is silent.
    assert!(loud(&hub, id).octaves[5] < -80.0);
}

#[test]
fn a_row_a_second_says_whether_there_was_sound() {
    let amp = 10f32.powf(-23.0 / 20.0);
    let (fake, eng) = rig2(sine_lr(997.0, amp, amp));
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Loudness { left: 1, right: 2, silence_dbfs: -50.0 }).unwrap();
    let t = run(&fake, &hub, 0.0, 5.0);
    let rows = hub.take_rows(id).unwrap();
    assert_eq!(rows.len(), 5);
    assert!(rows.iter().all(|r| r.sound));
    within("row momentary", rows[4].momentary.unwrap(), -23.0, 0.2);
    assert!(hub.take_rows(id).unwrap().is_empty(), "rows are handed out once");
    fake.set_signal("mic", signal::silence());
    run(&fake, &hub, t, 3.0);
    let rows = hub.take_rows(id).unwrap();
    assert!(rows.len() >= 2 && !rows.last().unwrap().sound, "{rows:?}");
}

#[test]
fn reset_starts_the_programme_over() {
    let amp = 10f32.powf(-23.0 / 20.0);
    let (fake, eng) = rig2(sine_lr(997.0, amp, amp));
    let hub = ProbeHub::manual(eng);
    let id = hub.open(ProbeSpec::Loudness { left: 1, right: 2, silence_dbfs: -50.0 }).unwrap();
    run(&fake, &hub, 0.0, 3.0);
    assert!(loud(&hub, id).integrated.is_some());
    hub.reset(id).unwrap();
    let r = loud(&hub, id);
    assert!(r.integrated.is_none() && r.seconds == 0, "{r:?}");
}

#[test]
fn probes_close_cleanly_and_unknown_ids_are_refused() {
    let (fake, eng) = rig2(signal::sine(1000.0, 48_000, 0.5));
    let hub = ProbeHub::manual(eng.clone());
    let a = hub.open(ProbeSpec::Spl { lane: 1, calibration_db: 110.0 }).unwrap();
    let b = hub.open(ProbeSpec::Spl { lane: 1, calibration_db: 100.0 }).unwrap();
    assert_ne!(a, b, "two meters may read the same lane");
    assert_eq!(eng.status().taps.len(), 2);
    assert!(hub.open(ProbeSpec::Spl { lane: 99, calibration_db: 110.0 }).is_err());
    assert_eq!(eng.status().taps.len(), 2, "a refused probe leaves no tap behind");
    run(&fake, &hub, 0.0, 3.0);
    within("second meter carries its own calibration", spl(&hub, b).a_slow, 90.97, 0.4);
    hub.close(a).unwrap();
    assert_eq!(eng.status().taps.len(), 1);
    assert!(hub.status(a).is_err() && hub.close(a).is_err());
}

#[test]
fn the_hub_thread_reads_every_sample_while_the_caller_does_nothing() {
    let amp = 10f32.powf(-23.0 / 20.0);
    let (fake, eng) = rig2(sine_lr(997.0, amp, amp));
    let hub = ProbeHub::new(eng);
    let id = hub.open(ProbeSpec::Loudness { left: 1, right: 2, silence_dbfs: -50.0 }).unwrap();
    // Three seconds of audio arrive while this thread never touches the hub; the ring holds four.
    fake.run_until(3.0);
    let deadline = Instant::now() + Duration::from_secs(4);
    while loud(&hub, id).seconds < 3 && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(5)); // allow-sleep: waits on the hub's own thread, which has no event to join on
    }
    let r = loud(&hub, id);
    assert_eq!(r.seconds, 3, "the thread took all of it");
    assert_eq!(hub.status(id).unwrap().dropped, 0);
    within("integrated", r.integrated.unwrap(), -23.0, 0.2);
}
