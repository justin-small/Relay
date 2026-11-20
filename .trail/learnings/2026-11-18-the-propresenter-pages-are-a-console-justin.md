---
title: "The ProPresenter pages are a console layout, and Slide view and lobby is three separately labelled jobs"
author: Justin
date: 2026-11-18
branch: propresenter-console
id: l-6dfd6e4a
scope: repo
status: active
src: agent
touches:
  - tests/test_pclobby.py
  - app/modules/propresenter/templates/control.html
---

## Decided
Justin, 2026-11-17, from four mockups (two made with /antialgorithm, two plain): the plain "console" option was chosen for Control, Slide view and lobby, and Settings, drawn inside the real admin shell on the shared tokens. Control shows the live and next slide as pictures with Previous/Next, the slide grid below, and tabs for Playlist, Looks and macros, Timers, Messages and Screens. Settings opens with a status card and a section list.
Slide view and lobby was confusing because it mixed three unrelated things under two dropdowns both labelled "Playlist". It is now three numbered cards: Slide view (a /stage web page for people who cannot see the screen), Lobby rotation (Relay presses Next on a playlist for lobby screens ProPresenter drives) and Signage copy (the playlist copied to Relay signage for signage TVs). Each card says what it does, when to use it, and where the slides go.

## Tried and ruled out
The two /antialgorithm options (stitched thread, carved relief) and the touch-first deck: not chosen. Per-screen on/off switches and a "time on this slide" timer from the mockups were left out; they need data the API pages do not have.

## Watch out
The jump links on these pages are buttons, not href="#...": a hash link would be read by the admin page's router as a page id. components.css gives a button display:inline-block, which overrides the hidden attribute, so a hidden button needs .ppbar [hidden]{display:none}. The page ids (lbStart, svRoot and the rest) are checked by tests/test_pclobby.py and read by the scripts, so keep them when restyling.

In the code: app/modules/propresenter/templates/control.html, live.html, settings.html; static/propresenter.css (the section at the end); ppcontrol.js (pcView and pcTabs), pplive.js (job tiles), propresenter.js (.ppsnav).
