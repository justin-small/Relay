---
title: "Restart the Docker Relay the way the start script does, or viewer links show only 127.0.0.1 and the container runs as the wrong user"
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 1, tokens: 1537 }
id: l-a570bd43
scope: repo
status: active
src: agent
touches:
  - app/static/admin.js
  - docker/docker-compose.yml
  - docker/docker-compose.macos.yml
  - tools/wait-ready.sh
  - app/main.py
---

## Decided
To rebuild or restart the Mac's Docker Relay from a session, run start.command, or pass the same environment it sets, because the container cannot see the Mac's LAN address or know which user owns the bind-mounted config; plain `docker compose up` supplies neither.

## Watch out
A bare compose restart came up healthy, otherwise looking fine, but listed viewer links (Viewer links page, Alerts page) as http://127.0.0.1/ only, because RELAY_ADMIN_IPS was empty, and ran the container as the image's default uid 10001 instead of the Mac user, because RELAY_UID/RELAY_GID were unset. Nothing failed loudly.

In the code: start.command sets RELAY_ADMIN_IPS (ipconfig getifaddr en0, then en1), RELAY_UID=$(id -u), RELAY_GID=$(id -g), then runs `docker compose -f docker/docker-compose.yml -f docker/docker-compose.macos.yml up --build -d` and tools/wait-ready.sh. _urls in app/main.py reads RELAY_ADMIN_IPS and RELAY_PUBLIC_HTTP. The container's root filesystem is read-only, so pipe scripts in with `docker exec -i ... python3 -` because `docker cp` fails there.
