---
title: "Pull requests here merge only after the required checks pass, and the repository has auto-merge turned off"
author: Justin
date: 2026-11-12
branch: overlay-builder-139
cost: { minutes: 1, tokens: 1291 }
id: l-1b828ac3
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - README.md
  - app/config.py
  - app/main.py
  - app/overlays.py
  - app/static/admin.js
  - app/static/fonts/OFL-atkinson-hyperlegible.txt
  - app/static/fonts/OFL-source-serif-4.txt
  - app/static/fonts/VERSION
  - app/static/fonts/atkinson-hyperlegible-latin-700-normal.woff2
---

## Decided
- To merge a pull request in this repository, wait for its required checks to go green, then squash-merge it. Bypassing with admin rights is the owner's call each time.

## Tried and ruled out
- Merging straight after opening the pull request (Nov 12, #171): main's branch protection refused it while checks were pending.
- Turning on GitHub auto-merge to land it when checks pass: the repository does not allow auto-merge.

## Watch out
- The tests passing locally on a Mac is not the same as CI, which runs them on Linux with PulseAudio.

In the code: `gh pr merge <n> --squash --delete-branch` after checks pass; `--auto` fails ("Auto merge is not allowed for this repository"); `--admin` bypasses protection. CI is .github/workflows/tests.yml.
