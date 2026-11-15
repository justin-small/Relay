---
title: "Signed-in phones get their own admin page, and each user manages their own phones from the crew page"
author: Justin
date: 2026-11-15
branch: main
cost: { minutes: 3, tokens: 62690 }
id: l-cf0d6cc9
scope: repo
status: active
src: agent
touches:
  - app/modules/intercom/__init__.py
---

## Decided
Justin answered questions on issue 273 (2026-11-15): the backend (device credentials, rotation, revocation) already shipped in #337, so this pass adds UI and closes gaps only. The admin "Signed-in phones" list is a separate admin page (permission intercom.manage), not a section of the Intercom page. A user sees and signs out their own phones in a "My phones" panel on the crew Intercom tab, not in core My account, so core stays free of the integration.

## Tried and ruled out
- Audit only, no new UI: offered, not chosen.
- Rate-limiting sign-in now: left to #278.
- A generic core My account extension point for modules: offered, not chosen; the crew tab already hosts module UI.
- Keeping phones as a tab on the Intercom admin page: offered, not chosen.

## Watch out
intercom.js is loaded on every admin page, so every element lookup for the phones page must tolerate it being absent (the error box differs per page: icErr vs icPhonesErr).

In the code: app/modules/intercom/__init__.py (AdminPage intercom-phones), templates/phones.html, templates/crew.html, static/crew.js (myPhones), static/intercom.js.
