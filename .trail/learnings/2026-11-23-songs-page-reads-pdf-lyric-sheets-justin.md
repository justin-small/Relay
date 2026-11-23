---
title: "Songs page reads PDF lyric sheets on the server with pypdf, and keeps templates and drafts in the browser"
author: Justin
date: 2026-11-23
branch: helper-releases-636
cost: { minutes: 8, tokens: 152548 }
id: l-e358dd80
scope: repo
status: active
src: agent
touches:
  - docs/mockups/propresenter-songs/common.css
  - requirements.txt
  - requirements-release.txt
---

## Decided
Justin, 2026-11-22, issue 633: of three mockups he chose C (slide sorter: tiles by section, inspector, arrangement dock). PDF import reads text on the server with pypdf, a pure-Python dependency added to the pins. Templates and the draft live in the operator's browser (localStorage), not shared between operators. Every import, auto-split and template change is shown first and applied only on a yes.

## Tried and ruled out
Vendoring pdf.js to read the PDF in the browser: no Python dependency but a large JS file in app/static. Skipping PDF for now: Justin wanted it in this pass.

## Watch out
Another page already uses the "sg" id prefix (signage), so the songs page uses "sng"; duplicate ids made a status line show another page's text. A scanned PDF has no text and is refused with a sentence. Real send through a helper and opening a real .pro file were not tried here.

In the code: app/modules/propresenter/ppsongparse.py, static/ppsongs.js, templates/songs.html, requirements.txt and requirements-release.txt (pypdf 7.1.1), tests/test_ppsongparse.py.
