#pragma once

#include <cstdint>

// The Elecrow CrowPanel Advance 7.0 (DIS02170A), hardware V1.2 and later: an ESP32-S3-WROOM-1-N16R8
// driving an 800 x 480 panel over the RGB interface, with the backlight behind an STC8 on the I2C bus.
// `config.h` includes this file when the build is for this board, and nothing else includes it.
// `board_rgb_stc8.cpp` is the driver that reads the pins and the timing.

#define RELAY_BOARD_NAME "CrowPanel Advance 7.0 DIS02170A"
// The board's short name: the build directory's and the sdkconfig file's (`sdkconfig.board.<this>`).
#define RELAY_BOARD_ID "crowpanel-adv7"

// ---------------------------------------------------------------------------
// The panel
//
// Every value here is an electrical fact about the board or the panel, not a
// choice. The sources, and what is still unconfirmed on the bench, are in
// README.md under "Pin map and timing". The pins are Elecrow's for the CrowPanel
// Advance 7.0 (DIS02170A) at hardware revision V1.2 and later, as its wiki and
// its V1.3 examples name them.
// ---------------------------------------------------------------------------

constexpr int kLcdWidth = 800;
constexpr int kLcdHeight = 480;

// The RGB interface's timing signals. None of them is a strapping pin.
constexpr int kPinPclk = 39;
constexpr int kPinHsync = 40;
constexpr int kPinVsync = 41;
constexpr int kPinDe = 42;

// The sixteen data lines in the order the driver numbers them: index 0 is the
// blue LSB (B0) and index 15 is the red MSB (R4). A swapped channel on the bench
// means the list is in the wrong order, and the test screen's colour bars show it.
constexpr int kPinData[16] = {
    21, 47, 48, 45, 38,      // B0..B4
    9, 10, 11, 12, 13, 14,   // G0..G5
    7, 17, 18, 3, 46,        // R0..R4
};

// Porches and sync widths, in pixels for the horizontal and lines for the vertical.
// These are the same on every V1.2 and V1.3-and-later example Elecrow publishes
// for this panel; the total is 820 by 500 clocks a frame.
constexpr int kHsyncPulse = 4;
constexpr int kHsyncBack = 8;
constexpr int kHsyncFront = 8;
constexpr int kVsyncPulse = 4;
constexpr int kVsyncBack = 8;
constexpr int kVsyncFront = 8;

// **GPIO0 is the BOOT button, active low, and it is not a display pin.** Holding it
// for five seconds opens the setup network, the same as `recover`
// on the serial console, and holding on to fifteen is a factory reset. It is a strapping pin, so the firmware reads it only after the
// ROM has sampled it at reset; a press held across a reset is the bootloader's.
constexpr int kPinBoot = 0;

// ---------------------------------------------------------------------------
// The board's I2C bus (`board_i2c.cpp`). Three devices share it: the GT911 touch
// at 0x5D (or 0x14, by its INT level at reset), which `touch.cpp` reads; the PCF8563 real-time clock at 0x51,
// which rtc.cpp reads at boot; and the STC8H1K28 microcontroller at 0x30, which
// switches the backlight here. The pins are Elecrow's; the speed is the one the
// touch controller's examples use.
// ---------------------------------------------------------------------------

constexpr int kPinI2cSda = 15;
constexpr int kPinI2cScl = 16;
constexpr uint32_t kI2cSpeedHz = 400000;

constexpr uint8_t kStc8Addr = 0x30;

// ---------------------------------------------------------------------------
// The STC8's backlight commands, one pair per revision. A command is a single byte
// written to 0x30 with nothing before it: there is no register byte. The value 0 is
// the brightest; the off values are the ones Elecrow's readme gives for each
// revision. The V1.2 off byte is the one no example sends, so the bench check is
// the first time it is used.
//
// **V1.2 and V1.3 or later cannot be told apart in software.** The STC8 takes any
// byte it is sent and does not answer with its version, so the Kconfig choice is
// the only thing that picks a pair. The STC8 touch-activate command is not sent: the
// firmware does not know it, so a board that needs it shows up as a GT911 that does not answer.
// ---------------------------------------------------------------------------

constexpr uint8_t kStc8BacklightOnV12 = 0x10;
constexpr uint8_t kStc8BacklightOffV12 = 0x05;
constexpr uint8_t kStc8BacklightOnV13 = 0;
constexpr uint8_t kStc8BacklightOffV13 = 245;

// ---------------------------------------------------------------------------
// Touch: a GT911, which reports up to five fingers. Its product ID register reads "911" and a NUL.
// ---------------------------------------------------------------------------

constexpr char kTouchProductId[] = "911";
constexpr int kTouchMaxPoints = 5;

// **`display.cpp` makes the 180 degree turn.** The RGB driver's mirror applies only to a bitmap it copies in,
// and the two frame buffers are flipped by handing the driver one of its own, which skips the copy and the
// mirror with it. So the turn is made while strips are written into the back buffer.
constexpr bool kPanelTurnsInSoftware = true;
