---
title: "NDI discovery servers are set through a config file Relay writes for the NDI runtime, and Relay links to NDI Tools instead of running a server"
author: Justin
date: 2026-11-17
branch: ndi-discovery-server
cost: { minutes: 10, tokens: 46045 }
id: l-1f71f03c
scope: repo
status: active
src: agent
touches:
  - app/modules/__init__.py
  - app/modules/ndi/__init__.py
  - app/modules/ndi/discovery.py
  - app/modules/ndi/routes.py
  - app/modules/ndi/runtime.py
  - app/modules/ndi/service.py
  - app/modules/ndi/static/ndi.js
  - app/modules/ndi/templates/ndi.html
  - docs/modules.md
  - tests/test_auth.py
---

## Decided
Issue 295, 2026-11-17, decided without asking: Relay does not run NDI's Discovery Service. NDI's licence does not allow distributing NDI Tools, so the page links to ndi.video/tools and Relay only connects. Discovery servers, the mDNS switch and groups reach the NDI runtime through a config file Relay writes in its own state folder, pointed at with NDI_CONFIG_DIR, so the machine's own NDI config is never touched. The runtime reads it once, so a change shows "restart Relay". Status is a TCP connect to the server (5959) plus the count of sources the runtime's finder reports.

## Tried and ruled out
Speaking the discovery server's protocol ourselves: it is private to NDI. Bundling and supervising the service: licence.

## Watch out
Config key names (networks.discovery, mdns.enable, groups.recv) follow NDI's docs from memory and cyndilib's Finder.find was written the same way; neither has run against a real install or server.

In the code: app/modules/ndi/runtime.py (sdk_config, configure), service.py (check_servers, _find_via_sdk), docs/ndi-discovery-server.md.
