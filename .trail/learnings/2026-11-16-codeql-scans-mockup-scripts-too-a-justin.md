---
title: "CodeQL scans mockup scripts too: a page chosen by the URL hash must be checked against a list first"
author: Justin
date: 2026-11-16
branch: redesign-admin-shell-spec
cost: { minutes: 21, tokens: 137335 }
id: l-e743e27b
scope: repo
status: active
src: agent
touches:
  - docs/mockups/admin-shell/index.html
  - docs/mockups/admin-shell/pages/README.md
  - docs/mockups/admin-shell/pages/core-live-output.js
  - docs/mockups/admin-shell/pages/core-settings.js
  - docs/mockups/admin-shell/pages/modules-av.js
  - docs/mockups/admin-shell/pages/modules-people.js
  - docs/mockups/admin-shell/pages/screens-crew.js
  - docs/mockups/admin-shell/pages/screens-display.js
  - docs/mockups/admin-shell/pages/screens-operator.js
  - docs/redesign/admin-shell.md
---

## Decided
Scripts under docs/ are scanned by CodeQL like app code, and it is a required check. The mockup router took a page id from location.hash and called P[id](); CodeQL raised js/unvalidated-dynamic-method-call. The fix is to look the id up in the page registry (REG.find) and use the registry's own id, plus a typeof === 'function' check, so a hash like #constructor falls back to the first page.
In the code: docs/mockups/admin-shell/index.html (route()), found on PR 435.

## Watch out
Any new mockup or docs script that dispatches on user-controlled text (hash, query string) needs the same allow-list before the call. Run tests/run_security.py before pushing; it does not run CodeQL, so read the PR's code-scanning alerts after the first push.
