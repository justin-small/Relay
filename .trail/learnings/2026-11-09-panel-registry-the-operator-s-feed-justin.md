---
title: "Panel registry: the operator's feed is sent by revision, and only operator settings are saved"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 13, tokens: 290127 }
id: l-c18511d1
scope: repo
status: active
src: agent
touches:
  - app/panels.py
  - tests/test_panels.py
  - firmware/relay-display/main/panel_link_format.h
  - firmware/relay-display/tests/fake_relay.py
  - app/main.py
  - app/config.py
---

## Decided
- A panel's check-in is held in memory only. Only what the operator sets (a name, a feed) is written to config.json, so a stray LAN client can never grow the config file. The registry holds at most 64 panels; a new one past that drops the stalest offline panel the operator never named, and is refused with 429 when there is none.
- The operator's feed carries a revision that rises with each setting. The panel reports the last revision it applied (`feed_rev`) and Relay sends `set_feed` only while the panel has not acknowledged it. This is how a feed chosen at the screen afterwards is not undone by the next 30 s check-in: newest wins from either side. The acknowledgement is held in Relay's memory, so after a Relay restart a panel that also rebooted is told the operator feed once more.
- The event stream is only served to a panel that has checked in (404 otherwise). The panel treats a 404 as "Relay restarted" and checks in again at once, instead of waiting out the 30 s.
- Operator control (list, rename, set feed, forget) is `/api/admin/panels*` on the operator port only. The `update_available` event exists and is logged by the panel, but there is no admin route to send it yet: that belongs with the firmware update work.

## Tried and ruled out
- Re-sending the operator feed on every check-in: it would overwrite a choice made at the screen within 30 s.
- Saving each panel's own name or last check-in to config.json: any device on the LAN could then write to it.

## Watch out
- A set_feed for a script the panel cannot draw is refused only if the panel has reported its scripts; the firmware reports `latin` only.
- `tests/test_panels.py` tests the real server and `firmware/relay-display/tests/fake_relay.py` copies its rule by hand; change one and the other has to follow.

In the code: app/panels.py (registry, `_pending_feed`), app/main.py (/api/panels/*, /api/admin/panels*), app/config.py (`panels` block, `save(replace=...)`), firmware/relay-display/main/panel_link.cpp and panel_link_format.h.
