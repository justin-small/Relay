---
title: A bad update is undone by restoring a copy of the app and settings on the next launch; the Linux desktop build runs on Ubuntu 24.04
author: Justin
date: 2026-11-13
branch: desktop-installers-updates-184
cost: { minutes: 4, tokens: 41826 }
id: l-b96df57d
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
  - ".github/dependabot.yml"
  - ".github/release-notes.md"
  - desktop/README.md
  - desktop/src-tauri/Cargo.lock
  - desktop/src-tauri/Cargo.toml
  - desktop/src-tauri/src/main.rs
  - desktop/src-tauri/src/paths.rs
  - desktop/src-tauri/src/proxy.rs
  - desktop/src-tauri/src/rollback.rs
---

## Decided
Justin asked that an update take a backup and fall back on its own. Before the installer runs, the app and the state folder (not recordings or logs) are copied into backups/ in the state folder; if the copy fails nothing is installed. The new version is on trial until its backend reports ready. A backend still in error after 30 seconds, or three launches without ever getting ready, flags the marker and restarts; the next launch restores before anything starts, so no file is in use. On Windows a detached PowerShell script does the copy-back after the process exits. The Linux desktop build uses ubuntu-24.04.
## Tried and ruled out
Ubuntu 22.04 for the AppImage's older glibc: orjson 3.13.0 has no Python 3.15 wheel there, so freezing the backend fails (same cause as the Docker image's move to trixie). Keeping the old installer to reinstall: no old installer is on disk, and a moved update URL cannot be used to fetch one.
## Watch out
The first install of a version with this code cannot protect itself. The app-file swap and the Windows script have not run on real machines. A rollback loses settings changed after the update. In the code: desktop/src-tauri/src/rollback.rs, .github/workflows/release.yml.
