//! The two queues between the non-realtime engine and a realtime callback, and the rule they enforce:
//! the realtime thread never allocates and never frees.
//!
//!  * commands go in through an SPSC ring (`CmdTx` -> the core's `Consumer`). Every command carries objects the
//!    engine already built (boxes, rings, resamplers), so applying one on the realtime side is a pointer move.
//!  * anything the realtime side retires goes out through the discard queue (`discard.rs`).
//!
//! This is the pattern from Splitwave's input_bridge.rs (SPSC command queue plus a discard queue).

use crate::error::Error;
use crate::health::Glitches;
use rtrb::{Consumer, Producer, RingBuffer};
use std::sync::atomic::Ordering::Relaxed;
use std::sync::Mutex;

/// Commands waiting at once, per stream. Applying is cheap and happens at the start of every callback.
const COMMAND_SLOTS: usize = 256;

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
