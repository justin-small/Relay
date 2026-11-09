---
title: "Panel caption client: snapshots replace the buffer, unsupported characters become one ?, and resume trusts Relay's seq"
author: Justin
date: 2026-11-09
branch: panels-caption-client
cost: { minutes: 2, tokens: 18541 }
id: l-75c9db12
scope: repo
status: active
replaces: l-6825259a
src: agent
touches:
  - app/hub.py
---

## Decided
- A snapshot replaces the panel's 200-line buffer, as the browser viewer does. Relay's history_lines must be 200 for a rebooted panel to refill fully.
- Relay answers a resume with a snapshot when the id is from before the channel's last reset or ahead of any seq it has issued (a restarted server). Without that, a panel away during a reset kept the old run's lines, and one resuming after a restart skipped the new, lower seqs.
- Unsupported characters are folded to one "?" per run, because "?" is in every font and so can never itself be a missing glyph.
- The client reads chunked and plain HTTP bodies: uvicorn chunks, and Caddy may or may not pass that through.

## Watch out
- The folding keeps U+0020-007E, U+00A1-024F, U+1E00-1EFF and the euro sign, so the caption font must cover those ranges. LVGL's stock Montserrat is ASCII only.
- Relay's seq is one counter for all feeds and partial lines, so seqs have gaps.

In the code: app/hub.py Channel.reset_seq and Hub._resume_frames; firmware/relay-display/main/caption_format.h (caption_feed::apply, caption_fold); tests/fake_relay.py copies the resume rule by hand and must follow hub.py.
