// ProPresenter's timers on the glass. `timers_view.h` says what it shows; the rules are in `pp_format.h`.

#include "timers_view.h"

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
constexpr uint32_t kNameHex = 0x8C8C8C;
constexpr uint32_t kIdleHex = 0x5C5C5C;
constexpr int kPad = kCaptionPadX;
constexpr int kNameH = ui_px(30);
constexpr int kCells = 8;   // "-0:00:00" is the longest text

const lv_font_t *digit_font(int index)
{
    switch (index) {
    case 0: return &relay_digits_140;
    case 1: return &relay_digits_80;
    default: return &lv_font_montserrat_48;
    }
}

struct Row {
    lv_obj_t *box = nullptr;
    lv_obj_t *name = nullptr;
    lv_obj_t *cell[kCells] = {};
    char shown[kCells + 1] = {};   // what the cells say now
    int placed = -1;               // how many cells are laid out (the text's length), -1 for none
    pp_level level = pp_level::ok;
    bool running = false;
    bool stale = false;
    bool colour_set = false;
};

struct TPane {
    bool built = false;
    int slot = 0;
    int w = 0;
    int h = 0;
    int area_h = 0;
    lv_obj_t *box = nullptr;
    lv_obj_t *idle = nullptr;
    Row rows[kPpTimersMax];
    int row_count = 0;
    int font = 0;                 // an index into kPpDigitFont
    uint32_t drawn_changes = 0xFFFFFFFF;
    caption_state drawn_state = caption_state::off;
    bool drawn = false;
    bool rows_up = false;
};

TPane s_p[2];
timers_state s_snap;   // copied out of the slot under its lock each tick; the LVGL task's own (a few hundred bytes)
caption_state s_snap_state = caption_state::off;

void copy_state(const timers_state *timers, caption_state state, void *)
{
    s_snap_state = state;
    if (timers != nullptr) {
        s_snap = *timers;
    } else {
        timers_clear(&s_snap);
    }
}

void set_ascii(lv_obj_t *label, const char *s)
{
    char out[96];
    service_ascii(s, out, sizeof out);
    ppv::set_text(label, out);
}

// The rows are made again when the timers themselves change (one added or renamed, one started or stopped, the
// thresholds), never because a count moved.
void build_rows(TPane &p)
{
    for (Row &r : p.rows) {
        if (r.box != nullptr) {
            lv_obj_delete(r.box);
        }
        r = Row();
    }
    p.row_count = s_snap.count;
    if (p.row_count == 0) {
        return;
    }
    const int w = p.w - 2 * kPad;
    p.font = pp_digit_pick(p.row_count, pp_timers_sample(s_snap), w, p.area_h, kNameH);
    const pp_digit_font &f = kPpDigitFont[p.font];
    const int row_h = p.area_h / p.row_count;
    const int top = kCaptionHeaderH + ui_px(4);
    for (int i = 0; i < p.row_count; ++i) {
        Row &r = p.rows[i];
        r.box = ppv::plain_box(p.box);
        lv_obj_set_pos(r.box, kPad, top + i * row_h);
        lv_obj_set_size(r.box, w, row_h);
        r.name = ppv::text(r.box, &RELAY_UI_FONT_SMALL, kNameHex);
        lv_obj_set_width(r.name, w);
        lv_label_set_long_mode(r.name, LV_LABEL_LONG_DOT);
        lv_obj_set_style_text_align(r.name, LV_TEXT_ALIGN_CENTER, 0);
        lv_obj_set_pos(r.name, 0, ui_px(4));
        lv_label_set_text(r.name, "");
        const int y = kNameH + (row_h - kNameH - f.line) / 2;
        for (int c = 0; c < kCells; ++c) {
            lv_obj_t *l = ppv::text(r.box, digit_font(p.font), 0xFFFFFF);
            lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
            lv_obj_set_pos(l, 0, y);
            lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
            lv_label_set_text(l, "");
            lv_obj_add_flag(l, LV_OBJ_FLAG_HIDDEN);
            r.cell[c] = l;
        }
    }
}

// Lay out the cells for a text of `n` characters, once, when the text's length changes.
void place_cells(TPane &p, Row &r, const char *text)
{
    const pp_digit_font &f = kPpDigitFont[p.font];
    int x[kCells];
    int cw[kCells];
    const int n = pp_timer_cells(text, f, p.w - 2 * kPad, x, cw, kCells);
    for (int c = 0; c < kCells; ++c) {
        if (c < n) {
            lv_obj_set_pos(r.cell[c], x[c], lv_obj_get_y(r.cell[c]));
            lv_obj_set_size(r.cell[c], cw[c], f.line);
            ppv::set_shown(r.cell[c], true);
        } else {
            ppv::set_shown(r.cell[c], false);
            r.shown[c] = '\0';
        }
    }
    r.placed = n;
}

// One row, for the time it is now: the digit that changed, and the colour if that did.
void draw_row(TPane &p, int i, uint32_t now_ms, bool stale)
{
    Row &r = p.rows[i];
    const pp_timer &t = s_snap.timers[i];
    const int32_t secs = pp_timer_seconds(t, s_snap.at_ms, now_ms);
    char text[kCells + 4];
    pp_timer_format(secs, text, sizeof text);
    const int n = static_cast<int>(strlen(text));
    if (n != r.placed) {
        place_cells(p, r, text);
    }
    char one[2] = {};
    for (int c = 0; c < n; ++c) {
        if (r.shown[c] != text[c]) {
            one[0] = text[c];
            lv_label_set_text(r.cell[c], one);
            r.shown[c] = text[c];
        }
    }
    const pp_level level = pp_timer_level(t, secs, s_snap.amber_s, s_snap.red_s);
    const bool running = pp_timer_counting(t.state);
    if (!r.colour_set || level != r.level || running != r.running || stale != r.stale) {
        const uint32_t rgb = pp_level_hex(level, running, stale);
        for (int c = 0; c < kCells; ++c) {
            ppv::set_colour(r.cell[c], rgb);
        }
        r.level = level;
        r.running = running;
        r.stale = stale;
        r.colour_set = true;
    }
}

// The name and the words for the state. These change only when the rows do, or when Relay goes quiet.
void draw_names(TPane &p, bool stale)
{
    for (int i = 0; i < p.row_count; ++i) {
        const pp_timer &t = s_snap.timers[i];
        char line[kPpTimerNameBytes + 40];
        snprintf(line, sizeof line, "%s  -  %s", t.name[0] != '\0' ? t.name : "Timer",
                 stale ? "not updating" : pp_timer_state_words(t.state, t.down));
        set_ascii(p.rows[i].name, line);
    }
}

}  // namespace

void timers_view_build(int pane, lv_obj_t *box, int slot, int w, int h)
{
    if (pane < 0 || pane > 1) {
        return;
    }
    TPane &p = s_p[pane];
    p = TPane();
    p.slot = slot;
    p.w = w;
    p.h = h;
    p.box = box;
    lv_obj_set_style_bg_color(box, ppv::hex(kBgHex), 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);
    p.area_h = h - kCaptionHeaderH - ui_px(12);

    p.idle = ppv::text(box, &RELAY_UI_FONT_MEDIUM, kIdleHex);
    lv_obj_set_width(p.idle, w - 2 * kPad);
    lv_label_set_long_mode(p.idle, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(p.idle, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(p.idle, LV_ALIGN_CENTER, 0, kCaptionHeaderH / 2);
    lv_label_set_text(p.idle, "");

    p.built = true;
    timers_view_tick(pane, lv_tick_get());
}

void timers_view_tick(int pane, uint32_t now_ms)
{
    if (pane < 0 || pane > 1 || !s_p[pane].built) {
        return;
    }
    TPane &p = s_p[pane];
    // The state is copied every tick, not only when the slot's revision moves: a repeat from Relay re-anchors the
    // count without announcing itself, and the count must follow it. The copy is a few hundred bytes; nothing is
    // drawn unless something shown differs.
    captions_read_timers(p.slot, copy_state, nullptr);
    const bool refused = s_snap_state == caption_state::feed_refused;
    const bool relay_down = s_snap_state == caption_state::relay_not_running;
    const char *idle = timers_idle_text(s_snap, refused, relay_down);
    const bool structure = !p.drawn || s_snap.changes != p.drawn_changes || s_snap_state != p.drawn_state;
    // Relay not being heard from, or the connection not up: the counts are no longer believed.
    const bool stale = s_snap.known && (pp_timers_stale(s_snap.at_ms, now_ms) ||
                                        s_snap_state != caption_state::connected);
    if (structure) {
        p.drawn = true;
        p.drawn_changes = s_snap.changes;
        p.drawn_state = s_snap_state;
        if (idle != nullptr) {
            for (Row &r : p.rows) {
                if (r.box != nullptr) {
                    lv_obj_delete(r.box);
                }
                r = Row();
            }
            p.row_count = 0;
            ppv::set_text(p.idle, idle);
            ppv::set_shown(p.idle, true);
            p.rows_up = false;
            return;
        }
        ppv::set_shown(p.idle, false);
        build_rows(p);
        p.rows_up = true;
        // Every row's words and digits are drawn the first time below.
        for (int i = 0; i < p.row_count; ++i) {
            p.rows[i].stale = !stale;   // differs from `stale`, so the words are made once
        }
    }
    if (idle != nullptr || !p.rows_up) {
        return;
    }
    // The words (name, state) change with the rows, and when the connection goes quiet or comes back.
    bool words_due = false;
    for (int i = 0; i < p.row_count; ++i) {
        words_due = words_due || p.rows[i].stale != stale || structure;
    }
    if (words_due) {
        draw_names(p, stale);
    }
    for (int i = 0; i < p.row_count; ++i) {
        draw_row(p, i, now_ms, stale);
    }
}

void timers_view_teardown()
{
    for (TPane &p : s_p) {
        p.built = false;
        for (Row &r : p.rows) {
            r = Row();   // the objects go with the screen
        }
        p.row_count = 0;
    }
}

void timers_view_report()
{
    for (int i = 0; i < 2; ++i) {
        if (s_p[i].built) {
            ESP_LOGI(TAG, "view: report: pane %d timers, %d shown", i + 1, s_p[i].row_count);
        }
    }
}
