---
title: "AES67 receives announced streams in Python, picks by stream id, and is unproven on real Dante gear"
author: Justin
date: 2026-11-20
branch: aes67-266
id: l-894a69be
scope: repo
status: active
src: agent
touches:
  - docs/guide/src/55-dante.md
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
Issue 266, Justin's answers (2026-11-20): AES67 is its own module that registers an audio source `aes67` with core. Streams are found by SAP and chosen by `<group>:<port>`. Receive is a Python multicast socket, no PTP lock; drift and lost packets are measured instead. First PR covers discovery, picker and receive. Inferno (Dante-native, GPL, run separately) goes to a follow-up issue after a macOS/Windows/Linux spike.

## Tried and ruled out
Bundling Inferno: GPL, Rust, mainly Linux; it also cannot replace AES67 for the macOS and Windows done condition.

## Watch out
No Dante device was available: built against hand-made SDP/RTP and a loopback multicast socket. The one-hour soak on real gear is still to run. The chosen stream's SDP is kept in the module settings so the input opens before the next announcement.

In the code: app/modules/aes67/ (sdp.py, receiver.py, source.py, service.py), tests/test_aes67.py, docs/modules.md "AES67 streams as the input".
