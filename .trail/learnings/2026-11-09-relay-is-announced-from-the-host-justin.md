---
title: "Relay is announced from the host, and a panel stays with the first Relay it meets"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 7, tokens: 171956 }
id: l-73cbb13b
scope: repo
status: active
src: agent
touches:
  - tools/announce.py
---

## Decided
Relay announces `_relay-captions._tcp` (port 80, TXT v/name/path) from the host, not the container: macOS uses the built-in `dns-sd -R`, Windows uses Windows' own DNS-SD API from a PowerShell script (no Bonjour), Linux runs a stdlib mDNS responder in a host-network container. A panel with no typed address browses, takes the first answer and saves that install's name; it only ever takes that name afterwards. A typed address always wins and is the fallback when multicast is blocked. Opt out with RELAY_ANNOUNCE=0.

## Tried and ruled out
Announcing from inside the relay container: on Docker Desktop it is not on the LAN. Saving the discovered address: DHCP moves it, so only the name is saved and the address is re-read every 30 s.

## Watch out
The panel can only be switched to another Relay by typing its address or a factory reset. The firmware discovery.cpp and the Windows script have not been run on real hardware; only the rules and the Python responder are tested (macOS dns-sd resolved the Python responder).

In the code: tools/announce.py, tools/announce.ps1, start.*, firmware/relay-display/main/discovery*.
