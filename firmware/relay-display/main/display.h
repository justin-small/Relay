#pragma once

#include <cstdint>

#include "esp_err.h"
#include "panel_format.h"

// The panel: an 800 x 480 RGB565 IPS panel driven by the ESP32-S3's RGB interface
// through `esp_lcd`. Pin numbers and timings are in `config.h`.

// Start the board's I2C bus, find the STC8 that switches the backlight, and send it the
// off command. This is the first hardware call in `app_main`, before any other work, so
// the panel is dark for the rest of the boot. A missing STC8 is logged and the boot goes
// on with the screen dark, so this returns ESP_OK in that case too.
esp_err_t display_backlight_init();

// Switch the backlight through the STC8. `true` is the brightest and `false` is off.
// Returns the bus error when the STC8 does not take the command.
esp_err_t display_backlight(bool on);

// Bring up the RGB panel with its frame buffer in PSRAM. `rotation_deg` is 0, 90, 180 or 270 (`panel_format.h`);
// every turn is made in software as rectangles are written.
esp_err_t display_init(int rotation_deg);

// The picture's size at the rotation the panel was set up with: 800 x 480, or 480 x 800 for 90 and 270. Everything
// LVGL lays out is in these coordinates.
int display_width();
int display_height();
int display_rotation();

// Put the picture back in place after a flash write. With the frame buffer in PSRAM and no bounce buffer, a
// flash write holds the bus the panel's DMA reads, the DMA falls behind, and the picture lands shifted to the
// right until the DMA is restarted. This asks the driver to restart it in the next vertical blank, and again half
// a second later for a write that Wi-Fi finishes after its call returns. Every caller that writes flash calls it.
// No-op before the panel starts and under the emulator.
void display_resync();

// Change the pixel clock while the panel runs, for the bench's `pclk` console command (development builds). The
// driver applies it in the next vertical blank. No-op under the emulator.
esp_err_t display_set_pclk(uint32_t hz);

// Frames the panel has completed since boot, for the development diagnostics' frame rate.
uint32_t display_frames_done();

// Paint the whole panel one RGB565 colour.
esp_err_t display_fill(uint16_t rgb565);

// Paint the bench test screen: eight colour bars, a red marker in the top-left
// corner of the frame, the name "VXP" and `version`, and a white border. Then wait
// for the panel to finish two whole frames and turn the backlight on, so the first
// lit picture is a complete one.
//
// Returns ESP_ERR_TIMEOUT when the panel does not complete two frames, and the bus
// error when the STC8 does not take the backlight command. The screen is painted
// either way and the backlight stays off.
esp_err_t display_show_test_screen(const char *version);

// Write a rectangle of logical pixels into the panel. `x1..x2` and `y1..y2` are inclusive, in the
// upright picture's coordinates. `pixels` holds the rows of the rectangle, `stride` pixels apart, and the
// first pixel is at (x1, y1). The turn for the rotation is made here, on the board and the emulator
// alike, as `paint` makes it for the bench screen.
// Partial updates: only the rectangle is written, and the rest of the frame stays as it was.
// On a board with two frame buffers the rectangle goes into the one that is not on the glass, and shows
// when `display_present` is called.
esp_err_t display_write_rect(int x1, int y1, int x2, int y2, const uint16_t *pixels, int stride);

// Show what `display_write_rect` has written since the last call, all at once. A no-op where there is one
// frame buffer, which shows each write as it lands.
esp_err_t display_present();

