#pragma once

// Small LVGL helpers the ProPresenter views share (`lyrics_view.cpp`, `timers_view.cpp`). Header-only, and every
// function runs on the LVGL task under `screen_lock`.
//
// **Nothing is set to the value it already has.** LVGL 9.2 redraws on every `lv_label_set_text`, local style set and
// un-hide, changed or not, and a view's tick runs twenty times a second: a still screen must not be redrawn (the
// CrowPanel flickered in bands from exactly that; README, "Caption view").

#include <cstring>

#include "lvgl.h"

namespace ppv {

inline lv_color_t hex(uint32_t rgb)
{
    return lv_color_hex(rgb);
}

inline lv_obj_t *plain_box(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
    return o;
}

inline lv_obj_t *text(lv_obj_t *parent, const lv_font_t *font, uint32_t colour)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, hex(colour), 0);
    lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
    return l;
}

inline void set_text(lv_obj_t *label, const char *words)
{
    if (strcmp(lv_label_get_text(label), words) != 0) {
        lv_label_set_text(label, words);
    }
}

inline void set_colour(lv_obj_t *label, uint32_t rgb)
{
    if (!lv_color_eq(lv_obj_get_style_text_color(label, 0), hex(rgb))) {
        lv_obj_set_style_text_color(label, hex(rgb), 0);
    }
}

inline void set_font(lv_obj_t *label, const lv_font_t *font)
{
    if (lv_obj_get_style_text_font(label, 0) != font) {
        lv_obj_set_style_text_font(label, font, 0);
    }
}

inline void set_shown(lv_obj_t *o, bool shown)
{
    if (lv_obj_has_flag(o, LV_OBJ_FLAG_HIDDEN) != shown) {
        return;
    }
    if (shown) {
        lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
    }
}

}  // namespace ppv
