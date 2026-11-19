---
title: "Forgetting a phone signs out its password sessions by matching the browser, and NDI page outputs are provided names"
author: Justin
date: 2026-11-19
branch: shared-concepts-leftovers
cost: { minutes: 12, tokens: 320506 }
id: l-c964f823
scope: repo
status: active
src: agent
touches:
  - app/devices.py
  - app/modules/ndi/outputs.py
  - app/modules/planning_center/rundown.py
  - tests/test_shared_concepts.py
---

## Decided
- Closing the leftovers of the shared-concepts epic (#521), 2026-11-19, after an audit Justin asked for.
- A password session now keeps the browser's user agent. Forgetting a phone (devices.revoke) deletes that person's sessions with the same user agent as the device. A session or device with no user agent signs nothing out. The device list shows a "Signed in" part for it.
- NDI page outputs: a module provides "ndi.outputs.<id>" (signage offers each screen). Core's public boards are listed by the NDI module itself next to screens and overlays, because core providing a name at import shows up in test_modules' "nothing on offer" checks. Before this, nothing ever filled the old ndi_output_providers dict, so signage and boards were never NDI outputs.
- A provider's url may be http://127.0.0.1:<port>/... (signage runs on its own port); any other host is ignored.
- The song leader's position on the rundown is a position ref plus a saved name, picked from a list. A typed name from before is matched on the next refresh.

## Tried and ruled out
- Linking sessions to core devices by a device_id column: at sign-in Relay can't tell which device it is, and making a device for every browser sign-in would fill the phones list with desktops.

## Watch out
- Two identical phones of one person with the same user agent are signed out together.
- crew chat role channels now use text.fold (casefold, not lower): a role containing "ß" gets a new channel name.

In the code: app/migrations/0010_session_agent.sql, app/devices.py revoke/signed_in, app/modules/ndi/outputs.py page_url, app/modules/planning_center/rundown.py is_leader, tests/test_shared_concepts.py name rule.
