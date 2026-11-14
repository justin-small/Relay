---
title: "ProPresenter announcements reach signage as library pictures, and signage tags are separate actions per kind and target"
author: Justin
date: 2026-11-14
branch: signage-propresenter-208
cost: { minutes: 7, tokens: 208017 }
id: l-a9f15072
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/modules/propresenter/__init__.py
  - app/modules/propresenter/ppcontrol.py
  - app/modules/propresenter/propresenter.py
  - app/modules/propresenter/routes.py
  - app/modules/propresenter/signagesync.py
  - app/modules/propresenter/signagetags.py
  - app/modules/propresenter/slidetags.py
  - app/modules/propresenter/static/pplive.js
  - app/modules/propresenter/static/propresenter.js
---

## Decided
Justin answered four questions on issue 208 (2026-11-14):
- Slides are fetched as large (1280) thumbnails from ProPresenter's playlist thumbnail API and cached as signage media rows named "ProPresenter: <presentation> <n>"; they are refreshed when the deck changes (every 2 minutes, or Sync now), not fetched live on each play.
- One auto-managed signage playlist, "ProPresenter announcements", is rewritten from the picked ProPresenter playlist. A slide that stays keeps its duration and dates. Only library rows whose name starts with the prefix are ever deleted.
- Tags are separate actions per kind and target (signage-screen/-group/-all for playlists, signage-push, -captions, -service, -resume each with -screen and -group forms), not one action with a target prefix, so a screen named "group" cannot be mistaken for the group form.
- A push plays once then returns; captions and service stay until a signage-resume tag. Plain signage-resume ends every takeover, not only tag ones.
## Tried and ruled out
Registering a signage source kind for the slides: library rows make push-by-name and the existing player work with no new source code, and the pictures survive ProPresenter being down.
## Watch out
Tag actions refuse with a fixed sentence while Signage is off; the "tag" schedule layer is registered in the module's attach and held in memory, so a restart forgets what tags put on screens.
In the code: app/modules/propresenter/signagetags.py, signagesync.py; README "Signage from ProPresenter".
