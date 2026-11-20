---
title: "Python tests are kept honest by a repo contract test with a shrink-only baseline, not a third-party linter"
author: Justin
date: 2026-11-18
branch: alerts-patchbay
cost: { minutes: 5, tokens: 63244 }
id: l-4350ff77
scope: repo
status: active
src: agent
touches:
  - tests/test_honest_tests.py
  - AGENTS.md
  - tests/conftest.py
---

## Decided
Justin, 2026-11-18, after trying interfere-inc/landmines (an oxlint plugin for JS tests): build a Python counterpart as a Relay contract test, the same shape as the UI and style contracts. It counts, per test file, fixed sleeps, unittest.mock / monkeypatch, hand-written swaps of a module's functions, wait_for/await_for timeouts above 5 s, and loops that catch AssertionError and retry. Counts may only go down. A use that is right is marked on its line: `# allow-<rule>: <reason>`.

## Tried and ruled out
- pytest plugins (pytest-test-categories, pytest-never-sleep): Relay's tests are plain scripts, not pytest tests, so they would not plug in.
- ruff: there are no custom rules, and banned-api cannot tell a seam from a swap or count a ratchet.
- landmines on tests/js: its five testing rules found nothing. Its no-comments and test-file-names rules fight the repo's layout. It was not added (no package.json in the repo).
- Counting every attribute assignment on an imported name: 296 hits, about half of them state set to a literal or the original put back. Those are no longer counted.

## Watch out
- A top-level `def test_*` in a script makes tests/conftest.py collect it as a pytest module, which would run main() at import. The helper is named scanned_files() for that reason.
- unittest.mock's patch.dict(os.environ, ...) is configuration and is not counted. UPPER_CASE constants are tuning values and are not counted either.

In the code: tests/test_honest_tests.py, tests/honest_tests_baseline.txt (369 uses when it came in: 117 sleep, 83 mock, 167 patch, 2 timeout), AGENTS.md "Tests wait on events and run real code".
