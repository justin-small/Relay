---
title: "Installs look for updates at an address on Relay's own domain that redirects to the GitHub release, so moving the repository is one edit"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 5, tokens: 72848 }
id: l-371bf2e3
scope: repo
status: active
src: agent
touches:
  - desktop/src-tauri/src/updater.rs
  - desktop/src-tauri/tauri.conf.json
---

## Decided
Justin wanted a repository move to reach installed apps without anyone touching the machines. The built-in update address is now https://relaychurchsystems.com/updates/latest.json, a 302 from the marketing site's _redirects to the GitHub release's releases/latest/download/latest.json. Moving the repo means changing that one line and redeploying the site. The RELAY_UPDATE_URL and update_url overrides still win over it.

## Tried and ruled out
- mackenly/tauri-update-cloudflare (a Worker that builds the update reply from the GitHub API): documents only Tauri v1, its private-repo download proxy looks broken, and it rebuilds what release_tools.py already writes. Only worth it for a private repo or staged rollouts.
- A 301: clients may cache it and stay pinned to the old target.

## Watch out
- Installs built before this change still use the GitHub address; they learn the new one only by taking a release that contains it, so ship one before any move.
- If the domain lapses, installs stop finding updates.
- The latest release (v2.0.0) has no latest.json, so the redirect currently ends at a 404 until the first desktop release; automatic checks are silent about that.
- The redirect lives in site/, which is still uncommitted.

In the code: site/public/_redirects, desktop/src-tauri/src/updater.rs DEFAULT_URL, desktop/src-tauri/tauri.conf.json plugins.updater.endpoints. Deploy: cf deploy public from site/. Check: curl -sI https://relaychurchsystems.com/updates/latest.json
