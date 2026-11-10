---
title: "Talkback captions with speaker labels: design chosen, sherpa-diarize repo ruled out"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 4, tokens: 64378 }
id: l-b7c704ff
scope: repo
status: active
src: agent
touches:
  - app/audio.py
  - app/hub.py
  - app/engine.py
---

## Decided
- Talkback is a second, fully separate audio feed (comms party line) for FOH, directors and bands to read. It never touches the main transcription/translation pipeline. Not built yet; design agreed in principle, awaiting go-ahead.
- Feed is one mixed party line, so speakers are told apart by voice, not by channel.
- Text comes from OpenAI (its own transcription-only session, silence-gated); only the voice-ID step runs locally, because the target is a low-end PC.
- Speakers appear as Speaker 1/2/3; renaming one in admin saves the name with that voice's fingerprint so later sessions recognise the person automatically.
- Viewing is a separate PIN-protected page, never listed on the public viewer, because comms chatter is private.

## Tried and ruled out
- Using github.com/qqlzfmn/sherpa-diarize itself: no licence (cannot copy or ship), only Chinese models (CNCeleb embedding, Chinese ASR), and it needs Python 3.12+ and numpy >= 2.5.2 while this app pins numpy 2.1.3. Its method (Silero VAD -> per-turn speaker embedding -> online centroid clustering with EMA and periodic merge) is small; reimplement it on sherpa-onnx (Apache-2.0) with an English speaker model.
- Fully local transcription: less accurate on comms audio and the heaviest CPU load.

## Watch out
- Speaker label only exists when a turn ends; text arrives first.
- Overtalk is attributed to one speaker; very short replies are too brief to fingerprint, show as unknown rather than guess.
- Similar voices will merge/split; admin needs a merge action. Saved fingerprints are biometric-style data; add a forget-voice action.
- A second feed is a second stream of billed audio-seconds.

In the code:
- app/audio.py AudioCapture is instantiable twice, but _set_capture_live / capture_is_live is a module-global flag that assumes one capture; make it a count.
- AudioCapture emits mono 24 kHz s16 PCM; sherpa-onnx VAD/embedding want 16 kHz float, so resample (soxr already a dependency).
- app/hub.py channels are keyed by (stream, lang), so a "talkback" stream fits; messages need a speaker field.
- Engine (app/engine.py) owns the single capture; talkback wants its own engine with independent start/stop.
