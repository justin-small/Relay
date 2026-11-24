//! The shared receiver pool (`Pool` of `service.py`, #294): one receiver per source however many listeners use it (the viewer, a multiview,
//! the router), closed when the last listener leaves. A receiver is a thread of `relay-ndi` that queues what arrives; this pool reads the queue
//! and hands each picture to its listeners on that thread, so a frame goes from the NDI library to a JPEG without leaving Rust.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

use relay_ndi::recv::{Audio, Event, Receiver, Video};
use tokio::sync::Notify;

use crate::analysis::Analyzer;

/// A source's low-bandwidth receiver is a pool entry of its own, under this suffix.
pub const LOW: &str = "\u{0}low";
/// How long a receiver waits for its source to show on the network before giving up.
const LOCATE_SECONDS: f64 = 3.0;
const POLL: Duration = Duration::from_millis(500);
const PERF_EVERY_S: f64 = 2.0;

pub type OnVideo = Arc<dyn Fn(&Video) + Send + Sync>;
pub type OnAudio = Arc<dyn Fn(&Audio) + Send + Sync>;

/// A version that goes up on every change, and a way to wait for it (the page long-polls it). Cheap to share: the pool's receiver threads bump it.
#[derive(Default)]
pub struct Changes {
    version: AtomicU64,
    notify: Notify,
}

impl Changes {
    pub fn bump(&self) {
        self.version.fetch_add(1, Ordering::SeqCst);
        self.notify.notify_waiters();
    }

    pub fn version(&self) -> u64 {
        self.version.load(Ordering::SeqCst)
    }

    /// Return when the version differs from `since`, or after `timeout`.
    pub async fn wait(&self, since: u64, timeout: Duration) {
        if self.version() != since {
            return;
        }
        let notified = self.notify.notified();
        let _ = tokio::time::timeout(timeout, notified).await;
    }
}

/// What the pool learned about a source from its frames.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Info {
    pub width: u32,
    pub height: u32,
    pub fps: f64,
}

struct Listener {
    id: u64,
    video: OnVideo,
    audio: Option<OnAudio>,
}

struct Entry {
    key: String,
    source: String,
    low: bool,
    listeners: Mutex<Vec<Listener>>,
    stop: AtomicBool,
    thread: Mutex<Option<JoinHandle<()>>>,
    error: Mutex<String>,
}

/// How a receiver is opened: the real one connects through the NDI runtime; a test gives its own.
pub type Opener = Arc<dyn Fn(&str, bool) -> Result<Receiver, String> + Send + Sync>;

pub struct Pool {
    entries: Mutex<HashMap<String, Arc<Entry>>>,
    info: Mutex<HashMap<String, Info>>,
    changes: Arc<Changes>,
    analyzer: Option<Arc<Analyzer>>,
    next_id: AtomicU64,
    open: Opener,
}

/// A listener's hold on a source's receiver. Letting go of it (`release`, or dropping it) is safe to do twice.
pub struct Lease {
    pool: Arc<Pool>,
    key: String,
    id: u64,
    done: AtomicBool,
}

impl Lease {
    pub fn release(&self) {
        if !self.done.swap(true, Ordering::SeqCst) {
            self.pool.release(&self.key, self.id);
        }
    }
}

impl Drop for Lease {
    fn drop(&mut self) {
        self.release();
    }
}

impl Pool {
    pub fn new(changes: Arc<Changes>, analyzer: Option<Arc<Analyzer>>) -> Arc<Pool> {
        Self::with_opener(changes, analyzer, Arc::new(|name, low| Receiver::open(name, low, LOCATE_SECONDS)))
    }

    pub fn with_opener(changes: Arc<Changes>, analyzer: Option<Arc<Analyzer>>, open: Opener) -> Arc<Pool> {
        Arc::new(Pool { entries: Mutex::default(), info: Mutex::default(), changes, analyzer, next_id: AtomicU64::new(1), open })
    }

    /// Hold `source`'s receiver (its low-bandwidth one when `low`) and call `video` with each picture on the receiver's thread.
    /// `audio` is called with each chunk of sound.
    pub fn acquire(self: &Arc<Self>, source: &str, low: bool, video: OnVideo, audio: Option<OnAudio>) -> Option<Lease> {
        if source.is_empty() {
            return None;
        }
        let key = if low { format!("{source}{LOW}") } else { source.to_owned() };
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        {
            let mut entries = self.entries.lock().ok()?;
            match entries.get(&key) {
                Some(e) => e.listeners.lock().ok()?.push(Listener { id, video, audio }),
                None => {
                    let entry = Arc::new(Entry {
                        key: key.clone(),
                        source: source.to_owned(),
                        low,
                        listeners: Mutex::new(vec![Listener { id, video, audio }]),
                        stop: AtomicBool::new(false),
                        thread: Mutex::default(),
                        error: Mutex::default(),
                    });
                    let (pool, e2) = (Arc::clone(self), Arc::clone(&entry));
                    let handle = std::thread::Builder::new().name("relay-ndi-pool".into()).spawn(move || pool.run(&e2)).ok()?;
                    *entry.thread.lock().ok()? = Some(handle);
                    entries.insert(key.clone(), entry);
                }
            }
        }
        self.changes.bump();
        Some(Lease { pool: Arc::clone(self), key, id, done: AtomicBool::new(false) })
    }

    fn release(&self, key: &str, id: u64) {
        let gone = {
            let Ok(mut entries) = self.entries.lock() else { return };
            let Some(e) = entries.get(key) else { return };
            let empty = e.listeners.lock().map(|mut l| {
                l.retain(|x| x.id != id);
                l.is_empty()
            });
            if empty.unwrap_or(false) {
                entries.remove(key)
            } else {
                None
            }
        };
        if let Some(e) = gone {
            Self::stop_entry(&e);
        }
        self.changes.bump();
    }

    fn stop_entry(e: &Arc<Entry>) {
        e.stop.store(true, Ordering::SeqCst);
        let t = e.thread.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = t {
            if t.thread().id() != std::thread::current().id() {
                let _ = t.join();
            }
        }
    }

    fn run(&self, e: &Arc<Entry>) {
        let rx = match (self.open)(&e.source, e.low) {
            Ok(r) => r,
            Err(why) => {
                tracing::info!("NDI receiver for a source stopped: {why}");
                if let Ok(mut err) = e.error.lock() {
                    *err = "Could not receive that source.".to_owned();
                }
                return;
            }
        };
        let mut perf_at = 0.0;
        while !e.stop.load(Ordering::SeqCst) {
            let event = rx.next(POLL);
            if let Some(a) = &self.analyzer {
                let at = crate::analysis::now();
                if at - perf_at >= PERF_EVERY_S {
                    perf_at = at;
                    a.on_perf(&e.key, rx.counts().video_dropped);
                }
            }
            match event {
                Some(Event::Video(v, arrived)) => {
                    if let Some(a) = &self.analyzer {
                        a.on_video(&e.key, &e.source, e.low, &v, arrived);
                    }
                    if !e.low {
                        self.note(&e.source, Info { width: v.width, height: v.height, fps: v.fps });
                    }
                    for cb in e.listeners.lock().map(|l| l.iter().map(|x| Arc::clone(&x.video)).collect::<Vec<_>>()).unwrap_or_default() {
                        cb(&v);
                    }
                }
                Some(Event::Audio(a)) => {
                    if let Some(an) = &self.analyzer {
                        an.on_audio(&e.key, &e.source, e.low, &a);
                    }
                    for cb in e.listeners.lock().map(|l| l.iter().filter_map(|x| x.audio.clone()).collect::<Vec<_>>()).unwrap_or_default() {
                        cb(&a);
                    }
                }
                Some(Event::Meta(xml)) => {
                    if let Some(a) = &self.analyzer {
                        a.on_meta(&e.key, &xml);
                    }
                }
                None => {}
            }
        }
        rx.close();
        if let Some(a) = &self.analyzer {
            a.drop_key(&e.key);
        }
    }

    fn note(&self, source: &str, now: Info) {
        let changed = self.info.lock().is_ok_and(|mut m| m.insert(source.to_owned(), now) != Some(now));
        if changed {
            self.changes.bump();
        }
    }

    /// What the frames of `source` last said about its size and rate.
    pub fn info(&self, source: &str) -> Option<Info> {
        self.info.lock().ok()?.get(source).copied()
    }

    /// Listeners per source (the low-bandwidth receivers are not counted).
    pub fn counts(&self) -> HashMap<String, usize> {
        let Ok(entries) = self.entries.lock() else { return HashMap::new() };
        entries.values().filter(|e| !e.low).map(|e| (e.source.clone(), e.listeners.lock().map_or(0, |l| l.len()))).collect()
    }

    /// Why a source could not be received, if it could not.
    pub fn error(&self, source: &str) -> String {
        self.entries.lock().ok().and_then(|m| m.get(source).and_then(|e| e.error.lock().ok().map(|x| x.clone()))).unwrap_or_default()
    }

    pub fn close_all(&self) {
        let all: Vec<Arc<Entry>> = self.entries.lock().map(|mut m| m.drain().map(|(_, e)| e).collect()).unwrap_or_default();
        for e in &all {
            e.stop.store(true, Ordering::SeqCst);
        }
        for e in &all {
            Self::stop_entry(e);
        }
        if let Ok(mut i) = self.info.lock() {
            i.clear();
        }
    }
}
