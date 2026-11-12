---
title: "Squash-merging stacked PRs conflicts on every file they share; keep the upper branch's version once main matches the lower branch"
author: Justin
date: 2026-11-12
branch: main
cost: { minutes: 1, tokens: 1434 }
id: l-d751764d
scope: repo
status: active
src: agent
---

## Decided
- Relay squash-merges PRs. After the bottom PR of a stack is squashed into main, the next PR conflicts (add/add) on every file both touched, because the squash dropped the shared history.
- First check that main's tree equals the lower branch's tip (`git diff` shows nothing). Then confirm the upper branch contains that tip (`git merge-base --is-ancestor`). If both hold, merge main into the upper branch (merge, never rebase or force-push), keep the upper branch's version of every conflicted file, and confirm the merge commit changed nothing. Then retarget the PR to main and squash it.

## Tried and ruled out
- Retargeting the next PR to main without merging main in first: it still conflicts, and its diff shows the lower PR's changes again.

## Watch out
- A merge commit added to the middle branch makes it no longer an ancestor of the branch above it. Check against its previous tip (`^1`) instead, which is still an ancestor.

In the code:
- git diff --stat origin/<lower> origin/main  (must be empty)
- git merge origin/main; git checkout --ours -- <conflicted files>; git diff --stat HEAD^1 HEAD  (must be empty)
- gh pr edit <n> --base main; gh pr merge <n> --squash
