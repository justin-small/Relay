---
title: "Every AI task picks Vercel or the OpenAI key per task on an AI models page, and text translation is the only engine"
author: Justin
date: 2026-11-20
branch: ai-models-495-496
cost: { minutes: 24, tokens: 458071 }
id: l-2f6efe84
scope: repo
status: active
src: agent
touches:
  - app/aimodels.py
  - app/static/aimodels.js
  - tests/test_aimodels.py
---

## Decided
Justin, 2026-11-20, issues 495 and 496 together, his answers: the AI models page lists all six tasks now, including Command Center, summaries and spoken translation, which are saved before they are built. Each task defaults to Vercel; when its Vercel key is missing it falls back to the OpenAI key with a warning on the task (same as 320). A task the operator switches to the OpenAI key stays there. Models are picked from a dropdown of Vercel's public catalogue, showing each model's price and listing free models first.
The realtime translation engine (one gpt-realtime-translate session per language) is removed; an old config with translation_mode "realtime" loads as text, and its text_model is carried into ai.tasks.translation.
In the code: app/aimodels.py (TASKS, resolve, Catalogue), ai.tasks in config, Engine.apply_ai, TextTranslator.use, /api/admin/ai and /api/admin/ai/models, app/static/aimodels.js.

## Tried and ruled out
- Moving transcription into ai.tasks: it stays on realtime.backend because modules register their own backends; the page edits realtime.backend, scribe_model and transcribe_model.

## Watch out
- Translation through the Vercel chat endpoint (response_format json_schema, reasoning_effort) was not run live: this checkout has no Vercel key, and the installed app keeps its key in the keychain.
- The catalogue (GET https://ai-gateway.vercel.sh/v1/models, no key) gives prices per token, per second of audio or per character; scribe-v2-realtime lists no price, so the page says "price not listed".
- A per-token price times a million lands just under the figure (0.0000001 * 1e6 = 0.0999...): round before formatting.
