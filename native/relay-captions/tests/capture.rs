//! The capture's meter, gate, resampler and fan-out (`app/audio.py` `AudioCapture`).

use relay_captions::capture::{dbfs, Consumers, Pump, Resampler, CONSUMER_QUEUE, PREROLL_FRAMES};
use std::f64::consts::PI;

fn tone(rate: u32, hz: f64, amp: f32, n: usize, from: usize) -> Vec<f32> {
    (from..from + n).map(|i| amp * (2.0 * PI * hz * i as f64 / f64::from(rate)).sin() as f32).collect()
}

fn rms(x: &[f32]) -> f64 {
    (x.iter().map(|s| f64::from(*s).powi(2)).sum::<f64>() / x.len() as f64).sqrt()
}

#[test]
fn the_scale_is_floored_not_minus_infinity() {
    assert_eq!(dbfs(0.0), -60.0);
    assert_eq!(dbfs(-1.0), -60.0);
    assert_eq!(dbfs(0.0001), -60.0);
    assert!((dbfs(0.5) - -6.02).abs() < 0.01);
    assert_eq!(dbfs(2.0), 0.0, "clamped at full scale");
}

#[test]
fn a_tone_survives_the_resampler_and_a_tone_above_the_new_nyquist_does_not() {
    let mut r = Resampler::new(48000, 24000);
    let mut out = Vec::new();
    for block in tone(48000, 1000.0, 0.5, 48000, 0).chunks(480) {
        out.extend(r.process(block));
    }
    assert!((out.len() as i64 - 24000).abs() <= 40, "{}", out.len());
    let steady = &out[2000..22000];
    assert!((rms(steady) - 0.5 / 2f64.sqrt()).abs() < 0.01, "a 1 kHz tone keeps its level: {}", rms(steady));
    let mut r = Resampler::new(48000, 24000);
    let mut out = Vec::new();
    for block in tone(48000, 20000.0, 0.5, 48000, 0).chunks(480) {
        out.extend(r.process(block));
    }
    assert!(rms(&out[2000..22000]) < 0.01, "20 kHz would alias into the band: {}", rms(&out[2000..22000]));
}

#[test]
fn another_rate_comes_out_at_24k() {
    let mut r = Resampler::new(44100, 24000);
    let n: usize = tone(44100, 440.0, 0.4, 44100, 0).chunks(441).map(|b| r.process(b).len()).sum();
    assert!((n as i64 - 24000).abs() <= 40, "{n}");
}

#[test]
fn frames_are_forty_milliseconds_of_16_bit_little_endian() {
    let mut p = Pump::new(48000, false);
    let mut frames = Vec::new();
    for (i, block) in tone(48000, 440.0, 0.5, 48000, 0).chunks(480).enumerate() {
        frames.extend(p.push(block, i as f64 * 0.01));
    }
    assert!(frames.len() >= 24 && frames.len() <= 25, "{}", frames.len());
    assert!(frames.iter().all(|f| f.len() == 1920), "960 samples of two bytes");
    let peak = frames.iter().flat_map(|f| f.chunks(2).map(|b| i16::from_le_bytes([b[0], b[1]]))).map(i16::unsigned_abs).max().unwrap();
    assert!((14000..=16500).contains(&peak), "0.5 of full scale: {peak}");
}

#[test]
fn the_gate_holds_silence_back_and_replays_a_lead_in_on_onset() {
    let mut p = Pump::new(24000, true);
    let quiet = vec![0.0f32; 960];
    let loud = tone(24000, 300.0, 0.3, 960, 0);
    let mut now = 0.0;
    let mut sent = 0;
    for _ in 0..30 {
        sent += p.push(&quiet, now).len();
        now += 0.04;
    }
    assert_eq!(sent, 0, "a silent room sends nothing");
    assert!(!p.speaking);
    let onset = p.push(&loud, now);
    assert!(p.speaking);
    // The speech frame, and up to PREROLL_FRAMES of what came before it (the resampler's delay costs a frame or so).
    assert!(onset.len() >= PREROLL_FRAMES && onset.len() <= PREROLL_FRAMES + 1, "{}", onset.len());
    // The speaking light holds 0.6 s after the voice stops.
    now += 0.04;
    assert_eq!(p.push(&quiet, now).len(), 1, "still speaking");
    now += 0.7;
    assert_eq!(p.push(&quiet, now).len(), 0, "the hold is over");
    assert!(!p.speaking);
}

#[test]
fn with_the_gate_off_every_frame_goes_silence_included() {
    let mut p = Pump::new(24000, false);
    let n: usize = (0..10).map(|i| p.push(&vec![0.0f32; 960], f64::from(i) * 0.04).len()).sum();
    assert!((9..=10).contains(&n), "{n}");
}

#[test]
fn clipping_is_counted_and_latched_for_three_seconds() {
    let mut p = Pump::new(24000, false);
    let mut hot = vec![0.1f32; 480];
    hot[10] = 1.0;
    hot[11] = -1.0;
    p.push(&hot, 10.0);
    assert_eq!(p.clipped_samples, 2);
    assert!(p.clipping(12.9));
    assert!(!p.clipping(13.1));
    let s = p.meter_status(11.0);
    assert_eq!((s["clipping"].clone(), s["clipped_samples"].clone(), s["peak"].clone()), (true.into(), 2.into(), 1.0.into()));
    assert_eq!(s["peak_dbfs"], 0.0);
}

#[test]
fn the_peak_marker_falls_twenty_db_a_second() {
    let mut p = Pump::new(24000, false);
    p.push(&vec![0.5f32; 480], 1.0);
    p.push(&vec![0.0f32; 480], 2.0);
    let s = p.meter_status(2.0);
    // 0.5 is -6.02 dBFS; a second later the marker has fallen 20 dB.
    assert!((s["peak_dbfs"].as_f64().unwrap() - -26.0).abs() < 0.2, "{s}");
    assert_eq!(s["rms_dbfs"], -60.0);
}

#[tokio::test]
async fn every_consumer_gets_the_same_frames_and_a_stalled_one_loses_its_oldest() {
    let c = Consumers::new();
    let (id_a, mut a) = c.add();
    let (_id_b, mut b) = c.add();
    assert_eq!(c.len(), 2);
    c.dispatch(&[1]);
    c.dispatch(&[2]);
    assert_eq!((a.recv().await, a.recv().await), (Some(vec![1]), Some(vec![2])));
    // b never reads: past its queue, the oldest go and are counted.
    for i in 0..(CONSUMER_QUEUE + 5) {
        c.dispatch(&[(i % 250) as u8 + 3]);
    }
    assert_eq!(b.dropped(), 7, "the two it never read, then five more");
    assert_eq!(b.recv().await, Some(vec![8]), "the oldest that survived");
    c.remove(id_a);
    assert_eq!(c.len(), 1);
    drop(b);
    c.dispatch(&[9]);
    assert!(c.is_empty(), "a consumer that went away is forgotten");
}
