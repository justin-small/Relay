---
title: "The intercom watchdog keeps one card per check, computes loss from packet counts the phone now sends, and other modules add cards through a source hook"
author: Justin
date: 2026-11-16
branch: intercom-watchdog-276
cost: { minutes: 16, tokens: 201997 }
id: l-6b6876b1
scope: repo
status: active
src: agent
touches:
  - app/db.py
  - app/modules/intercom/migrations/0004_watchdog.sql
  - app/modules/intercom/watchdog.py
  - app/modules/intercom/templates/admin.html
  - tests/test_intercom_watchdog.py
  - tests/test_secrets.py
  - tests/test_auth.py
---

## Decided
- Issue 276, built on the Nov 15 answers (one PR, thresholds as settings, only LiveKit restarted, loss as a percentage over 10 s).
- Each check is a plain evaluator of its measurements (eval_livekit, eval_app, eval_db, eval_bridge, eval_audio, eval_latency, eval_grant), so tests force warn and error without timing tricks.
- Phones now send packets_received alongside packets_lost; loss % is the change in both over about 10 s. A count that drops (the phone rejoined its rooms) restarts the window.
- The bridge card belongs to intercom_remote: it registers watchdog.sources["bridge"] and Bridge.on_state on start (intercom_remote may import intercom, never the reverse). Relay's cloud is asked at most every 30 s because of its ~30 calls a minute limit.
- Event names already in the log were kept (device_connected, device_disconnected and so on) rather than renamed to the issue's session_started/ended. New kinds: audio_warning (once per phone per 5 min), livekit_restarted, bridge_state, server_restart (each module start), thresholds_changed.
- A module pill with "level": "error" draws red in the top bar; core never names intercom.

## Watch out
- macOS memory is the peak RSS (getrusage); Linux and Windows show current. No psutil.
- The 15 s acceptance checks (kill LiveKit, fill the loop, unplug the internet) are covered by tests with fakes only; not run against a real LiveKit or phone.
- Main was found missing the last two commits of #403 (secretbox CodeQL fix, gitleaks ignore for tests/test_secrets.py:120), so Security fails on main until they land.

In the code:
- app/modules/intercom/watchdog.py (Watchdog, LagMeter, LossTracker, THRESHOLDS), app/modules/intercom_remote/watch.py
- Routes /api/admin/intercom/health, /health/thresholds, /events, /events.csv (listed in tests/test_auth.py)
- tests/test_intercom_watchdog.py
