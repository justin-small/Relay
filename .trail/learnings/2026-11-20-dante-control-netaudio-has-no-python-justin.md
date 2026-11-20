---
title: "Dante control: netaudio has no Python protocol code to reuse, and discovery already lives in core"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 1, tokens: 1496 }
id: l-c4c68ca3
scope: repo
status: active
src: agent
touches:
  - app/mdns.py
  - app/netsock.py
  - docs/modules.md
---

## Decided
Issue 257, Justin's answers: Dante discovery reuses core's mDNS scanner rather than adding zeroconf. The module surfaces devices keyed by Dante name, transmit channels and a reachability check. Control (rename, subscriptions) is written by Relay itself, not taken from netaudio.

## Tried and ruled out
- netaudio as a pip dependency: pulls typer, rich, zeroconf, cryptography, dbus-fast and pywin32, does its own sockets so it ignores the pinned Dante port, and wheels are per-platform.
- Vendoring netaudio's ARC code: 0.6.1 and 0.3.14 do the protocol in a native binary (libnetaudio_core.dylib); the Python only builds request dicts. 0.1.7 is a CLI with no protocol code. Nothing to copy.
- python-zeroconf: core already scans without it.

## Watch out
ARC packet bytes for renames and subscriptions are guesses without a packet capture or real Dante gear; the repo's rule is to prove an integration on real hardware first.

In the code: app/mdns.py (SERVICES already lists _netaudio-arc/cmc/dbc/chan), app/netsock.py (udp_socket, Dante-class pinning), docs/modules.md (Dante section, gear/routes seams).
