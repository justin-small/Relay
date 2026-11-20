---
title: "Shared port, channel and route pickers: mockup first, then all six pages in one PR with a zero-baseline contract test"
author: Justin
date: 2026-11-20
branch: main
cost: { minutes: 3, tokens: 106518 }
id: l-682c71c0
scope: repo
status: active
src: agent
touches:
  - docs/ui-audit/shared-pickers-mockup.html
  - tests/test_ui_contract.py
  - tests/ui_contract_baseline.txt
  - app/static/relay-ui.js
---

## Decided
Justin, 2026-11-19, issue 546: stop after an HTML mockup and wait for approval before real code. When built, migrate all six listed pages (Network, Blackmagic, NDI, Audio bus, Intercom, Planning Center) in one PR. The UI contract test gets a zero baseline: any module script building its own port, channel or route select fails. Mockup goes in docs/ui-audit/.

## Tried and ruled out
- Building channel and route pickers now and the port picker later, or building all three against a stub: dropped in favour of mockup review first.
- Pickers plus two pilot pages, or pickers with no migration: dropped; the issue's "Done when" needs every page on the same control.
- Shrink-only baseline of existing copies: dropped for zero baseline.

## Watch out
Other mockups live in docs/mockups/, not docs/ui-audit/. Mockups must not name their own classes row, card or grow (shared in components.css).

In the code: docs/ui-audit/shared-pickers-mockup.html; tests/test_ui_contract.py and tests/ui_contract_baseline.txt; app/static/relay-ui.js.
