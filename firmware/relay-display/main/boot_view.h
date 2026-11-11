#pragma once

// The boot picture: the Relay logo, drawn as it is written. The upright rises, the caption bubble appears, its two
// lines are written, and the word "relay" is written beside it. Then it holds, and the bubble's second line keeps
// being rewritten so the panel is seen to be working.
//
// It is on the glass from power-on until the feeds the panel shows are loaded (`screen.cpp` decides when it ends).
// The two images are `boot_logo.c`, generated from the logo's drawing by `brand/panel_assets.py`.
//
// Drawn by LVGL on its task, under `screen_lock`, like every other picture. Clearing the root ends it: LVGL stops an
// object's animations when the object is deleted.

#include "lvgl.h"

// Build the picture in `root`, which the caller has emptied, and start it moving.
void boot_view_build(lv_obj_t *root);

// How long the writing takes, from the first frame to the whole logo. The picture is never taken down sooner.
constexpr uint32_t kBootWriteMs = 2400;
