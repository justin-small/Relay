---
title: A channel is made of module-reported ends; Relay suggests joins and the operator confirms them
author: Justin
date: 2026-11-19
branch: channels-542
cost: { minutes: 14, tokens: 290513 }
id: l-f0430c73
scope: repo
status: active
src: agent
touches:
  - site/public/assets/base.css
  - site/public/assets/parts.js
  - site/public/index.html
  - app/migrations/0014_channels.sql
  - app/channels.py
  - tests/test_channels.py
  - tests/test_cables.py
---

## Decided
Issue 542 (2026-11-19), Justin's answers: one PR for every bullet, with a UI.
- A channel (`channel:relay:<id>`) is made of ends that modules report (`channels.report(provider, [...], gear_id, within)`). Relay suggests joins (same name, or same number on the same gear) and the operator confirms each; an operator's join is always right, and a declined pair is not suggested again. A merged-away ref follows to the channel that holds its ends.
- Producers: NDI sources and outputs, Blackmagic ATEM and Videohub, OBS scenes, the audio bus lanes and outputs, PortAudio input channels, metering sources, intercom (talk). The intercom keeps mode and talkers on its own table.
- A tally camera carries a channel ref and shows the channel's name. The rundown's board channels and mic labels keep a ref beside the typed name; a typed name that matches exactly one present audio channel adopts its ref once (auto-migration), otherwise it stays name only and the page flags it.
- The Channels section lives on the Network page under the same network.view/manage permissions.

## Tried and ruled out
- Joining ends by name on our own: "Input 3" on every device would join unrelated channels. Names a device gives everything are ignored when suggesting.
- One table with a provider column per channel: it cannot show NDI, Blackmagic and tally as one channel.

## Watch out
- A key must be unique per provider: Blackmagic and OBS put the device id in it.
- Report only while the source is readable (a disconnected Videohub does not say its inputs), or every channel turns missing.

In the code: app/channels.py, app/migrations/0014_channels.sql, tests/test_channels.py, tally.py, rundown.py (_refresh_channels), network_channels.js.
