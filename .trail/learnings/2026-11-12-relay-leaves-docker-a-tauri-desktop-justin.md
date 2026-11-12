---
title: "Relay leaves Docker: a Tauri desktop app runs the same Python backend, with SQLite instead of Postgres"
author: Justin
date: 2026-11-12
branch: users-rbac
cost: { minutes: 6, tokens: 63626 }
id: l-7188d861
scope: repo
status: active
replaces: l-2ff74757
src: agent
touches:
  - app/db.py
  - app/audio.py
  - run.py
---

## Decided
- Justin is dropping Docker completely because of its constraints: the PulseAudio bridge for audio on Mac and Windows, the container not being on the LAN, and the install friction of Docker Desktop plus setup and start scripts.
- Windows and Mac get a Tauri desktop app. Tauri is only a shell that launches the existing Python backend as a bundled sidecar; the Python app is not rewritten and there is one codebase.
- The database becomes SQLite. This reverses the earlier choice of Postgres, which Justin made when everything ran in one container; without a container, bundling a Postgres server was not worth it.
- Linux users run the Tauri app on a desktop, or the Python backend directly as a service on a headless machine.
- Phones (Android and iOS) will only ever be a remote-control app for a Relay running on a computer. That is deferred.
- Releases are direct downloads from GitHub, not app stores. Justin will buy the Apple and Windows signing credentials when the app is ready.

## Tried and ruled out
- Running Relay itself on a phone: Tauri has no sidecar support on mobile, iOS forbids child processes and suspends background servers, and the audio and numeric Python packages have no practical iOS builds.
- Keeping Docker for Linux only: it would not have been a second codebase, only a second packaging path, but Justin preferred to drop it entirely.
- Bundling Postgres binaries in the app: about 100 MB and major-version upgrades to manage.

## Watch out
- Leaving the container gives up its read-only root, dropped capabilities and unprivileged user; the app runs as the logged-in user.
- The other decisions in the user-accounts learning (roles, permissions, sign-in) still stand; only its database choice is reversed.
- Nothing was built or prototyped when this was decided, so freezing the backend with PyInstaller on Python 3.15 is unproven.

In the code:
- app/db.py is the Postgres layer (psycopg) that moves to SQLite.
- docker/, the setup/start/stop/logs scripts and the pulsectl path in app/audio.py are what goes away.
- run.py is the entry point the Tauri shell would launch.
