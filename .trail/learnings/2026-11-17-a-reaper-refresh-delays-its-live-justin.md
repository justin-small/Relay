---
title: "A REAPER refresh delays its live answers by seconds, and its save prompt silences it after a stop"
author: Justin
date: 2026-11-17
branch: reaper-413
id: l-7a52a0e2
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/modules/__init__.py
  - app/modules/propresenter/propresenter.py
  - app/modules/propresenter/slidetags.py
  - app/modules/propresenter/tagactions.py
  - docs/guide/src/54-propresenter.md
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
Issue 413, 2026-11-17, measured live against REAPER on Justin's Mac:
- REAPER paces OSC feedback (about 80 packets of 1 KB a second). A full refresh (action 41743) queues ahead of live changes: with a 64-track bank the answer to a change came 8 to 14 s late, with 8 tracks 1.4 s, with nothing queued 0.05 s. So Relay follows 16 tracks by default (a setting, 1 to 64), refreshes on open and then only after REAPER was quiet for 20 s, counts it lost after 45 s, and gives a record 15 s to be confirmed.
- After a stop REAPER took 9.9 s to say /record 0 (it finishes the files), so an unconfirmed stop warns only after 30 s.
- With Settings > Recording > "Prompt to save/delete/rename new files" on, the prompt blocks REAPER: no OSC and no web remote until someone answers it. Relay's warning points at it; the README says to switch it off.
- A listen port taken at start (often by a Relay still closing) is tried again every 5 s instead of giving up.

## Tried and ruled out
Refreshing every 10 s as a heartbeat: with 64 tracks the /record 1 answer never caught up, so Relay believed REAPER was not recording while it was.

## Watch out
The admin page's script URL carries a hash taken at server start: restart the test server to see a changed script.

In the code:
`app/modules/reaper/client.py` (`QUIET_S`, `LOST_S`, `DEFAULT_TRACKS`), `app/modules/reaper/service.py` (`START_GRACE_S`, `STOP_CONFIRM_S`, `RETRY_S`, `Machine._heartbeat`, `Machine._retry`).
