---
title: "ProPresenter presentation/active/next/trigger moves a slide, not a playlist item; announcements and screens are stream paths, the playlist is empty on the test…"
author: Justin
date: 2026-11-12
branch: propresenter-138-state
cost: { minutes: 1, tokens: 6712 }
id: l-e5419153
scope: repo
status: active
src: agent
---

## Decided
- Next/previous playlist ITEM is `GET /v1/playlist/{active playlist uuid}/{next|previous}/trigger` (the active playlist comes from `playlist/active`), falling back to `playlist/focused/{next|previous}/trigger`. Relay answers 404 "No playlist is active or focused" when ProPresenter has neither.
- The follower follows `announcement/active` + `announcement/slide_index` (position every second, the announcement only when the position names a different one), and `status/screens`, `status/audience_screens`, `status/stage_screens` (every 5th resync). Screens on/off is a `PUT` of a bare true/false (PUT of the current value returned 204 live).
- Playlist item thumbnails use `playlist/{id}/{item}/thumbnail/{cue}`, which ProPresenter draws for the item's own arrangement, cached in the same LRU as presentation thumbnails with a key that starts with "playlist". An item's cue list comes from `cue_list(detail, arrangement)`, the one the slide grid uses.

## Tried and ruled out
- `presentation/active/next/trigger` for "next item" (stage 2's "Next presentation" button used it): live it moved the slide 1 to 2 inside the same presentation. It is the next CUE of the active presentation, so the button now does the playlist call above.
- Reading announcement/active every second: it carries the whole presentation, so only the small slide_index is read each turn.

## Watch out
- The test install has one empty playlist and no announcement playlist, so playlist items, thumbnails, diagnostics and announcements are checked with fakes only; the item-details and screens paths were checked live.
- `playlist/focused/next/trigger` answers 404 when no playlist is focused, even though `playlist/focused` still names a playlist item.
- A restarted Relay forgets admin sessions: an open admin tab gets 401 on every poll until it signs in again.
