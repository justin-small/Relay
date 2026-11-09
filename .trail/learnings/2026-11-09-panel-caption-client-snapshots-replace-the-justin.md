---
title: "Panel caption client: snapshots replace the buffer, unsupported characters become one ?, and resume trusts Relay's seq"
author: Justin
date: 2026-11-09
branch: panels-caption-client
cost: { minutes: 1, tokens: 3518 }
id: l-6825259a
scope: repo
status: active
src: agent
touches:
  - app/hub.py
---

## Decided
- A snapshot replaces the panel's 200-line buffer, as the browser viewer does. It arrives on a fresh connection, on a resume older than Relay's history, and on a reset (an empty snapshot), and the panel cannot tell the second from a reset followed by new lines, so it shows exactly what Relay holds. Relay's history_lines must be 200 for a rebooted panel to refill fully.
- Text is folded when stored: Latin ranges stay, typographic punctuation becomes ASCII, invisible characters go, and every other run becomes one "?". "?" because it is in every font, so the fallback can never itself be a missing glyph.
- The client reads both chunked and plain HTTP bodies: uvicorn chunks, and Caddy may or may not pass that through.

## Watch out
- The folding keeps U+0020-007E, U+00A1-024F, U+1E00-1EFF and the euro sign, so whatever font draws captions has to cover exactly those ranges. LVGL's stock Montserrat is ASCII only and would leave accented letters blank.
- Relay's seq is one counter for all feeds and for partial lines, so a panel's seqs have gaps. A seq lower than the last that the panel does not hold is taken as a Relay restart.
- If Relay is reset while a panel is disconnected and no line is committed before it reconnects, the resume gets nothing (empty history) and the panel keeps its pre-reset lines until the next snapshot. Closing that needs the server to send a snapshot on a resume when its history is empty.

In the code: firmware/relay-display/main/caption_format.h (caption_feed::apply, caption_fold), main/captions.cpp, tests/test_captions.py and tests/fake_relay.py; the server side is app/hub.py _resume_frames.
