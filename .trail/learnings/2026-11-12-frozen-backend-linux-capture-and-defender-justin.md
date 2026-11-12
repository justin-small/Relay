---
title: "Frozen backend: Linux capture and Defender checked in CI; macOS mic prompt and Windows capture need real machines"
author: Justin
date: 2026-11-12
branch: spike/freeze-179
cost: { minutes: 9, tokens: 16632 }
id: l-aaccb069
scope: repo
status: active
replaces: l-cb90d965
src: agent
touches:
  - ".github/workflows/spike-freeze.yml"
  - spike/entry.py
---

## Decided
Follow-up to the issue 179 freeze spike (one-folder PyInstaller, Python 3.15, go). Linux x64 and arm64 capture real frames through the frozen app from a PulseAudio null sink (silence, peak 0, the PortAudio path works). Defender on the Windows runner flagged neither build.

## Tried and ruled out
- macOS mic prompt: a signed test .app launching the frozen binary via `open` captured audio with no visible prompt and no TCC record for its bundle id, so which app a first prompt names is unproven.
- Windows capture on CI: the runner has no input devices and cannot install a virtual driver.
- The Defender result is weak: each scan returned in under a second.

## Watch out
Settle the mic prompt with the real Tauri app on a Mac that never granted it mic access (NSMicrophoneUsageDescription in its Info.plist, sidecar inside the bundle). Windows capture and signed-build antivirus need a real Windows machine. Everything else from the earlier result stands (cryptography 50.0.0 lacks an Intel Mac wheel; one-folder, not one-file).

In the code: .github/workflows/spike-freeze.yml (probe --capture, Defender step), spike/entry.py.
