---
title: Transcription uses Scribe v2 Realtime on the one Vercel key until Vercel lists Scribe v3 Live; no ElevenLabs key
author: Justin
date: 2026-11-20
branch: routes-544
cost: { minutes: 1, tokens: 47494 }
id: l-90e1ba83
scope: repo
status: active
replaces: l-82601af2
src: agent
touches:
  - app/audiobus.py
  - app/boards.py
  - app/channels.py
  - app/main.py
  - app/migrations/0015_routes.sql
  - app/modules/blackmagic/__init__.py
  - app/modules/blackmagic/routes.py
  - app/modules/blackmagic/service.py
  - app/modules/ndi/__init__.py
  - app/modules/ndi/router.py
---

## Decided
Justin, 2026-11-19: no ElevenLabs API key. Live captions and talkback use ElevenLabs Scribe v2 Realtime (elevenlabs/scribe-v2-realtime) through the one Vercel AI Gateway key, and switch to Scribe v3 Live when Vercel lists it. Everything else from the earlier AI decision stands: translation on gpt-6-luna and Command Center on Claude Sonnet 5.5 through Vercel, OpenAI key as selectable fallback, text mode the only engine, Local AI epic dropped.
In the code: issues 495, 496, 320, 91, 93, 95, 96, 98, 100 were amended.

## Tried and ruled out
- OpenRouter: no realtime or WebSocket transcription.
- Cloudflare AI Gateway: WebSocket still needs a provider key.
- Keeping the local sherpa-onnx diarizer beside Scribe: dropped.

## Watch out
- Scribe v2 Realtime has word timestamps but no live speaker diarization, so talkback speaker labels (issues 91, 95, 96) wait for v3 Live on Vercel.
- Vercel lists only scribe-v2 and scribe-v2-realtime on 2026-11-19.
