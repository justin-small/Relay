---
title: "The NDI module installs the NDI runtime and cyndilib separately, discovers over plain mDNS, and stays off without them"
author: Justin
date: 2026-11-17
branch: youtube-pco-publishing
cost: { minutes: 7, tokens: 160124 }
id: l-3c9f01a2
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
---

## Decided
Issue 294, 2026-11-17, Justin's choices: build the full issue including the SDK, three kinds of board widget (summary, source list, per-source tile), and both group handling and BirdDog converter info. The binding is cyndilib (MIT). NDI's licence lets an app ship its libraries only under an EULA carrying NDI's terms, so Relay does not bundle them: the operator installs the NDI runtime and cyndilib. Without either, the module says so and stays off; Relay still starts. Discovery is Relay's own stdlib mDNS, not the SDK's finder.

## Tried and ruled out
Discovery only with no SDK, and a spike write-up only: both offered, not chosen. Bundling the runtime: licence terms.

## Watch out
Not proved on real NDI. The cyndilib calls (Finder, Receiver, Sender) follow its docs from memory and ran only against a fake backend; check them on macOS, Windows and the Linux service. Discovery Server settings are #295, not done.

In the code: app/modules/ndi/runtime.py (spike notes, backend interface), service.py (pool), discovery.py, tests/test_ndi.py.
