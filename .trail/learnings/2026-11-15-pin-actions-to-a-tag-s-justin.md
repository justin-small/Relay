---
title: "Pin actions to a tag's commit, and run zizmor with a GitHub token, or CI finds what the laptop missed"
author: Justin
date: 2026-11-15
branch: security/scans
id: l-844d7a0b
scope: repo
status: active
src: agent
touches:
  - tests/run_security.py
  - ".github/workflows/security.yml"
---

## Decided
Every workflow action is pinned by commit SHA with a version comment. For dtolnay/rust-toolchain the pin is the v1 tag's commit, with `toolchain: stable` passed as an input.

## Tried and ruled out
Pinning that action to the head of its `stable` branch with a `# v1` comment: pinact refuses a branch ref, and zizmor's online check says the comment does not match the commit, which failed the Security job in CI.

## Watch out
- zizmor runs offline without a token and then skips the version-comment check. Run `GH_TOKEN=$(gh auth token) python tests/run_security.py` to match CI.
- A new pin must be the commit the tag points at (`gh api repos/<owner>/<repo>/commits/<tag> --jq .sha`), not a branch head.

In the code: .github/workflows/security.yml, tests.yml, release.yml (rust-toolchain steps), tests/run_security.py.
