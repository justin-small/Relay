---
title: "Source transcript: one transcribing session, billed separately"
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 3, tokens: 38497 }
touches:
  - app/engine.py
  - tests/smoke_test.py
---

## Decided
- gpt-realtime-translate only emits session.input_transcript.* when session.update sets audio.input.transcription.model; that transcription bills separately as gpt-live-transcribe. OpenAI's translation guide shows the input_transcript events but documents neither the setting nor its billing.
- Engine._open_relay now passes transcribe_model only to the source owner (first session opened while there is none). Engine._close_relay hands ownership off: if the owner closes while others are live, min(remaining) is closed and reopened with transcription. _stop_all_sessions passes handoff=False.
- _elect_source_owner was removed; the owner is decided at open time because the transcription model is fixed then.

## Tried and ruled out
- Changing the transcription model mid-session with session.update on an existing session: never tested, so the handoff reopens the session instead (a short gap in that one language).

## Watch out
- Silence gating already exists in AudioCapture._run_pump (realtime.gate_silence, default true). The older learning saying audio streams with no gating is out of date.
- Expected effect: the gpt-live-transcribe bill drops to about 1/N of gpt-realtime-translate. Check this on the usage page after the next live run.
