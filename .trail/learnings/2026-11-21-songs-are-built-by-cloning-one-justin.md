---
title: "Songs are built by cloning one slide ProPresenter exported, with message classes made at run time from a descriptor file"
author: Justin
date: 2026-11-21
branch: propresenter-files-497
cost: { minutes: 4, tokens: 71631 }
id: l-ac9a04d0
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
  - requirements-release.in
---

## Decided
Justin, 2026-11-21, issue 497: Relay builds songs from a descriptor set compiled from the MIT protos, loaded when needed, and clones one lyric slide ProPresenter itself exported with its text emptied. Hand-built slide elements were not tried: the clone keeps ProPresenter's own fonts, margins and shadow, and it imported and displayed correctly on 22.9.

## Tried and ruled out
Committing protoc-generated Python: it names the protobuf version that made it and breaks on an older runtime.
A fixture song with real lyrics: replaced by placeholder text, since worship lyrics are copyrighted.

## Watch out
In the code: rebuild pro7.desc as described in pro7/README.md; slide text is RTF, text goes after the strokec0 marker. Adding protobuf means regenerating requirements-release.txt with uv (the command is in requirements-release.in).
Driving ProPresenter's Open dialog needs full-screen control: background typing is ignored by the panel.
