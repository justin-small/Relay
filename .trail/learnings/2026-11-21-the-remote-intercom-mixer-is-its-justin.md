---
title: "The remote intercom mixer is its own Rust engine with no devices, and main-bus audio reaches it through a Rust link"
author: Justin
date: 2026-11-21
branch: rust-intercom-mixer-605
cost: { minutes: 5, tokens: 116086 }
id: l-b8b7d7bf
scope: repo
status: active
src: agent
touches:
  - tests/test_intercom_remote.py
---

## Decided
Justin, 2026-11-21, issue 605: the remote intercom mixes in an engine of its own (relay_bus.Bus("none"), no sound device), not on the main bus, so main-bus reconfiguration never touches it and core never has to know a module's lanes. Each room track is an input lane fed with push, each channel's mix is a pull output drained on the 20 ms Cloudflare clock. Main-bus audio goes in through relay_bus Link (a tap on a main lane, carried by a Rust thread into an input of the intercom engine), not through Python. One mix per channel stays; a return per remote talker (mix-minus) is #614.

## Tried and ruled out
Python hand-over for the main-bus feed (tap read in Python, pushed to the other engine): Justin chose Rust to Rust so a busy event loop cannot make the feed choppy.
numpy down-mix of microphone frames: replaced by a PyAV AudioResampler (mono, s16, 48 kHz), so media.py has no sample maths.

## Watch out
The engine has 32 lanes in all: room tracks and main-bus feeds share them, and one past that is refused (logged) and left out of the mix.
Pulling is still paced by aiortc on the Python event loop: a busy loop makes the frame late, not lossy (the engine holds about 20 ms).
Not run: a real LiveKit room (Justin chose automated tests only); the PR says so. No operator UI for feeds yet; media.LiveMedia.feed(channel, audiobus.bus.engine, lane, gain_db) is the API.

In the code:
app/modules/intercom_remote/mixbus.py, media.py (LiveMedia.feed), native/relay-bus/crates/relay-bus/src/link.rs, relay-bus-py/src/lib.rs (Bus("none"), link_from), tests/test_intercom_remote.py.
