#pragma once

#include <cstdint>

#include "config.h"

// The board's choices that are pure, with no ESP-IDF in them. `display.cpp`
// sends the bytes; `test_native.cpp` checks the choice on a laptop, so a wrong
// backlight byte for a revision fails the build before a board is plugged in.

// The two STC8 commands the backlight uses: `on` is the brightest, `off` is dark.
struct stc8_backlight_commands {
    uint8_t on;
    uint8_t off;
};

// The pair for one revision. `v12` is CONFIG_RELAY_BOARD_ADV_V12 at the call in
// `display.cpp`. It is a parameter and not a `#if` here, so this header does not
// need `sdkconfig.h`, and the native test can check both pairs in one build.
constexpr stc8_backlight_commands stc8_backlight_for(bool v12)
{
    return v12 ? stc8_backlight_commands{kStc8BacklightOnV12, kStc8BacklightOffV12}
               : stc8_backlight_commands{kStc8BacklightOnV13, kStc8BacklightOffV13};
}
