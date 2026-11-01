---
title: "Blocklist in text mode: translate the filtered source line"
author: Justin
date: 2026-11-01
branch: main
cost: { minutes: 1, tokens: 14878 }
---

## Decided
- In text mode the translator gets the source line after the blocklist has run: hub.publish_final now returns the line as published ("" if nothing is left), and Engine's source_final submits that. Blocklist words are removed before the model sees them, so they can't reach any target language.
- The blocklist is Latin-script English and Spanish only (464 terms). The hub's filter on translated lines can't catch an English swear word once it's been translated into Arabic or Chinese, and word-boundary matching doesn't work for CJK. Filtering the source is the only check that covers every language.
- The system prompt also says to never add profanity and to render crude words as mildly as possible, for words that aren't on the list. Live check: "what the hell is this shit" -> filtered to "what the hell is this" -> Spanish «¿Qué demonios es esto?», Chinese 这到底是什么鬼.

## Watch out
- Realtime mode still translates the raw audio, so non-English captions there are filtered only against the Latin-script list.
- Removing a word between commas leaves ",," ("Well, damn, that" -> "Well,, that"). This is an existing quirk of redact._tidy, visible in the English pane too.
