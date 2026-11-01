---
title: Reading OpenAI realtime usage charts against the relay
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 1, tokens: 48854 }
---

## Decided
- OpenAI usage dashboard "seconds" for gpt-live-transcribe and gpt-realtime-translate = (enabled target languages) x (wall-clock the engine is running). Divide a day's seconds by the language count to get hours the relay actually ran (e.g. 27,825 s / 3 langs ~= 2.6 h).
- transcribe usage ~= translate usage is the signature of per-language transcription fan-out (every session has transcribe_model set). If only the source owner transcribed, transcribe should drop to ~1/N of translate.
- Cause analysis already lives in the 2026-10-31 "Realtime API cost drivers" learning; start there.

## Tried and ruled out
- Nothing new; did not change code this session.

## Watch out
- Scheduled windows bill for their whole span (container log "Scheduled start: ... until ..."), silence included, so over-long schedule windows are a direct cost.
