#pragma once

// The faces for the UI scale in `ui_scale.h`: LVGL's Montserrat for the small, medium and large tiers, and the
// generated caption font (`main/fonts/`, `tools/gen_caption_font.sh`) for caption-sized text.
//
//   tier      100%   75%   65%
//   small      20     16    14
//   medium     28     22    18
//   large      40     30    26
//   caption    36     27    23

#include "lvgl.h"
#include "ui_scale.h"

LV_FONT_DECLARE(relay_caption_27);
LV_FONT_DECLARE(relay_caption_23);

#if RELAY_UI_SCALE_PCT == 65
#define RELAY_UI_FONT_SMALL lv_font_montserrat_14
#define RELAY_UI_FONT_MEDIUM lv_font_montserrat_18
#define RELAY_UI_FONT_LARGE lv_font_montserrat_26
#define RELAY_UI_FONT_CAPTION relay_caption_23
#elif RELAY_UI_SCALE_PCT == 75
#define RELAY_UI_FONT_SMALL lv_font_montserrat_16
#define RELAY_UI_FONT_MEDIUM lv_font_montserrat_22
#define RELAY_UI_FONT_LARGE lv_font_montserrat_30
#define RELAY_UI_FONT_CAPTION relay_caption_27
#else
#error "RELAY_UI_SCALE_PCT must be 65 or 75; add its fonts to ui_fonts.h"
#endif
