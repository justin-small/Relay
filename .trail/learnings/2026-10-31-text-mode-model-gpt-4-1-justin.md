---
title: "Text mode model: gpt-4.1-nano retired, use gpt-6-luna with reasoning off"
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 6, tokens: 26676 }
touches:
  - docker-config/config.json
---

## Decided
- gpt-4.1-nano was shut down on 2026-10-23 (it returns 404 model_not_found "has been deprecated"), even though OpenAI's pricing page still listed it. The text_model default is now gpt-6-luna ($0.10 in / $0.50 out per 1M tokens), sent with reasoning_effort "none" (config realtime.text_reasoning_effort; blank omits it).
- "model_not_found" was added to FATAL_MARKERS, so a retired model stops the session instead of failing on every line.
- Live check on 2026-10-31: 8 languages in one request took ~2.5-2.9 s. Translations of a scripture reference were correct, and an attempt to instruct the model through the transcript was translated literally instead of being followed.

## Tried and ruled out
- Choosing a model from the pricing page alone: check the deprecations page (developers.openai.com/api/docs/deprecations) as well. gpt-5-nano and gpt-5-mini shut down on 2026-12-11, and gpt-5.4-nano on 2027-04-01.

## Watch out
- ~2.7 s per line for 8 languages is most of the caption delay. Splitting the languages across parallel requests should cut it, at a little extra input-token cost.
- A text_model saved in docker-config/config.json overrides the default. Changing the default doesn't change a model name that's already saved there.
