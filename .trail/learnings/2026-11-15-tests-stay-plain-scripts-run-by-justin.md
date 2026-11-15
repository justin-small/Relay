---
title: "Tests stay plain scripts run by a parallel runner, and CI-only failures come from Node 22, PulseAudio and CodeQL's password names"
author: Justin
date: 2026-11-15
branch: tests/harness-and-ci
cost: { minutes: 7, tokens: 49652 }
id: l-d3696049
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/tests.yml"
  - AGENTS.md
  - README.md
  - tests/test_auth.py
  - tests/test_users.py
  - ".coveragerc"
  - app/main.py
  - app/static/admin.js
  - firmware/relay-display/pytest.ini
  - pytest.ini
---

## Decided
Test files stay plain scripts, each run in its own process (shared checks in one harness, a runner that runs them in parallel). pytest only wraps each script as one item. Importing the scripts into one pytest process would run every check at collection time and share one database and config between files. Tests are moved to pytest fixtures one file at a time, if at all.

## Tried and ruled out
Passing a folder to `node --test`: it works on a newer local Node but fails on CI's Node 22. Pass the files.

## Watch out
- Any CI job that imports the app needs PulseAudio running, because sounddevice fails to import without it. A Mac never shows this.
- CodeQL follows any value named like a password (must_change_password, PASSWORD_SESSION_SECONDS, an API key) into the shared harness's print. Compare those values with check(a == b) instead of eq(...). To find which checks CodeQL means, download the analysis as SARIF and read each result's codeFlows; the alert page doesn't list them.

In the code: tests/_harness.py, tests/run_all.py, tests/conftest.py, .github/workflows/tests.yml; SARIF via `gh api -H "Accept: application/sarif+json" repos/<owner>/<repo>/code-scanning/analyses/<id>`.
