//! Listening to an NDI source in a browser (`listen.py`, #301): the source's sound, the channels chosen, as 16-bit stereo PCM.
//!
//! A browser on the LAN opens `/ndi/listen/<source>`; the answer is an endless body of little-endian 16-bit stereo samples and a header with the
//! sample rate, which the page plays through Web Audio. It takes a lease on the shared receiver pool (the low-bandwidth stream, so a listener
//! does not cost the picture's bandwidth), and mixing is `relay-ndi`'s (`pcm::mix_s16`). Nothing here opens a sound device: the sound goes to
//! the browser, not to this computer's speakers.

use std::collections::VecDeque;
use std::sync::atomic::AtomicUsize;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tokio::sync::Notify;

use crate::pool::{Lease, Pool};

/// Open listening streams at once: each is a stream of PCM to a browser.
pub const MAX_LISTENERS: usize = 4;
/// How long a listener waits for the first sound before being told there is none.
pub const FIRST_WAIT: Duration = Duration::from_secs(5);
/// How long a stream waits for more sound before it ends and the browser asks again.
pub const IDLE_WAIT: Duration = Duration::from_secs(10);
/// Chunks held for a slow browser; older ones are dropped.
pub const QUEUE: usize = 40;

/// "1,2" as `[0, 1]` (channels count from 1); `None` for something that is not a list of channel numbers.
pub fn parse_channels(text: &str) -> Option<Vec<usize>> {
    if text.is_empty() {
        return Some(vec![]);
    }
    let mut out: Vec<usize> = Vec::new();
    for part in text.split(',') {
        let part = part.trim();
        if part.is_empty() || !part.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        let n: usize = part.parse().ok().filter(|n| (1..=64).contains(n))?;
        if !out.contains(&(n - 1)) {
            out.push(n - 1);
        }
    }
    out.truncate(32);
    Some(out)
}

/// Counts the open streams so a busy service cannot be swamped by them.
#[derive(Default)]
pub struct Listeners(Mutex<usize>);

impl Listeners {
    pub fn take(&self) -> bool {
        let Ok(mut n) = self.0.lock() else { return false };
        if *n >= MAX_LISTENERS {
            return false;
        }
        *n += 1;
        true
    }

    pub fn give(&self) {
        if let Ok(mut n) = self.0.lock() {
            *n = n.saturating_sub(1);
        }
    }

    pub fn count(&self) -> usize {
        self.0.lock().map_or(0, |n| *n)
    }
}

/// One chunk of sound: the sample rate, the source's channel count, and stereo 16-bit PCM.
pub type Chunk = (u32, usize, Vec<u8>);

/// One browser's listening stream. `feed` runs on the receiver's thread; `next` on the runtime.
pub struct Stream {
    queue: Mutex<VecDeque<Chunk>>,
    notify: Notify,
    pub rate: AtomicUsize,
    pub channels: AtomicUsize,
    lease: Mutex<Option<Lease>>,
}

impl Stream {
    /// Hold the source's low-bandwidth receiver and pass each chunk of its sound, mixed to stereo from `pick`, to this stream.
    pub fn open(pool: &Arc<Pool>, source: &str, pick: Vec<usize>) -> Option<Arc<Stream>> {
        let stream = Arc::new(Stream { queue: Mutex::default(), notify: Notify::new(), rate: AtomicUsize::new(0), channels: AtomicUsize::new(0), lease: Mutex::default() });
        let s2 = Arc::clone(&stream);
        let lease = pool.acquire(
            source,
            true,
            Arc::new(|_| {}),
            Some(Arc::new(move |a: &relay_ndi::recv::Audio| {
                let data = relay_ndi::pcm::mix_s16(&a.data, a.channels, a.samples, &pick);
                if !data.is_empty() {
                    s2.push((a.rate, a.channels, data));
                }
            })),
        )?;
        *stream.lease.lock().ok()? = Some(lease);
        Some(stream)
    }

    pub fn push(&self, chunk: Chunk) {
        if let Ok(mut q) = self.queue.lock() {
            while q.len() >= QUEUE {
                q.pop_front();
            }
            q.push_back(chunk);
        }
        self.notify.notify_one();
    }

    /// The next chunk, waiting up to `wait`; `None` when none came.
    pub async fn next(&self, wait: Duration) -> Option<Chunk> {
        let deadline = tokio::time::Instant::now() + wait;
        loop {
            let notified = self.notify.notified();
            if let Some(c) = self.queue.lock().ok().and_then(|mut q| q.pop_front()) {
                return Some(c);
            }
            tokio::time::timeout_at(deadline, notified).await.ok()?;
        }
    }

    pub fn close(&self) {
        let lease = self.lease.lock().ok().and_then(|mut l| l.take());
        drop(lease);
    }
}
