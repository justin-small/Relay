---
title: "ProPresenter slide-note tags: tags default off, a lyrics/captions/lang push is not stored, alert= names an Alerts button, and live checks cannot tag a real pre…"
author: Justin
date: 2026-11-12
branch: propresenter-138-tags
cost: { minutes: 90, tokens: 15304 }
id: l-e423b6a3
scope: repo
status: active
src: agent
touches:
  - app/panels.py
  - app/alerts.py
  - app/propresenter.py
  - app/main.py
---

## Decided
- Slide-note tags live in app/slidetags.py: `[relay:action]`, `[relay:action=value]`, deck-wide `[relay-deck:...]` in any slide's notes. The follower passes `observe()` the live slide's notes (status/slide) and the deck's tags (parsed from the one presentation/{uuid} read already made on a presentation change, cached by uuid + hash of the notes). Off by default (`propresenter.tags.enabled`, like every other ProPresenter switch except songs_only); `settle_s` default 1.5, 0.2 to 30.
- A slide that settles on the same resolved tag set as the last settled one runs nothing; an untagged slide is a set of its own (empty), so a tag that comes back after it runs again. A slide that left before the settle time never counts as settled. A slide's own VALID tag beats the deck's; a mistyped tag counts as no tag, so that slide falls back to the deck's and shows a warning.
- `lyrics`, `captions`, `lang=` push a `set_feed` through `Registry._send_feeds` and never store it, so the operator's stored feed is not overwritten; each push takes a revision above anything the panel applied (the firmware ignores a rev equal to the one it last applied) and `update()` now also counts that floor. `captions` restores what each panel reported before the lyrics push, and leaves a panel whose reported feed moved (changed at its screen). Only transcription/translation feeds are swapped: a panel on service, alerts or timers is left alone.
- `alert=<text>` matches an Alerts button by NAME (Alerts has no free-text alert; a button is the alert) and never cancels one already on (`Alerts.raise_named`). `capture-start/stop` go through `scheduler.manual_start/stop` + `engine.start/stop` like the admin buttons, never ProPresenter's capture/start.

## Tried and ruled out
- Raising a free-text alert with no matching button: there is no such path in Alerts (#121), and adding one is a design change for Justin.
- Storing the lyrics switch with `Registry.update(feeds=...)`: it would overwrite the operator's own panel feed and need a second update to give it back.
- Re-reading `presentation/{uuid}` on every slide for deck tags: one read per presentation change is enough.

## Watch out
- Live ProPresenter 22 has no API to create or delete a presentation (only libraries are listed), and notes in the church's own presentations are off limits, so tag FIRING was tested with a fake ProPresenter and recorders; live only proved that the presentation detail and status/slide carry a `notes` key, that one deck read happens, and that untagged slides fire nothing.
- The follower sees a slide change within about a second (the stream misses changes), then the settle time: expect roughly 2.5 s from click to action.
- Panels that check in after a lyrics/lang tag are not switched; only reachable panels (an open event stream) get the push.
