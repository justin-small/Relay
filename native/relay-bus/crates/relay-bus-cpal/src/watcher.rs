//! Tells the engine when the device list or the default devices change, so `supervise()` retries a waiting stream
//! at once instead of after its backoff.
//!
//! * macOS: CoreAudio HAL property listeners on the device list and on the default input and output (no polling).
//! * Windows: polls the device list every 2 s. (The proper route is an IMMNotificationClient COM callback; it could
//!   not be built and tried from this Mac, so it is not here.)
//! * Linux and everything else: polls the device list every 2 s.
//! * macOS falls back to polling if a listener cannot be registered.
//!
//! The watcher only raises a `ChangeSignal`; whoever owns the engine waits on it.

use crate::list_names;
use std::sync::mpsc::{self, RecvTimeoutError};
use std::sync::{Arc, Condvar, Mutex};
use std::thread::JoinHandle;
use std::time::Duration;

pub const POLL_INTERVAL: Duration = Duration::from_secs(2);

/// A counter with a wake-up: `notify()` from the watcher, `wait()` from the supervising thread.
#[derive(Default)]
pub struct ChangeSignal {
    count: Mutex<u64>,
    cv: Condvar,
}

impl ChangeSignal {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn notify(&self) {
        *self.count.lock().unwrap_or_else(|e| e.into_inner()) += 1;
        self.cv.notify_all();
    }

    /// Waits up to `timeout` for a change since `seen` (updated to the current count). True if there was one.
    pub fn wait(&self, seen: &mut u64, timeout: Duration) -> bool {
        let g = self.count.lock().unwrap_or_else(|e| e.into_inner());
        let (g, _) = self.cv.wait_timeout_while(g, timeout, |c| *c == *seen).unwrap_or_else(|e| e.into_inner());
        let changed = *g != *seen;
        *seen = *g;
        changed
    }

    pub fn count(&self) -> u64 {
        *self.count.lock().unwrap_or_else(|e| e.into_inner())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WatchMethod {
    /// CoreAudio property listeners (macOS).
    CoreAudioListener,
    /// The device list compared every 2 s.
    Polling,
}

impl WatchMethod {
    pub fn as_str(self) -> &'static str {
        match self {
            WatchMethod::CoreAudioListener => "coreaudio-listener",
            WatchMethod::Polling => "polling-2s",
        }
    }
}

pub struct DeviceWatcher {
    method: WatchMethod,
    stop: Option<mpsc::Sender<()>>,
    thread: Option<JoinHandle<()>>,
    #[cfg(target_os = "macos")]
    listener: Option<mac::Listeners>,
}

impl DeviceWatcher {
    /// Starts watching. The signal is notified once per detected change (more than once for one physical change is
    /// possible and harmless).
    pub fn start(signal: Arc<ChangeSignal>) -> Self {
        #[cfg(target_os = "macos")]
        if let Some(l) = mac::Listeners::register(signal.clone()) {
            return Self { method: WatchMethod::CoreAudioListener, stop: None, thread: None, listener: Some(l) };
        }
        let (stop, rx) = mpsc::channel::<()>();
        let thread = std::thread::Builder::new()
            .name("relay-bus-device-poll".into())
            .spawn(move || poll_loop(signal, rx, POLL_INTERVAL))
            .ok();
        Self {
            method: WatchMethod::Polling,
            stop: Some(stop),
            thread,
            #[cfg(target_os = "macos")]
            listener: None,
        }
    }

    pub fn method(&self) -> WatchMethod {
        self.method
    }
}

impl Drop for DeviceWatcher {
    fn drop(&mut self) {
        self.stop.take(); // closes the channel, which ends the poll loop
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
        #[cfg(target_os = "macos")]
        drop(self.listener.take());
    }
}

/// What the polling compares: every device name and which ones are the defaults.
fn snapshot() -> (Vec<String>, Option<String>, Option<String>) {
    list_names()
}

fn poll_loop(signal: Arc<ChangeSignal>, stop: mpsc::Receiver<()>, every: Duration) {
    let mut last = snapshot();
    loop {
        match stop.recv_timeout(every) {
            Err(RecvTimeoutError::Timeout) => {}
            _ => return,
        }
        let now = snapshot();
        if now != last {
            last = now;
            signal.notify();
        }
    }
}

#[cfg(target_os = "macos")]
mod mac {
    use super::ChangeSignal;
    use std::sync::atomic::{AtomicBool, Ordering::Relaxed};
    use objc2_core_audio::{
        kAudioHardwarePropertyDefaultInputDevice, kAudioHardwarePropertyDefaultOutputDevice, kAudioHardwarePropertyDevices,
        kAudioObjectPropertyElementMain, kAudioObjectPropertyScopeGlobal, AudioObjectAddPropertyListener, AudioObjectPropertyAddress,
        AudioObjectRemovePropertyListener, AudioObjectSetPropertyData,
    };
    use std::ffi::c_void;
    use std::ptr::NonNull;
    use std::sync::Arc;

    const SYSTEM_OBJECT: u32 = 1; // kAudioObjectSystemObject
    /// kAudioHardwarePropertyRunLoop ('rnlp'). Not in the bindings.
    const PROPERTY_RUN_LOOP: u32 = 0x726e_6c70;
    static RUNLOOP_SET: AtomicBool = AtomicBool::new(false);

    fn address(selector: u32) -> AudioObjectPropertyAddress {
        AudioObjectPropertyAddress { mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal, mElement: kAudioObjectPropertyElementMain }
    }

    unsafe extern "C-unwind" fn on_change(_object: u32, _count: u32, _addresses: NonNull<AudioObjectPropertyAddress>, client: *mut c_void) -> i32 {
        // SAFETY: `client` is the pointer from `Arc::into_raw` in `register`, which is never freed (see `Listeners`).
        let signal = unsafe { &*(client as *const ChangeSignal) };
        signal.notify();
        0
    }

    pub struct Listeners {
        client: *const ChangeSignal,
        selectors: Vec<u32>,
    }

    // The raw pointer is to a leaked Arc<ChangeSignal> (Sync); it is only handed back to CoreAudio.
    unsafe impl Send for Listeners {}

    impl Listeners {
        pub fn register(signal: Arc<ChangeSignal>) -> Option<Self> {
            // By default the HAL delivers notifications on the process's main run loop, which a Python process
            // never runs. Setting the run loop to NULL tells it to use a thread of its own.
            if !RUNLOOP_SET.swap(true, Relaxed) {
                let mut addr = address(PROPERTY_RUN_LOOP);
                let null: *const c_void = std::ptr::null();
                // SAFETY: valid address and a pointer-sized value, as the property requires.
                unsafe {
                    AudioObjectSetPropertyData(
                        SYSTEM_OBJECT,
                        NonNull::from(&mut addr),
                        0,
                        std::ptr::null(),
                        std::mem::size_of::<*const c_void>() as u32,
                        NonNull::from(&null).cast(),
                    );
                }
            }
            let client = Arc::into_raw(signal);
            let mut done = Vec::new();
            for sel in [kAudioHardwarePropertyDevices, kAudioHardwarePropertyDefaultInputDevice, kAudioHardwarePropertyDefaultOutputDevice] {
                let mut addr = address(sel);
                // SAFETY: valid address, a correct listener, and client data that lives forever.
                let status = unsafe { AudioObjectAddPropertyListener(SYSTEM_OBJECT, NonNull::from(&mut addr), Some(on_change), client as *mut c_void) };
                if status != 0 {
                    for s in done {
                        remove(s, client);
                    }
                    // SAFETY: nothing holds the pointer now.
                    drop(unsafe { Arc::from_raw(client) });
                    return None;
                }
                done.push(sel);
            }
            Some(Self { client, selectors: done })
        }
    }

    fn remove(sel: u32, client: *const ChangeSignal) {
        let mut addr = address(sel);
        // SAFETY: the same arguments the listener was added with.
        unsafe { AudioObjectRemovePropertyListener(SYSTEM_OBJECT, NonNull::from(&mut addr), Some(on_change), client as *mut c_void) };
    }

    impl Drop for Listeners {
        fn drop(&mut self) {
            for &s in &self.selectors {
                remove(s, self.client);
            }
            // The Arc behind `client` is deliberately leaked (one small allocation per watcher): the HAL does not
            // promise a callback already running has returned when RemovePropertyListener does.
        }
    }
}
