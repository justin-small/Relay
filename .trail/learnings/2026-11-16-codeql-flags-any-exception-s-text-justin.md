---
title: "CodeQL flags any exception's text reaching a page, even a fixed sentence passed through str(e), so record the sentence on the object before raising"
author: Justin
date: 2026-11-16
branch: unifi-module-358
id: l-208fe119
scope: repo
status: active
src: agent
touches:
  - app/modules/unifi/controller.py
  - tests/test_unifi.py
---

## Decided
PR 438, 2026-11-16: CodeQL's "information exposure through an exception" fired on five admin routes, because the service stored `str(e)` from the module's own `Problem` exceptions, even though every one carried a sentence we wrote. The fix: the client sets its own `error` text (and `untrusted_fp`) before it raises, and the service reads those attributes, never the exception.

## Watch out
CodeQL also flags a test's server-side `ssl.SSLContext(PROTOCOL_TLS_SERVER)` with no minimum version (TLS 1.0 and 1.1 allowed). Set `minimum_version = TLSVersion.TLSv1_2` even in tests. Bandit and the local scans catch neither; only CodeQL on the PR does.

In the code: app/modules/unifi/controller.py (Controller._fail, .error, .untrusted_fp), service.py (check, extras, _events), tests/test_unifi.py.
