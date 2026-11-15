---
title: "Crew pages use hand-rolled web push on cryptography, limited to browsers' push services, because pywebpush cannot ship"
author: Justin
date: 2026-11-15
branch: crew-pages-that-buzz
cost: { minutes: 1, tokens: 3154 }
id: l-886b9b22
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/webpush.py
  - app/modules/crew/paging.py
  - app/modules/crew/migrations/0002_paging.sql
  - app/modules/crew/static/sw.js
  - tests/test_crew_paging.py
---

## Decided
Justin answered questions on issue 228 (2026-11-15):
- Web push is written on `cryptography` (VAPID plus aes128gcm), because pywebpush cannot be installed by the release build.
- Pages are sent by Operator and above through their own `crew.page` permission; crew only receive and confirm.
- The re-fire schedule is 0, 5, 10, 20, 40, 70 and 120 seconds, counted on the Relay host from the send time, so it survives a restart and stops per person on confirm.
- Each person has their own confirm link that works with no sign-in and can only confirm that one page.
- A team page reaches only the accounts matched to Planning Center people (#227); people with no account are reported back to the sender.

## Tried and ruled out
- pywebpush, which Justin first chose: its http-ece dependency has no wheel, and the release build installs wheel-only with hashes, so the desktop build would break. Justin then chose to hand-roll it.
- An even every-15-seconds schedule: offered, not chosen.
- Extending Alerts' seen/not-seen engine to people: pages keep their own receipts.

## Watch out
A subscription endpoint is typed by any signed-in crew account, so Relay posts only to https hosts on the browsers' push services and follows no redirect; supporting another browser means adding its host to the allowlist. The encryption is checked against the RFC 8291 appendix A vector. Confirm tokens are stored in the clear and never sent to the admin page. Not tried against a real phone or push service; iOS only allows push once the page is added to the home screen.

In the code:
- `app/modules/crew/webpush.py` (`encrypt`, `vapid_header`, `allowed`, `ALLOWED_HOSTS`)
- `app/modules/crew/paging.py` (`SCHEDULE_S`, `tick`, `resolve`)
- `app/modules/crew/migrations/0002_paging.sql`
- `app/modules/crew/static/sw.js` (push handlers)
- `tests/test_crew_paging.py`
