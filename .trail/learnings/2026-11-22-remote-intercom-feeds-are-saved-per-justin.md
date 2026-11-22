---
title: "Remote intercom feeds are saved per channel and lane, and only run while a remote phone listens to that channel"
author: Justin
date: 2026-11-22
branch: avantis-meters-findings
cost: { minutes: 6, tokens: 124481 }
id: l-749206cc
scope: repo
status: active
src: agent
touches:
  - docs/avantis-meters.md
  - docs/avantis-meters/live.html
  - docs/avantis-meters/live_server.py
  - tests/test_browser.py
---

## Decided
Issue 616: the list of main-bus lanes heard in a channel's remote mix is saved in the module's settings as feeds (channel, lane, gain). The admin page replaces the whole list in one save. Feeds start when a channel's mix starts (the bridge calls a hook) and again when the bridge reconnects, and edits apply live to running mixers.

## Tried and ruled out
Nothing was ruled out. trail check flagged two false positives: the feed still runs Rust to Rust, and the bridge's session id is not kept.

## Watch out
A feed for a channel nobody remote is listening to has no mixer, so it shows "Waiting" rather than carried. "Refused" means the channel is being heard but the mixer's 32 lanes are full.
The browser test must enable intercom_remote in its config to see the page.

In the code: app/modules/intercom_remote/feeds.py, bridge.on_listen in bridge.py, PUT /api/admin/intercom-remote/feeds in routes.py, tests/test_browser.py.
