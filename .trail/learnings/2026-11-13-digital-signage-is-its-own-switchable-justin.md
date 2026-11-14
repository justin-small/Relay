---
title: "Digital signage is its own switchable module, separate from Boards, with public screen URLs and sources from other modules"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 7, tokens: 57624 }
id: l-5d9095a8
scope: repo
status: active
src: agent
touches:
  - desktop/README.md
  - desktop/src-tauri/src/updater.rs
  - desktop/src-tauri/tauri.conf.json
  - docs/guide/src/16-upgrading.md
  - site/.gitignore
  - site/cloudflare.config.ts
  - site/package.json
  - site/public/_redirects
  - site/public/favicon.ico
  - site/public/favicon.svg
---

## Decided
Justin (2026-11-13, issue 204) scoped signage as an epic with sub-issues:
- A new switchable `signage` module owns screens, screen groups, the uploaded media library, playlists, schedules and the player page.
- ProPresenter announcement slides, Planning Center media and web/YouTube items come from their own modules and plug in as item sources. Signage never imports them.
- Screens are public by slug, with no login and no secret key.
- Sound is set per screen.
- Schedules are optional.
- Slide-note tags can switch one screen's playlist, a group's or every screen's, push one item, or take screens over to captions.
- Alerts, the live captions or service view, and operator push-now can take over screens.
- Devices: any browser, the Pi kiosk (#158) and ESP32 panels (images only).
- Playlist items have a duration, start and end dates, a transition, and fit or fill.

## Tried and ruled out
- Putting signage in core: Justin chose a module so a church can switch it off.
- Making signage part of #156 Boards, or giving screens zoned layouts: kept separate and simple first. A playlist widget for Boards can come later.
- Secret-key links or crew login for screens: public by slug was chosen.

## Watch out
Uploaded media is stored and served from Relay's data folder. That's the one core change, so say why in the pull request.
The tag syntax (e.g. [relay:signage=lobby:Welcome]) was a placeholder. Decide it while building, and keep it clear of the existing [relay:...] tags.

In the code: app/modules/signage/ (planned), /signage/<slug>; ProPresenter and Planning Center add sources through uses=("signage",). Draft: issue-204-draft.md
