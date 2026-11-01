---
title: "Realtime API cost drivers: continuous streaming and per-language session fan-out"
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 2, tokens: 109730 }
touches:
  - app/realtime.py
  - app/engine.py
---

## Decided
Two structural causes of high OpenAI realtime billing (usage measured in audio-seconds):
1. `_pump_audio` (app/realtime.py:316) streams every captured frame continuously, silence included — "No gating (PRD decision)". Billed per second connected, not per second of speech.
2. One TranslationRelaySession opens per enabled target language (app/engine.py:96). Each also has transcribe_model set (app/engine.py:187) so each independently transcribes the identical source audio, but only the elected `_source_owner` (app/engine.py:177) is displayed; the other N-1 transcription streams are billed and discarded.

This explains why gpt-live-transcribe ≈ gpt-realtime-translate usage: both scale with (languages × wall-clock).

## Tried and ruled out
- Translate endpoint rejects turn_detection/server_vad, so silence gating can't be server-side — would have to be client-side in _pump_audio using AudioCapture's existing `speaking`/VAD state.

## Watch out
- Lowest-risk cost win: set transcribe_model="" on all sessions except the _source_owner — cuts transcribe bill ~(N-1)/N with no visible change (those streams already discarded). transcribe_model is passed at session construction in _open_relay, so this needs the owner known before/at open time (currently owner is elected after open via min(relays)).
- Client-side silence gating risks clipping phrase edges; needs testing.
