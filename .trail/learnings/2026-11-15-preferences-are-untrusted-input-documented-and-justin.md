---
title: "Preferences are untrusted input: documented, and a test refuses a page that shows one as HTML"
author: Justin
date: 2026-11-15
branch: prefs-untrusted-288-289
id: l-03b3bbd1
scope: repo
status: active
src: agent
touches:
  - tests/test_prefs_render.py
  - docs/modules.md
  - app/prefs.py
  - ".github/pull_request_template.md"
---

## Decided
Justin chose for issues 288 and 289 (2026-11-14): document that any signed-in user or token can store any JSON
under any scope, so a module checks type and range and never lets a preference pick a host, path, URL, file name
or command argument; and add a guard test that fails when a page line sends a preference to an HTML sink.

## Tried and ruled out
- Docs only: offered, not chosen; nothing would stop the first page that renders prefs from using innerHTML.
- Typed readers in prefs (get_int with a range, get_choice): offered, not chosen.

## Watch out
The guard test reads one line at a time: a preference copied into a variable and then put in innerHTML on a
later line is not caught. Review and the PR checklist cover that.

In the code: tests/test_prefs_render.py (sinks: innerHTML, outerHTML, insertAdjacentHTML, document.write,
|safe, Markup), docs/modules.md "Per-user preferences", app/prefs.py docstring, .github/pull_request_template.md.
