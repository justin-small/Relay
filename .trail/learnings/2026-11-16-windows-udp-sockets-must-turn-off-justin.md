---
title: "Windows UDP sockets must turn off connection-reset on receive, or a reconnecting client never hears the reply"
author: Justin
date: 2026-11-16
branch: lighting-module
cost: { minutes: 7, tokens: 26684 }
id: l-67f6ce6c
scope: repo
status: active
src: agent
touches:
  - app/netsock.py
  - tests/test_atem.py
  - tests/test_netsock.py
---

## Decided
Every UDP socket opened through the routed helpers turns off Windows' SIO_UDP_CONNRESET, so an ICMP port-unreachable from a peer that closed its socket cannot fail the next receive. Found when the ATEM client said hello again after a quiet spell, the switcher answered each hello, and the client never heard any reply on Windows only.

## Watch out
A test's own fake UDP server on Windows needs the same setting (the helper is public). The CodeQL check fails on a PR with no Rust change, because the Rust analysis is skipped; it is not a code alert.

In the code: no_connreset and udp_socket in app/netsock.py; the fake switcher in tests/test_atem.py.
