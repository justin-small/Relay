//! Taps: a non-realtime reader on a lane (speech-to-text, a recorder). A tap is one more consumer in the lane's
//! fan-out with its own ring, so a slow reader costs the realtime side nothing: it only loses frames, and the
//! loss is reported. If the tap wants a rate other than 48 kHz, the conversion is done here, on the reader's
//! thread, once.

use crate::resamp::FixedIn;
use crate::util::LANE_RATE;
use rtrb::Consumer;
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy)]
pub struct TapOptions {
    /// Rate of the samples `read_tap` returns; `None` is the lane's 48 kHz.
    pub rate: Option<u32>,
    /// How much lane audio the tap's ring holds. A reader that falls further behind than this loses the frames
    /// that arrive while the ring is full, and the loss is counted.
    pub capacity_ms: u32,
}

impl Default for TapOptions {
    fn default() -> Self {
        Self { rate: None, capacity_ms: 4000 }
    }
}

/// What `read_tap` returns.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct TapRead {
    /// Mono samples at the tap's rate. Possibly fewer than asked for (never more); empty if nothing arrived in time.
    pub samples: Vec<f32>,
    /// Lane frames (48 kHz) lost since the previous read because this reader was too slow.
    pub dropped: u64,
}

struct State {
    rx: Option<Consumer<f32>>,
    rs: Option<FixedIn>,
    /// Converted samples that did not fit in the last read.
    spare: Vec<f32>,
    scratch: Vec<f32>,
    reported: u64,
}

pub(crate) struct TapReader {
    state: Mutex<State>,
    /// Frames the realtime side dropped for this tap (shared with its ring's producer).
    pub dropped: Arc<AtomicU64>,
    pub capacity: usize,
    pub rate: u32,
}

impl TapReader {
    pub fn new(opts: TapOptions) -> Self {
        let rate = opts.rate.unwrap_or(LANE_RATE);
        let rs = (rate != LANE_RATE).then(|| FixedIn::new(LANE_RATE, rate, 1, 480));
        Self {
            state: Mutex::new(State { rx: None, rs, spare: Vec::new(), scratch: vec![0.0; 4800], reported: 0 }),
            dropped: Arc::new(AtomicU64::new(0)),
            capacity: (opts.capacity_ms.max(100) as usize * LANE_RATE as usize / 1000).max(LANE_RATE as usize / 10),
            rate,
        }
    }

    pub fn set_rx(&self, rx: Option<Consumer<f32>>) {
        self.state.lock().unwrap_or_else(|e| e.into_inner()).rx = rx;
    }

    /// Up to `max_frames` samples; waits up to `timeout` for at least some to arrive when none are ready.
    pub fn read(&self, max_frames: usize, timeout: Duration) -> TapRead {
        let deadline = Instant::now() + timeout;
        let mut st = self.state.lock().unwrap_or_else(|e| e.into_inner());
        let st = &mut *st;
        // Not `max_frames`: callers ask for a ceiling (24000) and get a few hundred samples, and the caller may hold
        // on to what it gets. Grown as needed, and trimmed to its length at the end.
        let mut out: Vec<f32> = Vec::with_capacity(max_frames.min(2048));
        let keep = st.spare.len().min(max_frames);
        out.extend(st.spare.drain(..keep));
        loop {
            while out.len() < max_frames {
                let Some(rx) = st.rx.as_mut() else { break };
                let n = rx.slots().min(st.scratch.len());
                if n == 0 {
                    break;
                }
                let Ok(chunk) = rx.read_chunk(n) else { break };
                let (a, b) = chunk.as_slices();
                st.scratch[..a.len()].copy_from_slice(a);
                st.scratch[a.len()..n].copy_from_slice(&b[..n - a.len()]);
                chunk.commit_all();
                let got = &st.scratch[..n];
                match st.rs.as_mut() {
                    None => out.extend_from_slice(got),
                    Some(rs) => rs.feed(got, |o| out.extend_from_slice(o)),
                }
            }
            if out.len() >= max_frames || !out.is_empty() || Instant::now() >= deadline {
                break;
            }
            // Waiting on a real clock: this is the non-realtime side, and a short nap is what a reader that
            // wants to block does. Tests pass a zero timeout and never get here.
            std::thread::sleep(Duration::from_millis(1));
        }
        if out.len() > max_frames {
            st.spare.extend_from_slice(&out[max_frames..]);
            out.truncate(max_frames);
        }
        out.shrink_to_fit();
        let total = self.dropped.load(Relaxed);
        let dropped = total - st.reported;
        st.reported = total;
        TapRead { samples: out, dropped }
    }
}
