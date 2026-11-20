---
title: "Remote intercom is tested through a throwaway enrolled Relay with the repo's pinned cloudflared, and it still has no phone page"
author: Justin
date: 2026-11-15
branch: intercom-remote-277-media
cost: { minutes: 2, tokens: 28190 }
id: l-796585db
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom_remote/media.py
  - tests/test_intercom_remote.py
  - run.py
  - tools/fetch_cloudflared.py
  - app/modules/intercom_remote/routes.py
---

## Decided
Justin chose (2026-11-15, issue 277) to enroll a throwaway test Relay (its own state folder, ports 8080/8443) with a one-time code minted in production, and to fetch cloudflared with the repo's pinned, hash-checked script into a scratch folder. Dropped: `brew install cloudflared` (system-wide) and not downloading at all (the link would be dead).

## Tried and ruled out
- Enrolling the real install in the Relay app-support folder: left alone so a test never changes the operator's own address.

## Watch out
Through the tunnel only the sign-in API and the WebSocket exist; there is no phone page or browser code for the Cloudflare audio connection, so nobody can talk remotely yet. The test Relay also needs the intercom `binary_path` set to a livekit-server binary, or LiveKit shows as missing. livekit and aiortc are installed in the dev venv only, not in requirements.

In the code:
- RELAY_CLOUDFLARED points run.py at the fetched binary; tools/fetch_cloudflared.py fetches it
- app/modules/intercom_remote/routes.py: the only routes on the tunnel
