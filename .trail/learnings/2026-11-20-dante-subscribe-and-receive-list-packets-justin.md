---
title: Dante subscribe and receive-list packets follow two open projects; clock and levels still need ConMon
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 9, tokens: 161918 }
id: l-91ac5ca0
scope: repo
status: active
src: agent
---

## Decided
The Dante subscribe (0x3010), clear (0x3014) and paged receive list (0x3000) packets in arc.py now follow network-audio-controller (public domain) and aes67_visualizer (MIT), two independent projects that agree. Clock and levels stay guesses.

## Tried and ruled out
Copying from inferno (GPL/AGPL) or bundling the GPL macOS driver: licence. 0x3400 as meter levels: BabelBox shows it is a channel listing.

## Watch out
Still never run on real Dante gear. Clock and meters should come from ConMon multicast (224.0.0.230-233, ports 8702/8705/8708), see avstreamlens conmon.rs.

In the code: app/modules/dante/arc.py, tests/test_dante_routing.py.
