---
title: "Freezing the backend with PyInstaller works on macOS arm64 with Python 3.15, but needs three build fixes"
author: Justin
date: 2026-11-12
branch: spike/freeze-179
cost: { minutes: 8, tokens: 80690 }
id: l-2cd0f764
scope: repo
status: active
src: agent
touches:
  - ".dockerignore"
  - ".github/workflows/tests.yml"
  - README.md
  - app/db.py
  - app/main.py
  - app/migrations/0001_users.sql
  - app/users.py
  - docker/Dockerfile
  - docker/docker-compose.yml
  - docker/entrypoint.sh
---

## Decided
Spike for issue 179 (Tauri sidecar): PyInstaller one-folder on macOS arm64, Python 3.15, gives a 60 MB build that starts in about 0.3 s, serves pages, lists audio inputs and captures from the mic. Every pin in requirements.txt has a cp315 macOS arm64 wheel. Windows, macOS Intel, Linux and the 3.14 fallback were not tested.

## Tried and ruled out
Building straight from run.py: the frozen app failed with "Could not import module app.main", because uvicorn imports the app by string and PyInstaller cannot see that.

## Watch out
Three things the freeze needs, none in app code yet:
- the entry script must import app.main itself
- the spec must put the repo root on sys.path so collect_submodules("app") finds the module packages; otherwise module discovery returns nothing
- cryptography needs an explicit hidden import
Data files must be shipped at the same relative paths (app/templates and so on) so ROOT keeps working when frozen. The macOS mic permission prompt from a parent process is unchecked.

In the code: spike/relay.spec, spike/entry.py (branch spike/freeze-179, uncommitted); `entry.py --probe --capture` checks modules, data paths, tzdata and audio.
