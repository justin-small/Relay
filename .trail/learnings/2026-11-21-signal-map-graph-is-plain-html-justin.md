---
title: "Signal map graph is plain HTML nodes and edges, the show-file patch is imported as text, and the UniFi port is matched to Dante devices by IP"
author: Justin
date: 2026-11-21
branch: claude/nostalgic-shamir-b0df09
cost: { minutes: 4, tokens: 102640 }
id: l-a1aed568
scope: repo
status: active
src: agent
touches:
  - app/modules/signalmap/patch.py
---

## Decided
Justin's brief, 2026-11-21, issue 262: the graph is HTML nodes with CSS edges (not SVG, so the style contract holds); the A&H show-file patch is read through the provided `patch.audio_mixers` and stored as the same pasted text; the switch port comes from `gear.unifi` by matching a Dante device's IP to a UniFi client. The receiver on this computer is marked by IP.
## Tried and ruled out
Importing signalmap code from audio_mixers or unifi: modules only reach each other through provided names.
## Watch out
`dante.routing` now carries `ips`. A UniFi client list only holds online clients, so a silent box shows no port.
In the code: app/modules/signalmap/patch.py (from_desk, switch_ports), static/signalmap.js (graph).
