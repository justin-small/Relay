---
title: "Templates must render the same in Jinja2 and minijinja, checked against renders Python recorded"
author: Justin
date: 2026-11-23
branch: worktree-rippling-frolicking-mccarthy
cost: { minutes: 39, tokens: 183109 }
id: l-e46e9925
scope: repo
status: active
src: agent
touches:
  - tools/capture_pages.py
  - native/relay-server/src/pages.rs
  - native/relay-server/src/statics.rs
  - native/relay-server/tests/pages.rs
  - tools/capture_pages_all.py
  - native/relay-server/tests/pages_allowed.txt
  - app/main.py
  - docs/rust-server.md
---

## Decided
Justin, 2026-11-23, issue 654: the Rust server renders pages itself with minijinja, and parity is proved by recording what Python renders (template, context, HTML) and comparing byte for byte, with a reviewed allowlist (empty). Contexts come from recorded Python renders, not from live FastAPI calls, so Rust never depends on Python.

## Tried and ruled out
Forwarding to FastAPI for page context: keeps Python alive. minijinja's own escaping and tojson: they differ from Jinja2 (it writes `/` as `&#x2f;`, prints `none`, does not sort keys), so pages.rs replaces them.

## Watch out
Templates must not mutate lists (`list.append`): minijinja lists are immutable. admin.html was rewritten with an inline `if ... else none` and `reject('none')`. After a template or handler context changes, rerun tools/capture_pages.py. The hook's note about app/main.py is wrong: it was not changed. The cargo test failures were the unreachable crates mirror and an unfinished capture.

In the code: native/relay-server/src/pages.rs, native/relay-server/tests/pages.rs, tools/capture_pages.py, tools/capture_pages_all.py.
