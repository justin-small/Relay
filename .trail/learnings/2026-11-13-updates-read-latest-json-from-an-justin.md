---
title: "Updates read latest.json from an address an install can be re-pointed at, and only the operator's click installs"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 5, tokens: 149918 }
id: l-4a36463e
scope: repo
status: active
src: agent
touches:
  - desktop/src-tauri/src/updater.rs
  - ".github/workflows/release.yml"
---

## Decided
Justin's top requirement on #184 was that the update address can move with the repository. The shell takes it from RELAY_UPDATE_URL, then `update_url` in shell.json, then the built-in GitHub default; either override accepts a full URL or owner/repo. Installing is only ever the operator's click (tray item or the banner on the panel), with one extra confirm while captions run; the backend is quit gracefully before the installer replaces files. The panel has no Tauri IPC, so its banner calls /__shell/update/install on the loopback proxy with a per-run token that only the injected script holds. Release builds run the Tauri CLI directly (not tauri-action) and tools/release_tools.py writes latest.json, so the release still waits on tests, guide and firmware.
## Tried and ruled out
tauri-action's own latest.json upload: it wants to create the release itself, which would bypass the existing gate on tests, guide and panel firmware. createUpdaterArtifacts true in tauri.conf.json: local builds would fail without the key, so CI turns it on for tags only.
## Watch out
Nothing here has run on real machines: not the installers, an N to N+1 update, a tampered update, or the NSIS same-version sidecar issue (tauri-apps/tauri#15134). The updater key pair was generated locally in ~/.relay-release-keys; the private half must go in the TAURI_SIGNING_PRIVATE_KEY repository secret. In the code: desktop/src-tauri/src/updater.rs, tools/release_tools.py, .github/workflows/release.yml.
