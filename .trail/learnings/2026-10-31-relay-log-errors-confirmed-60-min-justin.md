---
title: "Relay log errors confirmed: 60-min session cap and spend limit"
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 2, tokens: 16789 }
---

## Decided
- Confirmed from the full log: the hourly error is "Your session hit the maximum duration" (OpenAI's 60-minute realtime cap). The relay reconnects about 4 s later on its own, so this is a short caption gap each hour, not an outage.
- Confirmed: from 2026-10-25 15:55 UTC the errors read "Your project has reached its configured" spend limit (1013 insufficient_quota.project_spend_limit_exceeded). By the manual start on 2026-10-30 19:22 UTC sessions connected again, so the limit had been raised.

## Tried and ruled out
- Reading the Windows `docker logs > file` output directly: PowerShell writes it as UTF-16LE and wraps it at the console width. Convert it with `iconv -f UTF-16LE -t UTF-8` before grepping.

## Watch out
- engine.stop() logs nothing, so a manual Stop leaves no trace. A manual Start outside a window shows up only as "Capture started" with no "Scheduled start" line.
