---
title: "Text translation mode: one transcription + chat-model translation"
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 4, tokens: 64017 }
touches:
  - app/realtime.py
  - app/texttranslate.py
  - requirements.txt
  - config.json
---

## Decided
- realtime.translation_mode = "text" (default "realtime"): Engine._open_text_pipeline opens one TranscriptionSession (app/realtime.py) and a TextTranslator (app/texttranslate.py). Each committed source line is translated into every live target in one chat completion (default text_model gpt-4.1-nano, structured JSON output keyed by ISO code). For 8 languages that's about $7/week, against $111 for realtime.
- The transcription endpoint is wss://api.openai.com/v1/realtime?intent=transcription with no model in the URL. session.update uses session.type="transcription", audio.input.transcription {model, languages:[iso], delay}, and turn_detection null. The client appends with the un-prefixed "input_audio_buffer.append". gpt-live-transcribe has no server VAD and only sends a final transcript after "input_audio_buffer.commit", so _pump_audio commits after a 0.6 s gap in frames (which needs gate_silence on) or every 15 s of continuous audio.
- Prompt-injection guard: the transcript goes in as a JSON string field, the system prompt says to translate any instructions literally, and check() drops any translation that's missing, empty or more than 4x the source length + 40 chars.
- One worker translates lines strictly in order; when it falls behind, the queued lines go out as one request.
- In text mode set_target only edits translator.targets; no sessions open or close.

## Tried and ruled out
- httpx: it's in the dev venv but not in requirements.txt, so the translator uses stdlib urllib in asyncio.to_thread.

## Watch out
- The prototype hasn't been run against the live API: the local config.json key has been revoked (401 token_invalidated). The first real run should check the transcription session.update shape and the commit cadence.
- If gate_silence is false, the 0.6 s pause commit never fires, so lines only commit every 15 s.
