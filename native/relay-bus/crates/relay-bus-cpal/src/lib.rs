//! The cpal `Backend` for the Relay audio bus (issue 599 spike, phase 2), and a watcher that tells the engine when
//! devices come and go.
//!
//! Devices are picked by name (`None` is the system default). Streams prefer f32 and fall back to i16 and i32 with
//! conversion in preallocated scratch, so the callbacks never allocate. A stream error that means the stream is
//! dead (device unplugged, driver gone) is kept for `Stream::error()`, which is how `Engine::supervise()` finds out
//! and reopens it; cpal's advisory errors (an xrun, a reroute to the new default, a refused priority boost) are
//! counted and ignored.

mod watcher;

pub use watcher::{ChangeSignal, DeviceWatcher, WatchMethod};

use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use cpal::{ErrorKind, FromSample, SampleFormat, SizedSample, StreamConfig, SupportedStreamConfig};
use relay_bus::{Backend, DeviceFormat, InputCallback, OutputCallback, Stream};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::{Arc, Mutex};
use std::time::Instant;

/// Samples converted per pass inside a callback (a multiple of every channel count is taken from this).
const SCRATCH_SAMPLES: usize = 16 * 1024;

pub struct CpalBackend {
    start: Instant,
    /// Advisory cpal errors seen on any stream (xruns mostly).
    advisories: Arc<AtomicU64>,
}

impl Default for CpalBackend {
    fn default() -> Self {
        Self::new()
    }
}

impl CpalBackend {
    pub fn new() -> Self {
        Self { start: Instant::now(), advisories: Arc::new(AtomicU64::new(0)) }
    }

    /// How many advisory errors (xruns, reroutes, refused priority) cpal has reported so far.
    pub fn advisories(&self) -> u64 {
        self.advisories.load(Relaxed)
    }
}

// -- devices ----------------------------------------------------------------------------------------------------

fn device_name(d: &cpal::Device) -> String {
    d.description().map(|x| x.name().to_string()).unwrap_or_else(|_| "unknown".into())
}

fn fold(s: &str) -> String {
    s.trim().to_lowercase()
}

fn find_device(device: Option<&str>, input: bool) -> Result<cpal::Device, String> {
    let host = cpal::default_host();
    let dir = if input { "input" } else { "output" };
    let Some(wanted) = device else {
        let d = if input { host.default_input_device() } else { host.default_output_device() };
        return d.ok_or_else(|| format!("no default {dir} device"));
    };
    let devices: Vec<cpal::Device> = host.devices().map_err(|e| e.to_string())?.collect();
    let usable = |d: &cpal::Device| if input { d.supports_input() } else { d.supports_output() };
    // Exact name or id first, then the same folded to lower case (a saved setting may differ in case).
    let hit = devices
        .iter()
        .filter(|d| usable(d))
        .find(|d| device_name(d) == wanted || d.id().map(|i| i.to_string() == wanted).unwrap_or(false))
        .or_else(|| devices.iter().filter(|d| usable(d)).find(|d| fold(&device_name(d)) == fold(wanted)));
    match hit {
        Some(d) => Ok(d.clone()),
        None => Err(format!("no {dir} device named {wanted:?}")),
    }
}

fn format_ok(f: SampleFormat) -> bool {
    matches!(f, SampleFormat::F32 | SampleFormat::I16 | SampleFormat::I32)
}

fn format_rank(f: SampleFormat) -> u8 {
    match f {
        SampleFormat::F32 => 0,
        SampleFormat::I16 => 1,
        _ => 2,
    }
}

/// A supported configuration with at least `channels` channels at `rate` (or at the device's default rate when
/// `rate` is `None`, and failing that the highest rate it supports), preferring an exact channel count and f32.
fn pick_config(dev: &cpal::Device, input: bool, channels: usize, rate: Option<u32>) -> Result<SupportedStreamConfig, String> {
    let default = if input { dev.default_input_config() } else { dev.default_output_config() }.ok();
    let want_rate = rate.or_else(|| default.as_ref().map(|c| c.sample_rate()));
    let ranges: Vec<_> = if input { dev.supported_input_configs().map(|i| i.collect()) } else { dev.supported_output_configs().map(|i| i.collect()) }
        .map_err(|e| e.to_string())?;
    let mut best: Option<(u8, usize, SupportedStreamConfig)> = None;
    for r in ranges {
        if (r.channels() as usize) < channels || !format_ok(r.sample_format()) {
            continue;
        }
        let cfg = match want_rate.and_then(|w| r.try_with_sample_rate(w)) {
            Some(c) => c,
            None if rate.is_none() => r.with_max_sample_rate(),
            None => continue,
        };
        let key = (format_rank(cfg.sample_format()), cfg.channels() as usize - channels);
        if best.as_ref().is_none_or(|(f, extra, _)| key < (*f, *extra)) {
            best = Some((key.0, key.1, cfg));
        }
    }
    match best {
        Some((_, _, c)) => Ok(c),
        None => Err(match rate {
            Some(r) => format!("{} has no {channels} channel configuration at {r} Hz", device_name(dev)),
            None => format!("{} has no usable {channels} channel configuration", device_name(dev)),
        }),
    }
}

// -- streams ------------------------------------------------------------------------------------------------------

struct CpalStream {
    _stream: cpal::Stream,
    error: Arc<Mutex<Option<String>>>,
}

impl Stream for CpalStream {
    fn error(&self) -> Option<String> {
        self.error.lock().unwrap_or_else(|e| e.into_inner()).clone()
    }
}

fn error_handler(slot: Arc<Mutex<Option<String>>>, advisories: Arc<AtomicU64>) -> impl FnMut(cpal::Error) + Send + 'static {
    move |e: cpal::Error| match e.kind() {
        ErrorKind::Xrun | ErrorKind::DeviceChanged | ErrorKind::RealtimeDenied => {
            advisories.fetch_add(1, Relaxed);
        }
        _ => {
            let mut s = slot.lock().unwrap_or_else(|p| p.into_inner());
            if s.is_none() {
                *s = Some(e.to_string());
            }
        }
    }
}

fn build_input<T>(dev: &cpal::Device, cfg: StreamConfig, mut cb: InputCallback, err: impl FnMut(cpal::Error) + Send + 'static) -> Result<cpal::Stream, String>
where
    T: SizedSample + Send + 'static,
    f32: FromSample<T>,
{
    let channels = cfg.channels as usize;
    let chunk = (SCRATCH_SAMPLES / channels).max(1) * channels;
    let mut scratch = vec![0.0f32; chunk];
    dev.build_input_stream(
        cfg,
        move |data: &[T], _| {
            for part in data.chunks(chunk) {
                for (o, s) in scratch.iter_mut().zip(part) {
                    *o = f32::from_sample_(*s);
                }
                cb(&scratch[..part.len()]);
            }
        },
        err,
        None,
    )
    .map_err(|e| e.to_string())
}

fn build_input_f32(dev: &cpal::Device, cfg: StreamConfig, mut cb: InputCallback, err: impl FnMut(cpal::Error) + Send + 'static) -> Result<cpal::Stream, String> {
    dev.build_input_stream(cfg, move |data: &[f32], _| cb(data), err, None).map_err(|e| e.to_string())
}

/// `want` channels are rendered by the engine; the device has `cfg.channels` (the same or more, the extra ones
/// are left silent). Converts to the device's sample type through preallocated scratch.
fn build_output<T>(dev: &cpal::Device, cfg: StreamConfig, want: usize, mut cb: OutputCallback, err: impl FnMut(cpal::Error) + Send + 'static) -> Result<cpal::Stream, String>
where
    T: SizedSample + FromSample<f32> + Send + 'static,
{
    let dev_ch = cfg.channels as usize;
    let frames_per_pass = (SCRATCH_SAMPLES / dev_ch.max(want)).max(1);
    let mut scratch = vec![0.0f32; frames_per_pass * want];
    dev.build_output_stream(
        cfg,
        move |data: &mut [T], _| {
            for part in data.chunks_mut(frames_per_pass * dev_ch) {
                let frames = part.len() / dev_ch;
                let block = &mut scratch[..frames * want];
                cb(block);
                for (f, frame) in part.chunks_mut(dev_ch).enumerate() {
                    for (c, o) in frame.iter_mut().enumerate() {
                        *o = if c < want { T::from_sample_(block[f * want + c]) } else { T::EQUILIBRIUM };
                    }
                }
            }
        },
        err,
        None,
    )
    .map_err(|e| e.to_string())
}

fn build_output_f32(dev: &cpal::Device, cfg: StreamConfig, mut cb: OutputCallback, err: impl FnMut(cpal::Error) + Send + 'static) -> Result<cpal::Stream, String> {
    dev.build_output_stream(cfg, move |data: &mut [f32], _| cb(data), err, None).map_err(|e| e.to_string())
}

impl Backend for CpalBackend {
    fn now(&self) -> f64 {
        self.start.elapsed().as_secs_f64()
    }

    fn input_format(&self, device: Option<&str>) -> Result<DeviceFormat, String> {
        let dev = find_device(device, true)?;
        let c = dev.default_input_config().map_err(|e| format!("{}: {e}", device_name(&dev)))?;
        Ok(DeviceFormat { channels: c.channels() as usize, rate: c.sample_rate() })
    }

    fn open_input(&self, device: Option<&str>, format: DeviceFormat, callback: InputCallback) -> Result<Box<dyn Stream>, String> {
        let dev = find_device(device, true)?;
        let sup = pick_config(&dev, true, format.channels, Some(format.rate))?;
        let cfg = StreamConfig { channels: format.channels as u16, sample_rate: format.rate, buffer_size: cpal::BufferSize::Default };
        let error = Arc::new(Mutex::new(None));
        let err = error_handler(error.clone(), self.advisories.clone());
        let stream = match sup.sample_format() {
            SampleFormat::F32 => build_input_f32(&dev, cfg, callback, err),
            SampleFormat::I16 => build_input::<i16>(&dev, cfg, callback, err),
            SampleFormat::I32 => build_input::<i32>(&dev, cfg, callback, err),
            other => Err(format!("sample format {other:?} is not supported")),
        }
        .map_err(|e| format!("{}: {e}", device_name(&dev)))?;
        stream.play().map_err(|e| format!("{}: {e}", device_name(&dev)))?;
        Ok(Box::new(CpalStream { _stream: stream, error }))
    }

    fn output_format(&self, device: Option<&str>, channels: usize) -> Result<DeviceFormat, String> {
        let dev = find_device(device, false)?;
        let c = pick_config(&dev, false, channels, None)?;
        Ok(DeviceFormat { channels, rate: c.sample_rate() })
    }

    fn open_output(&self, device: Option<&str>, format: DeviceFormat, callback: OutputCallback) -> Result<Box<dyn Stream>, String> {
        let dev = find_device(device, false)?;
        let sup = pick_config(&dev, false, format.channels, Some(format.rate))?;
        let cfg = StreamConfig { channels: sup.channels(), sample_rate: format.rate, buffer_size: cpal::BufferSize::Default };
        let exact = sup.channels() as usize == format.channels;
        let error = Arc::new(Mutex::new(None));
        let err = error_handler(error.clone(), self.advisories.clone());
        let want = format.channels;
        let stream = match sup.sample_format() {
            SampleFormat::F32 if exact => build_output_f32(&dev, cfg, callback, err),
            SampleFormat::F32 => build_output::<f32>(&dev, cfg, want, callback, err),
            SampleFormat::I16 => build_output::<i16>(&dev, cfg, want, callback, err),
            SampleFormat::I32 => build_output::<i32>(&dev, cfg, want, callback, err),
            other => Err(format!("sample format {other:?} is not supported")),
        }
        .map_err(|e| format!("{}: {e}", device_name(&dev)))?;
        stream.play().map_err(|e| format!("{}: {e}", device_name(&dev)))?;
        Ok(Box::new(CpalStream { _stream: stream, error }))
    }
}

/// Every device name, sorted, and the names of the default input and output: what the polling watcher compares.
pub(crate) fn list_names() -> (Vec<String>, Option<String>, Option<String>) {
    let host = cpal::default_host();
    let mut names: Vec<String> = host.devices().map(|d| d.map(|d| device_name(&d)).collect()).unwrap_or_default();
    names.sort();
    (names, host.default_input_device().map(|d| device_name(&d)), host.default_output_device().map(|d| device_name(&d)))
}

// -- listing --------------------------------------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq)]
pub struct DeviceInfo {
    pub name: String,
    /// Most input channels any configuration offers (0: not an input).
    pub inputs: usize,
    pub outputs: usize,
    pub default_rate: u32,
    pub is_default_input: bool,
    pub is_default_output: bool,
}

pub fn list_devices() -> Vec<DeviceInfo> {
    let host = cpal::default_host();
    let default_in = host.default_input_device().map(|d| device_name(&d));
    let default_out = host.default_output_device().map(|d| device_name(&d));
    let Ok(devices) = host.devices() else { return Vec::new() };
    devices
        .map(|d| {
            let name = device_name(&d);
            let max = |it: Option<Vec<cpal::SupportedStreamConfigRange>>| it.map_or(0, |v| v.iter().map(|r| r.channels() as usize).max().unwrap_or(0));
            let inputs = if d.supports_input() { max(d.supported_input_configs().ok().map(|i| i.collect())) } else { 0 };
            let outputs = if d.supports_output() { max(d.supported_output_configs().ok().map(|i| i.collect())) } else { 0 };
            let default_rate = d
                .default_output_config()
                .ok()
                .filter(|_| outputs > 0)
                .or_else(|| d.default_input_config().ok())
                .map_or(0, |c| c.sample_rate());
            DeviceInfo {
                is_default_input: default_in.as_deref() == Some(name.as_str()) && inputs > 0,
                is_default_output: default_out.as_deref() == Some(name.as_str()) && outputs > 0,
                name,
                inputs,
                outputs,
                default_rate,
            }
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Listing never panics, even on a machine with no audio devices (a CI runner).
    #[test]
    fn listing_does_not_panic() {
        let _ = list_devices();
    }

    #[test]
    fn a_missing_device_is_an_error_not_a_panic() {
        let b = CpalBackend::new();
        let e = b.input_format(Some("no such device, really")).unwrap_err();
        assert!(e.contains("no such device"), "{e}");
        let e = b.output_format(Some("no such device, really"), 2).unwrap_err();
        assert!(e.contains("no such device"), "{e}");
    }
}
