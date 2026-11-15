---
title: "LiveKit's webhook is verified by its signed JWT and goes to Relay's viewer port; Windows ties LiveKit to a kill-on-close job object"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 5, tokens: 99816 }
id: l-e3e4cc1f
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/webhook.py
  - app/modules/intercom/livekit.py
  - app/modules/intercom/winjob.py
  - tests/test_intercom_webhook.py
  - app/modules/intercom/__init__.py
---

## Decided
Justin chose (2026-11-14, issue 238): build the signed webhook and the Windows job object now; the /intercom/rtc proxy waits for the sign-in rules (#239/#273); API secret encryption stays in #278. The webhook route is public on purpose; the HS256 JWT in Authorization (iss = our key, sha256 claim = body hash, exp) is the credential. LiveKit posts to http://127.0.0.1:<viewer port>/intercom/livekit-webhook. Only ids are logged; rooms that are not ch-<id> are ignored.

## Tried and ruled out
Adding a JWT library: the check is a few lines of hmac, and the repo has none installed.

## Watch out
- The job object is untested on Windows here; the Windows-only check in tests/test_intercom_webhook.py kills a parent and expects its child to die, so CI is the first run.
- Checked on a Mac against real livekit-server 1.13.11 (brew): CreateRoom/DeleteRoom produced room_started and room_finished rows. Linux, Windows and a LAN phone are still unrun.
- The webhook URL is read when the config is written, so a changed viewer port needs a LiveKit restart.

In the code: app/modules/intercom/webhook.py, winjob.py, livekit.py (config_text, webhook_url)
