---
title: "Run every test file before pushing, not just the ones for the area changed"
author: Justin
date: 2026-11-14
branch: signage-propresenter-208
cost: { minutes: 1, tokens: 5938 }
id: l-39b16d3f
scope: repo
status: active
src: agent
touches:
  - AGENTS.md
  - tests/smoke_test.py
  - ".github/workflows/tests.yml"
---

## Decided
Before each push, run all of tests/test_*.py and tests/smoke_test.py, because CI runs all of them on three platforms and fails the pull request if any one fails.
## Tried and ruled out
Running only the tests for the area changed: on issue 208 that missed a failure in the auth test, and CI failed on all three platforms because of it.
## Watch out
The module contract test can pass while another test fails on the same change.
In the code: AGENTS.md section "Run the whole suite before every push" has the one-line loop; .github/workflows/tests.yml shows what CI runs.
