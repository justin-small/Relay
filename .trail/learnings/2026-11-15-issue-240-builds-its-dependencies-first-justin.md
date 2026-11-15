---
title: "Issue 240 builds its dependencies first: talk leases, then channel admin, then a crew page shell, then the Intercom tab"
author: Justin
date: 2026-11-15
branch: intercom-talk-leases-274
cost: { minutes: 5, tokens: 96742 }
id: l-bfdff106
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/arbiter.py
  - tests/test_intercom_leases.py
---

## Decided
Justin chose (2026-11-15, issue 240) to build what the Intercom tab depends on before the tab itself, in this order: talk leases (#274), channels and members admin (#275), a minimal crew page shell (#226), then the tab, with livekit-client vendored by downloading it once. Talk leases are done first as a pure in-memory arbiter with hooks, so the socket, LiveKit and log are wired in presence.py.

## Tried and ruled out
Building the tab as a standalone page under /intercom now and embedding it in the crew page later: Justin preferred the dependencies done properly first.

## Watch out
Not committed yet when saved; #275, the crew shell and the tab are still to do. Device tests (iPhone, Android, AirPods, mouth-to-ear under 150 ms) cannot be run by Claude.

In the code: app/modules/intercom/arbiter.py, presence.py (bind, talk.* messages), tests/test_intercom_leases.py
