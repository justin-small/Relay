# ProPresenter API: what a live install actually does

Checked on 2026-11-11 against ProPresenter 22 (API `v1`) on macOS, for issue 138. Every one of the 231 operations in [propresenter-api.md](propresenter-api.md) was called, including the ones that change things. Objects the test created were deleted again, and the install was compared with a snapshot taken before the run.

Read this alongside the reference: the reference says what the published description promises, and this file says where a real install differs.

## Summary

- **179 of 231 operations worked.** The other 52 returned 404 because the test install has none of what they act on: audio or media items, announcements, macros, props, masks, video inputs or stage screens. The API cannot create macros or props, so those could only be called with made-up IDs.
- **The live stream misses slide changes.** Re-read `GET /v1/status/slide` on every change instead of trusting what the stream pushes (see [Slide changes](#slide-changes)).
- **The port is set per install.** It was `50402` here. The description's default of `50001` is not what this install used.
- **ProPresenter can stall.** In one run, many endpoints stopped answering for about 4½ minutes. Use short timeouts and reconnect.

## Connecting

- `GET /version` returns `name`, `platform`, `os_version`, `host_description` (`"ProPresenter 22"`) and `api_version` (`"v1"`). Use it to test the connection.
- ProPresenter listens on all interfaces, on the HTTP port set in its Network preferences. A second open port, one higher, is not HTTP and Relay doesn't need it.
- Plain responses end with a blank line (`\r\n\r\n`), the same as stream messages.

## Live updates: `POST /v1/status/updates`

- **Response:** `content-type: application/octet-stream`, `connection: close`, chunked transfer.
- **Framing:** each message is one JSON object, `{"url": "<path>", "data": <the same body a GET returns>}`, followed by a blank line (`\r\n\r\n`). Split on that blank line, not on HTTP chunks: a whole presentation spans several chunks.
- **On connect:** the current state of every requested path arrives at once, so a reconnect needs no separate first fetch.
- **Unrequested messages:** `timer/system_time` arrives every 1–3 s without being asked for. It makes a good heartbeat: if it stops, the connection has stalled. Ignore any other path you didn't ask for.
- **One unknown path fails the whole request:** `400 ["URL: status/bogus. Error: 404 Not Found"]`, and nothing streams. Keep the list fixed in the code.
- **Several connections at once work.** A second connection received the same updates as the first.
- **Path names accepted (46):**
  - Announcements: `announcement/active`, `announcement/slide_index`, `announcement/active/timeline`
  - Audio: `audio/playlists`, `audio/playlist/active`, `audio/playlist/focused`, `audio/playlist/<id>/updates`
  - Capture: `capture/status`
  - Looks: `looks`, `look/current`
  - Macros: `macros`, `macro_collections`
  - Media: `media/playlists`, `media/playlist/active`, `media/playlist/focused`, `media/playlist/<id>/updates`
  - Messages: `messages`
  - Playlists: `playlists`, `playlist/active`, `playlist/focused`, `playlist/<id>/updates`
  - Presentations: `presentation/active`, `presentation/current`, `presentation/focused`, `presentation/slide_index`, `presentation/active/timeline`, `presentation/focused/timeline`, `presentation/chord_chart/updates`
  - Props: `props`, `prop_collections`
  - Stage: `stage/message`, `stage/layout_map`, `stage/screens`, `stage/layouts`
  - Status: `status/layers`, `status/stage_screens`, `status/audience_screens`, `status/screens`, `status/slide`
  - Timers: `timers`, `timers/current`, `timer/system_time`, `timer/video_countdown`
  - Transport: `transport/presentation/current`, `transport/presentation/time`, `transport/audio/time`
- **Path names rejected (400):** `capture/settings`, `clear/groups`, `groups`, `libraries`, `masks`, `presentation/chord_chart`, `themes`, `transport/presentation/auto_advance`, `video_inputs`.
- **`presentation/active` or `presentation/current`:** both work, as plain requests and inside the stream. They were the open question in issue 138.
- **Single requests:** `?chunked=true` on a GET streams that one path. Adding `&sse=true` turns it into proper `text/event-stream` (`event: /v1/status/slide` then `data: {…}`).

## Slide changes

Each of 9 slides was triggered through the API (`/v1/presentation/{uuid}/{index}/trigger`), with a one-second wait for the stream after each.

| | Result over 9 changes |
|---|---|
| `status/slide` pushed | 6 |
| `presentation/slide_index` pushed | 3 |
| At least one of the two pushed | 8 (the miss was a switch to another presentation, which sends `presentation/current` instead) |
| `GET /v1/status/slide` re-read gave the right text | **9 of 9**, in 1–3 ms |

When a slide was clicked by hand, the same pattern appeared: the slide position changed five times in a row with no new slide text. The same text is sometimes pushed twice in a row.

**What the follower should do:** treat any `status/slide`, `presentation/slide_index` or `presentation/current` message as a signal that something changed, then re-read `GET /v1/status/slide`. Ignore a result if its text matches the last one.

Other details:
- `current.text` and `next.text` separate lines with `\n`. A blank slide comes through as `""`, and the last slide of a presentation has `next` set to `null`.
- `next/trigger`, `previous/trigger`, `trigger/next` and `trigger/previous` all moved the slide. Again, only some of those moves were pushed.

## Clearing

| Action | What the stream sent |
|---|---|
| `GET /v1/clear/layer/slide` | `presentation/current` with no presentation, and `status/slide` with `null` |
| "Clear All" clear group | `presentation/current` with no presentation |
| Operator's manual clear, during an earlier listen | the presentation and slide position went empty, but `status/slide` kept the old text until a later clear |

**What the follower should do:** no presentation means no lyrics, whatever `status/slide` says.

## Telling songs from other slides

- The presentation object (`GET /v1/presentation/{uuid}`) has no library field. Its keys are `arrangements`, `current_arrangement`, `destination`, `groups`, `has_timeline`, `id`, `is_authorized`, `presentation_path`, `total_cues` and `type`.
- `presentation_path` is the file on disk and contains `…/Libraries/<library name>/<presentation>.pro`, so a "Songs" library can be recognised from it.
- Group names (Verse 1, Chorus, Bridge) come with every group, so they can be used too.
- Presentation names can end in a space; trim them.

## Stage message and messages

- `PUT /v1/stage/message` takes a bare JSON string, such as `"Sound desk needed"`. Sending an object gets `400 Json deserialize error: invalid type: map, expected a string`. The change appears on the stream straight away, and `DELETE` resets it to `""`.
- `POST /v1/messages`, `PUT`, `POST /v1/message/{id}/trigger` with `[{"name": "text", "text": {"text": "…"}}]`, `GET /v1/message/{id}/clear` and `DELETE` all work.
  - After a trigger, the message reads `is_active: true` and keeps the token value.
  - `status/layers.messages` turns on with the trigger and off with the clear.

## Timers

- On existing timers, `state` was correct every time: `running` while counting, `stopped` after a stop, and back to the full time after a reset.
- `GET /v1/timers/{operation}` works, but `start` would start every timer the church has, so the test only ran `stop` and `reset`.
- A countdown-to-time timer shows its target time (`05:00:00`) until it has been reset, then the time remaining.

## Creating, changing and deleting

- **Bodies need `id.uuid`.** The description says the UUID and index are ignored on create, but leaving the field out returns `400 missing field 'uuid'`. Send `""`. This applies to looks, clear groups, macros and props.
- **Response shapes differ:**
  - Wrapped in an extra key: `POST /v1/clear/groups` returns `{"group": …}`, `POST`/`PUT /v1/macro_collection(s)` returns `{"macro_collection": …}`, and `POST`/`PUT /v1/prop_collection(s)` returns `{"prop_collection": …}`.
  - Not wrapped: looks, timers, messages and playlists.
- **New items can 404 at first.** A timer just created returned 404 by UUID for about a second, though it was found by name and by index; it once happened to a look too.
- **No playlist delete.** Playlists can be created (`POST /v1/playlists`, `POST /v1/playlist/{id}`) and filled (`PUT`), but not deleted through the API.
- **Playlist items need `presentation_info.arrangement_uuid`** (send `""`), or the request returns 400.
- **Lookup by name works** for looks, timers, clear groups, themes and libraries, as the description says. Themes have empty UUIDs, so only names and indexes work for them.
- `PUT /v1/status/stage_screens` and `PUT /v1/status/audience_screens` take a bare `true` or `false` and do what they say. A `text/plain` content type gets `400 Content type error`.

## Reliability

- **A stall.** In one run, ProPresenter stopped answering many endpoints for about 4½ minutes and then recovered by itself.
  - It started right after `GET /v1/capture/start` (no capture source was set, so nothing recorded) and a few `GET /v1/clear/layer/…` calls.
  - The calls that hung included libraries, playlists, `presentation/focused`, `clear/layer/announcements` and `find_my_mouse`.
  - Simple status calls kept answering in a few milliseconds.
  - The second run made the same calls without stalling, so the cause is unknown.
- **What the follower should do:** use a short timeout on every request, watch the `timer/system_time` heartbeat, and reconnect the stream when it goes quiet.
- **Theme groups go missing.** `GET /v1/themes` sometimes leaves out a theme group (Lyric Styles) that still exists on disk.

## What could not be tested here

- **Content this install doesn't have:** audio and media playlist items, announcements, macros, props, masks, video inputs, stage screens and their layouts.
- **Which ProPresenter version added each endpoint:** this needs more than one install.
- **The cause of the stall** described under [Reliability](#reliability).
