---
title: A test that builds its fixtures on fixed calendar dates must freeze the clock the app reads
author: Justin
date: 2026-11-15
branch: intercom-phones-rooms
cost: { minutes: 2, tokens: 13132 }
id: l-ef9cd339
scope: repo
status: active
src: agent
touches:
  - tests/test_pclive.py
---

## Decided
The Planning Center live test used fixture plans dated mid-November 2026 and let the admin routes read the real clock, so it passed until that date arrived and then failed on every platform. The test now replaces the app's datetime with one pinned to the fixture's day for the admin section.

## Tried and ruled out
Moving the fixture dates forward: it would only fail again later.

## Watch out
A red CI that appears with no code change is often a date; check the failing test's fixtures before blaming the branch.

In the code: tests/test_pclive.py (_Frozen), replacing pclive.datetime
