---
title: Windows has no OS-API fallback for announcing; the in-app responder is the only path
author: Justin
date: 2026-11-13
branch: announce-in-app-cleanup
cost: { minutes: 2, tokens: 56550 }
id: l-7d25aed3
scope: repo
status: active
src: agent
touches:
  - README.md
  - app/announce.py
  - firmware/relay-display/README.md
  - firmware/relay-display/main/discovery_format.h
---

## Decided
Issue 182 is closed with the stdlib mDNS responder alone. The Windows DNS-SD helper script was deleted along with the standalone entry point, so if another program holds port 5353 Relay logs it and carries on unannounced (panel still works with a typed address).

## Tried and ruled out
Keeping the helper script as a fallback, or rebuilding it inside the app with DnsServiceRegister: no Windows host to test on, and the earlier script was never run on real Windows.

## Watch out
Not verified on real Windows, macOS or Linux hosts (dns-sd -B, avahi-browse, goodbye on stop). If Windows panels do not find Relay, a DnsServiceRegister fallback is the next thing to build.

In the code: app/announce.py (Announcer, open_socket).
