//! Prints a line every time the device watcher reports a change. Run it, then plug or unplug a USB or Bluetooth
//! audio device or change the default in the sound settings:
//!
//!     cargo run -p relay-bus-cpal --example watch

use relay_bus_cpal::{list_devices, ChangeSignal, DeviceWatcher};
use std::time::{Duration, Instant};

fn main() {
    let signal = ChangeSignal::new();
    let watcher = DeviceWatcher::start(signal.clone());
    println!("watching with {:?} ({}); Ctrl-C to stop", watcher.method(), watcher.method().as_str());
    for d in list_devices() {
        println!("  {} in={} out={} default_in={} default_out={}", d.name, d.inputs, d.outputs, d.is_default_input, d.is_default_output);
    }
    let start = Instant::now();
    let mut seen = signal.count();
    loop {
        if signal.wait(&mut seen, Duration::from_secs(1)) {
            println!("[{:7.2}s] device change (count {seen})", start.elapsed().as_secs_f64());
            for d in list_devices() {
                println!("  {} in={} out={} default_in={} default_out={}", d.name, d.inputs, d.outputs, d.is_default_input, d.is_default_output);
            }
        }
    }
}
