#pragma once

// What the touch controller reports and how it becomes a position on the picture, with no
// ESP-IDF in it. `touch.cpp` owns the bus and LVGL and calls these; `test_native.cpp` compiles
// this header with g++ and checks it on a laptop.

#include <cstdint>

#include "config.h"
#include "panel_format.h"

// ---------------------------------------------------------------------------
// The GT911
//
// Registers are 16-bit and are sent high byte first. The controller answers at one of two
// addresses, picked by the level of its INT pin while it comes out of reset: 0x5D (INT low) or
// 0x14 (INT high). `kGt911AddrPrimary` is the one the board's documentation gives; the firmware
// tries the other if nothing answers there (`touch.cpp`).
// ---------------------------------------------------------------------------

constexpr uint8_t kGt911AddrPrimary = 0x5D;
constexpr uint8_t kGt911AddrAlternate = 0x14;

constexpr uint16_t kGt911RegProductId = 0x8140;  // four ASCII bytes: "911" and a NUL
constexpr uint16_t kGt911RegStatus = 0x814E;     // bit 7: a report is ready; bits 0-3: how many points
constexpr uint16_t kGt911RegPoint1 = 0x814F;     // the first point's eight bytes follow the status

// Status byte, then the first point: track id, x low, x high, y low, y high, size low, size high.
constexpr int kGt911ReadBytes = 9;
// How many fingers the board's controller can report (`kTouchMaxPoints`, in the board's header): five for
// the GT911, ten for the GT9271, which speaks the same registers. Only the first is read either way.
constexpr int kGt911MaxPoints = kTouchMaxPoints;

// Whether the four bytes of the product ID register are this board's controller: the board's ID
// (`kTouchProductId`, "911" or "9271") and then NULs to the end of the register.
constexpr bool gt911_id_matches(const uint8_t (&id)[4])
{
    bool ended = false;
    for (int i = 0; i < 4; ++i) {
        const char want = ended ? '\0' : kTouchProductId[i];
        ended = ended || want == '\0';
        if (id[i] != static_cast<uint8_t>(want)) {
            return false;
        }
    }
    return true;
}

// One report from the controller, in the panel's own (unmirrored) coordinates.
struct touch_raw {
    bool pressed = false;
    int x = 0;
    int y = 0;
};

enum class touch_report : uint8_t {
    none,      // nothing new since the last read: the previous state stands
    released,  // a report with no points: the finger is up
    pressed,   // a report with at least one point; `out` holds the first
};

// Decode the nine bytes read from `kGt911RegStatus`. The ready bit says whether the controller has
// a fresh report; without it the bytes are the old ones and are ignored. A point count above what
// the chip can track is a glitch on the bus, and is also ignored rather than read as a release,
// so a noisy read cannot lift a finger that is down.
constexpr touch_report gt911_decode(const uint8_t (&buf)[kGt911ReadBytes], touch_raw *out)
{
    const uint8_t status = buf[0];
    if ((status & 0x80) == 0) {
        return touch_report::none;
    }
    const int count = status & 0x0F;
    if (count > kGt911MaxPoints) {
        return touch_report::none;
    }
    if (count == 0) {
        out->pressed = false;
        return touch_report::released;
    }
    out->pressed = true;
    out->x = buf[2] | (buf[3] << 8);
    out->y = buf[4] | (buf[5] << 8);
    return touch_report::pressed;
}

// ---------------------------------------------------------------------------
// Rotation
// ---------------------------------------------------------------------------

// The controller reports where the glass was touched, and the glass does not know how the picture
// is turned. The touch is turned back the way the picture was turned onto the glass
// (`panel_logical_x`, `panel_logical_y`), so it lands on what was drawn there. A report outside the panel
// (the controller's range is its own and a worn edge can read past it) is pulled onto the edge first.
constexpr int touch_clamp(int v, int max)
{
    return v < 0 ? 0 : (v > max ? max : v);
}

constexpr int touch_logical_x(int raw_x, int raw_y, int rotation_deg)
{
    return panel_logical_x(touch_clamp(raw_x, kLcdWidth - 1), touch_clamp(raw_y, kLcdHeight - 1), rotation_deg);
}

constexpr int touch_logical_y(int raw_x, int raw_y, int rotation_deg)
{
    return panel_logical_y(touch_clamp(raw_x, kLcdWidth - 1), touch_clamp(raw_y, kLcdHeight - 1), rotation_deg);
}
