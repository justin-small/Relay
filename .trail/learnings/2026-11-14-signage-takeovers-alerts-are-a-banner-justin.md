---
title: "Signage takeovers: alerts are a banner, pushes and live views sit above the playlist, a push beats a live view"
author: Justin
date: 2026-11-14
branch: main
cost: { minutes: 11, tokens: 173140 }
id: l-636e7747
scope: repo
status: active
src: agent
touches:
  - tests/test_signage.py
---

## Decided
Justin, 2026-11-14, issue 207: all three takeovers plus a hook for slide-note tags. Alerts show as a banner strip over the playlist (not full-screen), on all screens, chosen groups or none, set once in signage Settings. Live captions and the service view are Relay's own /screen pages framed from the viewer port; a push uses the media library only. Priority: push, then live view, then playlist; a screen's own beats its group's beats all. A once-push ends when the player says it finished (memory only; an hour fail-safe), and the playlist resumes at the item it was on.

## Tried and ruled out
Using the #206 takeover layer (playlist-only) for these: a push, a framed view and a banner are not playlists. Hooking core alerts: signage reads alerts.state() once a second instead, so core stays untouched.

## Watch out
The admin preview cannot frame live views: the panel is HTTPS and the viewer port is HTTP. The service view falls back to captions when Planning Center's rundown is off. X-Frame-Options DENY applies only to the HTTPS panel socket, so framing the viewer port works.

In the code: app/modules/signage/takeover.py, migration 0003_takeover.sql, player.js _takeover/_banner, tests/test_signage.py "takeovers (#207)".
