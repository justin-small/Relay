---
title: "Issue 659 runs captions in Rust behind --rust-captions, reading the room from a bus lane tap and following settings by polling"
author: Justin
date: 2026-11-28
branch: learning-admin-pages-719
cost: { minutes: 35, tokens: 685488 }
id: l-2c8c43ef
scope: repo
status: active
src: agent
touches:
  - native/relay-captions/src/redact.rs
  - native/relay-captions/src/lib.rs
  - native/relay-captions/tests/redact.rs
  - native/relay-captions/tests/transcript.rs
  - native/relay-captions/src/clock.rs
  - native/relay-captions/src/recorder.rs
  - native/relay-captions/src/exporting.rs
  - native/relay-captions/src/aimodels.rs
  - native/relay-captions/src/translate.rs
  - native/relay-captions/src/transcribe.rs
---

## Decided
Issue 659 (live captions) is a library crate relay-captions plus a mounting file in relay-server, switched on with --rust-captions, and it needs --rust-audio: the room's audio is a 24 kHz tap on a lane of the Rust bus, not a sound card Rust opens. Settings the Python config route saves (lane, language, realtime, stream_captions, keys, blocklist file) are followed by reading config.json once a second, because that route stays in the app. Times (run ids, exports) use the one Relay zone, not the computer's. Python modules hear stream.started/stopped and supply pills and health through two new /api/internal routes in app/front.py.
## Tried and ruled out
Porting audio.py's capture (PortAudio sources, reconnect loop): not in the issue's list and the bus already owns devices. An mpsc channel as the audio queue: it cannot drop its oldest frame, so the queue is its own type that evicts the oldest and counts it.
## Watch out
Python's scheduler still starts Python's pipeline; leave the schedule off in the app while captions run here. Not run against the live Vercel gateway or OpenAI; the real clients are tested against local HTTP and WebSocket servers. gitleaks git failed in this checkout with no output before any commit of this work, so it is not from this change.
In the code: native/relay-captions/, native/relay-server/src/captions.rs, docs/rust-server.md "Live captions (#659)".
