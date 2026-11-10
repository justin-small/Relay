---
title: "Panels get both an operator lock and a return to the operator's choice, set per panel"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 3, tokens: 54050 }
id: l-e56c71f5
scope: repo
status: active
src: agent
touches:
  - app/panels.py
  - firmware/relay-display/main/feeds.cpp
  - firmware/relay-display/tests/fake_relay.py
---

## Decided
Issue #126 settled by Justin (2026-11-10): both features are wanted, and each is set per panel from the panel drawer in admin, not once for every panel.
- Lock: the operator can lock a panel so nobody at the screen can change what it shows. The screen's menu is still shown, and a change is refused with a short note, rather than hiding the menu.
- Return: after a period with no touch, a panel goes back to the operator's choice. It applies to the split layout as well as the feed.

## Tried and ruled out
- Lock only, with no return: dropped; the operator wants panels to drift back on their own.
- Neither (newest change wins from either side, as before): dropped.
- One setting for every panel: dropped in favour of per panel.

## Watch out
Both need a field sent to the panel (like set_feed) and firmware that follows it; panels without the new firmware must ignore the unknown fields and keep working.

In the code: app/panels.py (operator settings, set_feed push), firmware/relay-display/main/feeds.cpp, firmware/relay-display/tests/fake_relay.py, README feed picker section.
