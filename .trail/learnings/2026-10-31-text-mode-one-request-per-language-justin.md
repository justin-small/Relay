---
title: "Text mode: one request per language, in parallel"
author: Justin
date: 2026-10-31
branch: main
cost: { minutes: 2, tokens: 10697 }
touches:
  - app/texttranslate.py
---

## Decided
- TextTranslator now sends one chat request per target, all at once (asyncio.gather over a 12-thread pool). Each language is published as soon as its reply lands. Live check with gpt-6-luna and 8 languages: first language ~0.8-1.0 s, all done 1.45-1.6 s, against ~2.7 s for one request covering all 8. Repeating the prompt costs fractions of a cent per event.
- Line order holds: the worker starts the next line only after every language of the current one is back. A target switched off while its request is in flight is not published.

## Tried and ruled out
- One request returning all languages as JSON: cheapest, but its delay grows with the number of languages.

## Watch out
- The slowest language holds up the next line for everyone. If one language is consistently slow, its delay applies to all of them.
