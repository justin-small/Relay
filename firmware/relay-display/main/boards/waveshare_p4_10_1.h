#pragma once

#include <cstdint>

// The Waveshare ESP32-P4-WIFI6-Touch-LCD-10.1 (SKU 33672): an ESP32-P4 (32 MB PSRAM in the package, 32 MB
// flash) driving a 10.1" 800 x 1280 panel over two-lane MIPI-DSI, with Wi-Fi from an ESP32-C6 beside it.
// `config.h` includes this file when the build is for this board, and nothing else includes it.
// `board_dsi_jd9365.cpp` is the driver that reads the panel's values.
//
// **Nothing here has been on a bench.** Every value is Waveshare's, from their board support package
// (`waveshare/esp32_p4_wifi6_touch_lcd_x` 2.0.3) and their repository's README, except where a line says
// otherwise. README.md, "Boards", lists what the first board on a desk has to confirm.

#define RELAY_BOARD_NAME "Waveshare ESP32-P4-WIFI6-Touch-LCD-10.1"
// The board's short name: the build directory's and the sdkconfig file's (`sdkconfig.board.<this>`).
#define RELAY_BOARD_ID "waveshare-p4-10.1"

// ---------------------------------------------------------------------------
// The panel
//
// **The glass is portrait: 800 wide and 1280 tall, as the JD9365 scans it.** That is the picture this
// firmware draws for now. A panel on a wall wants it on its side, 1280 by 800, and a DSI panel has no
// quarter turn of its own: that turn is made in `display.cpp`, beside the half turn it already makes, and
// is not written yet (README.md, "Boards").
// ---------------------------------------------------------------------------

constexpr int kLcdWidth = 800;
constexpr int kLcdHeight = 1280;

// The DSI driver has no mirror, so `display.cpp` makes the 180 degree turn, as it does for the emulator.
constexpr bool kPanelTurnsInSoftware = true;

// MIPI-DSI: two data lanes at 1.5 Gbit/s each. The bus has no GPIO numbers to give: its pads are the chip's own.
constexpr int kDsiLanes = 2;
constexpr uint32_t kDsiLaneMbps = 1500;

// The DSI pads are powered from the chip's own regulator: channel 3 (LDO_VO3, wired to VDD_MIPI_DPHY) at 2.5 V.
// Without it the bus stays in "no power" and the panel is dark.
constexpr int kDsiPhyLdoChannel = 3;
constexpr int kDsiPhyLdoMillivolts = 2500;

// The panel's reset line. The touch controller's reset and interrupt are not wired to the chip.
constexpr int kPinLcdReset = 27;

// The video timing is not here: it is the JD9365 driver's own for an 800 x 1280 panel at 60 Hz
// (`JD9365_800_1280_PANEL_60HZ_DPI_CONFIG`, in the managed component), which is what Waveshare's package uses.

// ---------------------------------------------------------------------------
// The backlight: a PWM pin, high for on. Waveshare drive it at 5 kHz with ten bits of duty, and full
// brightness is a duty of 1023. This firmware only uses full and off for now.
// ---------------------------------------------------------------------------

constexpr int kPinBacklight = 26;
constexpr uint32_t kBacklightPwmHz = 5000;
constexpr uint32_t kBacklightDutyMax = 1023;

// **GPIO35 is the BOOT button, active low.** It is the ESP32-P4's boot-mode strapping pin, which is where a
// BOOT button has to be; Waveshare's package does not name it (it lists no buttons), so this is the chip's
// fact and the board's is a bench item. Read only after the ROM has sampled it at reset, as on the CrowPanel.
constexpr int kPinBoot = 35;

// ---------------------------------------------------------------------------
// The board's I2C bus (`board_i2c.cpp`): the touch controller, and the audio codecs this firmware does not use.
// ---------------------------------------------------------------------------

constexpr int kPinI2cSda = 7;
constexpr int kPinI2cScl = 8;
constexpr uint32_t kI2cSpeedHz = 400000;

// ---------------------------------------------------------------------------
// Touch: a GT9271, which speaks the GT911's registers and reports up to ten fingers. It answers at 0x5D or
// 0x14, and `touch.cpp` tries both, as Waveshare's package does. Its product ID register reads "9271".
// Coordinates are the glass's own: 800 by 1280.
// ---------------------------------------------------------------------------

constexpr char kTouchProductId[] = "9271";
constexpr int kTouchMaxPoints = 10;

// ---------------------------------------------------------------------------
// Wi-Fi is not this chip's: the ESP32-P4 has no radio. An ESP32-C6-MINI-1 on the board is the radio, reached
// over SDIO by `esp_hosted`, and `esp_wifi_remote` gives the application the same `esp_wifi` calls `net.cpp`
// already makes. The SDIO pins are esp_hosted's defaults for the ESP32-P4 (`sdkconfig.board.waveshare-p4-10.1`),
// so there are none to name here.
// ---------------------------------------------------------------------------
