//! An NDI source Relay publishes: BGRA pictures, with optional 48 kHz float audio.

use crate::ffi::{self, Api, AudioFrame, Handle, SendCreate, VideoFrame};
use crate::find::cstr;
use std::ptr;
use std::sync::{Arc, Mutex};

/// A frame rate as NDI wants it, a fraction: the broadcast rates keep their 1001 denominator.
pub fn frame_rate(fps: f64) -> (i32, i32) {
    let fps = if fps.is_finite() && fps > 0.0 { fps } else { 30.0 };
    for (n, d) in [(24000, 1001), (30000, 1001), (60000, 1001), (120000, 1001)] {
        if (fps - n as f64 / d as f64).abs() < 0.005 {
            return (n, d);
        }
    }
    ((fps * 1000.0).round() as i32, 1000)
}

pub struct Sender {
    api: Arc<Api>,
    h: Mutex<Option<usize>>,
    width: i32,
    height: i32,
    rate: (i32, i32),
    fourcc: u32,
    bytes_per_pixel: usize,
    /// Some(ms): label frames from a counter instead of letting the SDK make timecodes up, with the audio labelled this
    /// many milliseconds later than the picture it goes with. For proving the receiver's A/V sync reading (issue 561).
    audio_offset_ms: Option<f64>,
    /// Pictures and audio samples sent so far, for those timecodes.
    sent: Mutex<(i64, i64)>,
}

impl Sender {
    /// `fourcc` is "BGRA" (the default; pictures with alpha) or "UYVY" (what cameras send; no alpha).
    pub fn open(name: &str, width: u32, height: u32, fps: f64, fourcc: &str) -> Result<Sender, String> {
        Self::open_timed(name, width, height, fps, fourcc, None)
    }

    pub fn open_timed(name: &str, width: u32, height: u32, fps: f64, fourcc: &str, audio_offset_ms: Option<f64>) -> Result<Sender, String> {
        let (fourcc, bytes_per_pixel) = match fourcc {
            "BGRA" => (ffi::FOURCC_BGRA, 4),
            "UYVY" => (ffi::FOURCC_UYVY, 2),
            other => return Err(format!("cannot send {other} pictures; use BGRA or UYVY")),
        };
        let api = ffi::api()?;
        let n = cstr(name);
        let create = SendCreate { name: n.as_ptr(), groups: ptr::null(), clock_video: true, clock_audio: true };
        // SAFETY: `create` and `n` outlive the call.
        let h = unsafe { (api.send_create)(&create) };
        if h.is_null() {
            return Err("NDI would not create the output.".into());
        }
        Ok(Sender { api, h: Mutex::new(Some(h as usize)), width: width as i32, height: height as i32, rate: frame_rate(fps), fourcc, bytes_per_pixel, audio_offset_ms, sent: Mutex::new((0, 0)) })
    }

    /// Send one picture of `width * height * bytes_per_pixel` bytes and, if given, planar float audio (`channels` rows of
    /// `samples`) with it. The video call returns when the frame has been copied, paced to the frame rate.
    pub fn send(&self, bgra: &[u8], audio: Option<(&[f32], usize, usize)>) -> Result<(), String> {
        let need = self.width as usize * self.height as usize * self.bytes_per_pixel;
        if bgra.len() < need {
            return Err(format!("a {}x{} picture is {need} bytes; got {}", self.width, self.height, bgra.len()));
        }
        let Some(h) = *self.h.lock().unwrap_or_else(|p| p.into_inner()) else { return Err("closed".into()) };
        let h = h as Handle;
        let (n_video, n_samples) = {
            let mut c = self.sent.lock().unwrap_or_else(|p| p.into_inner());
            let before = *c;
            c.0 += 1;
            if let Some((_, _, samples)) = audio {
                c.1 += samples as i64;
            }
            before
        };
        let tick = |ok: bool, ticks: f64| if ok { ticks as i64 } else { ffi::TIMECODE_SYNTHESIZE };
        let video_tc = tick(self.audio_offset_ms.is_some(), n_video as f64 * 1e7 * self.rate.1 as f64 / self.rate.0 as f64);
        let audio_tc = tick(self.audio_offset_ms.is_some(), n_samples as f64 * 1e7 / 48000.0 + self.audio_offset_ms.unwrap_or(0.0) * 1e4);
        if let Some((planar, channels, samples)) = audio {
            if planar.len() >= channels * samples && channels > 0 && samples > 0 {
                let a = AudioFrame {
                    sample_rate: 48000,
                    channels: channels as i32,
                    samples: samples as i32,
                    timecode: audio_tc,
                    fourcc: ffi::FOURCC_FLTP,
                    data: planar.as_ptr() as *mut u8,
                    channel_stride: (samples * 4) as i32,
                    metadata: ptr::null(),
                    timestamp: 0,
                };
                // SAFETY: `a` points at `planar`, which the library only reads and which outlives the call.
                unsafe { (self.api.send_audio)(h, &a) };
            }
        }
        let v = VideoFrame {
            xres: self.width,
            yres: self.height,
            fourcc: self.fourcc,
            frame_rate_n: self.rate.0,
            frame_rate_d: self.rate.1,
            aspect: self.width as f32 / self.height as f32,
            format: ffi::FORMAT_PROGRESSIVE,
            timecode: video_tc,
            data: bgra.as_ptr() as *mut u8,
            stride: self.width * self.bytes_per_pixel as i32,
            metadata: ptr::null(),
            timestamp: 0,
        };
        // SAFETY: the synchronous send reads `bgra` (at least `need` bytes) and finishes with it before returning.
        unsafe { (self.api.send_video)(h, &v) };
        Ok(())
    }

    pub fn close(&self) {
        if let Some(h) = self.h.lock().unwrap_or_else(|p| p.into_inner()).take() {
            // SAFETY: taken out of `h`, so destroyed once.
            unsafe { (self.api.send_destroy)(h as Handle) }
        }
    }
}

impl Drop for Sender {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod tests {
    use super::frame_rate;

    #[test]
    fn rates() {
        assert_eq!(frame_rate(30.0), (30000, 1000));
        assert_eq!(frame_rate(29.97), (30000, 1001));
        assert_eq!(frame_rate(59.94), (60000, 1001));
        assert_eq!(frame_rate(0.0), (30000, 1000));
    }
}
