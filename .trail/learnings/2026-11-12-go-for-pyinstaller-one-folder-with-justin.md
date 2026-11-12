---
title: Go for PyInstaller one-folder with Python 3.15 as the Tauri sidecar; only Intel Macs need a cryptography pin change
author: Justin
date: 2026-11-12
branch: spike/freeze-179
cost: { minutes: 27, tokens: 40406 }
id: l-cb90d965
scope: repo
status: active
replaces: l-2cd0f764
src: agent
touches:
  - spike/RESULT.md
  - ".github/workflows/spike-freeze.yml"
  - spike/entry.py
  - spike/relay.spec
  - spike/smoke.py
  - run.py
---

## Decided
Issue 179 spike result: the backend freezes with PyInstaller 6.22 on Windows x64, macOS arm64 and Intel, Linux x64 and arm64, on Python 3.13, 3.14 and 3.15 (15 CI jobs green). Ship one-folder: start-up 0.5-1.6 s, 81-205 MB. One-file doubles start-up (extracts every launch, 0.9-3.9 s) and is what antivirus flags most. No Nuitka or embedded-runtime fallback needed.

## Tried and ruled out
- Building straight from run.py: uvicorn imports "app.main:app" by string, so PyInstaller never sees the app and the frozen binary says "Could not import module app.main".
- cryptography==50.0.0 on Intel Macs: no macosx_x86_64 wheel (48.0.1 has one). Loosen the pin with a platform marker, or ship Intel on 48.x.

## Watch out
- Module discovery (pkgutil.iter_modules) only finds modules if they are in the archive; the spec must put the repo root on sys.path so collect_submodules("app") sees them. Otherwise modules_found is empty with no error.
- cryptography needs an explicit hidden import; data ships at the same relative paths so ROOT resolves unchanged.
- uvloop has no Windows build; uvicorn falls back to asyncio and the frozen app serves fine.
- Linux needs libportaudio2 and a running PulseAudio server or audio init raises at import. CI runners have no mic, so real capture was only checked on macOS arm64. Unchecked: Windows antivirus, macOS mic-prompt attribution from a parent process.
- A Windows one-file smoke run 2 reported 0.00 s because the previous process still held port 8000; ignore that number.

In the code: spike/relay.spec, spike/entry.py (--probe), spike/smoke.py, spike/RESULT.md, .github/workflows/spike-freeze.yml on branch spike/freeze-179.
