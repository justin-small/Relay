//! Clips injected on top of an output: heard on the next block, in the right channels, stoppable.

mod common;
use common::*;
use relay_bus::fake::{signal, DeviceSpec};
use relay_bus::*;

const BLOCK: usize = 256;

fn one_output(latency_ms: u32, with_lane: bool) -> Rig {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128), signal::dc(0.2));
    r.fake.add_output("o", DeviceSpec::new(2, 48_000, BLOCK));
    r.fake.capture_output("o", true);
    let mut cfg = Config { enabled: true, outputs: vec![device_output("o", "o", 2, latency_ms)], ..Default::default() };
    if with_lane {
        cfg.lanes.push(device_lane(1, "mic", ChannelSel::Index(0)));
        cfg.matrix = vec![cell(1, "o", 1, 0.0), cell(1, "o", 2, 0.0)];
    }
    r.eng.apply(&cfg).unwrap();
    r
}

/// The block the device was handed after the last `run_output_blocks(1)`.
fn last_block(r: &Rig) -> Vec<f32> {
    r.fake.take_output("o")
}

#[test]
fn a_clip_is_heard_in_the_very_next_block_in_the_chosen_channel() {
    let r = one_output(500, false); // the biggest latency setting: a clip must not wait behind it
    r.fake.run_until(1.0);
    r.fake.take_output("o");
    r.eng.inject("o", &[1], &vec![0.5; 4800], 48_000).unwrap();
    r.fake.run_output_blocks("o", 1);
    let blk = last_block(&r);
    assert_eq!(blk.len(), BLOCK * 2);
    assert_eq!(channel(&blk, 2, 1, 0, 1)[0], 0.5, "the first frame of the next block");
    assert!(channel(&blk, 2, 1, 0, BLOCK).iter().all(|&x| x == 0.5), "all of the block, right channel");
    assert!(channel(&blk, 2, 0, 0, BLOCK).iter().all(|&x| x == 0.0), "and nothing in the other channel");
}

#[test]
fn a_clip_sits_on_top_of_live_audio_not_behind_the_buffer() {
    let r = one_output(300, true);
    r.fake.run_until(2.0);
    r.fake.take_output("o");
    r.eng.inject("o", &[0], &vec![0.5; 480], 48_000).unwrap();
    r.fake.run_output_blocks("o", 1);
    let blk = last_block(&r);
    let (c0, c1) = (channel(&blk, 2, 0, 0, BLOCK), channel(&blk, 2, 1, 0, BLOCK));
    assert!(c0.iter().all(|&x| near(x, 0.7, 2e-3)), "live 0.2 plus the clip 0.5 from the first frame");
    assert!(c1.iter().all(|&x| near(x, 0.2, 2e-3)), "the other channel is just the live audio");
    assert_eq!(r.eng.status().glitches.underruns, 0);
}

#[test]
fn a_clip_ends_by_itself_and_clips_add() {
    let r = one_output(60, false);
    r.fake.run_until(0.5);
    r.fake.take_output("o");
    r.eng.inject("o", &[0, 1], &vec![0.25; 300], 48_000).unwrap();
    r.eng.inject("o", &[0], &vec![0.25; 100], 48_000).unwrap();
    r.fake.run_output_blocks("o", 2);
    let cap = r.fake.take_output("o");
    let c0 = channel(&cap, 2, 0, 0, 2 * BLOCK);
    let c1 = channel(&cap, 2, 1, 0, 2 * BLOCK);
    assert!(c0[..100].iter().all(|&x| x == 0.5), "both clips overlap for 100 frames");
    assert!(c0[100..300].iter().all(|&x| x == 0.25));
    assert!(c0[300..].iter().all(|&x| x == 0.0), "ends by itself");
    assert!(c1[..300].iter().all(|&x| x == 0.25) && c1[300..].iter().all(|&x| x == 0.0));
}

#[test]
fn stopping_cuts_the_clip_at_the_next_block() {
    let r = one_output(60, false);
    r.fake.run_until(0.5);
    r.eng.inject("o", &[0, 1], &vec![0.5; 48_000], 48_000).unwrap();
    r.fake.run_output_blocks("o", 3);
    r.fake.take_output("o");
    r.eng.stop_injects("o").unwrap();
    r.fake.run_output_blocks("o", 1);
    let blk = last_block(&r);
    assert!(blk.iter().all(|&x| x == 0.0), "silent from the first frame after stop");
    // And a new clip after a stop plays normally.
    r.eng.inject("o", &[0], &vec![0.5; 480], 48_000).unwrap();
    r.fake.run_output_blocks("o", 1);
    assert_eq!(channel(&last_block(&r), 2, 0, 0, 1)[0], 0.5);
}

#[test]
fn a_clip_at_another_rate_is_converted_once() {
    let r = one_output(60, false);
    r.fake.run_until(0.5);
    r.fake.take_output("o");
    r.eng.inject("o", &[0], &vec![0.5; 2400], 24_000).unwrap(); // 100 ms at 24 kHz
    r.fake.run_until(1.0);
    let cap = r.fake.take_output("o");
    let c0 = channel(&cap, 2, 0, 0, cap.len() / 2);
    let loud = c0.iter().filter(|&&x| x > 0.25).count();
    assert!((4700..=4900).contains(&loud), "100 ms is 4800 frames at 48 kHz, got {loud}");
    assert!(c0.iter().all(|&x| x < 0.56), "no overshoot beyond the filter's small ripple");
    // It started at the next block.
    assert!(c0[..BLOCK].iter().any(|&x| x > 0.25));
}

#[test]
fn injecting_into_an_output_that_is_not_there_is_an_error() {
    let r = rig();
    assert_eq!(r.eng.inject("nope", &[0], &[0.1], 48_000), Err(Error::UnknownOutput("nope".into())));
}
