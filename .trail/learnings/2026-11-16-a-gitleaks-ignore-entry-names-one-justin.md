---
title: "A gitleaks ignore entry names one commit, so a squash merge needs a second entry, and follow-up commits pushed after the merge are lost"
author: Justin
date: 2026-11-16
branch: intercom-watchdog-276
cost: { minutes: 1, tokens: 8767 }
id: l-5db4a54b
scope: repo
status: active
src: agent
touches:
  - ".gitleaksignore"
  - app/secretbox.py
  - tests/test_secrets.py
---

## Decided
- PR #403 was squash-merged with only its first commit; the CodeQL fix and the gitleaks ignore that came after it were never on main, so main's Security check failed. They were cherry-picked onto the next branch (issue 276) on Justin's say-so.

## Watch out
- .gitleaksignore fingerprints are commit:file:rule:line. When a branch is squash-merged, main gets a new commit with the same finding, so the branch commit's entry does not cover it: add the squash commit's fingerprint too.
- Push every follow-up commit before merging, and check the PR's commit list matches the branch.

In the code:
- .gitleaksignore: f2fcea9 (branch) and 8eef503 (squash on main) for tests/test_secrets.py:generic-api-key:120
- Find fingerprints: gitleaks git --report-format json --report-path <file> .
