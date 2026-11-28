//! `relay-core`'s [`LaneBus`] on `relay-audio`'s bus: what a module that drives a network source or sender (NDI sound, #658) sees of the
//! engine. Each driver's status is kept apart and merged, so one module reporting never wipes another's.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use relay_audio::AudioBus;
use relay_core::lanes::LaneBus;
use serde_json::{json, Value};

pub struct CoreLanes {
    pub bus: Arc<AudioBus>,
    status: Mutex<BTreeMap<String, (Value, Value)>>,
}

impl CoreLanes {
    pub fn new(bus: Arc<AudioBus>) -> CoreLanes {
        CoreLanes { bus, status: Mutex::default() }
    }
}

impl LaneBus for CoreLanes {
    fn config(&self) -> Value {
        self.bus.config()
    }

    fn running(&self) -> bool {
        self.bus.running()
    }

    fn push(&self, input_id: &str, data: &[f32], channels: usize, rate: u32) -> bool {
        self.bus.push(input_id, data, channels, rate)
    }

    fn pull(&self, output_id: &str, frames: usize) -> Option<Vec<f32>> {
        self.bus.pull(output_id, frames)
    }

    fn set_status(&self, driver: &str, inputs: Value, outputs: Value) {
        let Ok(mut all) = self.status.lock() else { return };
        all.insert(driver.to_owned(), (inputs, outputs));
        let (mut ins, mut outs) = (serde_json::Map::new(), serde_json::Map::new());
        for (i, o) in all.values() {
            ins.extend(i.as_object().cloned().unwrap_or_default());
            outs.extend(o.as_object().cloned().unwrap_or_default());
        }
        self.bus.set_external(json!({"inputs": ins, "outputs": outs}));
    }
}
