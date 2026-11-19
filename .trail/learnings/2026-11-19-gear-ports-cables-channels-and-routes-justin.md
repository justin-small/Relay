---
title: "Gear, ports, cables, channels and routes get one core record each; the gear is the source of truth for names"
author: Justin
date: 2026-11-19
branch: shared-gear-audit
cost: { minutes: 1, tokens: 1667 }
id: l-29cc59bb
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-19, after the audit in docs/reference/gear-ports-channels-audit.md (epic #539, sub-issues #547, #540–#546):
- A new core gear record (gear:relay:<id>) per piece of equipment, adopted by key like screens. Phones stay core devices (#528).
- A port is any physical connector (Ethernet, SFP, SDI, HDMI, XLR, USB, power) and is called `ports` in code too. A TCP number is only a field of a host address, never a table or ref.
- Channels are audio, video or intercom talk. Core owns cables and routes; the module that owns the gear applies a route and reports what the gear really does.
- Names: the gear is the source of truth, Relay may rename on the gear where supported, and may keep a Relay-only display name.
- Stored and refreshed from modules, like teams and positions; missing items are flagged, not dropped.
- Racks sit inside core places (Booth > Rack A).

## Tried and ruled out
- "connector" as the code name: offered, not chosen.
- A Dante module in this epic: devices from mDNS discovery only for now.
- Racks as places of their own, and live-only lists: offered, not chosen.

## Watch out
- Keep "unknown" apart from "no signal" on ports and channels: the Blackmagic page lights a connector only when the device reports it.
