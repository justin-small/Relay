---
title: "A pushed release tag stays where it is; CI fixes land in a later PR, and only the timeouts were added"
author: Justin
date: 2026-11-11
branch: ci-step-timeouts
cost: { minutes: 17, tokens: 58880 }
id: l-71df3155
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/release.yml"
---

## Decided
v2.0.0 was tagged on main and its release run hung in an apt install (Ubuntu mirror DNS outage) until the 10 minute job limit. Justin chose to keep the tag as is, not move it onto a fixed workflow, and to add only step timeouts (no apt retry options, no runner pin to ubuntu-24.04). The fix went in a separate PR; re-running the tag's run uses the workflow as tagged.

## Tried and ruled out
Moving the v2.0.0 tag to a commit with a hardened workflow: rewrites a published release tag. Pinning ubuntu-24.04: the outage was not shown to come from the 26.04 image.

## Watch out
The tag's run will not have the new timeouts, so it can hang again if the mirrors are still down.

In the code: .github/workflows/release.yml (timeout-minutes: 3 on the Tests apt step, 5 on the guide's install step); PR 136.
