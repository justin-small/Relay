---
title: "macOS and Windows tests are a separate CI job, because main requires the check named Tests"
author: Justin
date: 2026-11-13
branch: feature/state-folder-180
cost: { minutes: 1, tokens: 145440 }
id: l-e6282dfe
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/tests.yml"
  - requirements.txt
---

## Decided
The macOS and Windows test runs are their own job beside the Linux one, not a matrix on it. The Linux job keeps the name "Tests", which main's branch protection requires; a matrix would rename it to "Tests (ubuntu-latest)" and block merges. The new jobs are "Tests (macos-latest)" and "Tests (windows-latest)" and are not required yet. uvloop is skipped on Windows with a platform marker in requirements.txt.

## Tried and ruled out
A single OS matrix on the existing job: renames the required check.

## Watch out
The suite had never run on Windows before this; failures there may be real (permissions, file locking, paths) rather than CI noise. Once the new jobs are green, adding them to the required checks is a repository setting, not a file change.

In the code: .github/workflows/tests.yml (job `desktop`), requirements.txt (uvloop marker).
