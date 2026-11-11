---
title: "Relay's static files are versioned and revalidated, because browsers kept an old stylesheet and new pages rendered unstyled"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 2, tokens: 69448 }
id: l-e1ec33fa
scope: repo
status: active
src: agent
touches:
  - app/main.py
---

## Decided
After the alert sender page (#121) shipped, it showed up in Justin's browser as bare default HTML: the browser reused a viewer.css cached before the alert styles existed (10.8 KB against 14.5 KB) and never asked Relay. Static files now send Cache-Control: no-cache (an unchanged file is a cheap 304 from its ETag), and templates link CSS and JS through static('<file>'), which adds ?v=<mtime> so copies cached before the change are bypassed too.

## Watch out
The header alone does not fix a browser that already holds a heuristically fresh copy; the versioned URL does. ES module imports inside flasher.js (flash_steps.js, vendor esptool) are not versioned, only revalidated. New templates should use {{ static('x.css') }}, not a bare /static/ path.

In the code: app/main.py RevalidatedStaticFiles and _static_url (Jinja global "static"); app/templates/*.html.
