#pragma once

// ProPresenter's timers on the glass (#138): a pane on the Timers feed (`kFeedTimers`) under the header
// `caption_view.cpp` draws. Each timer Relay sends is a row: its name and state small, its time large, white while
// it runs and amber and then red as the countdown nears zero (the thresholds are Relay's, set in admin), red with a
// minus sign once it is past it.
//
// **The panel counts the time itself** between Relay's messages (`pp_timer_seconds`), so it ticks smoothly with no
// request a second, and redraws only the digit that changed: the digits sit in fixed-width cells, one label each,
// because the font's digits are proportional and a ticking "1" would otherwise shift its neighbours. A still timer
// (stopped, finished) is not redrawn at all.
//
// The rules (parsing, counting, the colour, the sizes) are in `pp_format.h`, checked on a laptop. **Everything that
// draws runs on the LVGL task under `screen_lock`.**

#include <cstdint>

#include "lvgl.h"
#include "pp_format.h"

// Build the Timers pane `pane` (0 or 1) in `box`, a column `w` x `h`, reading caption slot `slot`.
void timers_view_build(int pane, lv_obj_t *box, int slot, int w, int h);

// Count on, and take in what changed.
void timers_view_tick(int pane, uint32_t now_ms);

// Stop drawing both panes; the objects go with the screen.
void timers_view_teardown();

// The console's `view`: what each timers pane shows.
void timers_view_report();
