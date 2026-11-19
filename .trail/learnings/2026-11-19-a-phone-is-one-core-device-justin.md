---
title: "A phone is one core device; sign-in, intercom, check-in and push are parts that go with it, and existing rows became one device each"
author: Justin
date: 2026-11-19
branch: screens-and-places
cost: { minutes: 7, tokens: 241121 }
id: l-a3f41470
scope: repo
status: active
src: agent
touches:
  - tests/test_shared_concepts.py
  - app/users.py
  - app/modules/intercom/devices.py
  - app/modules/crew/devices.py
  - docs/modules.md
---

## Decided
Issue 528 (2026-11-19), built to the epic's decision "one core device per phone, with credentials as parts":
- `devices` (app/migrations/0009_devices.sql, app/devices.py) holds one row per phone, owned by a user. Each part keeps its own table with `device_id references devices (id) on delete cascade`; modules register a part with `devices.register(kind, label, table, revoke, describe)` in attach() and drop it in detach(), the way `users.signed_out_hooks` works. Core never imports a module.
- A new part joins the user's device with the same non-empty user agent that has no part of its kind yet, else it gets a new device. Join-by-user-agent is the issue's rule.
- Forgetting a phone (`devices.revoke`) runs each part's revoke (the intercom closes its socket), then deletes the device row, which cascades to every part row, including a module that is switched off.

## Tried and ruled out
- Joining existing rows at migration time: only `intercom_devices` stored a user agent, so no two parts of different kinds could be joined with certainty. Every existing row became one device each; an admin joins the same phone with Join.
- Cascading sign-in trust with the device: a trusted booth iPad must outlive the admin who trusted it (test_users "a device outlives the admin"). `trusted_devices.device_id` is `on delete set null` and `devices.revoke` deletes the trusted row by hand.
- Soft-revoking the intercom credential only: it would come back when the module is switched on again. The cascade deletes it.
- Merging only the registered modules' tables: a merge while a module is off would lose that module's parts. `devices.part_tables` finds every table with a foreign key to `devices` in the database itself.

## Watch out
Core migration 0009 numbers the new devices by the row's place in `trusted_devices.id` order (the table starts empty); the module migrations (intercom 0005, crew 0013) use a temp `device_map` with `row_number()` offset by `max(id)`. A new module part table needs the same `device_id` column (tests/test_shared_concepts.py "device" rule; `intercom_remote_devices` is baselined because it flags an intercom credential).
In the code: app/devices.py, app/users.py trust_device, app/modules/intercom/devices.py, app/modules/crew/devices.py and paging.py, docs/modules.md "Phones and computers".
