---
title: "ProPresenter follower: stdlib only, and the live stream misses slide changes entirely, so the slide is re-read every second"
author: Justin
date: 2026-11-12
branch: propresenter-138-follower
cost: { minutes: 120, tokens: 1843 }
id: l-2408f3b2
scope: repo
status: active
src: agent
---

Decided: the follower and client use the standard library (http.client in a reader thread, a hand-written mDNS query), not httpx and zeroconf. Issue 138 and the brief both said they were already dependencies; they are not in requirements.txt (httpx is only in the dev venv; zeroconf is not installed anywhere, and panel announcement is a hand-written responder in tools/announce.py). Adding two packages to the image for this was not worth it.

Ruled out: trusting the stream for slide changes. On a live ProPresenter 22, after connecting, three slide triggers in a row produced no status/slide, slide_index or presentation/current push at all. The live-test notes said 8 of 9 changes were pushed; that was optimistic. The follower therefore also reads presentation/slide_index and status/slide every second (a few ms) and reads presentation/current only when the index names a different presentation. Without that, lyrics lagged by seconds or never changed.

Watch out: ProPresenter 22 announces `_proapiv1ws._tcp` over Bonjour, and its SRV port is the API port, so discovery needs no guessing. A presentation's library on the test Mac was "Default", not "Songs", so the songs-library name must be configurable. A GET can act (trigger/next, clear/layer/..., timers/start), so the client's read() refuses those paths and they go through action(), which is a write that view-only refuses.
