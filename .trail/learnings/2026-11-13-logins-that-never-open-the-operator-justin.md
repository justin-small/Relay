---
title: "Logins that never open the operator panel are a Member role below Viewer, and the first feature is signed-in sending on /alert"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 12, tokens: 202745 }
id: l-418442dd
scope: repo
status: active
src: agent
touches:
  - app/templates/home.html
  - app/users.py
  - app/main.py
  - app/modules/__init__.py
  - tests/test_alerts.py
---

## Decided
Justin chose for issue 175 (Nov 13): one new fixed role, Member, ranked lowest (member < viewer < operator < admin < owner), not several named roles or per-user grants. It holds no core permission except alerts.send; opening the operator panel is now its own core permission, admin.open (min viewer), so a Member that opens /admin is sent to /home.
Non-admin accounts always land on /home, a page listing what they may use (core tools plus modules' UserPage entries), with change-password and sign-out. Admin roles still land on /admin.
Sign-in for these accounts happens only on the HTTPS operator port, so passwords never cross plain HTTP. The first gated feature is /alert: an operator setting, alerts.require_signin (off by default), moves /alert to the operator port (the viewer port's /alert answers 303 to https), and a phone then sends only as an account holding alerts.send, named by that account.

## Tried and ruled out
Several unranked roles (translator, volunteer): would need a role-to-permission table instead of min_role. Per-user feature grants: most schema and UI work. Returning to the page that asked for sign-in (?next=): Justin preferred always landing on the home page.

## Watch out
Panels still send alerts by panel id from the viewer port with no account, even with sign-in required; a client that knows a known panel's id can do the same. The alert state stream (/stream?stream=alert) stays open to read.
The admin page's alert save used to drop each button's stage flag (it posted without `stage`); it now sends it.
Not checked in a browser: only the Python tests cover the new pages.

In the code: app/users.py ROLES and _CORE (admin.open, alerts.send); app/main.py _home_for, home_page, CORE_HOME_TOOLS, _alert_account, page_alert; app/modules/__init__.py UserPage and user_nav; app/templates/home.html; tests in tests/test_alerts.py ("sign-in required (#175)", "member home (#175)").
