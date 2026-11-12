---
title: "ProPresenter 22's API checked by a full write run: the slide streams drop changes, so re-read the slide on every change"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 16, tokens: 145194 }
id: l-61fe1be9
scope: repo
status: active
replaces: l-4cbc8b80
src: agent
---

## Decided
Ran all 231 operations against ProPresenter 22 (API v1) on Justin's Mac, with his go-ahead to change things; 179 succeeded. The other 52 need content this install has none of (audio/media items, announcements, macros, props, masks, video inputs, stage screens). The follower re-reads GET /v1/status/slide whenever slide_index, status/slide or presentation/current arrives on the status/updates stream, and treats no presentation as no lyrics.

## Watch out
- Neither stream is complete: over 9 triggered slides, status/slide was pushed 6 times and slide_index 3; each change sent one or the other, but not reliably both. The re-read returned the right text 9 of 9 times, in 1-3 ms.
- Port is per install (50402 here). status/updates frames {"url","data"} objects separated by a blank line, sends current state on connect, adds timer/system_time every 1-3 s unasked (use it as a heartbeat), and rejects the whole request if one path is unknown. 46 path names are accepted. Rejected: capture/settings, clear/groups, groups, libraries, masks, presentation/chord_chart, themes, transport/*/auto_advance, video_inputs.
- Create bodies need id.uuid present (send ""), although the description says it is ignored. Create responses are sometimes wrapped ({"group":…}, {"macro_collection":…}, {"prop_collection":…}) and sometimes not (looks, timers, playlists).
- A newly created timer (and once a look) returns 404 by UUID for about a second.
- There is no playlist delete; test playlists stay behind.
- One run hung many endpoints for about 4.5 minutes (libraries, playlists, presentation/focused; simple status kept answering) right after capture/start and clear/layer calls. Not reproduced on the second run. Short timeouts and reconnects are needed.
- Library is not in the presentation object, but presentation_path contains .../Libraries/<name>/<file>.pro, which can be used to detect songs.
- Stage message: PUT takes a bare JSON string (an object gets 400) and shows on the stream at once. Messages with text tokens trigger and clear correctly; status/layers.messages follows them.
- The timer state field is correct on existing timers (running/stopped).
- GET /v1/themes sometimes leaves out theme groups (Lyric Styles), even though they are on disk.

In the code: not built yet; issue 138. Throwaway harness, not committed.
