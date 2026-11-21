---
title: "Tests for the remote intercom's optional libraries must skip when the library is missing, because CI does not install them"
author: Justin
date: 2026-11-21
branch: rust-intercom-mixer-605
cost: { minutes: 1, tokens: 11196 }
id: l-525f6a58
scope: repo
status: active
src: agent
touches:
  - tests/test_intercom_remote.py
  - tests/test_intercom_remote_media.py
---

## Decided
Justin's Auto-fix run, 2026-11-21, issue 605: PyAV (av), aiortc and the livekit SDK are optional for the intercom_remote module, and CI installs none of them. A test that imports one must check for it first and skip that part, as the module itself does with its LIBS list.

## Watch out
The local venv has all three, so a local full-suite run passes while the macOS and Windows CI jobs fail with ModuleNotFoundError. Before pushing a test that touches them, run it once without them, or check `importlib.util.find_spec` first.

In the code:
tests/test_intercom_remote.py (the microphone-frame section), tests/test_intercom_remote_media.py (skips without livekit-server or the SDK).
