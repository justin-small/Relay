---
title: "Panel LIVE buttons: Relay presses as the token's owner, never toggles control the wrong way, and only presses past the panel key are logged"
author: Justin
date: 2026-11-10
branch: pc-panel-control-80
cost: { minutes: 6, tokens: 89178 }
id: l-788e71ed
scope: repo
status: active
src: agent
touches:
  - app/pccontrol.py
  - tests/test_pccontrol.py
  - app/planningcenter.py
  - app/pclive.py
  - app/panels.py
  - app/main.py
---

## Decided
- Tried on a real account (throwaway plan): the action paths are .../plans/{plan}/live/{action} with no LIVE id; each answers 201 with the new Live document, so Relay shows that to every panel at once instead of re-reading. Next/Previous answer 403 unless the token's owner has control.
- toggle_control releases control when the token's owner already has it. So Take control and Release first read LIVE and compare its controller with /services/v2/me, and do nothing when it is already as asked. Panels get a separate Release.
- Every panel starts "off". "control" or "both" in admin gives it a random 128-bit key, sent on its check-in reply and event stream (plain HTTP: stops casual misuse, not sniffing). Off drops the key; turning control back on makes a new one.
- One press per second per panel, and one press at a time across all panels, so two toggles cannot interleave.
- The admin log lists presses only once a panel's key checks out: a stranger without the key cannot flood or wipe the operator's list.
- A panel is told a short code (not_controlling, unreachable, refused...), never Planning Center's error text.

## Watch out
- Whether toggle_control takes control from a different person is untested (needs a second account); the fake assumes it does.
- POSTs retry only on 429 (not carried out); a dropped connection is not retried, since Next may have happened.

In the code: app/pccontrol.py (Control), app/planningcenter.py (post, me, live_action, LIVE_ACTIONS), Follower.apply_live in app/pclive.py, service mode and control_key in app/panels.py, POST /api/panels/{id}/service/{action} in app/main.py, tests/test_pccontrol.py.
