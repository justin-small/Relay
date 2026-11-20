---
title: "Clean up branches by their pull request's merge state, not git cherry, because every PR is squash-merged"
author: Justin
date: 2026-11-16
branch: ci-step-timeouts
cost: { minutes: 28, tokens: 85338 }
id: l-b933ccb0
scope: repo
status: active
src: agent
touches:
  - ".github/workflows/guide.yml"
  - ".github/workflows/release.yml"
  - ".github/workflows/tests.yml"
  - README.md
  - app/modules/youtube/__init__.py
  - app/modules/youtube/routes.py
  - app/modules/youtube/service.py
  - app/modules/youtube/static/youtube.js
  - app/modules/youtube/templates/youtube.html
  - app/oauth.py
---

## Decided
- Justin, 2026-11-16: delete every branch, locally and on GitHub, whose work is already in main; give any unmerged work its own PR first; leave open PRs (Dependabot, the throwaway Windows installer draft) and their branches alone; remove worktrees holding merged branches, including ones in other sessions' scratch folders.
- The one unmerged change (time limits on the apt install steps, old PR #136) was re-applied on main and opened as PR 428.

## Tried and ruled out
- `git cherry origin/main <branch>` to find unmerged work: squash merges change patch ids, so nearly every merged branch looked unmerged. Matching each branch to its PR (`gh pr list --state all`, MERGED and no commits after the PR's head) was reliable.

## Watch out
- A branch can have commits after its PR merged (merges of main, or work that landed later by another PR). Check the files those commits touch against main before deleting.
- Deleting a list of branches in this shell: zsh doesn't word-split `$VAR`, so `git push origin --delete $R` passed one huge name and deleted nothing. Run it under `bash -c` or use `${=R}`.

In the code: gh pr list --state all --json headRefName,state,headRefOid; git push origin --delete; git worktree remove; .github/workflows/{release,tests,guide}.yml (PR 428).
