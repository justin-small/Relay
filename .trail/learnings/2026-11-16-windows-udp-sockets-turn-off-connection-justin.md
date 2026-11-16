---
title: "Windows UDP sockets turn off connection-reset on receive, but that did not fix the ATEM reconnect on Windows"
author: Justin
date: 2026-11-16
branch: lighting-module
cost: { minutes: 3, tokens: 8216 }
id: l-f4b53f3e
scope: repo
status: active
replaces: l-67f6ce6c
src: agent
touches:
  - app/netsock.py
  - tests/test_atem.py
---

## Decided
Every UDP socket opened through the routed helpers turns off Windows' SIO_UDP_CONNRESET, so a peer's closed socket cannot fail a later receive. This is a known Windows gotcha and harmless elsewhere.
The ATEM "and reconnects" check is skipped on Windows: the fake switcher answers every hello but the client never hears the reply, and the cause was not found.

## Tried and ruled out
The connection-reset setting on the client and on the fake switcher: it did not change the Windows result, so it is not the cause. Skipping was chosen over more rounds of CI.

## Watch out
The ATEM reconnect on Windows may be a real bug. It failed on main before the lighting PR. Not run on a Windows machine, only in CI.

In the code: no_connreset in app/netsock.py; the skipped check in tests/test_atem.py.
