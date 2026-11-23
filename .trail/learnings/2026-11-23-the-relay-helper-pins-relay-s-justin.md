---
title: "The Relay Helper pins Relay's self-signed certificate, installs per user to run at login, and ships as installers on the release"
author: Justin
date: 2026-11-23
branch: helper-releases-636
cost: { minutes: 8, tokens: 174712 }
id: l-0df016c6
scope: repo
status: active
src: agent
touches:
  - tools/release_tools.py
  - ".github/workflows/release.yml"
---

## Decided
Justin, 2026-11-22, issue 636, his answers: build everything buildable; ship unsigned installers (no signing certificates exist yet, the desktop installers are unsigned too); real installers (macOS .pkg for Apple silicon and Intel, Windows NSIS); start at login as the signed-in user (macOS launchd agent, Windows Run key); trust a Relay's own certificate by pinning its SHA-256 fingerprint (`relay-helper pair wss://... --pin`), the same idea as the desktop app's pinning proxy. Release assets have version-less names so the Helpers page links to releases/latest/download.
The helper pings every 20 s and drops a connection silent for 50 s, so a sleeping laptop reconnects; the hub itself never pings.
An unpaired helper started at login waits for `relay-helper pair` instead of exiting (launchd would restart it forever).

## Tried and ruled out
A Windows service: it runs apart from the desktop session, so `start` of a .pro could not reach ProPresenter's window.
Version-stamped installer names: they end in -setup.exe or .app.tar.gz style names tools/release_tools.py treats as updater artifacts needing a .sig; names also would break latest/download links.

## Watch out
Not yet proved on a real Windows PC with ProPresenter (the issue's last two boxes): the Windows installer, Run-key autostart and `cmd /C start` of a .pro are only compiled by CI, never run. ring cannot be cross-compiled to Windows from a Mac here, so only CI checks the Windows build.
In the code: native/relay-helper/src/{tls,autostart,client}.rs, packaging/, the `helper` job in .github/workflows/release.yml, /api/admin/helpers returns `fingerprint`.
