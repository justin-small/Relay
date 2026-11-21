---
title: The Relay Helper is a Rust program that dials out to Relay and runs capabilities; ProPresenter is the first
author: Justin
date: 2026-11-21
branch: propresenter-files-497
cost: { minutes: 6, tokens: 79311 }
id: l-37de1a07
scope: repo
status: active
src: agent
touches:
  - native/relay-helper/tests/end_to_end.rs
  - docs/helper.md
---

## Decided
Justin, 2026-11-21, issue 497: Relay may run on a different computer from ProPresenter, so a small generic helper runs beside ProPresenter on a Mac or Windows PC. Rust, one binary, outbound WebSocket to Relay (no firewall changes), a one-use pairing code traded for a token, and capabilities added later as new Rust types on the same connection. The first capability opens song files with ProPresenter and forwards calls to its local API.

## Tried and ruled out
Putting the helper inside the Tauri desktop app: heavier; the helper has no screen.
An HTTP client for pairing: pairing is the first frame on the same WebSocket, so the helper needs no TLS HTTP client.

## Watch out
In the code: native/relay-helper (tests need no Relay or ProPresenter), protocol in docs/helper.md. Proved on this Mac against ProPresenter 22: status, a GET /v1/looks, and a song sent over the connection appeared in the library. Not tried on Windows (`cmd /C start` opens the .pro). Relay's side (app/helperhub.py, the pairing page, /ws/helper) is not built yet. The API proxy only accepts plain /v1/ paths on the configured port.
