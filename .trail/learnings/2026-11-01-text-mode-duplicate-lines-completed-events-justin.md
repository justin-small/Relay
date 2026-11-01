---
title: "Text mode duplicate lines: completed events vs the idle segmenter"
author: Justin
date: 2026-11-01
branch: main
cost: { minutes: 1, tokens: 7262 }
---

## Decided
- The /realtime?intent=transcription endpoint sends conversation.item.input_audio_transcription.completed for every turn the client commits, with the turn's full text. The base _commit_idle_segments had usually committed that text already, so the base _handle published it a second time: every transcript line showed twice and was translated twice.
- TranscriptionSession._handle now handles completed events itself: it commits only what's left in the partial buffer and ignores the event's transcript. The deltas are the only source of text.

## Watch out
- The translations endpoint (TranslationRelaySession) never sends completed events, so the base class's "publish the event transcript" path was never exercised before. Any new session type on an endpoint that does send them needs the same handling.
- If gpt-live-transcribe ever revises text in the completed event (the deltas don't match the final), those corrections are now dropped.
