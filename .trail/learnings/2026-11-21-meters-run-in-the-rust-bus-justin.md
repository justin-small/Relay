---
title: "Meters run in the Rust bus core: several sound meters, a probe per meter, and exact loudness gating ported from PLVS"
author: Justin
date: 2026-11-21
branch: audio-first-setup-599
cost: { minutes: 90, tokens: 1615 }
id: l-ff237dbf
scope: repo
status: active
replaces: l-117df199
src: agent
touches:
  - app/metering.py
  - app/mixreport.py
---

## Decided
Justin, 2026-11-21, issue 604: the metering and loudness maths moves into the Rust bus core and Python keeps no DSP. Start clean: no parity fixtures against the old Python (it was never used), old `metering`/`mixreport` settings are dropped. The operator adds several sound meters, each on its own lane, and a board's Sound level and Spectrum widgets pick one by name (`sources[id]` in the status feed's sound block). The stream-mix report still records one row a second, but from a Rust loudness probe: exact BS.1770 gating, true peak, loudness range, correlation and octaves. "Depend on PLVS" means ported with attribution: it is a Tauri app, not a crate, so `filters.rs` and `gating.rs` are copied under native/relay-bus/crates/relay-bus/src/analysis/ with its MIT notice.

## Tried and ruled out
- A Rust function the Python tap thread still feeds: it keeps the dropped-sample problem the issue is about.
- A loudness board widget: not built; only the existing Sound level and Spectrum widgets pick meters. Left as a follow-up.
- Exact gating in Python: replaced the old "one momentary value a second" approximation (the earlier learning ruled exact gating out).

## Watch out
The hub thread is one thread for every probe. A tap's ring holds four seconds, so a test must not push more than that onto a lane at once (a real input never does). `calibrate` clamps a reference outside 30 to 140 dB rather than refusing it, as the old code did. A new `/api/admin/metering/{mid}/calibrate` route replaced the single-meter one. Not run against a real measurement mic or a hardware loudness meter.
In the code: native/relay-bus/crates/relay-bus/src/analysis/, crates/relay-bus-py/src/lib.rs (`open_probe`), app/metering.py, app/mixreport.py, tests/probes.rs.
