---
title: "Remote phones share the intercom socket through a hook, and their cleanup must start before any await because the test client cancels a closing socket"
author: Justin
date: 2026-11-15
branch: intercom-remote-277
cost: { minutes: 10, tokens: 171668 }
id: l-b6af99be
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/presence.py
---

## Decided
Remote crew (issue 277) reuses the intercom's own socket code instead of copying it: a remote module hands presence a small hook object (allow, handle, opened, closed) and its phones then use the same channels, choices and talk leases as local ones, except they never get a LiveKit token. The module is `intercom_remote` (module ids are lower_snake_case). The intercom's /intercom/api, /ws and /rtc paths are now HTTPS-port only, so the tunnel carries nothing local; the LiveKit webhook stays on the viewer port on purpose.

## Tried and ruled out
- Awaiting the end-of-session record and the Cloudflare cleanup inside the socket's finally block: Starlette's test client cancels the handler at its first await there, so nothing after it ran.
- Writing the audio bridge without real LiveKit and Cloudflare sessions: left unwritten rather than guessed.

## Watch out
Cleanup for a closing remote socket is started as its own task before any await (`closed` in the remote module). The audio bridge (media.py) is still a stub, so no remote phone can connect yet. The deployed relay-cloud Worker's source had only ever existed in a local folder.

In the code:
- app/modules/intercom/presence.py: `Remote` hook class and `serve(ws, remote)`
- app/modules/intercom_remote/remote.py: `closed` and `_finish`; bridge.py has the reconnect backoff
- tests/test_intercom_remote.py
