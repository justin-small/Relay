---
title: "NDI sender and router proved on real NDI: frames must be writable, routing goes through ctypes, and the Magewell and BirdDog drivers wait"
author: Justin
date: 2026-11-17
branch: ndi-viewer-signage-widgets
cost: { minutes: 10, tokens: 124334 }
id: l-e7021a84
scope: repo
status: active
src: agent
touches:
  - app/modules/ndi/runtime.py
---

## Decided
Justin, 2026-11-17, issues 298 and 299, built in one branch. Magewell and BirdDog support are each their own module, so they are not in this change: the integration rule makes every product a module, and neither device could be reached. Page outputs render with Playwright's headless Chromium (about 30 fps at 720p with real alpha on macOS); a hidden Tauri webview was not tried because it cannot give an alpha screenshot off screen and does not exist on the headless Linux service.

## Tried and ruled out
- Sending frames as bytes: cyndilib's write_video refuses with "Object is not writable"; the sender copies bytes to a bytearray and the renderer returns a writable numpy array.

## Watch out
- Proved on this Mac with the real NDI library: Chromium page, Relay sender, Relay router, then a receiver got 640x360 BGRA. Alpha values in the received frame were not read back, only the BGRA format. No Magewell, TV or switcher was involved; the CPU figure is Relay's own thread time, not Chromium's.
- A Playwright page must be made and used on the same thread, so the output engine creates its renderer inside the output's own thread.

In the code: app/modules/ndi/runtime.py (_Sender, _Router), render.py, outputs.py, router.py.
