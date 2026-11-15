---
title: "Audio sources and outputs are registered in core's audio module, and capture keeps the pump"
author: Justin
date: 2026-11-15
branch: audio-sources-265
cost: { minutes: 4, tokens: 101866 }
id: l-5a1fe5a1
scope: repo
status: active
src: agent
touches:
  - app/audio.py
  - app/config.py
  - app/engine.py
  - app/main.py
  - config.example.json
  - docs/modules.md
  - tests/test_audio_sources.py
---

## Decided
Issue 265: capture pulls frames from a pluggable source; core registers PortAudio only, and modules register their own while on and remove it when off. The pump (resample, meter, gate, reconnect, consumers) stays in core, so a source only delivers float32 mono blocks. Outputs get the same registry for alert tones and spoken translation.
Everything lives in the existing core audio file rather than a new core file, so the core-files list is unchanged.

## Tried and ruled out
A separate core file for the interfaces: it would have needed a core-files entry for no gain.

## Watch out
- The live-capture flag is still a single flag; the talkback second input (#92) must turn it into a count.
- The PortAudio output has no caller yet and was never run on real hardware.
- trail check flags two "brought back" learnings on this change; both are word matches, not real conflicts.

In the code: app/audio.py (AudioSource, AudioOutput, register_source, register_output, PortAudioSource), config key audio_source, tests/test_audio_sources.py, docs/modules.md.
