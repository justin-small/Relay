//! The input side of the bus: what runs inside an input stream's callback (or, for an external input fed by
//! `Engine::push`, on the caller's thread).
//!
//! One `InputCore` per open input, however many lanes use it. It takes the device's interleaved block, brings it
//! to 48 kHz when the device runs at another rate (once, for all channels), then for every lane tapped on this
//! input picks a channel (or averages them all), meters it, and writes it into every consumer's own ring
//! (the fan-out). A consumer that is too slow loses frames and the loss is counted; it never blocks this thread.

use crate::config::ChannelSel;
use crate::health::Glitches;
use crate::meter::{Meter, MeterShared};
use crate::resamp::FixedIn;
use crate::discard::{Discard, Discards};
use crate::util::LANE_RATE;
use rtrb::{Consumer, Producer};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::Arc;

/// Consumers per lane. The Vec is allocated with this capacity so that adding a consumer never reallocates.
pub(crate) const MAX_SINKS: usize = 16;
const MAX_TAPS: usize = crate::util::MAX_LANES;
/// Frames handled per pass over the taps; bounds the scratch buffer whatever block size the device uses.
const SUB: usize = 1024;
/// Frames fed to the rate converter at a time.
const RATE_CHUNK: usize = 256;

/// One consumer's ring, producer end.
pub(crate) struct Sink {
    pub id: u64,
    pub tx: Producer<f32>,
    /// Frames lost to this consumer, counted per input and per consumer (an output or a tap).
    pub input_dropped: Arc<AtomicU64>,
    pub own_dropped: Arc<AtomicU64>,
    pub glitches: Arc<Glitches>,
}

/// One lane fed from an input.
pub(crate) struct Tap {
    pub lane: usize, // 0-based
    pub channel: ChannelSel,
    /// Boxed so that moving a sink in from a command, or out to the discard queue, moves a pointer; unboxing
    /// one on the realtime thread would free the box there.
    #[allow(clippy::vec_box)]
    pub sinks: Vec<Box<Sink>>,
    pub meter: Meter,
}

impl Tap {
    pub fn new(lane: usize, channel: ChannelSel, meter: Arc<MeterShared>) -> Self {
        Self { lane, channel, sinks: Vec::with_capacity(MAX_SINKS), meter: Meter::new(meter) }
    }
}

pub(crate) enum InCmd {
    AddTap(Box<Tap>),
    RemoveTap(usize),
    SetChannel(usize, ChannelSel),
    AddSink(usize, Box<Sink>),
    RemoveSink(usize, u64),
}

/// Counters the realtime side keeps for one input.
#[derive(Debug, Default)]
pub struct InputShared {
    pub frames_in: AtomicU64,
    pub dropped: Arc<AtomicU64>,
}

pub(crate) struct InputCore {
    channels: usize,
    rate: u32,
    rs: Option<FixedIn>,
    /// Boxed for the same reason as `Tap::sinks`.
    #[allow(clippy::vec_box)]
    taps: Vec<Box<Tap>>,
    mono: Vec<f32>,
    cmd: Consumer<InCmd>,
    discards: Discards,
    shared: Arc<InputShared>,
}

impl InputCore {
    pub fn new(channels: usize, rate: u32, cmd: Consumer<InCmd>, discards: Discards, shared: Arc<InputShared>) -> Self {
        let mut core = Self {
            channels: channels.max(1),
            rate,
            rs: None,
            taps: Vec::with_capacity(MAX_TAPS),
            mono: vec![0.0; SUB],
            cmd,
            discards,
            shared,
        };
        core.rs = core.make_converter();
        core
    }

    fn make_converter(&self) -> Option<FixedIn> {
        (self.rate != LANE_RATE).then(|| FixedIn::new(self.rate, LANE_RATE, self.channels, RATE_CHUNK))
    }

    /// Non-realtime: an external input may change its rate or channel count between pushes.
    pub fn ensure_format(&mut self, channels: usize, rate: u32) {
        let channels = channels.max(1);
        if channels != self.channels || rate != self.rate {
            self.channels = channels;
            self.rate = rate;
            self.rs = self.make_converter();
        }
    }

    /// Process one interleaved block from the device. Realtime-safe.
    pub fn process(&mut self, data: &[f32]) {
        self.drain();
        let ch = self.channels;
        let data = &data[..data.len() - data.len() % ch];
        self.shared.frames_in.fetch_add((data.len() / ch) as u64, Relaxed);
        let Self { rs, taps, mono, .. } = self;
        match rs {
            None => distribute(taps, mono, ch, data),
            Some(rs) => rs.feed(data, |out| distribute(taps, mono, ch, out)),
        }
    }

    fn drain(&mut self) {
        while let Ok(cmd) = self.cmd.pop() {
            match cmd {
                InCmd::AddTap(t) => {
                    if self.taps.len() < MAX_TAPS && !self.taps.iter().any(|x| x.lane == t.lane) {
                        self.taps.push(t);
                    } else {
                        self.discards.send(Discard::Item(t));
                    }
                }
                InCmd::RemoveTap(lane) => {
                    if let Some(i) = self.taps.iter().position(|t| t.lane == lane) {
                        let t = self.taps.swap_remove(i);
                        self.discards.send(Discard::Item(t));
                    }
                }
                InCmd::SetChannel(lane, sel) => {
                    if let Some(t) = self.taps.iter_mut().find(|t| t.lane == lane) {
                        t.channel = sel;
                    }
                }
                InCmd::AddSink(lane, sink) => match self.taps.iter_mut().find(|t| t.lane == lane) {
                    Some(t) if t.sinks.len() < MAX_SINKS => t.sinks.push(sink),
                    _ => {
                        sink.glitches.rejected.fetch_add(1, Relaxed);
                        self.discards.send(Discard::Item(sink));
                    }
                },
                InCmd::RemoveSink(lane, id) => {
                    if let Some(t) = self.taps.iter_mut().find(|t| t.lane == lane) {
                        if let Some(i) = t.sinks.iter().position(|s| s.id == id) {
                            let s = t.sinks.swap_remove(i);
                            self.discards.send(Discard::Item(s));
                        }
                    }
                }
            }
        }
    }
}

/// Take a block at 48 kHz, give every tap its channel, meter it, and write it into each consumer's ring.
fn distribute(taps: &mut [Box<Tap>], mono: &mut [f32], ch: usize, buf: &[f32]) {
    let frames = buf.len() / ch;
    let mut start = 0;
    while start < frames {
        let n = (frames - start).min(SUB);
        let block = &buf[start * ch..(start + n) * ch];
        for tap in taps.iter_mut() {
            let out = &mut mono[..n];
            match tap.channel {
                ChannelSel::Index(c) => {
                    let c = c.min(ch - 1);
                    for (o, frame) in out.iter_mut().zip(block.chunks_exact(ch)) {
                        *o = frame[c];
                    }
                }
                ChannelSel::Mix if ch == 1 => out.copy_from_slice(block),
                ChannelSel::Mix => {
                    let inv = 1.0 / ch as f32;
                    for (o, frame) in out.iter_mut().zip(block.chunks_exact(ch)) {
                        *o = frame.iter().sum::<f32>() * inv;
                    }
                }
            }
            tap.meter.feed(out, 0, 1, n, LANE_RATE as f32);
            for sink in tap.sinks.iter_mut() {
                push_sink(sink, out);
            }
        }
        start += n;
    }
}

/// Write as much of `block` as fits; count the rest as dropped. Never waits.
#[inline]
fn push_sink(sink: &mut Sink, block: &[f32]) {
    let fit = sink.tx.slots().min(block.len());
    if fit > 0 {
        if let Ok(chunk) = sink.tx.write_chunk_uninit(fit) {
            chunk.fill_from_iter(block[..fit].iter().copied());
        }
    }
    let lost = (block.len() - fit) as u64;
    if lost > 0 {
        sink.input_dropped.fetch_add(lost, Relaxed);
        sink.own_dropped.fetch_add(lost, Relaxed);
        sink.glitches.dropped_frames.fetch_add(lost, Relaxed);
    }
}
