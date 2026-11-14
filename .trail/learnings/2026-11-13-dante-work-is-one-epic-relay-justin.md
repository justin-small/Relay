---
title: "Dante work is one epic: Relay may change routes and presets behind operator permission, talks to Dante through netaudio in-process, and sends alert tones and s…"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 7, tokens: 84773 }
id: l-5574daec
scope: repo
status: active
src: agent
touches:
  - app/modules/propresenter/pclink.py
  - app/modules/propresenter/ppcontrol.py
  - app/tls.py
  - firmware/relay-display/main/timers_view.cpp
  - tests/test_pclink.py
  - tests/test_propresenter.py
  - tests/test_serving.py
  - app/audio.py
---

## Decided
- Justin (2026-11-13, issue 192): all Dante work lives in one epic (#192). #144 is closed as superseded; its parts moved to #257-#266.
- Relay may write Dante routing: subscriptions and saved routing presets only, behind dante.control (operator), with confirm, undo, change history and admin-set per-channel locks. This reverses #144's "never write a subscription". Device settings (names, gain, sample rate, latency, clock leader, AES67 mode) stay in Dante Controller.
- Protocol via the netaudio Python package (chris-ritsen/network-audio-controller, Unlicense) in-process inside the module; no daemon for the church to install.
- Input: both documented driver routes (DVS, Via, AVIO, openaes67 on Windows, AES67 macOS driver, Inferno on Linux) and a built-in AES67 receiver as its own aes67 module.
- Output: alert/cue tones and spoken translation per language onto Dante/AES67. TTS is its own issue (#256), outside the epic.
- Shape: dante module, aes67 module, Avantis patch in the sound desk module (#150), and one core seam for audio sources and outputs shared with talkback (#92).

## Tried and ruled out
- Read-only Dante (as #144 had it): the request was management, so gated writes instead.
- Full device configuration from Relay: too risky during a service; left to Dante Controller.
- Running an external netaudio daemon: one more thing for a church to install.
- Loopback/monitor output of Relay's input: not chosen.

## Watch out
- Never bundle the GPL-3.0 macOS AES67 driver; link to it.
- Dante's control protocol is reverse engineered; mark hardware untested until tried. DDM networks refuse unmanaged writes.
- Check netaudio wheels exist for every desktop/appliance platform (incl. Linux arm64) before building on it.

In the code:
- Issues: epic #192; children #257-#267; TTS #256; app/audio.py AudioCapture is where the source/output seam (#265) goes.
