---
title: "The home page leads with one Sunday in lanes, under 'One view of your whole production, built from the tools you already run.'"
author: Justin
date: 2026-11-19
branch: site-one-view
id: l-849742ad
scope: repo
status: active
replaces: l-b4f589ff
src: agent
touches:
  - site/public/index.html
---

## Decided
Justin, 2026-11-19: of three home page redesigns (a floor plan, a rack, a Sunday in lanes), he chose the Sunday in lanes. It replaces the hub hero.
- Hero: "One view of your whole production, built from the tools you already run." over a table of the service, one row per system and one column per moment of the service, with a playhead moving through it. Green cells are handoffs Relay makes on its own; a green cell may only show a handoff Relay really does, never a planned one.
- After the table: the status view with the YouTube line flipping to "needs a look", the green room section (the real rundown screen in its three views), How it fits (the hub), four jobs, captions, integrations with Planned labels, the volunteer section, and Talk to us.
- The page sits on Relay's ink green (#04130a) with Relay green (#4ade80).

## Tried and ruled out
- Keeping the hub as the hero: it moved down to How it fits.
- A section retelling the table's handoffs as a timeline, and the six-language welcome: removed as repeats.
- Drawn copies of the rundown boards: Justin wanted the real look, so the page runs the app's rundown.js and rundown.css with sample data.

## Watch out
- site/public/assets/rundown/ holds copies of app/static/rundown.js, rundown.css and relay-ui.js; refresh them when the rundown screen changes.
- Cloudflare redirects the .html addresses of the rundown frames to the address without it; they still load.
- The page was built from site/mockups/one-view/c-service.html (git-ignored); edit site/public from now on.

In the code: site/public/index.html, site/public/assets/ (base.css, parts.js, base.js, rundown/). Deploy from site/: cf deploy public
