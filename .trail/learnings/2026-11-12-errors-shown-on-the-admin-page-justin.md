---
title: "Errors shown on the admin page are fixed sentences looked up by key, never an exception's text, or CodeQL flags them"
author: Justin
date: 2026-11-12
branch: codeql-error-messages
cost: { minutes: 22, tokens: 52088 }
id: l-f25214cf
scope: repo
status: active
src: agent
touches:
  - app/modules/planning_center/__init__.py
  - app/modules/planning_center/planningcenter.py
  - app/modules/planning_center/routes.py
  - app/modules/propresenter/pclink.py
  - app/modules/propresenter/routes.py
  - tests/test_planningcenter.py
  - README.md
  - app/config.py
  - app/engine.py
  - app/main.py
---

## Decided
- A route answering an error sends a fixed sentence chosen by the error's kind, key or status (a dict lookup or a small function), and logs the exception itself. CodeQL's "information exposure through an exception" check fails on `str(exc)` reaching a response, even when the text was written to be safe.
- Existing errors follow this: ProPresenter by kind (`_pp_error`), the lobby and the Planning Center link by `exc.key` into their MESSAGES, Planning Center by `operator_message(exc)`.

## Watch out
- Moving code into a new file makes CodeQL treat old `str(exc)` responses as new alerts: that is how #168/#169 picked up alerts #25 to #27.
- A sentence built from an exception attribute is still flagged; numbers through int() and dict lookups by key are not.

In the code:
- app/modules/planning_center/planningcenter.py operator_message; app/modules/propresenter/pclink.py and pplobby.py MESSAGES; app/modules/propresenter/routes.py _lobby_run, _link_run
