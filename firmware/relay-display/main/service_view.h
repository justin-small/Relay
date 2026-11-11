#pragma once

// The Planning Center service in one pane (#82), under the pane's header that `caption_view.cpp` draws:
//
//   * **Now**: the current item in large type, its time left counting down (red once over), and the next item;
//   * **the order of service**, scrollable by touch, the current item highlighted, headers as section breaks, songs
//     with their key and arrangement sequence. It keeps the current item in view, and pauses with "Jump to now" when
//     a finger scrolls it, like the caption view; a minute without a touch goes back;
//   * a tap on an item with notes (only sent while the operator allows them) shows them;
//   * **the footer**: over/under and who is controlling LIVE, or "Not live", "Planning Center unreachable".
//
// The rules (the message, the timers, the words) are in `service_format.h`, checked on a laptop. This is the drawing.
// **Everything here runs on the LVGL task under `screen_lock`**, called from the caption view's tick; the service
// state is copied out of the caption slot under its lock (`captions_read_service`), in the screen-then-feed order.

#include <cstddef>
#include <cstdint>

#include "lvgl.h"

// Build the service body for pane `pane` (0 or 1) in `box`, a column `w` x `h`, under the
// header. It reads caption slot `slot`.
void service_view_build(int pane, lv_obj_t *box, int slot, int w, int h);

// Take in what changed and move the timers. `touching` is whether a finger is on the panel.
void service_view_tick(int pane, uint32_t now_ms, bool touching);

// The service's name for the pane's header (ASCII: the header's font has no accents). False until Relay has said.
bool service_view_title(int pane, char *out, size_t cap);

// Stop drawing both panes; the objects go with the screen.
void service_view_teardown();

// The console's `view`: one line per service pane.
void service_view_report();
