---
title: "Modules reach each other by provided names, looked up when needed, and the remote intercom keeps its imports"
author: Justin
date: 2026-11-19
branch: shared-concepts-helpers
cost: { minutes: 13, tokens: 323837 }
id: l-8e2862dc
scope: repo
status: active
src: agent
touches:
  - tests/shared_concepts_baseline.txt
  - app/modules/__init__.py
  - docs/modules.md
  - tests/test_modules.py
  - tests/test_shared_concepts.py
---

## Decided
Issue 523 (2026-11-18): a module offers something with `modules.provide("<area>.<what>", impl)` in attach() and anyone asks with `modules.using(name)` at the moment of use, coping with None. The registry withdraws everything a module provided when it is switched off. Names are looked up at call time, never captured in attach(), so the order modules came on in does not matter. Where something must be told of changes (crew and the ProPresenter link hearing the followed plans change), it is `modules.listen/announce("plan.changed")`, not a list on the other module's object. Signage looks up other modules' sources and schedule layers itself (`signage.source.<kind>`, `signage.layer.tag`) instead of them registering into signage.

## Tried and ruled out
Having each module register into the other's registry in attach() (signage sources, follower.listeners): breaks when the other module is attached later or not at all. intercom_remote -> intercom stays an import (one feature); intercom_remote -> remote_access stays too, because it reads the enrolment while the tunnel module is off; both are on tests/shared_concepts_baseline.txt.

## Watch out
Tests that enable a module in config do not attach it: they must call `modules.attach(id)` (and `detach()` to mimic switching off) for anything another module asks for by name.
In the code: app/modules/__init__.py (provide, using, listen), docs/modules.md "Reaching another module", tests/test_modules.py "provide and using".
