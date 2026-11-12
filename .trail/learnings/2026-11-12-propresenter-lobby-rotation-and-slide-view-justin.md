---
title: "ProPresenter lobby rotation and slide view: the API cannot say which playlist is an announcement playlist, so the operator picks, and takeover is judged by wha…"
author: Justin
date: 2026-11-12
branch: propresenter-138-lobby
cost: { minutes: 9, tokens: 31889 }
id: l-d6a9dd25
scope: repo
status: active
src: agent
touches:
  - app/propresenter.py
  - app/main.py
---

## Decided
- The playlist list has no destination or type field (only field_type playlist/group), so the lobby page lists every playlist and the operator picks. Rotation steps with GET announcement/active/{n}/trigger (next cue) and GET playlist/{id}/{index}/trigger (next item), both through Client.action so view-only refuses them.
- A rotation is in memory only, never a setting: Start is the only way it begins, and a Relay restart does not resume it (a lobby that starts writing to ProPresenter by itself after a restart would surprise people).
- Takeover is judged from what the follower already reads each second (status/layers.announcements, announcement uuid and cue), not from playlist/active, which was never seen populated for announcements on the test install. A slide of the playlist's own presentations is adopted (operator nudge); anything else pauses writes; an empty layer for 5 s resumes from the top; a Clear by the operator pauses until Resume. A 2.5 s grace after each trigger lets the 1 s resync see it, so the minimum interval is 3 s.
- The follower reads transport/presentation/time and /current each second only while status/layers has slide or media on.
- Slide images are served only for 'current' and 'next' (never an arbitrary uuid/index) at quality=960; a stale v is a 404. /stage and /api/stage/* exist only while propresenter.view.public is on (default off).

## Watch out
- Verified live: slide and next images (960x540 JPEG, cached), timer overlay, the follower's media read when the slide layer has content (a song has no media, so duration 0 and no bar). NOT verified live: the announcement trigger calls, since the test install has one empty playlist and no announcement playlist, and video progress on real media. Rotation is checked with fakes only.
- ProPresenter's thumbnails of text-only slides are transparent: the page shows a dark checkerboard behind the text, that is the picture, not a bug.
- A CSS display rule on a class overrides the hidden attribute: .sv-media needed an explicit [hidden] rule or its bar showed with no video.
