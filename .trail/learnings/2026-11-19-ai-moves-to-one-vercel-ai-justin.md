---
title: "AI moves to one Vercel AI Gateway key, with Scribe v3 Live on an ElevenLabs key until Vercel lists it, and the Local AI epic is dropped"
author: Justin
date: 2026-11-19
branch: main
cost: { minutes: 22, tokens: 267831 }
id: l-82601af2
scope: repo
status: active
src: agent
---

## Decided
Justin, 2026-11-19: every AI task goes through one Vercel AI Gateway key (translation on gpt-6-luna, Command Center on Claude Sonnet 5.5). The old OpenAI key stays selectable per task as a fallback until a later full move to Vercel. Live captions and talkback use ElevenLabs Scribe v3 Live (word timestamps, live speaker diarization), on an ElevenLabs key until Vercel lists it. Text translation mode is the only engine. The Local AI epic (Whistle, Needle, Cactus) and the local diarizer were closed as not planned. The command registry and AI safety issues stay under Command Center.
In the code: issues 495 (rewritten), 496, 494, 320, 324, 326, 91, 93, 95, 96, 98, 100; closed 319, 321 to 323, 325, 327 to 333, 94, 99.

## Tried and ruled out
- OpenRouter: no realtime or WebSocket transcription, only a REST endpoint.
- Cloudflare AI Gateway: unified billing documented for HTTP only, and its WebSocket still needs a provider key.
- Scribe v2 Realtime on Vercel: has word timestamps but no live speaker diarization.
- Keeping the local sherpa-onnx diarizer beside Scribe: dropped, Scribe v3 Live does it.

## Watch out
- Vercel lists only scribe-v2 and scribe-v2-realtime on 2026-11-19; the scribe-v3-live model page was a 404, so captions and talkback need an ElevenLabs key as well until it is listed.
- The scribe_v3_live model id and diarize=true came from search results, not ElevenLabs' own docs; confirm before building.
- Saved voices across sessions (issue 96) are on hold, because Scribe labels speakers within a session only.
