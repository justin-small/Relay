---
title: "Text-only target languages: Arabic, Nepali, Telugu, Hmong"
author: Justin
date: 2026-11-01
branch: main
cost: { minutes: 8, tokens: 46291 }
---

## Decided
- languages.TARGETS entries can carry text_only=True and a "name" that's sent to the model. Arabic (ar), Nepali (ne), Telugu (te) and Hmong (hmn, ISO 639-2, sent as "White Hmong (Hmoob Dawb), in the Romanized Popular Alphabet") are text-only: gpt-realtime-translate can't produce them.
- In realtime mode, Engine.start skips text-only targets and says so in last_error; set_target never opens a relay for one. The panel shows them as "Text engine only".
- TextTranslator._request now takes {code: name}, so the model isn't left to guess what "hmn" means. The schema keys are still the ISO codes.
- The viewer sets dir=rtl for ar/fa/he/ur (viewer.js setDir), both on a language switch and for screen/overlay pages that pin a language in their markup.
- Live check on 2026-10-31 with gpt-6-luna: all four came back in 1.2-2.4 s with nothing dropped. Hmong was the slowest (~2.2-2.4 s).

## Watch out
- A translation that comes back while its target isn't in translator.targets is discarded on purpose (it was switched off mid-request). Test harnesses must set tr.targets, or nothing is published.
- No native speaker has checked any of the four yet. Small models are least reliable in Hmong.
