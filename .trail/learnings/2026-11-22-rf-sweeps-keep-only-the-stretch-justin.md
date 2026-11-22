---
title: "RF sweeps keep only the stretch above each hop's centre, and lines every 4.8 MHz are not the DC spike"
author: Justin
date: 2026-11-22
branch: rf-monitor-rust-spike-360
cost: { minutes: 1, tokens: 12502 }
id: l-91e6f65a
scope: repo
status: active
src: agent
touches:
  - native/relay-rf/src/main.rs
  - native/relay-rf/src/sweep.rs
  - native/relay-rf/Cargo.lock
  - native/relay-rf/Cargo.toml
  - native/relay-rf/deny.toml
  - native/relay-rf/src/dsp.rs
  - native/relay-rf/src/imd.rs
  - native/relay-rf/src/lib.rs
  - native/relay-rf/src/rtltcp.rs
  - native/relay-rf/src/sdr.rs
---

## Decided
Justin, 2026-11-22, issue 366: each sweep hop keeps only 5% to 37.5% of the sample rate above its tuner centre, and the next hop centres one stretch (780 kHz at 2.4 MS/s) higher, so the RTL-SDR's DC spike is never kept and the hops leave no gap. 470-608 MHz is 177 hops. Mic levels are read 300 kHz off the tuner centre for the same reason.

## Tried and ruled out
- Keeping the middle 75% of every hop (77 hops, faster): the DC spike sat inside what was kept and showed as a peak in every hop.
- Blaming the lines at 518.4, 528.0 and 547.2 MHz on that spike: they stayed at the same frequency under the new hop plan, so they are not the spike. They are multiples of 4.8 MHz (28.8 MHz crystal / 6), so either dongle clock spurs or real signals.

## Watch out
- To tell spurs from real signals, unplug the antenna and run `relay-rf peaks 515 530`; do not alert on those frequencies until that is done (issue 365).
- The sweep now takes about twice as long as the first 4.9 s; the hardware test only bounds it at 20 s.
In the code: native/relay-rf/src/sweep.rs (SKIP, KEEP_TO, plan), src/main.rs (roster, peaks).
