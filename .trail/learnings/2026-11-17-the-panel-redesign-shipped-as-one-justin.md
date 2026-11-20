---
title: "The panel redesign shipped as one pull request that restyles module pages through shared CSS; the viewer pages and rundown stay out, and three presets' text co…"
author: Justin
date: 2026-11-17
branch: network-map-core
cost: { minutes: 25, tokens: 524196 }
id: l-ef669464
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/mdns.py
  - app/modules/__init__.py
  - app/modules/atem/__init__.py
  - app/modules/obs/__init__.py
  - app/modules/propresenter/__init__.py
  - app/modules/unifi/__init__.py
  - app/modules/unifi/service.py
  - app/network_map.py
  - app/static/network_map.js
---

## Decided
Justin, 2026-11-16, issue 434: do the whole redesign in one pull request, and build the network map (437) on its own branch from main. The shell, tokens, presets, Preferences, palette, users table and the sign-in and operator-tool pages are new; the module pages and the settings pages keep their markup and take the new look from admin.css (the old section, button, input and table rules now read the tokens), so no module file changed for the look.
Three presets differ from the reference's numbers so every text pair meets WCAG AA (tests/test_contrast.py reads tokens.css): the primary button text is near-black on Tangerine and black on Brutalist light, muted text is darker on Relay and Tangerine light, and Relay light's hover text is darker.

## Tried and ruled out
Retheming the viewer pages: the issue's "Must hold" list says viewer.css is out of scope, though its rollout step 9 says tokens only; only the 375px top-bar wrap was done. rundown.html keeps its own palette (a kiosk board).

## Watch out
The network-map branch was cut from main before the shell, so its admin.html edits are in the old markup (span.ico icons, inline style) and conflict with the redesign on merge: re-apply them in the new markup.
Browser pane screenshots lag one step behind JavaScript changes, so a page can look unchanged; read the DOM before deciding something failed.
In code: app/static/tokens.css, admin.css, shell.js, look.js, standalone.css; app/prefs.py shell_view; app/network_map.py, app/mdns.py.
