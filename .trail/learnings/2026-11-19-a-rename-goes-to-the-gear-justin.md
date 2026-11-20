---
title: "A rename goes to the gear only when its module says it can; otherwise it is a Relay-only name with the gear's name beside it"
author: Justin
date: 2026-11-19
branch: channels-542
cost: { minutes: 8, tokens: 131623 }
id: l-d6828726
scope: repo
status: active
src: agent
touches:
  - app/channels.py
  - app/modules/blackmagic/service.py
  - app/main.py
---

## Decided
Issue 543 (2026-11-19), Justin's answers: one PR for every bullet, with a UI. The gear's name is the one already stored on each port and channel end; a Relay-only name is kept apart. A module offers `gear.<id>` with `can_rename(key)` and `rename(key, name)`. If the gear refuses, Relay changes nothing and shows a fixed sentence. "Rename on the device" clears the Relay-only name so the two cannot disagree.

## Tried and ruled out
- Reading the gear's name live from the module each time: gone while the device is disconnected.
- A renamable flag on every reported port or end: the module still needs a rename function, so one provider object is simpler.
- Falling back to a Relay-only name when the gear refuses: hides that the router was not changed.

## Watch out
Ports do not record which module reported them, so ports can only be renamed in Relay for now; only channel ends can be renamed on a Videohub. Return fixed sentences from routes, never a module's exception text (CodeQL).

In the code: app/channels.py (`rename_on_device`, `_gear_api`), app/modules/blackmagic/service.py (`can_rename`, `rename_end`), app/main.py (channel and port rename routes).
