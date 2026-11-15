---
title: "The remote phone page is a static page and script served on the tunnel, and a signed-in socket plus a real Cloudflare session already work end to end"
author: Justin
date: 2026-11-15
branch: intercom-remote-277-media
cost: { minutes: 3, tokens: 32000 }
id: l-bedd5651
scope: repo
status: active
src: agent
---

## Decided
The remote phone client is two static files in the intercom_remote module (phone.html, phone.js) served at /intercom/remote/ and /intercom/remote/phone.js; they are added to the fixed list of tunnel routes. The page signs in, opens the socket and runs the Cloudflare push/pull/renegotiate flow in the browser; Relay still picks which tracks it may pull and when it may talk.

## Tried and ruled out
- Sending the link before a page existed: the tunnel's root is the viewer caption page, so the first link showed live captions only.

## Watch out
Verified from outside through the real tunnel: sign-in, socket welcome and cf.open returning a real Cloudflare session id. The browser audio path (mic, push, pull, renegotiation) has not run in a real browser or phone. Cloudflare's edge rejects Python's default User-Agent with 403, so scripts need a browser-like one.

In the code:
- app/modules/intercom_remote/static/phone.js, routes.py: REMOTE_ROUTES
