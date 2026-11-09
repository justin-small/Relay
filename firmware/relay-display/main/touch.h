#pragma once

#include "esp_err.h"
#include "touch_format.h"

// The touch screen, as LVGL's pointer.
//
// The reader is behind `touch_source`, so what produces touches can be swapped: the board
// reads the GT911 over I2C, and the emulator, which has no touch controller, takes touches
// injected from the serial console (`touch <x> <y>` and `touch up`, in `provision.cpp`'s console).
// LVGL sees the same pointer either way, and polls it from its own timer.

struct touch_source {
    virtual ~touch_source() = default;
    // The state of the finger now, in the panel's own coordinates. Returns `none` when there is
    // nothing new, and an error when the controller could not be read.
    virtual esp_err_t read(touch_report *report, touch_raw *out) = 0;
};

// Find the controller (on the board) or set up the injection (in the emulator), and register
// LVGL's pointer with coordinates turned to match `rotation_deg`. Call after `screen_start`.
// A board whose controller does not answer returns an error and the panel carries on without
// touch: the captions do not need it.
esp_err_t touch_start(int rotation_deg);

// Emulator only: put the finger down at the panel position (x, y), or lift it. Coordinates are
// the controller's own, so the rotation is applied to them the way it is to a real report.
// No-ops on the board.
void touch_inject(int x, int y, bool pressed);
