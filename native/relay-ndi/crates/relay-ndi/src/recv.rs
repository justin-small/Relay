//! One receiver: a Rust thread calls the NDI capture function in a loop and queues what arrives, so Python (which
//! reads the queue) never sits inside the capture call holding the GIL. That is what lost about a third of a 30 fps
//! source's frames before (issue 566).

use crate::ffi::{self, Api, AudioFrame, Handle, MetadataFrame, Performance, RecvCreate, VideoFrame};
use crate::find::{cstr, Finder};
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

/// Pictures waiting for the reader. A reader that falls this far behind loses the oldest, and `overflow` counts them.
const VIDEO_QUEUE: usize = 8;
/// Audio chunks waiting (about 20 ms to 100 ms each): a couple of seconds.
const AUDIO_QUEUE: usize = 64;
const CAPTURE_MS: u32 = 100;
pub const LEVEL_FLOOR: f64 = -120.0;

pub struct Video {
    pub width: u32,
    pub height: u32,
    pub fps: f64,
    /// "UYVY", "BGRA", "BGRX", "RGBA", "RGBX": the pixel format of `data`.
    pub fourcc: &'static str,
    /// Tightly packed rows, no stride padding.
    pub data: Vec<u8>,
}

pub struct Audio {
    pub rate: u32,
    pub channels: usize,
    pub samples: usize,
    /// Planar: channel 0's samples, then channel 1's, and so on.
    pub data: Vec<f32>,
    /// The loudest sample of the chunk in dBFS, -120 for silence.
    pub peak_db: f64,
}

pub enum Event {
    Video(Video, Instant),
    Audio(Audio),
    Meta(String),
}

#[derive(Default, Clone, Copy, Debug)]
pub struct Counts {
    pub video_total: i64,
    pub video_dropped: i64,
    pub audio_total: i64,
    pub audio_dropped: i64,
    pub meta_total: i64,
    pub meta_dropped: i64,
    /// Pictures the reader was too slow to take (queue full), on top of what the NDI library dropped.
    pub overflow: u64,
    pub connections: i32,
}

struct Shared {
    queue: Mutex<VecDeque<Event>>,
    wake: Condvar,
    stop: AtomicBool,
    overflow: AtomicU64,
    video_seen: AtomicU64,
}

pub struct Receiver {
    api: Arc<Api>,
    h: usize,
    shared: Arc<Shared>,
    thread: Mutex<Option<JoinHandle<()>>>,
    _finder: Mutex<Finder>,
}

fn fourcc_name(f: u32) -> Option<&'static str> {
    match f {
        ffi::FOURCC_UYVY => Some("UYVY"),
        ffi::FOURCC_BGRA => Some("BGRA"),
        ffi::FOURCC_BGRX => Some("BGRX"),
        ffi::FOURCC_RGBA => Some("RGBA"),
        ffi::FOURCC_RGBX => Some("RGBX"),
        _ => None,
    }
}

pub fn db(peak: f32) -> f64 {
    if peak > 1e-6 {
        ((20.0 * (peak as f64).log10()) * 10.0).round() / 10.0
    } else {
        LEVEL_FLOOR
    }
}

impl Receiver {
    /// Connect to the source called `name`, waiting up to `wait` seconds for it to show on the network.
    pub fn open(name: &str, low_bandwidth: bool, wait: f64) -> Result<Receiver, String> {
        let api = ffi::api()?;
        let mut finder = Finder::open(true)?;
        let src = finder.locate(name, wait).ok_or_else(|| "That NDI source is not on the network.".to_string())?;
        let (n, u, me) = (cstr(&src.name), cstr(&src.address), cstr("Relay"));
        let create = RecvCreate {
            source: ffi::Source { name: n.as_ptr(), url: if src.address.is_empty() { std::ptr::null() } else { u.as_ptr() } },
            color_format: ffi::COLOR_UYVY_BGRA,
            bandwidth: if low_bandwidth { ffi::BANDWIDTH_LOWEST } else { ffi::BANDWIDTH_HIGHEST },
            allow_video_fields: false,
            name: me.as_ptr(),
        };
        // SAFETY: the strings outlive the call; the library copies them.
        let h = unsafe { (api.recv_create)(&create) };
        if h.is_null() {
            return Err("NDI would not open that source.".into());
        }
        let shared = Arc::new(Shared {
            queue: Mutex::new(VecDeque::new()),
            wake: Condvar::new(),
            stop: AtomicBool::new(false),
            overflow: AtomicU64::new(0),
            video_seen: AtomicU64::new(0),
        });
        let (a2, s2, h2) = (api.clone(), shared.clone(), h as usize);
        let thread = std::thread::Builder::new()
            .name("relay-ndi-recv".into())
            .spawn(move || capture_loop(a2, h2 as Handle, s2))
            .map_err(|e| e.to_string())?;
        Ok(Receiver { api, h: h as usize, shared, thread: Mutex::new(Some(thread)), _finder: Mutex::new(finder) })
    }

    /// The next thing that arrived, waiting up to `timeout` for it.
    pub fn next(&self, timeout: Duration) -> Option<Event> {
        let mut q = self.shared.queue.lock().unwrap_or_else(|p| p.into_inner());
        if q.is_empty() {
            q = self.shared.wake.wait_timeout_while(q, timeout, |q| q.is_empty()).unwrap_or_else(|p| p.into_inner()).0;
        }
        q.pop_front()
    }

    pub fn counts(&self) -> Counts {
        let (mut total, mut dropped) = (Performance::default(), Performance::default());
        // SAFETY: both out-pointers are valid; the handle is live until `close`.
        unsafe { (self.api.recv_performance)(self.h as Handle, &mut total, &mut dropped) };
        Counts {
            video_total: total.video,
            video_dropped: dropped.video,
            audio_total: total.audio,
            audio_dropped: dropped.audio,
            meta_total: total.metadata,
            meta_dropped: dropped.metadata,
            overflow: self.shared.overflow.load(Relaxed),
            connections: 0,
        }
    }

    pub fn video_seen(&self) -> u64 {
        self.shared.video_seen.load(Relaxed)
    }

    /// Stop the thread and let go of the source. Safe to call twice.
    pub fn close(&self) {
        self.shared.stop.store(true, Relaxed);
        self.shared.wake.notify_all();
        if let Some(t) = self.thread.lock().unwrap_or_else(|p| p.into_inner()).take() {
            let _ = t.join();
            // SAFETY: the capture thread has ended, so nothing else uses the handle; destroyed once.
            unsafe { (self.api.recv_destroy)(self.h as Handle) };
        }
    }
}

impl Drop for Receiver {
    fn drop(&mut self) {
        self.close();
    }
}

fn push(shared: &Shared, ev: Event) {
    let mut q = shared.queue.lock().unwrap_or_else(|p| p.into_inner());
    let (is_video, cap) = match ev {
        Event::Video(..) => (true, VIDEO_QUEUE),
        Event::Audio(_) => (false, AUDIO_QUEUE),
        Event::Meta(_) => (false, usize::MAX),
    };
    let kind = |e: &Event| match e {
        Event::Video(..) => 0,
        Event::Audio(_) => 1,
        Event::Meta(_) => 2,
    };
    let k = kind(&ev);
    if cap != usize::MAX && q.iter().filter(|e| kind(e) == k).count() >= cap {
        if let Some(i) = q.iter().position(|e| kind(e) == k) {
            q.remove(i);
            if is_video {
                shared.overflow.fetch_add(1, Relaxed);
            }
        }
    }
    q.push_back(ev);
    shared.wake.notify_one();
}

fn capture_loop(api: Arc<Api>, h: Handle, shared: Arc<Shared>) {
    while !shared.stop.load(Relaxed) {
        // SAFETY: all-zero is a valid empty frame for these plain C structs.
        let mut v: VideoFrame = unsafe { std::mem::zeroed() };
        let mut a: AudioFrame = unsafe { std::mem::zeroed() };
        let mut m: MetadataFrame = unsafe { std::mem::zeroed() };
        // SAFETY: the out-structs are valid for the call; `h` stays live until the thread is joined.
        let t = unsafe { (api.recv_capture)(h, &mut v, &mut a, &mut m, CAPTURE_MS) };
        match t {
            ffi::FRAME_VIDEO => {
                if let Some(video) = copy_video(&v) {
                    shared.video_seen.fetch_add(1, Relaxed);
                    push(&shared, Event::Video(video, Instant::now()));
                }
                // SAFETY: `v` was filled by this capture and is freed once.
                unsafe { (api.recv_free_video)(h, &v) };
            }
            ffi::FRAME_AUDIO => {
                if let Some(audio) = copy_audio(&a) {
                    push(&shared, Event::Audio(audio));
                }
                // SAFETY: `a` was filled by this capture and is freed once.
                unsafe { (api.recv_free_audio)(h, &a) };
            }
            ffi::FRAME_METADATA => {
                // SAFETY: the library gave a NUL-terminated string in `m`, freed once after the copy.
                let xml = unsafe { ffi::text(m.data) };
                unsafe { (api.recv_free_metadata)(h, &m) };
                if !xml.is_empty() {
                    push(&shared, Event::Meta(xml));
                }
            }
            _ => {}
        }
    }
}

fn copy_video(v: &VideoFrame) -> Option<Video> {
    let name = fourcc_name(v.fourcc)?;
    let (w, h) = (v.xres.max(0) as usize, v.yres.max(0) as usize);
    let bpp = if name == "UYVY" { 2 } else { 4 };
    let row = w * bpp;
    if w == 0 || h == 0 || v.data.is_null() || (v.stride as usize) < row {
        return None;
    }
    let stride = v.stride as usize;
    // SAFETY: NDI guarantees `stride * yres` readable bytes at `data` until the frame is freed.
    let src = unsafe { std::slice::from_raw_parts(v.data, stride * h) };
    let data = if stride == row {
        src.to_vec()
    } else {
        let mut d = Vec::with_capacity(row * h);
        for y in 0..h {
            d.extend_from_slice(&src[y * stride..y * stride + row]);
        }
        d
    };
    let fps = if v.frame_rate_d > 0 { v.frame_rate_n as f64 / v.frame_rate_d as f64 } else { 0.0 };
    Some(Video { width: w as u32, height: h as u32, fps, fourcc: name, data })
}

fn copy_audio(a: &AudioFrame) -> Option<Audio> {
    let (ch, n) = (a.channels.max(0) as usize, a.samples.max(0) as usize);
    if ch == 0 || n == 0 || a.data.is_null() || a.fourcc != ffi::FOURCC_FLTP || a.sample_rate <= 0 {
        return None;
    }
    let stride = a.channel_stride as usize;
    if stride < n * 4 || !stride.is_multiple_of(4) {
        return None;
    }
    let mut data = Vec::with_capacity(ch * n);
    let mut peak = 0f32;
    for c in 0..ch {
        // SAFETY: planar float audio: channel `c` starts `c * stride` bytes in and holds `n` aligned f32 samples.
        let row = unsafe { std::slice::from_raw_parts(a.data.add(c * stride) as *const f32, n) };
        for &s in row {
            peak = peak.max(s.abs());
        }
        data.extend_from_slice(row);
    }
    Some(Audio { rate: a.sample_rate as u32, channels: ch, samples: n, data, peak_db: db(peak) })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn peak_in_db() {
        assert_eq!(db(0.0), LEVEL_FLOOR);
        assert_eq!(db(1.0), 0.0);
        assert_eq!(db(0.5), -6.0);
    }

    #[test]
    fn full_queue_drops_the_oldest_picture() {
        let shared = Shared {
            queue: Mutex::new(VecDeque::new()),
            wake: Condvar::new(),
            stop: AtomicBool::new(false),
            overflow: AtomicU64::new(0),
            video_seen: AtomicU64::new(0),
        };
        for i in 0..VIDEO_QUEUE + 3 {
            let v = Video { width: i as u32, height: 1, fps: 30.0, fourcc: "UYVY", data: vec![] };
            push(&shared, Event::Video(v, Instant::now()));
        }
        assert_eq!(shared.overflow.load(Relaxed), 3);
        let q = shared.queue.lock().unwrap();
        assert_eq!(q.len(), VIDEO_QUEUE);
        match &q[0] {
            Event::Video(v, _) => assert_eq!(v.width, 3),
            _ => panic!(),
        }
    }
}
