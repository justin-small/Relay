//! RF monitor core (issues 360-376). Pure Rust: no SoapySDR, no Python, no numpy. Measurement code takes plain
//! `&[u8]` IQ so synthetic signals test it; `IqSource` is the one seam between that code and a dongle, local or remote.
pub mod dsp;
pub mod imd;
pub mod rtltcp;
pub mod sdr;
pub mod sweep;

/// Anything that can be tuned and read for IQ: a local dongle (`sdr::Sdr`) or an `rtl_tcp` server (`rtltcp::Client`).
pub trait IqSource {
    fn tune(&mut self, hz: u32) -> Result<(), String>;
    /// Fill `buf` with interleaved unsigned 8-bit IQ; return bytes read.
    fn read(&mut self, buf: &mut [u8]) -> Result<usize, String>;
    fn sample_rate(&self) -> u32;
}

#[cfg(test)]
pub mod tests_support {
    /// Poll for a condition instead of sleeping a fixed time.
    pub fn wait_until(mut f: impl FnMut() -> bool) {
        let t = std::time::Instant::now();
        while !f() {
            assert!(t.elapsed().as_secs() < 5, "timed out");
            std::thread::yield_now();
        }
    }
}
