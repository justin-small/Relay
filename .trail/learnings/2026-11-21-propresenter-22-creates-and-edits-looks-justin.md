---
title: "ProPresenter 22 creates and edits looks, macros, messages, props, timers, themes and playlists through its own network API; only songs, playlists bundles and t…"
author: Justin
date: 2026-11-21
branch: propresenter-files-497
cost: { minutes: 5, tokens: 79332 }
id: l-9c19182f
scope: repo
status: active
src: agent
touches:
  - app/modules/propresenter/ppfile.py
  - app/modules/propresenter/pro7/LICENSE
  - app/modules/propresenter/pro7/README.md
  - app/modules/propresenter/pro7/lyric_slide.bin
  - app/modules/propresenter/pro7/pro7.desc
  - requirements-release.txt
  - requirements.txt
  - tests/fixtures/pp/exported-song.pro
  - tests/test_ppfile.py
---

## Decided
Justin, 2026-11-21, issue 497: looks, macros and the other settings are not file-imported. Relay makes and edits them through ProPresenter's network API, which has create, change and delete routes, and files are for songs only.

## Tried and ruled out
Writing looks or macros into the workspace database or as files: ProPresenter 22 has no file type for them (its document types are .pro, .proBundle, .proTheme, .proPlaylist bundles).

## Watch out
In the code: the JSON shapes are the API_v1_* messages in the vendored protos (proApiV1Looks.proto and its siblings). Proved live on 22.9: POST /v1/looks needs id.uuid (Relay makes it) and returns the look; PUT /v1/look/{id} renames and changes layers; DELETE /v1/look/{id} gives 204. Routes seen in ProCore but not yet tried: macro_collections, macro/{id}, messages, message/{id}, v1/timers, v1/prop_collections, theme/{id}/{slide}, clear/group, stage/layout_map, playlists, DELETE presentation/{uuid}. A macro's actions are only {type}, so macro contents may not be settable through the API.
Bibles: this Mac has no installed Bible (RVBibles/v2 is empty) and the protos have no Bible format; install one in ProPresenter before reading the format.
