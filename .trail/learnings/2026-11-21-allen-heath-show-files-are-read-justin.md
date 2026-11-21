---
title: "Allen & Heath show files are read in memory from the live scene, and only formats checked on real exports are accepted"
author: Justin
date: 2026-11-21
branch: claude/strange-saha-5154e5
cost: { minutes: 10, tokens: 218753 }
id: l-16e09978
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/modules/audio_mixers/__init__.py
  - app/modules/audio_mixers/routes.py
  - app/modules/audio_mixers/service.py
  - app/modules/audio_mixers/showfile.py
  - app/modules/audio_mixers/static/audio_mixers.js
  - app/modules/audio_mixers/templates/audio_mixers.html
  - tests/fixtures/ah/avantis-bridge-3.tar.gz
  - tests/fixtures/ah/avantis-bridge-4.tar.gz
  - tests/test_ah_showfile.py
---

## Decided
Patch reading takes the whole exported show, reads only Show/Version.dat and the live scene (StageBoxScene65535) in memory, and refuses any format version or patch-record count it was not checked against. Avantis Bridge-2 to Bridge-4 are verified on real exports; dLive follows published notes and is marked unverified. Qu is refused until a real sample exists. Bridge-5 (Avantis firmware 2.x) is refused too, because its output records shift and no fixture was available.

## Tried and ruled out
Guessing a scene file alone: it carries no firmware version, so it is refused. Committing the real shows as fixtures: the repo is public and they hold staff first names, so the fixtures are trimmed and renamed.

## Watch out
Names are 9-byte slots with leftover garbage after the NUL; the reliable patch is the Channel Mapper (type + big-endian index). The user's own decode lives in "board configs/AllenHeath-Avantis/Shows/Show/Findings.md" and Channel_Routing.csv, and matches this reader.

In the code: app/modules/audio_mixers/showfile.py, tests/fixtures/ah/, tests/test_ah_showfile.py.
