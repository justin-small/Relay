//! NDI audio going out (issue 301): an audio-only source the lane bus writes to, and a combiner that takes the picture
//! from one source and the sound from another.

use crate::ffi::{self, Api, AudioFrame, Handle, SendCreate};
use crate::find::cstr;
use crate::recv::{Event, Receiver};
use crate::send::Sender;
use std::collections::VecDeque;
use std::ptr;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

/// Planar audio waiting to go out: `channels` queues of samples, all the same length.
pub struct Ring {
    ch: Vec<VecDeque<f32>>,
}

impl Ring {
    pub fn new(channels: usize) -> Ring {
        Ring { ch: (0..channels.max(1)).map(|_| VecDeque::new()).collect() }
    }

    pub fn channels(&self) -> usize {
        self.ch.len()
    }

    pub fn len(&self) -> usize {
        self.ch[0].len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Add `frames` of interleaved audio (`frames * channels` samples).
    pub fn push_interleaved(&mut self, block: &[f32], frames: usize) {
        let n = self.ch.len();
        for f in 0..frames.min(block.len() / n) {
            for (c, q) in self.ch.iter_mut().enumerate() {
                q.push_back(block[f * n + c]);
            }
        }
    }

    /// Add planar audio of `n` frames, ignoring channels the ring does not have and filling ones the block lacks.
    pub fn push_planar(&mut self, planar: &[f32], channels: usize, n: usize) {
        for (c, q) in self.ch.iter_mut().enumerate() {
            if c < channels && planar.len() >= (c + 1) * n {
                q.extend(&planar[c * n..(c + 1) * n]);
            } else {
                q.extend(std::iter::repeat_n(0.0, n));
            }
        }
    }

    /// Take `frames` of planar audio, padded with silence when there is less; says how many were real.
    pub fn take(&mut self, frames: usize) -> (Vec<f32>, usize) {
        let real = frames.min(self.len());
        let mut out = Vec::with_capacity(frames * self.ch.len());
        for q in &mut self.ch {
            out.extend(q.drain(..real));
            out.extend(std::iter::repeat_n(0.0, frames - real));
        }
        (out, real)
    }

    /// Keep only the newest `frames`.
    pub fn trim(&mut self, frames: usize) {
        for q in &mut self.ch {
            while q.len() > frames {
                q.pop_front();
            }
        }
    }
}

/// `planar` (`ch` rows of `n`) at `from` Hz as `to` Hz, by linear interpolation. Good enough for monitoring a feed that
/// is not 48 kHz; the bus path resamples properly.
pub fn resample(planar: &[f32], ch: usize, n: usize, from: u32, to: u32) -> (Vec<f32>, usize) {
    if from == to || n == 0 || from == 0 {
        return (planar.to_vec(), n);
    }
    let m = ((n as u64 * to as u64) / from as u64).max(1) as usize;
    let mut out = Vec::with_capacity(ch * m);
    for c in 0..ch {
        let row = &planar[c * n..(c + 1) * n];
        for i in 0..m {
            let pos = i as f64 * from as f64 / to as f64;
            let (a, frac) = (pos.floor() as usize, pos - pos.floor());
            let (x, y) = (row[a.min(n - 1)], row[(a + 1).min(n - 1)]);
            out.push(x + (y - x) * frac as f32);
        }
    }
    (out, m)
}

struct SinkShared {
    ring: Mutex<Ring>,
    stop: AtomicBool,
    underruns: AtomicU64,
    sent: AtomicU64,
    capacity: usize,
}

/// An audio-only NDI source fed from the lane bus. `write` never blocks; a thread sends 10 ms at a time on the library's
/// own clock, with silence when the bus has not supplied enough.
pub struct AudioSink {
    api: Arc<Api>,
    h: usize,
    shared: Arc<SinkShared>,
    rate: u32,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl AudioSink {
    pub fn open(name: &str, channels: usize, rate: u32) -> Result<AudioSink, String> {
        let api = ffi::api()?;
        let n = cstr(name);
        let create = SendCreate { name: n.as_ptr(), groups: ptr::null(), clock_video: false, clock_audio: true };
        // SAFETY: `create` and `n` outlive the call.
        let h = unsafe { (api.send_create)(&create) };
        if h.is_null() {
            return Err("NDI would not create the output.".into());
        }
        let capacity = rate as usize / 5; // 200 ms
        let shared = Arc::new(SinkShared {
            ring: Mutex::new(Ring::new(channels)),
            stop: AtomicBool::new(false),
            underruns: AtomicU64::new(0),
            sent: AtomicU64::new(0),
            capacity,
        });
        let (a2, s2, h2) = (api.clone(), shared.clone(), h as usize);
        let thread = std::thread::Builder::new()
            .name("relay-ndi-audio-out".into())
            .spawn(move || sink_loop(a2, h2 as Handle, s2, rate))
            .map_err(|e| e.to_string())?;
        Ok(AudioSink { api, h: h as usize, shared, rate, thread: Mutex::new(Some(thread)) })
    }

    pub fn rate(&self) -> u32 {
        self.rate
    }

    pub fn channels(&self) -> usize {
        self.shared.ring.lock().unwrap_or_else(|p| p.into_inner()).channels()
    }

    /// Frames `write` takes now.
    pub fn free(&self) -> usize {
        self.shared.capacity.saturating_sub(self.queued())
    }

    pub fn queued(&self) -> usize {
        self.shared.ring.lock().unwrap_or_else(|p| p.into_inner()).len()
    }

    pub fn underruns(&self) -> u64 {
        self.shared.underruns.load(Relaxed)
    }

    pub fn sent_frames(&self) -> u64 {
        self.shared.sent.load(Relaxed)
    }

    /// Queue interleaved float audio; returns the frames taken.
    pub fn write(&self, block: &[f32], frames: usize) -> usize {
        let take = frames.min(self.free());
        self.shared.ring.lock().unwrap_or_else(|p| p.into_inner()).push_interleaved(block, take);
        take
    }

    pub fn close(&self) {
        self.shared.stop.store(true, Relaxed);
        if let Some(t) = self.thread.lock().unwrap_or_else(|p| p.into_inner()).take() {
            let _ = t.join();
            // SAFETY: the sending thread has ended, so nothing else uses the handle; destroyed once.
            unsafe { (self.api.send_destroy)(self.h as Handle) };
        }
    }
}

impl Drop for AudioSink {
    fn drop(&mut self) {
        self.close();
    }
}

fn send_planar(api: &Api, h: Handle, planar: &[f32], ch: usize, n: usize, rate: u32) {
    let a = AudioFrame {
        sample_rate: rate as i32,
        channels: ch as i32,
        samples: n as i32,
        timecode: ffi::TIMECODE_SYNTHESIZE,
        fourcc: ffi::FOURCC_FLTP,
        data: planar.as_ptr() as *mut u8,
        channel_stride: (n * 4) as i32,
        metadata: ptr::null(),
        timestamp: 0,
    };
    // SAFETY: `a` points at `planar`, which the library only reads (synchronously) and which outlives the call.
    unsafe { (api.send_audio)(h, &a) };
}

fn sink_loop(api: Arc<Api>, h: Handle, shared: Arc<SinkShared>, rate: u32) {
    let chunk = (rate / 100) as usize; // 10 ms
    while !shared.stop.load(Relaxed) {
        let (planar, real, ch) = {
            let mut r = shared.ring.lock().unwrap_or_else(|p| p.into_inner());
            let ch = r.channels();
            let (p, real) = r.take(chunk);
            (p, real, ch)
        };
        if real < chunk {
            shared.underruns.fetch_add(1, Relaxed);
        }
        send_planar(&api, h, &planar, ch, chunk, rate);
        shared.sent.fetch_add(chunk as u64, Relaxed);
    }
}

// ------------------------------------------------------------------------------------------------- combiner
struct CombineShared {
    stop: AtomicBool,
    frames: AtomicU64,
}

/// One NDI source whose picture comes from one source and whose sound comes from another (issue 301: audio routed
/// separately from video). The NDI routing call switches a whole source, picture and sound together, so this decodes
/// both and sends them again; use it only when the two differ.
pub struct Combiner {
    shared: Arc<CombineShared>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

impl Combiner {
    /// `video` and `audio` are source names; either may be empty (then the output has only the other). Fails when a named
    /// source is not on the network.
    pub fn open(name: &str, video: &str, audio: &str, wait: f64) -> Result<Combiner, String> {
        if video.is_empty() && audio.is_empty() {
            return Err("Choose a picture source or a sound source.".into());
        }
        let vrx = if video.is_empty() { None } else { Some(Receiver::open(video, false, wait)?) };
        let arx = if audio.is_empty() { None } else { Some(Receiver::open(audio, true, wait)?) };
        let shared = Arc::new(CombineShared { stop: AtomicBool::new(false), frames: AtomicU64::new(0) });
        let (s2, name) = (shared.clone(), name.to_string());
        let thread = std::thread::Builder::new()
            .name("relay-ndi-combine".into())
            .spawn(move || combine_loop(&name, vrx, arx, s2))
            .map_err(|e| e.to_string())?;
        Ok(Combiner { shared, thread: Mutex::new(Some(thread)) })
    }

    /// Pictures sent so far.
    pub fn frames(&self) -> u64 {
        self.shared.frames.load(Relaxed)
    }

    pub fn close(&self) {
        self.shared.stop.store(true, Relaxed);
        if let Some(t) = self.thread.lock().unwrap_or_else(|p| p.into_inner()).take() {
            let _ = t.join();
        }
    }
}

impl Drop for Combiner {
    fn drop(&mut self) {
        self.close();
    }
}

const OUT_RATE: u32 = 48000;
const KEEP_MS: usize = 250;

/// Move what the sound receiver has queued into `ring` at 48 kHz.
fn drain_audio(arx: &Receiver, ring: &mut Ring) {
    while let Some(ev) = arx.next(Duration::ZERO) {
        if let Event::Audio(a) = ev {
            let (p, n) = resample(&a.data, a.channels, a.samples, a.rate, OUT_RATE);
            ring.push_planar(&p, a.channels, n);
        }
    }
    ring.trim(OUT_RATE as usize * KEEP_MS / 1000);
}

fn combine_loop(name: &str, vrx: Option<Receiver>, arx: Option<Receiver>, shared: Arc<CombineShared>) {
    let mut ring = Ring::new(2);
    let mut sender: Option<(Sender, u32, u32, &'static str)> = None;
    let mut audio_sink: Option<AudioSink> = None;
    while !shared.stop.load(Relaxed) {
        if let Some(a) = &arx {
            drain_audio(a, &mut ring);
        }
        match &vrx {
            Some(v) => {
                let Some(ev) = v.next(Duration::from_millis(40)) else { continue };
                let Event::Video(pic, _) = ev else { continue };
                let fourcc: &'static str = if pic.fourcc == "UYVY" { "UYVY" } else { "BGRA" };
                if pic.fourcc != "UYVY" && pic.fourcc != "BGRA" {
                    continue;
                }
                let stale = sender.as_ref().is_none_or(|(_, w, h, f)| (*w, *h, *f) != (pic.width, pic.height, fourcc));
                if stale {
                    sender = None; // closes the old one first, so the name is free
                    match Sender::open(name, pic.width, pic.height, pic.fps, fourcc) {
                        Ok(s) => sender = Some((s, pic.width, pic.height, fourcc)),
                        Err(_) => {
                            std::thread::sleep(Duration::from_millis(200));
                            continue;
                        }
                    }
                }
                let per_frame = if pic.fps > 1.0 { (OUT_RATE as f64 / pic.fps).round() as usize } else { 1600 };
                if let Some((tx, ..)) = &sender {
                    let audio = if arx.is_some() { Some(ring.take(per_frame).0) } else { None };
                    let _ = tx.send(&pic.data, audio.as_ref().map(|a| (a.as_slice(), 2usize, per_frame)));
                    shared.frames.fetch_add(1, Relaxed);
                }
            }
            None => {
                // Sound only: pass it on as it arrives.
                if audio_sink.is_none() {
                    audio_sink = AudioSink::open(name, 2, OUT_RATE).ok();
                }
                if let Some(s) = &audio_sink {
                    let queued = ring.len();
                    if queued > 0 {
                        let (p, _) = ring.take(queued);
                        let mut inter = Vec::with_capacity(p.len());
                        for f in 0..queued {
                            inter.push(p[f]);
                            inter.push(p[queued + f]);
                        }
                        s.write(&inter, queued);
                    }
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        }
    }
    if let Some(a) = &arx {
        a.close();
    }
    if let Some(v) = &vrx {
        v.close();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ring_hands_back_what_was_put_in_and_pads_with_silence() {
        let mut r = Ring::new(2);
        r.push_interleaved(&[1.0, -1.0, 2.0, -2.0, 3.0, -3.0], 3);
        let (out, real) = r.take(4);
        assert_eq!(real, 3);
        assert_eq!(out, vec![1.0, 2.0, 3.0, 0.0, -1.0, -2.0, -3.0, 0.0]);
        assert!(r.is_empty());
    }

    #[test]
    fn a_channel_the_block_lacks_is_silent_and_extras_are_dropped() {
        let mut r = Ring::new(2);
        r.push_planar(&[0.5, 0.5], 1, 2);
        assert_eq!(r.take(2).0, vec![0.5, 0.5, 0.0, 0.0]);
        r.push_planar(&[1.0, 2.0, 3.0, 4.0, 5.0, 6.0], 3, 2);
        assert_eq!(r.take(2).0, vec![1.0, 2.0, 3.0, 4.0]);
    }

    #[test]
    fn trim_keeps_the_newest() {
        let mut r = Ring::new(1);
        r.push_planar(&[1.0, 2.0, 3.0, 4.0], 1, 4);
        r.trim(2);
        assert_eq!(r.take(2).0, vec![3.0, 4.0]);
    }

    #[test]
    fn resampling_changes_length_by_the_rate_ratio_and_keeps_a_ramp_straight() {
        let ramp: Vec<f32> = (0..441).map(|i| i as f32).collect();
        let (out, m) = resample(&ramp, 1, 441, 44100, 48000);
        assert_eq!(m, 480);
        assert!((out[100] - 100.0 * 44100.0 / 48000.0).abs() < 1e-3);
        assert_eq!(resample(&ramp, 1, 441, 48000, 48000).1, 441);
    }
}
