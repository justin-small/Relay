---
title: "Talk leases live in memory and the intercom asks other modules for the plan name through the registry, never by import"
author: Justin
date: 2026-11-15
branch: intercom-channels-275
cost: { minutes: 1, tokens: 1476 }
id: l-7cad1732
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/arbiter.py
  - app/modules/__init__.py
---

## Decided
Keep talk leases in memory only, with a synchronous decision core, so two presses can never take the last place; only replies, unmuting and logging are awaited afterwards. Pre-empt picks the highest priority number among talkers, newest first on a tie, and equal priority never pre-empts. Take the production label's plan name from a Module.plan_label hook on the registry: None means no plan source, "" means a source with no plan today. Show the plan button only when a switched-on module answers.

## Tried and ruled out
Importing the Planning Center module from the intercom: integrations never import each other. Detecting a concurrent channel save by updated_at: timestamps have one-second resolution, so use a version counter column.

## Watch out
store.remove_member and set_member must also fire config_hooks or phones keep showing a removed channel. Exceptions in work scheduled from other threads were silent until presence.run got a done callback that logs them.

In the code: app/modules/intercom/arbiter.py, store.py (update_channel, config_hooks), app/modules/__init__.py (plan_label), planning_center/__init__.py
