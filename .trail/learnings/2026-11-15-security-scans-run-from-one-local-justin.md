---
title: "Security scans run from one local script that CI also calls; CodeQL stays on GitHub's default setup"
author: Justin
date: 2026-11-15
branch: tests/harness-and-ci
cost: { minutes: 7, tokens: 98353 }
id: l-b770a98f
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/firmware.yml"
  - ".github/workflows/release.yml"
  - ".github/workflows/spike-freeze.yml"
  - ".github/workflows/tests.yml"
  - app/modules/planning_center/signagesync.py
  - app/modules/propresenter/ppcontrol.py
  - app/modules/propresenter/signagesync.py
  - app/modules/propresenter/slidetags.py
  - app/schedules.py
  - tests/test_users.py
---

## Decided
Justin (2026-11-15) wanted bandit, gitleaks, actionlint, zizmor and cargo-deny in CI and runnable locally before a push. One script runs them; the Security workflow installs the tools and calls it with --strict, so a laptop and CI agree. Existing bandit findings are a baseline (medium and up), so only new ones fail. Actions are pinned by commit SHA with a version comment. dependency audits stay in tests.yml.

## Tried and ruled out
A CodeQL workflow: the repository already has CodeQL default setup (Python, JS, Rust, Actions, C++), and an advanced workflow would conflict with it. CodeQL has no local run here.

## Watch out
- The old Docker panel's TLS private key is still in public git history (added fd81f66, removed 514102e). It is accepted in .gitleaksignore only so new secrets can fail the scan; treat the key as exposed.
- release.yml's cache-poisoning finding is ignored in .github/zizmor.yml for build time; disabling caches on tag builds would close it.
- The 26 baselined bandit findings (string-built SQL, urlopen) were not reviewed.
- gitleaks and actionlint downloads in CI are checked against sha256 values; update them with the versions.

In the code: tests/run_security.py, .github/workflows/security.yml, security/bandit-baseline.json (`--rebaseline`), .gitleaksignore, .github/zizmor.yml, desktop/src-tauri/deny.toml.
