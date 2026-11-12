---
title: An unpublished release tag may be moved to current main so the release includes late merges; check main first
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 1, tokens: 76152 }
id: l-088a88f5
scope: repo
status: active
replaces: l-71df3155
src: agent
touches:
  - firmware/relay-display/main/caption_view.cpp
  - firmware/relay-display/main/service_view.cpp
  - ".github/workflows/release.yml"
---

## Decided
v2.0.0 was first tagged on e0cb83e, but #137 (panel flicker fix) merged right after, so the release would have lacked a panel firmware fix. Justin wanted the release run to include the latest main, so the tag was force-moved to 9c4880e. This was fine because no GitHub release existed yet (gh release view v2.0.0: not found). This replaces the earlier "keep the tag where it is" decision: it only holds once a release has been published from the tag.

## Tried and ruled out
Re-running the PR's run or the old tag run: neither can put main's newer commits into a tag build. Moving the tag onto the timeouts-only branch: that branch (PR 136) is not on main.

## Watch out
Run git fetch and look at origin/main before tagging; main moved between the tag and the release. The moved tag does not include PR 136's apt step timeouts until that PR merges and the tag is moved again; the apt mirror DNS outage can still hang the Tests job to the 10 minute limit.

In the code: .github/workflows/release.yml (tag trigger v*); PR 136; git tag -f v2.0.0 <sha> && git push --force origin v2.0.0.
