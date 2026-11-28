//! The discard queue: the second SPSC ring between the realtime side and the engine. Anything the realtime side retires (a
//! replaced tap, a removed ring, a finished clip) goes out through it, and the engine drops it on its own thread
//! (`DiscardRx::reap`), so the realtime thread never frees. It holds objects of any type, so it uses no other module.

use crate::health::Glitches;
use rtrb::{Consumer, Producer, PushError, RingBuffer};
use std::any::Any;
use std::sync::atomic::Ordering::Relaxed;
use std::sync::Arc;

const DISCARD_SLOTS: usize = 256;

/// Things the realtime side hands back to be dropped off its thread. The fields are never read; holding them
/// until the engine drops them is the point. `Item` takes any boxed object without allocating (the box already exists);
/// a boxed slice cannot be turned into one of those without a second allocation, so it has its own variant.
#[allow(dead_code)]
pub(crate) enum Discard {
    Item(Box<dyn Any + Send>),
    Matrix(Box<[f32]>),
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
