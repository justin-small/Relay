---
title: "UniFi is tested against the church's real controller from a git-ignored credentials file before anything is built on it"
author: Justin
date: 2026-11-16
branch: unifi-module-358
id: l-a0b3c80e
scope: repo
status: active
src: agent
touches:
  - docs/mockups/admin-shell/index.html
---

## Decided
Justin, 2026-11-16, issue 358: the UniFi connection is proven on the real controller before building on it. He fills in a git-ignored credentials file (a read-only API key plus a local read-only user), and a spike tool reads every endpoint, fixes what does not match, and reports which features the controller supports. The crew Wi-Fi check-in waits in a stash until then.

## Tried and ruled out
Building the client from the reference projects and moving straight to crew check-in without running it: Justin rejected that, because endpoints vary by firmware.

## Watch out
Tell the user the full path of any file they must fill in. A worktree outside the session cannot be opened in their editor, so give the path and an `open` command. Never print the key or the password.

In the code: unifi-test.env (git-ignored, beside planningcenter-test.env in .gitignore) with UNIFI_HOST, UNIFI_PORT, UNIFI_SITE, UNIFI_API_KEY, UNIFI_USERNAME, UNIFI_PASSWORD; spike tool tools/check_unifi.py.
