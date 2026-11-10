---
title: "Production relay logs: hourly session cap, spend-limit outage, 8 languages enabled"
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 8, tokens: 41891 }
touches:
  - app/scheduler.py
  - app/schedules.py
---

## Decided
- The production schedules are correct (all America/New_York): Sun 09:55-12:30, Sun 17:55-20:30, Wed 18:55-20:10. Every "Scheduled start" in the Oct 18-29 logs has a matching "Scheduled window ended: stopping" at the right time. The scheduler is not what's driving cost.
- Each session logs "[translate:X] API error: Your ..." exactly 60 min after it starts and then reconnects. This is most likely OpenAI's 60-minute realtime session cap. The text is cut off at the PowerShell console width; to get the full line, send `docker logs` to a file.
- On 2026-10-25 at 15:55 UTC, the reconnects got 1013 insufficient_quota.project_spend_limit_exceeded. That matches FATAL_MARKERS, so sessions stopped retrying, and every later window (Oct 25 PM, Oct 28) failed within 2 s of starting. The OpenAI project spend limit, not the code, took the captions down.

## Tried and ruled out
- Expecting billing to follow wall-clock time: with 3 languages, a full Sunday comes to at most ~55.8k s, but the peak day was 27.8k s, so gate_silence probably does reduce the billed seconds. Not confirmed.

## Watch out
- As of Oct 31 the production config has 8 targets enabled (ES, FR, RU, ZH, JA, HI, ID, VI), but the logs show only 3 ran. That's about 2.7x the cost on the next run, and with transcription in every session it's 8 transcription streams.
- docker inspect RestartCount does not count manual restarts; container logs survive `docker restart`.
