---
title: "On Windows an unbound socket has no address, so a UDP receiver must always be bound"
author: Justin
date: 2026-11-16
branch: network-sockets
cost: { minutes: 1, tokens: 11580 }
id: l-f238b046
scope: repo
status: active
src: agent
touches:
  - app/netsock.py
  - tests/test_netsock.py
---

## Decided
An unrouted UDP socket is bound whenever the caller names a host (a receiver); only a sender (port 0, no host) is left unbound for the OS to bind on first send.

## Watch out
getsockname() on an unbound socket raises WinError 10022 on Windows but returns ('0.0.0.0', 0) on macOS and Linux, so a Mac run passes and only Windows CI fails. Tests that read a receiver's port must bind it first.

In the code: udp_socket in app/netsock.py; the real-socket section of tests/test_netsock.py passes host=server_at to its receivers.
