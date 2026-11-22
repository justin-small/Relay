---
title: "The RF monitor is pure Rust on rtl-sdr-rs and nusb, and a retune needs its stale samples discarded"
author: Justin
date: 2026-11-22
branch: main
cost: { minutes: 4, tokens: 60601 }
id: l-7c6552f9
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-22, issues 360-376: the RF monitor core is pure Rust, no SoapySDR and no numpy. rtl-sdr-rs (MPL-2.0, over nusb) drives the RTL-SDR, rustfft does the spectrum, and remote nodes use the rtl_tcp protocol with both ends written in Rust. Proven on a Nooelec Nano Three (0bda:2838) before building on it.
- Levels are dBFS with Hann coherent-gain normalisation: a 0.5 amplitude tone reads -6 dB.
- After a retune, discard the first read(s) (sweep) or read until the carrier shows (rtl_tcp): blocks already in flight still hold the old frequency.

## Tried and ruled out
- rtlsdr_mt: GPL-2.0+. soapysdr crate: defeats the purpose. seify/futuresdr: only if non-RTL dongles are wanted later.
- Dividing by the window's power sum: made a -6 dB tone read -7.8 dB.

## Watch out
- A synthetic test tone outside +-rate/2 aliases back into band and fakes a peak (a sweep test found 535.7 MHz instead of 521.3); the synth drops out-of-band tones.
- One dongle means one hardware test at a time (a single test runs the steps); it skips when no dongle is present.
In the code: native/relay-rf/src/dsp.rs (power_spectrum, synth), sweep.rs, rtltcp.rs, tests/hardware.rs; run `cargo test --release --test hardware -- --nocapture`.
