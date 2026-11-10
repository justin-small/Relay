#pragma once

// What is on the glass, drawn with LVGL into the RGB panel's frame buffer.
//
// This draws three things: lines of centred text (the setup network, the Wi-Fi
// status), with an optional QR code beside them, the bench picture (the boot's colour
// bars), and the caption view (`caption_view.cpp`), which draws through the same lock and task.
//
// Written for this target from the VXP compact display's `room_screen.cpp` (vxp-compact-display
// 0.2.2): the LVGL port (partial buffer, flush callback, one drawing task, one recursive
// lock) is the same, and the room, notice and evacuation drawing is not here. The fonts are
// LVGL's own Montserrat, so no generated font files are carried.
//
// **Every LVGL call is under `screen_lock`.** LVGL is not thread-safe. Callers ask for a
// picture and the drawing task draws it, so a caller on a small stack never builds a
// screen itself.

#include "esp_err.h"
#include "panel_format.h"

// Start LVGL: the display, its partial buffer, and the task that runs its timers. Nothing is
// written to the panel until a picture is asked for.
esp_err_t screen_start();

void screen_lock();
void screen_unlock();

// Show `lines` of text, centred. `qr`, when given, is drawn as a QR code to the left of the
// lines, which then stand in the column beside it: the setup network's join code
// (`setup_wifi_qr`). Null for every other screen.
esp_err_t screen_show_lines(const panel_text_line *lines, int count, const char *qr = nullptr);

// Show the caption view (`caption_view.h`): one feed or two, as `feeds.h` says. It takes over the glass until another
// picture is asked for, and draws what the feeds hold and keeps up with them. Asked again, it is built again, which
// is how a change between one feed and two reaches the glass.
esp_err_t screen_show_captions();

// The bench picture: the boot's colour bars. A bench aid, shown only by the `bench` console command. The panel
// never falls back to it: with nothing to say it shows the captions view, which says it is offline.
// Written raw, not drawn by LVGL, and LVGL is inactive while it is up.
esp_err_t screen_show_bench(const char *version);
