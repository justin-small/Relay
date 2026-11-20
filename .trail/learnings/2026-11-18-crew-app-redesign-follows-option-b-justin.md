---
title: "Crew app redesign follows option B: a home screen and bottom bar on the phone, a live board for the operator, and full-screen page, join and offline screens"
author: Justin
date: 2026-11-18
branch: network-closets-redesign
cost: { minutes: 5, tokens: 15322 }
id: l-3c9cff50
scope: repo
status: active
src: agent
touches:
  - app/modules/crew/templates/crew.html
---

## Decided
Justin, 2026-11-18: of three crew app mockups he chose B. Phone: opens on a "what do I do now" home (the item on stage, your next step, the last page), with a bottom bar of Home, Chat, Checklist, Files and Talk, and your settings and other tools in a panel opened from your picture. Operator page: a live board with a column per team and paging plus activity always on the right; Call times, Checklists, People and Setup are tabs across the top, like the Planning Center page.
He asked that the page alert, join and offline screens each be a full page, not a panel over home or a warning band: the page alert fills the screen with a big Got it button, join is one page with an iPhone/Android switch and every step listed, and offline keeps the saved position and times with a Try again button.

## Tried and ruled out
A (tabs across the top on the phone too) and C (Me / my team / Talk on the phone, one operator tab per Planning Center team): not chosen. In B itself, the page alert as a panel rising over the home screen, and offline as a band over the home screen: replaced by full pages at his request.

## Watch out
Nothing is built yet; the real crew page still has the old card stack with pill tabs.

In the code: mockups in docs/mockups/crew/ (b-home.html is the chosen one; common.css and common.js are shared). The real pages are app/modules/crew/templates/crew.html, page.html, join.html, offline.html and admin.html, with static/crew.css, crew.js and crewadmin.js.
