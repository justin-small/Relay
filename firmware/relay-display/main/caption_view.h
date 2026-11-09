#pragma once

// The caption screen: a header (the feed's name and the connection), a list of finished lines that follows
// live, and a "Jump to live" button that appears once a finger has taken the list back in time.
//
// The rules (following, pausing, what to add or drop when the buffer changes, the header's words) are in
// `caption_view_format.h`, checked on a laptop. This is the drawing and the touch.
//
// **Everything here runs on the LVGL task**, under `screen_lock`: `screen.cpp` builds the view from its
// task, and the view's own timer polls `captions_revision` and redraws what changed. It never touches the
// caption client's task. The lock order is the screen's, then a feed's.

#include "lvgl.h"

// Build the view for caption slot `slot` on `root`, which `screen.cpp` has just emptied.
void caption_view_build(lv_obj_t *root);

// Stop updating. Called before the screen is cleared or handed to another picture; the objects themselves
// go with the screen.
void caption_view_teardown();

// The console's `view`: log whether the list follows, how many lines it holds and how far it is from the
// bottom. Takes the screen lock.
void caption_view_report();
