//! Thin wrappers over rubato's asynchronous sinc resampler. Everything is built up front so that `feed` and the
//! realtime callers allocate nothing.

use audioadapter_buffers::direct::InterleavedSlice;
use rubato::{
    calculate_cutoff, Async, FixedAsync, Resampler, SincInterpolationParameters, SincInterpolationType,
    WindowFunction,
};

/// An async sinc resampler. 64 taps with linear interpolation between 256 oversampled filters is the "fast"
/// preset from rubato's own examples: for ratios within a fraction of a percent of 1 (our drift correction) the
/// error is far below anything audible, and it costs a few MFLOPs per lane.
pub(crate) fn new_async(ratio: f64, max_relative: f64, chunk: usize, channels: usize, fixed: FixedAsync) -> Async<f32> {
    let window = WindowFunction::Blackman2;
    let sinc_len = 64;
    let params = SincInterpolationParameters {
        sinc_len,
        f_cutoff: calculate_cutoff(sinc_len, window),
        oversampling_factor: 256,
        interpolation: SincInterpolationType::Linear,
        window,
    };
    Async::<f32>::new_sinc(ratio, max_relative, &params, chunk, channels, fixed)
        .expect("resampler parameters are fixed and valid")
}

/// Fixed-size input chunks in, a varying number of output frames out; the ratio never changes. Used to bring a
/// device's rate to 48 kHz on the way in, and 48 kHz to a device's rate on the way out. Input of any length is
/// accepted: it is staged until a whole chunk is there.
pub(crate) struct FixedIn {
    rs: Async<f32>,
    channels: usize,
    chunk: usize,
    stage: Vec<f32>,
    filled: usize,
    out: Vec<f32>,
    out_frames_max: usize,
}

impl FixedIn {
    pub fn new(in_rate: u32, out_rate: u32, channels: usize, chunk: usize) -> Self {
        let rs = new_async(out_rate as f64 / in_rate as f64, 1.0, chunk, channels, FixedAsync::Input);
        let out_frames_max = rs.output_frames_max();
        Self { rs, channels, chunk, stage: vec![0.0; chunk * channels], filled: 0, out: vec![0.0; out_frames_max * channels], out_frames_max }
    }

    /// Output frames per call, at most.
    pub fn out_frames_max(&self) -> usize {
        self.out_frames_max
    }

    /// Take interleaved `data` (a whole number of frames); call `emit` with each interleaved block produced.
    pub fn feed(&mut self, mut data: &[f32], mut emit: impl FnMut(&[f32])) {
        let ch = self.channels;
        let full = self.chunk * ch;
        while !data.is_empty() {
            let take = (full - self.filled).min(data.len());
            self.stage[self.filled..self.filled + take].copy_from_slice(&data[..take]);
            self.filled += take;
            data = &data[take..];
            if self.filled < full {
                break;
            }
            self.filled = 0;
            let produced = {
                let input = InterleavedSlice::new(&self.stage[..], ch, self.chunk).expect("stage is chunk x channels");
                let mut output = InterleavedSlice::new_mut(&mut self.out[..], ch, self.out_frames_max)
                    .expect("out is out_frames_max x channels");
                match self.rs.process_into_buffer(&input, &mut output, None) {
                    Ok((_, n)) => n,
                    Err(_) => 0,
                }
            };
            if produced > 0 {
                emit(&self.out[..produced * ch]);
            }
        }
    }
}

/// Resample a whole mono clip. Not for the realtime thread: it allocates.
pub(crate) fn resample_offline(x: &[f32], from: u32, to: u32) -> Vec<f32> {
    if from == to || x.is_empty() {
        return x.to_vec();
    }
    let mut rs = new_async(to as f64 / from as f64, 1.0, 1024, 1, FixedAsync::Input);
    let need = rs.process_all_needed_output_len(x.len());
    let mut out = vec![0.0f32; need];
    let input = InterleavedSlice::new(x, 1, x.len()).expect("mono clip");
    let mut output = InterleavedSlice::new_mut(&mut out[..], 1, need).expect("mono out");
    match rs.process_all_into_buffer(&input, &mut output, x.len(), None) {
        Ok((_, n)) => out.truncate(n.min(need)),
        Err(_) => out.clear(),
    }
    out
}
