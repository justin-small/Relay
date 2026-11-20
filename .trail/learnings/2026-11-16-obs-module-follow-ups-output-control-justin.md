---
title: "OBS module follow-ups: output control, studio mode, replay, audio, caption delay, history, health alerts, slide tags, tally and a YouTube link, in the same PR…"
author: Justin
date: 2026-11-16
branch: main
cost: { minutes: 21, tokens: 75188 }
id: l-6e299586
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/tools/build_windows.py
  - app/tally.py
  - firmware/relay-display/build.sh
  - firmware/relay-display/tests/build_update.py
---

## Decided
- 2026-11-16, Justin chose after issue 250: add to the OBS module start/stop stream and recording (stop asks first), studio mode with a Take button, the replay buffer with a save button, audio meters and mute for chosen inputs, a caption delay per computer, and a bitrate/congestion history on the admin page.
- Stream-health alerts: per OBS computer, pick one of the existing alert buttons for error and optionally one for warn; raised once per problem, not repeated while it lasts.
- Slide tags switch OBS scenes, through a core hook like lighting's fire_cue (ProPresenter never imports obs).
- Tally and Companion: OBS scenes map to camera numbers and feed core tally (app/tally.py); Companion is documented as HTTP calls to Relay's routes with an API token. A panel firmware scene picker is a separate issue for later.
- YouTube: OBS's WebSocket cannot manage broadcasts (title, privacy, schedule). Show on the OBS page that OBS streams to YouTube and which broadcast it picked, with a YouTube Studio link; real management waits for issue 252 with Relay's own YouTube sign-in.
- One pull request for issue 250 and all of this.

## Tried and ruled out
- Automatic recording from Relay's schedule (idea 9): not chosen.
- A built-in fixed OBS alert: Justin chose to pick existing alert buttons.
- Building YouTube OAuth now: deferred to 252.
- Two stacked PRs: Justin chose one.

## Watch out
- Never read or keep OBS's stream key: from GetStreamServiceSettings take only the service name and broadcast_id.
