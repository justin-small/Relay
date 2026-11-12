---
title: "What ProPresenter 22's live update stream really sends, checked on a local install"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 4, tokens: 63067 }
id: l-4cbc8b80
scope: repo
status: active
src: agent
---

## Decided
Checked the issue 138 plan against ProPresenter 22 (API v1) on Justin's Mac, read-only, while Justin clicked slides. The follower should hold one POST /v1/status/updates connection and treat slide_index as the trigger, re-reading the slide text rather than trusting status/slide pushes alone.

## Watch out
- Port is whatever Network preferences say (50402 here), not the description's 50001. A second open port (50403) is not HTTP.
- Framing: content-type application/octet-stream, each message is {"url": "<path>", "data": ...} followed by a blank line (\r\n\r\n). Split on that, not on HTTP chunks; whole presentations span several chunks. Current state of every path is sent at once on connect.
- One unknown path in the list fails the whole request: 400 ["URL: status/bogus. Error: 404 Not Found"].
- presentation/active and presentation/current both work, standalone and inside status/updates.
- timer/system_time arrives every 1-3 s without being asked: usable as a heartbeat to detect a stalled stream; ignore other unrequested urls.
- status/slide did not push on every slide click: slide_index changed five times in a song with no status/slide message. It can also arrive twice in a row with the same text.
- Clearing: presentation/current went to no name and slide_index to {"presentation_index": null}, but status/slide kept the old text until a later clear; a blank slide sends current="" and next=None.
- timers/current pushes about once a second while a timer is touched; its state field read "stopped" while the time was changing.
- Presentation names can carry trailing spaces.

In the code: not built yet; issue 138. Probe script was throwaway (POST /v1/status/updates with ["status/slide","presentation/current","presentation/slide_index","stage/message","timers/current"]).
