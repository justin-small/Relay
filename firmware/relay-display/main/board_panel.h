#pragma once

#include <cstdint>

#include "esp_err.h"
#include "esp_lcd_types.h"

// What a board's own driver gives `display.cpp`: the glass and its light. One file per kind of board
// implements this, and `CMakeLists.txt` compiles the one the build's board names:
//
//   board_rgb_stc8.cpp     the CrowPanel Advance 7.0: the ESP32-S3's RGB interface, backlight through an STC8
//   board_dsi_jd9365.cpp   the Waveshare ESP32-P4 10.1": MIPI-DSI to a JD9365, backlight on a PWM pin
//
// Everything above this line is shared: `display.cpp` paints strips and rectangles into whatever
// `esp_lcd` panel the board hands back, so LVGL, the bench picture and the views do not know which
// board they are on. The emulator build compiles neither file: `display.cpp` makes the virtual panel itself.

// Reach the backlight and switch it off. The first hardware call of the boot, so the panel is dark until a
// whole picture is on it. A backlight that cannot be reached is logged and is not an error: the boot goes on
// with the screen dark, so this returns ESP_OK then too.
esp_err_t board_backlight_init();

// Full brightness, or off. Returns the reason when the backlight did not take it.
esp_err_t board_backlight(bool on);

// Bring up the panel and hand back its `esp_lcd` handle, ready for `esp_lcd_panel_draw_bitmap`. A board whose
// driver can mirror applies `rotation_deg` here; one that cannot (`kPanelTurnsInSoftware`) ignores it, and
// `display.cpp` turns the picture.
esp_err_t board_panel_init(int rotation_deg, esp_lcd_panel_handle_t *out);

// The panel's two frame buffers, when the driver keeps two. `display.cpp` then draws into the one that is not
// being scanned and flips by passing that buffer to `esp_lcd_panel_draw_bitmap`, so a redraw is never seen half
// done. ESP_ERR_NOT_SUPPORTED on a board with one buffer, which is written in place.
esp_err_t board_panel_frame_buffers(void **a, void **b);

// Frames the panel has finished scanning since `board_panel_init`.
uint32_t board_panel_frames_done();

// Put the picture back in place after a flash write, on a board where a write can shift it. A no-op elsewhere.
void board_panel_resync();

// Change the pixel clock while the panel runs, for the bench. ESP_ERR_NOT_SUPPORTED on a board with no such knob.
esp_err_t board_panel_set_pclk(uint32_t hz);
