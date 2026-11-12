---
title: "Issue 180 ships in slices, state folder first; a checkout's own config.json keeps being used"
author: Justin
date: 2026-11-12
branch: feature/state-folder-180
cost: { minutes: 5, tokens: 74999 }
id: l-47ca97ba
scope: repo
status: active
src: agent
touches:
  - app/paths.py
  - app/db.py
  - tests/test_paths.py
  - tools/write_config.py
  - app/config.py
---

## Decided
Justin chose to build issue 180 in slices, starting with the per-user state folder (requirements 1-4 and 12). Audio, LAN address detection, browser first-run and the three-OS CI matrix follow as separate PRs. A source checkout that already has config.json in the repo root keeps using it, so development setups keep working; a fresh checkout uses the per-user folder. Relative blocklist and recordings paths resolve to the state folder, not the repo root. For LAN detection Justin picked adding ifaddr or psutil over a stdlib-only approach.

## Tried and ruled out
Doing all 13 requirements in one PR: too large to review. Removing tools/write_config.py and setup.* now (Justin's answer): left in place for this slice, because they are the only first-run setup until the browser first-run page exists.

## Watch out
SQLite (#178) is merged, so relay.db already lands in the state folder. trail check flags a "panel config json" conflict at the new import in app/config.py; it is a false positive.

In the code: app/paths.py (state_dir, config_path, restrict), app/config.py (CONFIG_PATH, STATE_DIR, ensure_file, ensure_blocklist_file), app/db.py, tests/test_paths.py.
