//! The NDI single-source viewer (`viewer.py`, #296): one source, re-served to browsers as MJPEG.
//!
//! A **feed** is one source being watched. It holds a lease on the shared receiver pool (so ten people watching one camera, or the viewer
//! plus a multiview, cost one receiver), encodes the newest frame to JPEG at most `view_fps` times a second on the receiver's thread, and hands
//! that same JPEG to every browser watching it. Nothing is encoded while nobody watches, and a feed closes (and gives its lease back) when its
//! last viewer leaves. `view_max` feeds can be open at once; a viewer beyond that is told to try later.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use bytes::Bytes;
use relay_ndi::recv::Video;
use tokio::sync::watch;

use crate::pool::{Lease, Pool};

pub const BOUNDARY: &str = "relayndiframe";
/// A viewer waits this long for a frame before the stream gives up and the browser retries.
pub const IDLE_WAIT: Duration = Duration::from_secs(10);

/// The viewer's limits, read each frame so a change of settings applies at once.
#[derive(Clone, Copy, Debug)]
pub struct Limits {
    pub fps: u32,
    pub width: u32,
    pub quality: u8,
    pub max: usize,
}

impl Limits {
    pub fn from_settings(s: &serde_json::Value) -> Limits {
        let n = |k: &str, d: i64| s[k].as_i64().unwrap_or(d);
        Limits { fps: n("view_fps", 15).max(1) as u32, width: n("view_width", 1280).max(2) as u32, quality: n("view_quality", 60).clamp(1, 100) as u8, max: n("view_max", 4).max(1) as usize }
    }
}

/// The newest JPEG and its sequence number (0 before the first).
type Latest = (u64, Bytes);

pub struct Feed {
    pub source: String,
    viewers: AtomicI64,
    tx: watch::Sender<Latest>,
    last: Mutex<Option<Instant>>,
    failed: AtomicBool,
    lease: Mutex<Option<Lease>>,
}

impl Feed {
    /// Hold the receiver this feed is fed from; `false` when the feed cannot take it.
    pub(crate) fn hold(&self, lease: Lease) -> bool {
        match self.lease.lock() {
            Ok(mut slot) => {
                *slot = Some(lease);
                true
            }
            Err(_) => false,
        }
    }

    /// Give the receiver back.
    pub(crate) fn release(&self) {
        let lease = self.lease.lock().ok().and_then(|mut l| l.take());
        drop(lease);
    }

    /// Every browser is gone: forget them and give the receiver back.
    pub(crate) fn end(&self) {
        self.viewers.store(0, Ordering::SeqCst);
        self.release();
    }

    pub(crate) fn new(source: &str) -> Arc<Feed> {
        Arc::new(Feed { source: source.to_owned(), viewers: AtomicI64::new(1), tx: watch::channel((0, Bytes::new())).0, last: Mutex::new(None), failed: AtomicBool::new(false), lease: Mutex::new(None) })
    }

    /// A picture from the receiver's thread: encode it if a viewer is watching and `view_fps` allows, and publish it.
    fn on_frame(&self, v: &Video, limits: Limits) {
        let now = Instant::now();
        if self.viewers.load(Ordering::SeqCst) <= 0 {
            return;
        }
        {
            let Ok(mut last) = self.last.lock() else { return };
            if last.is_some_and(|l| now.duration_since(l) < Duration::from_secs_f64(1.0 / f64::from(limits.fps))) {
                return;
            }
            *last = Some(now);
        }
        match relay_ndi::picture::jpeg(&v.data, v.width as usize, v.height as usize, v.fourcc, limits.width as usize, limits.quality) {
            Some(jpeg) => self.publish(jpeg),
            None => self.failed.store(true, Ordering::SeqCst),
        }
    }

    /// Set the newest picture and wake every browser waiting for one (any thread).
    pub fn publish(&self, jpeg: Vec<u8>) {
        self.tx.send_modify(|(seq, bytes)| {
            *seq += 1;
            *bytes = Bytes::from(jpeg);
        });
    }

    pub(crate) fn add_viewer(&self) {
        self.viewers.fetch_add(1, Ordering::SeqCst);
    }

    /// One viewer fewer; the number left.
    pub(crate) fn remove_viewer(&self) -> i64 {
        self.viewers.fetch_sub(1, Ordering::SeqCst) - 1
    }

    pub fn viewers(&self) -> i64 {
        self.viewers.load(Ordering::SeqCst)
    }

    pub fn failed(&self) -> bool {
        self.failed.load(Ordering::SeqCst)
    }

    /// (sequence, JPEG) of a frame newer than `after`, waiting for one; `None` when none came within `timeout`.
    pub async fn next(&self, after: u64, timeout: Duration) -> Option<(u64, Bytes)> {
        let mut rx = self.tx.subscribe();
        let deadline = tokio::time::Instant::now() + timeout;
        loop {
            {
                let cur = rx.borrow_and_update();
                if cur.0 != after && !cur.1.is_empty() {
                    return Some((cur.0, cur.1.clone()));
                }
            }
            tokio::time::timeout_at(deadline, rx.changed()).await.ok()?.ok()?;
        }
    }
}

/// Something a browser watches as a stream of JPEG frames: one source, or a composed multiview.
pub trait Frames: Send + Sync + 'static {
    /// (sequence, JPEG) of a frame newer than `after`, waiting up to `timeout` for one.
    fn next(&self, after: u64, timeout: Duration) -> impl std::future::Future<Output = Option<(u64, Bytes)>> + Send;
}

impl Frames for Feed {
    async fn next(&self, after: u64, timeout: Duration) -> Option<(u64, Bytes)> {
        Feed::next(self, after, timeout).await
    }
}

/// One part of the multipart body: the boundary, the headers and the JPEG.
pub fn part(jpeg: &[u8]) -> Bytes {
    let mut out = format!("--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {}\r\n\r\n", jpeg.len()).into_bytes();
    out.extend_from_slice(jpeg);
    out.extend_from_slice(b"\r\n");
    Bytes::from(out)
}

pub struct Viewers {
    pool: Arc<Pool>,
    settings: Arc<dyn Fn() -> Limits + Send + Sync>,
    feeds: Mutex<HashMap<String, Arc<Feed>>>,
}

impl Viewers {
    pub fn new(pool: Arc<Pool>, settings: Arc<dyn Fn() -> Limits + Send + Sync>) -> Viewers {
        Viewers { pool, settings, feeds: Mutex::default() }
    }

    /// Start (or join) the feed for `source`. `None` when it cannot be received, or `view_max` other sources are already being watched.
    pub fn open(&self, source: &str) -> Option<Arc<Feed>> {
        let mut feeds = self.feeds.lock().ok()?;
        if let Some(f) = feeds.get(source) {
            f.add_viewer();
            return Some(Arc::clone(f));
        }
        let limits = (self.settings)();
        if feeds.len() >= limits.max {
            return None;
        }
        let feed = Feed::new(source);
        let (f2, settings) = (Arc::clone(&feed), Arc::clone(&self.settings));
        let lease = self.pool.acquire(source, false, Arc::new(move |v| f2.on_frame(v, settings())), None)?;
        if !feed.hold(lease) {
            return None;
        }
        feeds.insert(source.to_owned(), Arc::clone(&feed));
        Some(feed)
    }

    /// A viewer left: the last one to go closes the feed and gives the receiver back.
    pub fn close(&self, feed: &Arc<Feed>) {
        let last = {
            let Ok(mut feeds) = self.feeds.lock() else { return };
            if feed.remove_viewer() > 0 {
                return;
            }
            if feeds.get(&feed.source).is_some_and(|f| Arc::ptr_eq(f, feed)) {
                feeds.remove(&feed.source);
            }
            true
        };
        if last {
            feed.release();
        }
    }

    pub fn count(&self) -> HashMap<String, i64> {
        self.feeds.lock().map(|f| f.iter().map(|(s, f)| (s.clone(), f.viewers())).collect()).unwrap_or_default()
    }

    pub fn close_all(&self) {
        let all: Vec<Arc<Feed>> = self.feeds.lock().map(|mut f| f.drain().map(|(_, f)| f).collect()).unwrap_or_default();
        for f in all {
            f.end();
        }
    }
}
