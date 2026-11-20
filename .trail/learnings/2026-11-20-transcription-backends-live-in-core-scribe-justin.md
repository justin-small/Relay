---
title: "Transcription backends live in core, Scribe on Vercel is the default, OpenAI is the fallback, and relay feeds use text translation"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 13, tokens: 197285 }
id: l-242a34ec
scope: repo
status: active
src: agent
touches:
  - app/config.py
  - app/engine.py
  - app/realtime.py
---

## Decided
Justin, 2026-11-20, issue 320: the backend registry and both built-in backends (Scribe v2 Realtime through the Vercel key, and OpenAI) are core, not modules; modules may still register extra backends. Default backend is always scribe; a missing Vercel key or unknown backend falls back to OpenAI with a warning in status and the log. A backend that cannot translate (Scribe) uses the text-translation pipeline, so every feed ends up on Scribe plus text translation.
In the code: app/transcribe.py (register_backend, resolve, ScribeSession), realtime.backend in app/config.py, Engine.start/_open_text_pipeline in app/engine.py.

## Tried and ruled out
- Scribe as a module with only OpenAI in core, as the issue was written: Justin said the whole stack is moving to Vercel, so core.
- Default openai for existing installs: Justin chose always scribe; the fallback keeps OpenAI-only installs running.

## Watch out
- Scribe was never run against the live gateway; the wire format came from the @ai-sdk/gateway source, not docs.
- Translation still uses the OpenAI key until #496. OpenAI shows no cost (no verified price).
