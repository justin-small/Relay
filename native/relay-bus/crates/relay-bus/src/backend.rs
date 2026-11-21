//! How the engine opens audio streams. Phase 1 has one implementation, the deterministic fake in `fake.rs`;
//! phase 2 adds a cpal one. Callbacks are what the realtime threads run, so everything inside them follows the
//! realtime rule (no allocation, no locks, no I/O).

/// What a device offers (inputs) or was asked for and gave (outputs).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct DeviceFormat {
    pub channels: usize,
    pub rate: u32,
}

/// Gets interleaved f32 blocks from an input device. Runs on the device's realtime thread.
pub type InputCallback = Box<dyn FnMut(&[f32]) + Send + 'static>;
/// Fills an interleaved f32 block for an output device. Runs on the device's realtime thread.
pub type OutputCallback = Box<dyn FnMut(&mut [f32]) + Send + 'static>;

/// An open stream. Dropping it closes the stream and its callback.
pub trait Stream: Send {
    /// The error the stream reported (device unplugged, driver gone), if it has failed. A failed stream will not
    /// call its callback again; the engine's `supervise()` closes it and tries to reopen it with backoff.
    fn error(&self) -> Option<String>;
}

pub trait Backend: Send + Sync {
    /// Seconds on the clock the engine uses for backoff and "last input age". The fake backend's is virtual.
    fn now(&self) -> f64;

    /// The format of input `device` (`None` = the default). Errors are shown to the operator as they are.
    fn input_format(&self, device: Option<&str>) -> Result<DeviceFormat, String>;
    fn open_input(&self, device: Option<&str>, format: DeviceFormat, callback: InputCallback) -> Result<Box<dyn Stream>, String>;

    /// The format an output will run at when `channels` channels are wanted from `device`.
    fn output_format(&self, device: Option<&str>, channels: usize) -> Result<DeviceFormat, String>;
    fn open_output(&self, device: Option<&str>, format: DeviceFormat, callback: OutputCallback) -> Result<Box<dyn Stream>, String>;
}
