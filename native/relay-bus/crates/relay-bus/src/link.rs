//! A link carries one lane of one engine into an external input of another engine, entirely in Rust (#605). The
//! intercom has its own engine for its crew mixes; a link is how audio from the main bus (a program feed, a
//! talkback lane) reaches it without passing through Python, so a busy Python event loop cannot make it choppy.
//!
//! A link is a tap on the source lane read by a thread of its own and pushed into the destination's input, the same
//! path a network source takes. Neither engine's realtime side knows the link exists: the tap's ring is one more
//! consumer of the lane, and a destination that is not ready only costs the link some frames, which are counted.

use crate::engine::Engine;
use crate::error::Error;
use crate::tap::TapOptions;
use crate::util::LANE_RATE;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::Arc;
use std::thread::JoinHandle;
use std::time::Duration;

/// How long the thread waits for audio before looking at its stop flag again.
const WAIT: Duration = Duration::from_millis(20);
/// The most the thread carries in one go (100 ms), so one push is never large.
const CHUNK: usize = 4800;
/// What the source tap holds when this thread is held up (one second).
const TAP_MS: u32 = 1000;

/// What a link has done, for the panel and the tests.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct LinkStatus {
    /// Frames pushed into the destination.
    pub carried: u64,
    /// Frames the destination refused because it had no such input yet (not configured, or not running).
    pub refused: u64,
    /// Lane frames the source tap lost because this thread was too slow.
    pub dropped: u64,
}

#[derive(Default)]
struct Counts {
    carried: AtomicU64,
    refused: AtomicU64,
    dropped: AtomicU64,
}

pub struct Link {
    src: Arc<Engine>,
    tap: u64,
    stop: Arc<AtomicBool>,
    counts: Arc<Counts>,
    thread: Option<JoinHandle<()>>,
}

impl Link {
    /// Carry lane `lane` (1..=32) of `src` into the external input `input_id` of `dst` (see `config::input_id`).
    /// The input may be configured later: until then the frames are counted as refused.
    pub fn start(src: Arc<Engine>, lane: usize, dst: Arc<Engine>, input_id: &str) -> Result<Link, Error> {
        let tap = src.open_tap(lane, TapOptions { rate: None, capacity_ms: TAP_MS })?;
        let stop = Arc::new(AtomicBool::new(false));
        let counts = Arc::new(Counts::default());
        let spawned = {
            let (src, stop, counts, id) = (src.clone(), stop.clone(), counts.clone(), input_id.to_string());
            std::thread::Builder::new().name("relay-bus-link".into()).spawn(move || carry(&src, tap, &dst, &id, &stop, &counts))
        };
        match spawned {
            Ok(thread) => Ok(Link { src, tap, stop, counts, thread: Some(thread) }),
            Err(e) => {
                let _ = src.close_tap(tap);
                Err(Error::Backend(format!("could not start the link thread: {e}")))
            }
        }
    }

    pub fn status(&self) -> LinkStatus {
        LinkStatus { carried: self.counts.carried.load(Relaxed), refused: self.counts.refused.load(Relaxed), dropped: self.counts.dropped.load(Relaxed) }
    }

    /// Stop carrying audio and release the tap.
    pub fn stop(&mut self) {
        self.stop.store(true, Relaxed);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
            let _ = self.src.close_tap(self.tap);
        }
    }
}

impl Drop for Link {
    fn drop(&mut self) {
        self.stop();
    }
}

fn carry(src: &Engine, tap: u64, dst: &Engine, input_id: &str, stop: &AtomicBool, counts: &Counts) {
    while !stop.load(Relaxed) {
        let Ok(read) = src.read_tap(tap, CHUNK, WAIT) else { return };
        counts.dropped.fetch_add(read.dropped, Relaxed);
        if read.samples.is_empty() {
            continue;
        }
        let n = read.samples.len() as u64;
        match dst.push(input_id, &read.samples, 1, LANE_RATE) {
            Ok(()) => counts.carried.fetch_add(n, Relaxed),
            Err(_) => counts.refused.fetch_add(n, Relaxed),
        };
    }
}
