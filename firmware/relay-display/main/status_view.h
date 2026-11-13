#pragma once

// The status view on the glass (#224): a pane on the Status feed (`kFeedStatus`) under the header
// `caption_view.cpp` draws. A band across the top says whether the livestream is on air (red, with how long) or off,
// and rows below say whether captions are running and for how long, the input level, the live languages, how many
// are watching, and each livestream source with its health.
//
// The panel counts the elapsed times itself between Relay's messages (`status_seconds`), and sets a label only when
// its words or colour change, so a still status is not redrawn. When Relay has not been heard from for 20 s the
// values go grey: they are no longer believed.
//
// The rules (parsing, the words, the colours) are in `status_format.h`, checked on a laptop. **Everything that draws
// runs on the LVGL task under `screen_lock`.**

#include <cstdint>

#include "lvgl.h"
#include "status_format.h"

// Build the Status pane `pane` (0 or 1) in `box`, a column `w` x `h`, reading caption slot `slot`.
void status_view_build(int pane, lv_obj_t *box, int slot, int w, int h);

// Count on, and take in what changed.
void status_view_tick(int pane, uint32_t now_ms);

// Stop drawing both panes; the objects go with the screen.
void status_view_teardown();

// The console's `view`: what each status pane shows.
void status_view_report();
