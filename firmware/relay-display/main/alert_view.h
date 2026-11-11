#pragma once

// Alerts on the glass (#121): two things.
//
//  * **The Alert pane**, a feed like the Service (`kFeedAlert`): Relay's alert buttons, one per row, each with how its
//    last alert went ("Flashing - from Drums - tap to stop", "Seen on Booth - just now"). A tap sends it, or stops the
//    one flashing (`alert_client.h`). Under the header `caption_view.cpp` draws.
//  * **The flash**: when Relay says this panel is flashed, the whole screen, over any view, turns red and white once a
//    second with who needs whom, until a tap anywhere acknowledges it, Relay ends it, or its auto-stop passes.
//
// The rules are in `alert_format.h`, checked on a laptop. **Everything that draws runs on the LVGL task under
// `screen_lock`**; `alert_view_flash` and `alert_view_flash_end` are the panel link's, from its own task, and only
// leave word for the next tick.

#include <cstddef>
#include <cstdint>

#include "alert_format.h"
#include "lvgl.h"

// Build the Alert pane `pane` (0 or 1) in `box`, a column `w` x `h`, reading caption slot `slot`.
void alert_view_build(int pane, lv_obj_t *box, int slot, int w, int h);

// Take in what changed.
void alert_view_tick(int pane, uint32_t now_ms);

// Stop drawing both panes; the objects go with the screen.
void alert_view_teardown();

// Start the flash's own timer. Call once, after `screen_start`.
void alert_view_start();

// From the panel link: flash for `alert`, or stop flashing alert `id` (a different one is left alone).
void alert_view_flash(const alert_flash &alert);
void alert_view_flash_end(const char *id);

// The console's `view`: the panes, and the flash if one is up.
void alert_view_report();
