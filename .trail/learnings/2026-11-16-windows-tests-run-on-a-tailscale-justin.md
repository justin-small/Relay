---
title: "Windows tests run on a Tailscale PC over SSH by syncing the working tree, not through git"
author: Justin
date: 2026-11-16
branch: lighting-cue-pages-379
cost: { minutes: 8, tokens: 32510 }
id: l-9cb2b0ac
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/modules/__init__.py
  - app/modules/lighting/__init__.py
  - app/modules/lighting/routes.py
  - app/modules/lighting/service.py
  - app/modules/lighting/static/lighting-buttons.js
  - app/modules/lighting/static/lighting.js
  - app/modules/lighting/static/screen.css
  - app/modules/lighting/static/screen.js
  - app/modules/lighting/templates/screen.html
---

## Decided
Justin has a Windows PC on Tailscale (desktop-3f1897k, SSH alias relay-win, user justin) for running the suite on Windows before CI. tests/run_windows.py tars the files git knows about (tracked plus untracked-not-ignored, so uncommitted work is included) into a fresh %USERPROFILE%\relay-test\src, keeps a Python 3.15 venv beside it that reinstalls only when requirements.txt changes, and runs tests/run_all.py (plus node tests with --js). Rust is not set up there: CI runs cargo test on macOS only.

## Tried and ruled out
Tailscale SSH: its SSH server does not run on Windows, so the PC uses Windows' OpenSSH Server with key login.
`powershell -Command -` with the script on stdin: PowerShell reads stdin line by line and silently drops multi-line blocks (no output, exit 0). The script is sent with -EncodedCommand instead.

## Watch out
The SSH shell on the PC is cmd: %VAR% expands when the line is read, so `set X=... & use %X%` on one line uses the old value. Admin accounts' keys live in C:\ProgramData\ssh\administrators_authorized_keys (Administrators and SYSTEM only), not ~\.ssh. winget is not on PATH over SSH; call %LOCALAPPDATA%\Microsoft\WindowsApps\winget.exe.
test_propresenter.py's "the slide's tags and what it resolved to are shown" failed once on Windows under the full parallel run (got 2, want 3) and passed three times alone: a timing race, not a Windows bug.

In the code: tests/run_windows.py; ~/.ssh/config host relay-win.
