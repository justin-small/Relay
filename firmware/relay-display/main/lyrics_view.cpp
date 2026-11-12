// ProPresenter's lyrics on the glass. `lyrics_view.h` says what it shows; the rules are in `pp_format.h`.

#include "lyrics_view.h"

#include <cstdio>
#include <cstring>

#include "caption_view_format.h"
#include "captions.h"
#include "esp_log.h"
#include "pp_view_util.h"
#include "service_format.h"
#include "ui_fonts.h"
#include "ui_scale.h"

namespace {

const char *TAG = "view";

constexpr uint32_t kBgHex = 0x101010;
constexpr uint32_t kTextHex = 0xFFFFFF;
constexpr uint32_t kNextHex = 0x7A7A7A;
constexpr uint32_t kIdleHex = 0x5C5C5C;   // quiet: a screen with nothing up should not glow
constexpr uint32_t kPartHex = 0x6EA8FE;
constexpr uint32_t kTitleHex = 0x8C8C8C;
constexpr int kPad = kCaptionPadX;
constexpr int kStripH = ui_px(40);
constexpr int kNextH = ui_px(66);   // two lines of the caption font, kept whether or not there is a next slide

struct LPane {
    bool built = false;
    int slot = 0;
    int w = 0;
    int h = 0;
    int area_w = 0;
    int area_h = 0;
    lv_obj_t *box = nullptr;
    lv_obj_t *area = nullptr;
    lv_obj_t *words = nullptr;   // the slide's lines
    lv_obj_t *idle = nullptr;
    lv_obj_t *next = nullptr;
    lv_obj_t *part = nullptr;
    lv_obj_t *title = nullptr;
    uint32_t revision = 0xFFFFFFFF;
    uint32_t drawn_changes = 0xFFFFFFFF;
    caption_state drawn_state = caption_state::off;
    bool drawn = false;
    bool lines_up = false;   // the slide's lines were on screen at the last draw
};

LPane s_p[2];
lyrics_state s_snap;   // copied out of the slot under its lock; the LVGL task's own (about 700 bytes)
caption_state s_snap_state = caption_state::off;
bool s_snap_has = false;

void copy_state(const lyrics_state *lyrics, caption_state state, void *)
{
    s_snap_state = state;
    s_snap_has = lyrics != nullptr;
    if (lyrics != nullptr) {
        s_snap = *lyrics;
    } else {
        lyrics_clear(&s_snap);
    }
}

// The fonts have no accents but the caption font's: names are folded to ASCII.
void set_ascii(lv_obj_t *label, const char *s)
{
    char out[96];
    service_ascii(s, out, sizeof out);
    ppv::set_text(label, out);
}

const lv_font_t *tier_font(int tier)
{
    switch (tier) {
    case 0: return &RELAY_UI_FONT_ALERT;     // Montserrat 48
    case 1: return &RELAY_UI_FONT_LARGE;
    default: return &RELAY_UI_FONT_CAPTION;  // draws accents
    }
}

void draw(LPane &p)
{
    const bool refused = s_snap_state == caption_state::feed_refused;
    const bool relay_down = s_snap_state == caption_state::relay_not_running;
    const char *idle = lyrics_idle_text(s_snap, refused, relay_down);
    if (idle != nullptr) {
        ppv::set_shown(p.words, false);
        ppv::set_shown(p.next, false);
        ppv::set_shown(p.part, false);
        ppv::set_shown(p.title, false);
        ppv::set_text(p.idle, idle);
        ppv::set_shown(p.idle, idle[0] != '\0');
        p.lines_up = false;
        return;
    }
    ppv::set_shown(p.idle, false);
    static const int kPx[kLyricsTiers] = {48, RELAY_UI_PX_LARGE, RELAY_UI_PX_CAPTION};
    const int tier = lyrics_pick_tier(s_snap.text, pp_ascii_only(s_snap.text), kPx, p.area_w, p.area_h);
    ppv::set_font(p.words, tier_font(tier));
    ppv::set_text(p.words, s_snap.text);
    ppv::set_shown(p.words, true);
    ppv::set_text(p.next, s_snap.next);
    ppv::set_shown(p.next, s_snap.next[0] != '\0');
    set_ascii(p.part, s_snap.part);
    set_ascii(p.title, s_snap.title);
    ppv::set_shown(p.part, s_snap.part[0] != '\0');
    ppv::set_shown(p.title, s_snap.title[0] != '\0');
    p.lines_up = true;
}

}  // namespace

void lyrics_view_build(int pane, lv_obj_t *box, int slot, int w, int h)
{
    if (pane < 0 || pane > 1) {
        return;
    }
    LPane &p = s_p[pane];
    p = LPane();
    p.slot = slot;
    p.w = w;
    p.h = h;
    p.box = box;
    lv_obj_set_style_bg_color(box, ppv::hex(kBgHex), 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);

    const int top = kCaptionHeaderH + ui_px(8);
    p.area_w = w - 2 * kPad;
    p.area_h = h - top - kNextH - kStripH;
    if (p.area_h < ui_px(60)) {
        p.area_h = ui_px(60);
    }

    // The lines are centred both ways in the area: a flex column does the centring, so it holds for one line or six.
    p.area = ppv::plain_box(box);
    lv_obj_set_pos(p.area, kPad, top);
    lv_obj_set_size(p.area, p.area_w, p.area_h);
    lv_obj_set_flex_flow(p.area, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_flex_align(p.area, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    p.words = ppv::text(p.area, &RELAY_UI_FONT_CAPTION, kTextHex);
    lv_obj_set_width(p.words, lv_pct(100));
    lv_label_set_long_mode(p.words, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(p.words, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_set_style_text_line_space(p.words, ui_px(6), 0);
    lv_label_set_text(p.words, "");

    p.idle = ppv::text(box, &RELAY_UI_FONT_MEDIUM, kIdleHex);
    lv_obj_set_width(p.idle, p.area_w);
    lv_label_set_long_mode(p.idle, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(p.idle, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(p.idle, LV_ALIGN_CENTER, 0, top / 2);
    lv_label_set_text(p.idle, "");

    p.next = ppv::text(box, &RELAY_UI_FONT_CAPTION, kNextHex);
    lv_obj_set_size(p.next, p.area_w, kNextH);
    lv_obj_set_pos(p.next, kPad, h - kStripH - kNextH);
    lv_label_set_long_mode(p.next, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(p.next, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(p.next, "");

    p.part = ppv::text(box, &RELAY_UI_FONT_SMALL, kPartHex);
    lv_obj_set_width(p.part, p.area_w / 3);
    lv_label_set_long_mode(p.part, LV_LABEL_LONG_DOT);
    lv_obj_set_pos(p.part, kPad, h - kStripH + ui_px(10));
    lv_label_set_text(p.part, "");

    p.title = ppv::text(box, &RELAY_UI_FONT_SMALL, kTitleHex);
    lv_obj_set_width(p.title, p.area_w * 2 / 3 - ui_px(12));
    lv_label_set_long_mode(p.title, LV_LABEL_LONG_DOT);
    lv_obj_set_style_text_align(p.title, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_set_pos(p.title, kPad + p.area_w / 3 + ui_px(12), h - kStripH + ui_px(10));
    lv_label_set_text(p.title, "");

    p.built = true;
    lyrics_view_tick(pane, lv_tick_get());
}

void lyrics_view_tick(int pane, uint32_t)
{
    if (pane < 0 || pane > 1 || !s_p[pane].built) {
        return;
    }
    LPane &p = s_p[pane];
    // `captions_revision` moves for a new slide, a new part, and a new connection state, and for nothing else: a
    // repeat of the same slide is not announced, so a still screen costs this one compare.
    const uint32_t rev = captions_revision(p.slot);
    if (rev == p.revision) {
        return;
    }
    p.revision = rev;
    captions_read_lyrics(p.slot, copy_state, nullptr);
    if (p.drawn && s_snap.changes == p.drawn_changes && s_snap_state == p.drawn_state) {
        return;
    }
    p.drawn = true;
    p.drawn_changes = s_snap.changes;
    p.drawn_state = s_snap_state;
    draw(p);
}

void lyrics_view_teardown()
{
    for (LPane &p : s_p) {
        p.built = false;
    }
}

void lyrics_view_report()
{
    for (int i = 0; i < 2; ++i) {
        if (s_p[i].built) {
            ESP_LOGI(TAG, "view: report: pane %d lyrics, %s", i + 1, s_p[i].lines_up ? "a slide up" : "idle");
        }
    }
}
