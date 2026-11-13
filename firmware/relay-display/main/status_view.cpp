// The status view on the glass. `status_view.h` says what it shows; the rules are in `status_format.h`.

#include "status_view.h"

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
constexpr uint32_t kBandHex = 0x1C1C1C;
constexpr uint32_t kLiveHex = 0xB3261E;
constexpr uint32_t kKeyHex = 0x8C8C8C;
constexpr uint32_t kIdleHex = 0x5C5C5C;
constexpr uint32_t kTextHex = 0xFFFFFF;
constexpr uint32_t kGoodHex = 0x4ADE80;
constexpr uint32_t kWarnHex = 0xFBBF24;
constexpr uint32_t kBadHex = 0xF87171;
constexpr int kPad = kCaptionPadX;
constexpr int kRowsMax = 4 + kStatusStreamsMax;   // captions, input, languages, watching, then the streams

uint32_t tone_hex(status_tone t, bool stale)
{
    if (stale) {
        return kIdleHex;
    }
    switch (t) {
    case status_tone::good: return kGoodHex;
    case status_tone::warn: return kWarnHex;
    case status_tone::bad:
    case status_tone::live: return kBadHex;
    case status_tone::idle: break;
    }
    return kTextHex;
}

struct Row {
    lv_obj_t *key = nullptr;
    lv_obj_t *value = nullptr;
};

struct SPane {
    bool built = false;
    int slot = 0;
    int w = 0;
    int h = 0;
    lv_obj_t *box = nullptr;
    lv_obj_t *idle = nullptr;
    lv_obj_t *band = nullptr;
    lv_obj_t *air = nullptr;
    Row rows[kRowsMax];
    int room = 0;              // how many rows fit below the band
    uint32_t band_hex = 0;
    const char *shown_idle = nullptr;
};

SPane s_p[2];
status_state s_snap;   // copied out of the slot under its lock each tick; the LVGL task's own (a few hundred bytes)
caption_state s_snap_state = caption_state::off;

void copy_state(const status_state *status, caption_state state, void *)
{
    s_snap_state = state;
    if (status != nullptr) {
        s_snap = *status;
    } else {
        status_clear(&s_snap);
    }
}

void set_ascii(lv_obj_t *label, const char *s)
{
    char out[96];
    service_ascii(s, out, sizeof out);
    ppv::set_text(label, out);
}

void set_band(SPane &p, uint32_t rgb)
{
    if (p.band_hex != rgb) {
        lv_obj_set_style_bg_color(p.band, ppv::hex(rgb), 0);
        p.band_hex = rgb;
    }
}

// One row: shown with these words and colour, or hidden when `key` is null.
void put_row(SPane &p, int i, const char *key, const char *value, uint32_t rgb)
{
    if (i >= p.room) {
        return;
    }
    Row &r = p.rows[i];
    if (key == nullptr) {
        ppv::set_shown(r.key, false);
        ppv::set_shown(r.value, false);
        return;
    }
    set_ascii(r.key, key);
    set_ascii(r.value, value);
    ppv::set_colour(r.value, rgb);
    ppv::set_shown(r.key, true);
    ppv::set_shown(r.value, true);
}

void show_status(SPane &p, uint32_t now_ms, bool stale)
{
    const status_state &s = s_snap;
    char line[96];
    char t[16];

    // The band: on air red with how long, otherwise dark.
    const bool live = s.on_air == status_air::on;
    status_elapsed_text(status_seconds(status_air_live_s(s), s.at_ms, now_ms), t, sizeof t);
    if (stale) {
        snprintf(line, sizeof line, "%s - not updating", status_air_words(s));
    } else if (live && t[0] != '\0') {
        snprintf(line, sizeof line, "%s  %s", status_air_words(s), t);
    } else {
        snprintf(line, sizeof line, "%s", status_air_words(s));
    }
    ppv::set_text(p.air, line);
    ppv::set_colour(p.air, live && !stale ? kTextHex : s.stream_count == 0 ? kIdleHex : kTextHex);
    set_band(p, live && !stale ? kLiveHex : kBandHex);

    int i = 0;
    // Captions
    if (s.running) {
        status_elapsed_text(status_seconds(s.run_s, s.at_ms, now_ms), t, sizeof t);
        snprintf(line, sizeof line, "Running%s%s", t[0] != '\0' ? "  " : "", t);
    } else {
        snprintf(line, sizeof line, "Stopped");
    }
    put_row(p, i++, "Captions", line,
            tone_hex(s.running ? (s.lang_count > 0 ? status_tone::good : status_tone::warn) : status_tone::idle, stale));
    // Input
    if (s.input == status_input::off) {
        snprintf(line, sizeof line, "%s", status_input_words(s.input));
    } else {
        snprintf(line, sizeof line, "%s  %d dB", status_input_words(s.input), s.dbfs);
    }
    put_row(p, i++, "Input", line, tone_hex(status_input_tone(s.input), stale));
    // Languages
    size_t o = 0;
    line[0] = '\0';
    for (int k = 0; k < s.lang_count && o + 4 < sizeof line; ++k) {
        o += snprintf(line + o, sizeof line - o, "%s%s", k > 0 ? ", " : "", s.langs[k]);
    }
    put_row(p, i++, "Languages", s.lang_count > 0 ? line : "None live", tone_hex(status_tone::idle, stale));
    // Watching
    snprintf(line, sizeof line, "%ld", static_cast<long>(s.viewers));
    put_row(p, i++, "Watching", line, tone_hex(status_tone::idle, stale));
    // The streams
    for (int k = 0; k < s.stream_count; ++k) {
        const status_stream &st = s.streams[k];
        status_stream_text(st, s.at_ms, now_ms, line, sizeof line);
        put_row(p, i++, st.label, line,
                tone_hex(st.air == status_air::on && st.health == status_health::ok ? status_tone::good
                                                                                     : status_health_tone(st.health),
                         stale));
    }
    for (; i < p.room; ++i) {
        put_row(p, i, nullptr, nullptr, 0);
    }
}

}  // namespace

void status_view_build(int pane, lv_obj_t *box, int slot, int w, int h)
{
    if (pane < 0 || pane > 1) {
        return;
    }
    SPane &p = s_p[pane];
    p = SPane();
    p.slot = slot;
    p.w = w;
    p.h = h;
    p.box = box;
    lv_obj_set_style_bg_color(box, ppv::hex(kBgHex), 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);

    const int inner = w - 2 * kPad;
    const int top = kCaptionHeaderH + ui_px(8);
    // The band is as tall as the room allows: the big font on a full screen, the large one in a short split pane.
    const bool roomy = h - top >= ui_px(300);
    const lv_font_t *air_font = roomy ? &RELAY_UI_FONT_ALERT : &RELAY_UI_FONT_LARGE;
    const int band_h = roomy ? ui_px(84) : ui_px(48);
    p.band = ppv::plain_box(box);
    lv_obj_set_pos(p.band, kPad, top);
    lv_obj_set_size(p.band, inner, band_h);
    lv_obj_set_style_radius(p.band, ui_px(8), 0);
    lv_obj_set_style_bg_opa(p.band, LV_OPA_COVER, 0);
    lv_obj_set_style_bg_color(p.band, ppv::hex(kBandHex), 0);
    p.band_hex = kBandHex;
    p.air = ppv::text(p.band, air_font, kTextHex);
    lv_obj_set_width(p.air, inner - 2 * ui_px(12));
    lv_label_set_long_mode(p.air, LV_LABEL_LONG_CLIP);
    lv_obj_set_style_text_align(p.air, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_center(p.air);
    lv_label_set_text(p.air, "");

    const int row_h = ui_px(34);
    const int rows_top = top + band_h + ui_px(8);
    p.room = (h - rows_top - ui_px(4)) / row_h;
    if (p.room > kRowsMax) {
        p.room = kRowsMax;
    }
    if (p.room < 0) {
        p.room = 0;
    }
    const int key_w = inner * 2 / 5;
    for (int i = 0; i < p.room; ++i) {
        Row &r = p.rows[i];
        r.key = ppv::text(box, &RELAY_UI_FONT_MEDIUM, kKeyHex);
        lv_obj_set_pos(r.key, kPad, rows_top + i * row_h);
        lv_obj_set_width(r.key, key_w - ui_px(8));
        lv_label_set_long_mode(r.key, LV_LABEL_LONG_DOT);
        lv_label_set_text(r.key, "");
        r.value = ppv::text(box, &RELAY_UI_FONT_MEDIUM, kTextHex);
        lv_obj_set_pos(r.value, kPad + key_w, rows_top + i * row_h);
        lv_obj_set_width(r.value, inner - key_w);
        lv_label_set_long_mode(r.value, LV_LABEL_LONG_DOT);
        lv_obj_set_style_text_align(r.value, LV_TEXT_ALIGN_RIGHT, 0);
        lv_label_set_text(r.value, "");
        lv_obj_add_flag(r.key, LV_OBJ_FLAG_HIDDEN);
        lv_obj_add_flag(r.value, LV_OBJ_FLAG_HIDDEN);
    }

    p.idle = ppv::text(box, &RELAY_UI_FONT_MEDIUM, kIdleHex);
    lv_obj_set_width(p.idle, inner);
    lv_label_set_long_mode(p.idle, LV_LABEL_LONG_WRAP);
    lv_obj_set_style_text_align(p.idle, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(p.idle, LV_ALIGN_CENTER, 0, kCaptionHeaderH / 2);
    lv_label_set_text(p.idle, "");
    lv_obj_add_flag(p.idle, LV_OBJ_FLAG_HIDDEN);

    p.built = true;
    status_view_tick(pane, lv_tick_get());
}

void status_view_tick(int pane, uint32_t now_ms)
{
    if (pane < 0 || pane > 1 || !s_p[pane].built) {
        return;
    }
    SPane &p = s_p[pane];
    // Copied every tick: a repeat from Relay re-anchors the elapsed times without announcing itself. Nothing is drawn
    // unless something shown differs (pp_view_util.h).
    captions_read_status(p.slot, copy_state, nullptr);
    const bool refused = s_snap_state == caption_state::feed_refused;
    const bool relay_down = s_snap_state == caption_state::relay_not_running;
    const char *idle = status_idle_text(s_snap, refused, relay_down);
    if (idle != nullptr) {
        ppv::set_text(p.idle, idle);
        ppv::set_shown(p.idle, true);
        ppv::set_shown(p.band, false);
        for (int i = 0; i < p.room; ++i) {
            ppv::set_shown(p.rows[i].key, false);
            ppv::set_shown(p.rows[i].value, false);
        }
        p.shown_idle = idle;
        return;
    }
    if (p.shown_idle != nullptr) {
        ppv::set_shown(p.idle, false);
        ppv::set_shown(p.band, true);
        p.shown_idle = nullptr;
    }
    const bool stale = status_stale(s_snap.at_ms, now_ms) || s_snap_state != caption_state::connected;
    show_status(p, now_ms, stale);
}

void status_view_teardown()
{
    for (SPane &p : s_p) {
        p.built = false;
        p.room = 0;
        p.shown_idle = nullptr;
    }
}

void status_view_report()
{
    for (int i = 0; i < 2; ++i) {
        const SPane &p = s_p[i];
        if (!p.built) {
            continue;
        }
        if (p.shown_idle != nullptr) {
            ESP_LOGI(TAG, "view: report: pane %d status, idle: %s", i + 1, p.shown_idle);
            continue;
        }
        ESP_LOGI(TAG, "view: report: pane %d status, air \"%s\", %d streams, %d rows", i + 1,
                 lv_label_get_text(p.air), s_snap.stream_count, p.room);
    }
}
