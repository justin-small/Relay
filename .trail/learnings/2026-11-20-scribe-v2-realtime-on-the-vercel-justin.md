---
title: "Scribe v2 Realtime on the Vercel gateway sends revised cumulative partials and no deltas, so the final line is the truth"
author: Justin
date: 2026-11-20
branch: shared-pickers-546
cost: { minutes: 1, tokens: 1345 }
id: l-a8bc3a0f
scope: repo
status: active
src: agent
---

## Decided
Checked live on 2026-11-20 with the real Vercel key: the gateway's transcription socket sends `transcript-partial` (the whole turn so far, revised as the model hears more, e.g. "Good morning, Evie." then "Good morning, everyone. Welcome") and `transcript-final`, and never `transcript-delta`. Relay shows a partial only while it grows and publishes the final's text as the settled line; the idle segmenter is off for this backend.
In the code: ScribeSession._handle in app/transcribe.py; wire format taken from the @ai-sdk/gateway package (URL ?ai-model-id=, subprotocols ai-gateway-transcription.v1 and ai-gateway-auth.<key>, start frame, binary audio).

## Tried and ruled out
- Committing the buffered partial text when the final arrives: it kept the first wrong guess ("Evie").

## Watch out
- A revised partial that does not extend the last one is not shown until the final, so a caption can pause mid-turn.
- Engine and admin UI were not run live with a real key and capture; only the session was.
