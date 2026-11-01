---
title: "Redactor: no doubled punctuation or lost capitals after a removal"
author: Justin
date: 2026-11-01
branch: main
cost: { minutes: 2, tokens: 18174 }
touches:
  - tests/test_redact.py
---

## Decided
- redact._tidy collapses the punctuation pair a removal leaves (",," ";," ".," -> the first mark) and drops a clause mark that leads a line, or that follows a mark from an earlier stream fragment (prev_char). "Well, damn, that" -> "Well, that"; "Damn, that" -> "That".
- Capitals carry over: in _remove, a capitalised removed word after . ! ? or … gives its capital to the next word. _recase does the same at a fragment or line start, and only when the original opened with a capital and the opening actually changed, so "iPhone damn, ok" stays lowercase.
- tests/test_redact.py "case-insensitive" now expects "DAMN it" -> "It". That's intended: the remaining word opens the line.

## Watch out
- These rules assume speech never produces doubled clause marks on its own. Something like "1,,000" would be collapsed too.
