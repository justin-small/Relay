---
title: "Merge stacked pull requests one at a time: retarget the next one to main before deleting a merged branch, then rebase and push it so main's checks run"
author: Justin
date: 2026-11-16
branch: network-routing
cost: { minutes: 6, tokens: 22096 }
id: l-cb5a7f1b
scope: repo
status: active
src: agent
touches:
  - app/config.py
  - app/main.py
  - app/modules/__init__.py
  - app/netroute.py
  - docs/modules.md
  - tests/test_modules.py
  - tests/test_netroute.py
  - app/netinfo.py
  - tests/test_netinfo.py
---

## Decided
Stacked PRs (405 → 409, Nov 16) merge bottom-up with squash, like every PR here. After a merge, the next PR is pointed at main and its own commits rebased onto main (git rebase --onto origin/main <old tip of the merged branch> <branch>) and force-pushed with a lease; the push is what reruns CI.

## Tried and ruled out
gh pr merge --delete-branch on the bottom PR: deleting the base branch CLOSED the next PR instead of retargeting it (the repo does not delete branches on merge, so GitHub did not retarget). A closed PR cannot be retargeted; the branch had to be pushed back from its recorded SHA, the PR reopened, retargeted, and only then the branch deleted.

## Watch out
A PR whose base is not main runs about 7 checks; into main it runs 13 (security and CodeQL only run for main). Changing a PR's base does not rerun workflows (pull_request fires on synchronize, not edited), so push after retargeting or the required checks stay missing. Record every branch tip before the first merge.

In the code: .github/workflows (pull_request triggers); gh pr edit <n> --base main; git push --force-with-lease=<branch>:<old sha>.
