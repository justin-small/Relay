---
title: "LAN addresses come from ifaddr in a core file; the admin page polls for changes, the server only logs them"
author: Justin
date: 2026-11-13
branch: feature/state-folder-180
cost: { minutes: 3, tokens: 78146 }
id: l-f703ef23
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - app/netinfo.py
  - app/static/admin.js
  - requirements.txt
  - tests/test_auth.py
  - tests/test_modules.py
  - tests/test_netinfo.py
  - tools/setup_caddy.py
---

## Decided
Network identity (#180 item 9) reads interfaces with ifaddr, chosen over psutil because it is pure Python with no compiled wheel to worry about on Windows, macOS or Linux. The default-route address is listed first, then other interface addresses, then RELAY_ADMIN_IPS extras. The admin page re-reads /api/admin/urls every 10 s and redraws only on a difference. The server-side watcher only logs a change.

## Tried and ruled out
Pushing the change to the page over the existing status stream: more plumbing than a 10 s poll for something that changes a few times a day.

## Watch out
netinfo.py is core, not a module: it reads this machine's own interfaces, not another product, so it is in CORE_FILES and CORE_NETWORK (socket) in tests/test_modules.py. The certificate (tools/setup_caddy.py) still uses its own address logic and is not regenerated on a change; that belongs to the HTTPS issue. Only unit-tested; not tried on a real Wi-Fi switch.

In the code: app/netinfo.py (lan_ips, watch), app/main.py (_urls, admin_urls), app/static/admin.js (watchUrls), tests/test_netinfo.py.
