//! Local RTL-SDR over USB (issues 361, 362, 373).
use crate::IqSource;
use rtl_sdr_rs::{DeviceDescriptors, DeviceId, RtlSdr, TunerGain};

pub const RATE: u32 = 2_400_000;

pub struct Found {
    pub index: usize,
    pub vid: u16,
    pub pid: u16,
    pub manufacturer: String,
    pub product: String,
    pub serial: String,
}

/// Enumerate without opening: safe to poll for hot-plug.
pub fn enumerate() -> Result<Vec<Found>, String> {
    let d = DeviceDescriptors::new().map_err(|e| e.to_string())?;
    Ok(d.iter()
        .enumerate()
        .map(|(index, x)| Found {
            index,
            vid: x.vendor_id,
            pid: x.product_id,
            manufacturer: x.manufacturer.clone(),
            product: x.product.clone(),
            serial: x.serial.clone(),
        })
        .collect())
}

pub struct Sdr {
    dev: RtlSdr,
    rate: u32,
}

impl Sdr {
    pub fn open(index: usize) -> Result<Sdr, String> {
        let mut dev = RtlSdr::open(DeviceId::Index(index)).map_err(|e| e.to_string())?;
        dev.set_sample_rate(RATE).map_err(|e| e.to_string())?;
        dev.set_tuner_gain(TunerGain::Auto).map_err(|e| e.to_string())?;
        dev.reset_buffer().map_err(|e| e.to_string())?;
        Ok(Sdr { dev, rate: RATE })
    }
    pub fn gains(&self) -> Result<Vec<i32>, String> {
        self.dev.get_tuner_gains().map_err(|e| e.to_string())
    }
    pub fn set_gain_tenth_db(&mut self, g: i32) -> Result<(), String> {
        self.dev.set_tuner_gain(TunerGain::Manual(g)).map_err(|e| e.to_string())
    }
    pub fn set_auto_gain(&mut self) -> Result<(), String> {
        self.dev.set_tuner_gain(TunerGain::Auto).map_err(|e| e.to_string())
    }
    pub fn set_ppm(&mut self, ppm: i32) -> Result<(), String> {
        self.dev.set_freq_correction(ppm).map_err(|e| e.to_string())
    }
    pub fn set_rate(&mut self, hz: u32) -> Result<(), String> {
        self.dev.set_sample_rate(hz).map_err(|e| e.to_string())?;
        self.rate = hz;
        Ok(())
    }
}

impl IqSource for Sdr {
    fn tune(&mut self, hz: u32) -> Result<(), String> {
        self.dev.set_center_freq(hz).map_err(|e| e.to_string())
    }
    fn read(&mut self, buf: &mut [u8]) -> Result<usize, String> {
        self.dev.read_sync(buf).map_err(|e| e.to_string())
    }
    fn sample_rate(&self) -> u32 {
        self.rate
    }
}
