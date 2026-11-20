---
title: "On real hardware a Constellation ATEM announces _switcher_ctrl._udp, not _blackmagic._tcp, and the UniFi mDNS proxy answers from the gateway's address"
author: Justin
date: 2026-11-16
branch: redesign-admin-shell-spec
cost: { minutes: 14, tokens: 85431 }
id: l-d5f514a7
scope: repo
status: active
src: agent
touches:
  - spike/av_ports.py
  - app/modules/atem/discovery.py
---

## Decided
Survey of a real church network on 2026-11-16 (findings posted on issues 242, 257, 294, 437 and others, without addresses because the repo is public). Older ATEMs (Television Studio HD III, firmware 8.1.1) announce _blackmagic._tcp port 9910 with TXT name, class=AtemSwitcher, protocol version=0.0, release version, unique id (12 hex). An ATEM 2 M/E Constellation HD (firmware 9.6.2) announces no _blackmagic._tcp: it announces _switcher_ctrl._udp 9910 (no TXT), _videohub._tcp 9990 (no TXT) and _http._tcp 80 whose TXT carries device name, product id, release version and a 32-hex unique id.

## Watch out
ATEM discovery does not ask for _switcher_ctrl._udp and only follows service types with Blackmagic words, so a Constellation is found only through _videohub._tcp, with no unique id or model; its firmware column reads "protocol version" (0.0) instead of "release version".
With UniFi's mDNS proxy on, replies come from the gateway's IP, often PTR/TXT without SRV/A: resolve SRV then A, never use the sender address as the device IP.
Dante stage boxes (Allen & Heath DT168) have A&H MACs, not Audinate's, so Dante detection by MAC prefix misses them; Dante Via uses ARC port 24440, not 4440.
NDI decoders (Magewell Pro Convert) announce nothing over mDNS.
The UniFi integration API v1 gives a client's switch but not its port; the same X-API-KEY works on the classic /proxy/network/api/s/default/stat/sta (sw_mac, sw_port) and stat/device (port_table names).

In the code: app/modules/atem/discovery.py SERVICES and collect(); spike/av_ports.py.
