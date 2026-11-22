---
title: "Avantis live meters come from AH-Net on 51321: a Metering Sources subscribe makes the desk stream 8 meter groups at 50 Hz"
author: Justin
date: 2026-11-22
branch: main
cost: { minutes: 10, tokens: 121036 }
id: l-564a1dda
scope: repo
status: active
src: agent
touches:
  - desktop/src-tauri/Cargo.lock
  - native/relay-rf/Cargo.lock
  - native/relay-rf/Cargo.toml
  - native/relay-rf/deny.toml
  - native/relay-rf/src/dsp.rs
  - native/relay-rf/src/imd.rs
  - native/relay-rf/src/lib.rs
  - native/relay-rf/src/main.rs
  - native/relay-rf/src/rtltcp.rs
  - native/relay-rf/src/sdr.rs
---

## Decided
Justin, 2026-11-22: MIDI on 51325 cannot give meters (no meter message), so live meters on an Avantis come from AH-Net (TCP 51321). Decoded from a packet capture of Allen & Heath Director on the church Avantis, then replayed read-only from this Mac: it worked.
Handshake: ahnet_hello(), then a "Metering Sources" manager frame, then 11 subscribe frames (k = 0..10). After that the desk streams meters by itself at 50 Hz per group, with no keep-alive needed in a 3-minute run.
Meter frame from the desk: `F0 00 <session> 01 72 03 50 10 <group> <len16> <16-bit big-endian values> F7`. 8 groups (0,1,2,4,5,6,7,8; group 3 never came), sizes 480/384/504/320/256/64/112/48 values. Level is probably signed int16 / 256 = dB (0xA000 = -96 floor); this is inferred, not confirmed. 0xFFFF looks like a separator, not a level.
Justin paused the work on 2026-11-22 to study the protocol himself; do not restart logging against the live desk until he says so.

## Tried and ruled out
Matching frames on the exact header bytes: byte 3 of the frame is the session number (04 in Director's session, 05 in ours), so match `F0 00 ?? 01 72 03 50 10`.
Passive listening on 51321 after hello alone: the desk pushes nothing until the meter subscribe is sent.

## Watch out
Not yet known: which value positions belong to which channel and measurement point, and whether the dB scale is right. It needs a calibration run (one channel at a time) or matching against the MIDI fader and mute pushes during a service.
The desk is used live: only hello, the meter subscribe and name/colour reads were sent, never a write.
In the code: subscribe frame is `bytes([0xF0,0,1,3,0x50,0,3,0,6,0,0x0A,0,0,1,0x72,0x10,k,0x10,k+1,0,0,0xF7])`; manager frame is `F0 00 01 00 00 00 03 00 04 00 <len> "Metering Sources" 00 F7`; existing codec in app/modules/audio_mixers/ah.py (AH-Net section) and the session loop in drivers.py (_ahnet_session).
