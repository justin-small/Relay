---
title: "Pull requests skip the Intel Mac installer and guide-only changes build only the guide, to keep CI near 3 minutes"
author: Justin
date: 2026-11-16
branch: ci/faster-prs
id: l-7ce0577c
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/guide.yml"
  - ".github/zizmor.yml"
---

## Decided
The 14+ minute pull request waits were the Release workflow's Intel Mac installer (7 to 15 minutes on macos-15-intel), triggered by any change to the guide or desktop/. Tests itself takes about 3 minutes. Pull requests now build Windows, Apple silicon and Linux only; the Intel Mac is built on tags. A guide-only change runs its own small workflow. cargo-deny and cargo-audit are installed prebuilt instead of compiled (about 2 minutes each).

## Tried and ruled out
Dropping the Intel Mac with a matrix `exclude`: an `include` entry that no longer matches a combination is added back as a new one, so the exclude does nothing. A job-level `if` cannot read `matrix` either. The include list is built with fromJSON(format(...)) instead, with the Intel entry added only when ref_type is tag.

## Watch out
An Intel-only build break shows up first on a tag; push an -rc tag before a real release. zizmor flags the local `uses: ./.github/workflows/guide.yml` (self-repository); it is ignored for release.yml in the zizmor config.

In the code: .github/workflows/guide.yml (pull_request + workflow_call), release.yml desktop matrix `include: >- ${{ fromJSON(format('[{0}{1}]', ...)) }}`, security.yml and tests.yml use taiki-e/install-action v2.88.1 (68a389c7) with cargo-deny@0.20.2 / cargo-audit@0.23.0, .github/zizmor.yml self-repository ignore.
