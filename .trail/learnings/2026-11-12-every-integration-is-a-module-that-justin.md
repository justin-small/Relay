---
title: "Every integration is a module that can be switched off, enforced by a CI test; hooks are attached separately from background work"
author: Justin
date: 2026-11-12
branch: modules-157-propresenter
cost: { minutes: 31, tokens: 454356 }
id: l-506bd298
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/config.py
  - app/engine.py
  - app/main.py
  - app/modules/__init__.py
  - app/modules/planning_center/__init__.py
  - app/modules/planning_center/static/planningcenter.js
  - app/modules/propresenter/__init__.py
  - app/modules/propresenter/pclink.py
  - app/modules/propresenter/plmatch.py
---

## Decided
- Justin (2026-11-12, issue 157): every integration with another product must be a module in app/modules/<id>/ that the operator switches on and off on the admin Modules page. Modules ship inside the signed Docker image only; nothing is downloaded at run time.
- Enforced, not just written down: tests/test_modules.py runs on every pull request (new Tests workflow) and fails if core imports a module, a new .py lands in app/ outside app/modules/ without being listed as core, core imports a network client, or a module's routes answer while it is off. Also AGENTS.md (CLAUDE.md points to it), docs/modules.md and a PR template checkbox. Justin did not want a trail always-follow learning for this.
- Upgrade: a module with no saved switch starts on only if its integration was already set up (Module.configured), and settle() writes that switch once at startup so setting it up later never turns it on by itself.
- Settings stay under their old config keys (planning_center, propresenter); only the switch lives under "modules".
- Core reaches a module only through the registry: status(), stream() for /stream names it declares, and hooks the module sets in attach() and clears in detach() (panel lyrics/timers, followed service types, alert on stage, tag actions).

## Tried and ruled out
- Setting hooks inside start(): the test suites never run startup, so every hook was missing. Split into attach()/detach() (sync hooks) and start()/stop() (background work); tests call modules.attach(id).
- Detaching hooks at shutdown: starlette's TestClient runs the app's lifespan around requests even without `with`, so shutdown wiped hooks mid-test. stop_all() stops work but keeps hooks; only switching a module off detaches.

## Watch out
- A module's tests must switch it on in config ("modules": {"<id>": {"enabled": true}}) and call modules.attach("<id>"), or its routes answer 404 and its hooks are core's defaults.
- A module cannot import app.main (main imports the registry). What its routes need from the web layer is in app/modules/__init__.py: require_admin, read_limited, sse, event_stream, HEARTBEAT_S, templates.
- Nav links for module pages come from the module_links(group) macro calls in admin.html; deleting a core nav line once took a macro call with it, so the contract test now also checks each module page's href.

In the code:
- app/modules/__init__.py (Module, Registry), app/modules/planning_center/, app/modules/propresenter/
- tests/test_modules.py (CORE_FILES, CORE_NETWORK), .github/workflows/tests.yml, docs/modules.md, AGENTS.md
