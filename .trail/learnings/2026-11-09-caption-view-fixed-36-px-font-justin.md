---
title: "Caption view: fixed 36 px font with a DejaVu fallback, a paused list returns to live after 60 s, and LVGL allocates from the heap"
author: Justin
date: 2026-11-09
branch: main
cost: { minutes: 40, tokens: 301805 }
id: l-e2e46958
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/caption_format.h
  - firmware/relay-display/README.md
  - firmware/relay-display/main/caption_view_format.h
---

## Decided
- A scrolled-back caption list returns to live by itself after 60 s with no touch (the issue's open question), because a wall panel nobody is touching should not show old lines for good. Font size is fixed at 36 px; it does not follow Relay's default_font_px yet because the panel does not receive it.
- Montserrat does not cover everything the client keeps (about 225 Latin Extended letters are missing), so DejaVu Sans is the generated fallback font. The kept set stops at U+1EFB: neither font has U+1EFC-1EFF, so those fold to "?".
- LVGL allocates from the heap (CONFIG_LV_USE_CLIB_MALLOC) instead of its 64 KB pool, because one label per line for 200 lines does not fit in the pool.
- Scroll events caused by the view's own work (dropping the oldest lines shrinks the content and LVGL pulls the position back to the new end) are ignored; otherwise a paused view resumed live by itself when the 201st line arrived.

## Tried and ruled out
- One label holding all the text: it saves memory but loses the gap between captions and makes keeping the scroll position on eviction depend on measuring text by hand.

## Watch out
- The emulator reads the finger once per frame and a frame takes about a second, so tests must wait for "pointer down" before moving or lifting, hold still before lifting (or LVGL throws the list on), and wait for the bench picture after "feed 0 off" before reading the framebuffer.
- Smoothness of drag scrolling with 200 lines is unmeasured: only the bench can say.

In the code: firmware/relay-display/main/caption_view.cpp, caption_view_format.h (native-tested rules), tools/gen_caption_font.sh, tests/test_caption_view.py, tests/test_caption_font.py.
