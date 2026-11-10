#pragma once

#include <cstdint>

// The board and the firmware, as the code knows them. Nothing in this file names
// an installation. The Wi-Fi network, the Relay address, the panel ID and the saved feed are settings:
// they are written to NVS by the device's own page, and `settings.cpp`
// reads them back. The released image holds none of them, which is the rule the
// I/O board follows, so one image can be sold to every site.

// The version the panel shows and the boot line reports. `CMakeLists.txt` reads
// this line; keep it in exactly this form. House rule 12 holds the I/O board's
// version to the product version, and this target is not covered by it yet, so
// the number here is the firmware's own until a release says otherwise.
#define RELAY_FW_VERSION "0.1.0"

// ---------------------------------------------------------------------------
// The board
//
// One firmware, several boards. Everything that is an electrical fact about one board is in that
// board's header under `boards/`, and the build picks one: `build.sh --board <name>` applies
// `sdkconfig.board.<name>`, which sets the chip, and "Board" in `Kconfig.projbuild` follows the chip.
// The native test has no sdkconfig, so it names the board with a `-D` (`build.sh`), and with none it
// checks the CrowPanel.
//
// **What every board header defines**, and the shared code reads:
//
//   RELAY_BOARD_NAME, RELAY_BOARD_ID     the name on the boot line, and the short name builds use
//   kLcdWidth, kLcdHeight                the picture LVGL draws, in pixels
//   kPanelTurnsInSoftware                true when the panel's driver cannot mirror, so `display.cpp` turns
//                                        the picture for 180 degrees itself, as it does for the emulator
//   kPinBoot                             the BOOT button, active low
//   kPinI2cSda, kPinI2cScl, kI2cSpeedHz  the board's one I2C bus (`board_i2c.cpp`)
//   kTouchProductId, kTouchMaxPoints     what the Goodix touch controller answers with, and how many
//                                        fingers it can report (`touch_format.h`)
//
// Everything else in a board header (the panel's pins and timing, the backlight) is read only by that
// board's driver, `board_<...>.cpp`, which implements `board_panel.h`.
// ---------------------------------------------------------------------------

#if defined(ESP_PLATFORM)
#include "sdkconfig.h"
#endif

#if defined(CONFIG_RELAY_BOARD_WAVESHARE_P4_10_1) || defined(RELAY_NATIVE_BOARD_WAVESHARE_P4_10_1)
#include "boards/waveshare_p4_10_1.h"
#else
#include "boards/crowpanel_adv7.h"
#endif
