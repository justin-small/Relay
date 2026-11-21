//! The two queues between the non-realtime engine and a realtime callback, and the rule they enforce:
//! the realtime thread never allocates and never frees.
//!
//!  * commands go in through an SPSC ring (`CmdTx` -> the core's `Consumer`). Every command carries objects the
//!    engine already built (boxes, rings, resamplers), so applying one on the realtime side is a pointer move.
//!  * anything the realtime side retires (a replaced tap, a removed ring, a finished clip) goes out through a
//!    second SPSC ring, the discard queue, and the engine drops it on its own thread (`DiscardRx::reap`).
//!
//! This is the pattern from Splitwave's input_bridge.rs (SPSC command queue plus a discard queue).

use crate::error::Error;
use crate::health::Glitches;
use crate::input::{Sink, Tap};
use crate::output::{Clip, LaneRx};
use rtrb::{Consumer, Producer, PushError, RingBuffer};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::{Arc, Mutex};

/// Commands waiting at once, per stream. Applying is cheap and happens at the start of every callback.
const COMMAND_SLOTS: usize = 256;
const DISCARD_SLOTS: usize = 256;

/// Things the realtime side hands back to be dropped off its thread. The fields are never read; holding them
/// until the engine drops them is the point.
#[allow(dead_code)]
pub(crate) enum Discard {
    Tap(Box<Tap>),
    Sink(Box<Sink>),
    LaneRx(Box<LaneRx>),
    Clip(Box<Clip>),
    Matrix(Box<[f32]>),
}

/// The engine's end of a command queue. A mutex because several API callers (inject, apply, ...) may send;
/// the realtime side never touches it.
pub(crate) struct CmdTx<T>(Mutex<Producer<T>>);

impl<T> CmdTx<T> {
    pub fn send(&self, cmd: T, glitches: &Glitches) -> Result<(), Error> {
        let mut tx = self.0.lock().unwrap_or_else(|e| e.into_inner());
        tx.push(cmd).map_err(|_| {
            glitches.command_busy.fetch_add(1, Relaxed);
            Error::Busy
        })
    }
}

pub(crate) fn command_queue<T>() -> (CmdTx<T>, Consumer<T>) {
    let (tx, rx) = RingBuffer::new(COMMAND_SLOTS);
    (CmdTx(Mutex::new(tx)), rx)
}

/// The realtime side's end of the discard queue.
pub(crate) struct Discards {
    tx: Producer<Discard>,
    glitches: Arc<Glitches>,
}

impl Discards {
    #[inline]
    pub fn send(&mut self, d: Discard) {
        if let Err(PushError::Full(d)) = self.tx.push(d) {
            // Dropping here would free memory on the realtime thread. A leak of one object, counted, is the
            // lesser harm; with 256 slots and a reaper that runs every apply and supervise it should never happen.
            self.glitches.discard_overflow.fetch_add(1, Relaxed);
            std::mem::forget(d);
        }
    }
}

/// The engine's end: drop whatever the realtime side retired.
pub(crate) struct DiscardRx(Consumer<Discard>);

impl DiscardRx {
    pub fn reap(&mut self) {
        while let Ok(d) = self.0.pop() {
            drop(d);
        }
    }
}

pub(crate) fn discard_queue(glitches: Arc<Glitches>) -> (Discards, DiscardRx) {
    let (tx, rx) = RingBuffer::new(DISCARD_SLOTS);
    (Discards { tx, glitches }, DiscardRx(rx))
}
