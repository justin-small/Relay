---
title: "Retaking guide screenshots next to a running Relay: rename the throwaway container, and give panel pages the real status stream"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 16, tokens: 232236 }
id: l-54ae28f0
scope: repo
status: active
src: agent
touches:
  - README.md
  - docs/guide/README.md
  - docs/guide/images/admin-alerts.png
  - docs/guide/images/admin-appearance.png
  - docs/guide/images/admin-audio-clipping.png
  - docs/guide/images/admin-audio-input.png
  - docs/guide/images/admin-blocked-words.png
  - docs/guide/images/admin-credentials.png
  - docs/guide/images/admin-flash.png
---

## Decided
The guide's screenshots are retaken from a throwaway rehearsal copy. The admin is now a sidebar of pages (#health, #panels, ...), so the capture script opens a page by hash instead of expanding sections. Panels and Alerts shots are taken without the status override, with three fake panels checked in and three alert buttons seeded first.

## Tried and ruled out
Running the copy as the guide's README says: the compose file pins a container name, so it clashed with a real Relay already running on the machine. Changing it in the temp copy only (never the real one) fixed it.

## Watch out
The script's status override replaces the admin's live stream with a one-shot, which drops the panels frame, so the Panels page stays empty. Only pages that need the real stream (Panels, Alerts, Planning Center) must skip the override. The copy's image shares the real Relay's image name. The two flashing screenshots are taken by hand from a real flash and are not retaken.

In the code: docs/guide/tools/capture.mjs (openPage, seedPanels, adminShots), docs/guide/README.md.
