#pragma once

// **The one number that sizes the panel's UI.** Every pixel size in `screen.cpp`, `caption_view.cpp`,
// `service_view.cpp`, `caption_view_format.h` and `panel_format.h` is written as `ui_px(<size at 100%>)`, so
// changing `RELAY_UI_SCALE_PCT` rescales margins, gaps, buttons and cards together. The fonts are bitmaps and
// come in fixed sizes, so `ui_fonts.h` picks the nearest face for each tier; the sizes it knows are 75 and 65.
//
// To change the scale: edit the number here, run `tools/gen_caption_font.sh` if that size's caption font is not in
// `main/fonts/` yet, and make sure the Montserrat sizes `ui_fonts.h` names are on in `sdkconfig.defaults`.
// 100 is the size the panel shipped at first (a 36 px caption font); 75 and 65 were tried on the bench.
#ifndef RELAY_UI_SCALE_PCT
#define RELAY_UI_SCALE_PCT 65
#endif

constexpr int kUiScalePct = RELAY_UI_SCALE_PCT;

// A size designed at 100%, at the current scale, rounded to the nearest pixel.
constexpr int ui_px(int at_100)
{
    return (at_100 * kUiScalePct + (at_100 < 0 ? -50 : 50)) / 100;
}
