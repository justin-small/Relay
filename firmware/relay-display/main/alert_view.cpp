// Alerts on the glass. `alert_view.h` says what it shows; the rules are in `alert_format.h`.

#include "alert_view.h"

#include <cstdio>
#include <cstring>
#include <initializer_list>

#include "alert_client.h"
#include "caption_view_format.h"
#include "captions.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "screen.h"
#include "service_format.h"
#include "ui_fonts.h"
#include "ui_scale.h"

namespace {

const char *TAG = "view";

constexpr uint32_t kBgHex = 0x101010;
constexpr uint32_t kRowHex = 0x1C1C1E;
constexpr uint32_t kDimHex = 0x8C8C8C;
constexpr uint32_t kGoodHex = 0x3DD68C;
constexpr uint32_t kWarnHex = 0xF5B342;
constexpr uint32_t kRedHex = 0xE00000;
constexpr uint32_t kDarkRedHex = 0xB00000;
constexpr int kPad = kCaptionPadX;
constexpr int kRowGap = ui_px(8);
constexpr uint32_t kFlashTickMs = 50;

// ---------------------------------------------------------------------------
// The pane
// ---------------------------------------------------------------------------

struct APane {
    bool built = false;
    int slot = 0;
    int w = 0;
    int h = 0;
    lv_obj_t *box = nullptr;
    lv_obj_t *list = nullptr;
    lv_obj_t *empty = nullptr;
    lv_obj_t *message = nullptr;
    lv_obj_t *rows[kAlertButtonsMax] = {};
    lv_obj_t *words[kAlertButtonsMax] = {};
    char ids[kAlertButtonsMax][kAlertIdBytes] = {};
    int row_count = 0;
    uint32_t revision = 0xFFFFFFFF;
    uint32_t drawn_changes = 0xFFFFFFFF;
    uint32_t drawn_second = 0xFFFFFFFF;
    uint32_t client_rev = 0;
};

APane s_p[2];
alert_board s_snap;     // copied out of the slot under its lock; the LVGL task's own
caption_state s_snap_state = caption_state::off;

lv_color_t hex(uint32_t rgb)
{
    return lv_color_hex(rgb);
}

lv_obj_t *plain_box(lv_obj_t *parent)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    return o;
}

lv_obj_t *text(lv_obj_t *parent, const lv_font_t *font, uint32_t colour)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_obj_set_style_text_font(l, font, 0);
    lv_obj_set_style_text_color(l, hex(colour), 0);
    return l;
}

// The fonts have no accents: names are folded to ASCII.
void set_ascii(lv_obj_t *label, const char *s)
{
    char out[160];
    service_ascii(s, out, sizeof out);
    lv_label_set_text(label, out);
}

void copy_board(const alert_board *alerts, caption_state state, void *)
{
    s_snap_state = state;
    if (alerts != nullptr) {
        s_snap = *alerts;
    } else {
        alert_board_clear(&s_snap);
    }
}

void on_row(lv_event_t *e)
{
    const char *id = static_cast<const char *>(lv_event_get_user_data(e));
    if (id != nullptr && id[0] != '\0') {
        alert_client_send(id);
    }
}

uint32_t row_colour(alert_button_state st)
{
    return st == alert_button_state::on ? kRedHex : kRowHex;
}

uint32_t words_colour(alert_button_state st)
{
    switch (st) {
    case alert_button_state::on:
        return 0xFFFFFF;
    case alert_button_state::seen:
        return kGoodHex;
    case alert_button_state::missed:
        return kWarnHex;
    default:
        return kDimHex;
    }
}

void build_rows(APane &p)
{
    lv_obj_clean(p.list);
    p.row_count = 0;
    // Compact rectangles, as tall as their text (Justin, on the bench): the name, and under it the smallest type for
    // how the last alert went, hidden while there is nothing to say. Laid out as a column, so the two never overlap.
    for (int i = 0; i < s_snap.count; ++i) {
        const alert_button &b = s_snap.buttons[i];
        lv_obj_t *row = lv_button_create(p.list);
        lv_obj_remove_style_all(row);
        lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
        lv_obj_set_style_min_height(row, ui_px(64), 0);
        lv_obj_set_style_radius(row, ui_px(6), 0);
        lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
        lv_obj_set_style_bg_color(row, hex(row_colour(b.state)), 0);
        lv_obj_set_style_bg_color(row, hex(0x1F52C2), LV_STATE_PRESSED);
        lv_obj_set_style_pad_hor(row, ui_px(16), 0);
        lv_obj_set_style_pad_ver(row, ui_px(10), 0);
        lv_obj_set_style_pad_row(row, ui_px(2), 0);
        lv_obj_set_flex_flow(row, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_align(row, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);
        lv_obj_remove_flag(row, LV_OBJ_FLAG_SCROLL_ON_FOCUS);
        lv_obj_add_flag(row, LV_OBJ_FLAG_SCROLL_CHAIN_VER);
        snprintf(p.ids[i], sizeof p.ids[i], "%s", b.id);
        lv_obj_add_event_cb(row, on_row, LV_EVENT_CLICKED, p.ids[i]);
        lv_obj_t *name = text(row, &RELAY_UI_FONT_MEDIUM, 0xFFFFFF);
        lv_label_set_long_mode(name, LV_LABEL_LONG_DOT);
        lv_obj_set_width(name, lv_pct(100));
        set_ascii(name, b.name);
        lv_obj_t *words = text(row, &RELAY_UI_FONT_SMALL, words_colour(b.state));
        lv_label_set_long_mode(words, LV_LABEL_LONG_DOT);
        lv_obj_set_width(words, lv_pct(100));
        lv_label_set_text(words, "");
        lv_obj_add_flag(words, LV_OBJ_FLAG_HIDDEN);
        p.rows[i] = row;
        p.words[i] = words;
        ++p.row_count;
    }
}

// The second line of every row, with ages counted on from when the message came.
void draw_words(APane &p, uint32_t now_ms)
{
    const uint32_t since = (now_ms - s_snap.at_ms) / 1000;
    for (int i = 0; i < p.row_count && i < s_snap.count; ++i) {
        const alert_button &b = s_snap.buttons[i];
        char w[160];
        char ascii[160];
        alert_button_words(b, b.age_s + since, w, sizeof w);
        service_ascii(w, ascii, sizeof ascii);
        lv_label_set_text(p.words[i], ascii);
        if (ascii[0] != '\0') {
            lv_obj_remove_flag(p.words[i], LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(p.words[i], LV_OBJ_FLAG_HIDDEN);
        }
    }
}

void draw_empty(APane &p)
{
    const bool none = s_snap.count == 0;
    if (none) {
        lv_label_set_text(p.empty, !s_snap.known ? (s_snap_state == caption_state::feed_refused
                                                        ? "This Relay has no alert buttons"
                                                        : "Connecting to Relay...")
                                                 : "No alert buttons yet.\nThe operator sets them up in Relay admin, "
                                                   "under Alerts.");
        lv_obj_remove_flag(p.empty, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(p.empty, LV_OBJ_FLAG_HIDDEN);
    }
}

void draw_message(APane &p, uint32_t now_ms)
{
    const alert_client_state st = alert_client_get();
    const bool recent = now_ms - st.at_ms < 4000;
    const char *words = !recent                                          ? ""
                        : st.result == alert_send_result::sending       ? "Sending..."
                        : st.result == alert_send_result::refused       ? "Relay refused it"
                        : st.result == alert_send_result::unreachable   ? "Relay did not answer"
                                                                        : "";
    lv_label_set_text(p.message, words);
}

// ---------------------------------------------------------------------------
// The flash
// ---------------------------------------------------------------------------

struct Flash {
    // Left by the panel link, under `s_mux`.
    bool want = false;
    bool changed = false;
    alert_flash alert;
    // The LVGL task's own.
    bool shown = false;
    char shown_id[kAlertFlashIdBytes] = {};
    uint32_t started_ms = 0;
    bool red = false;
    lv_obj_t *overlay = nullptr;
    lv_obj_t *from = nullptr;
    lv_obj_t *to = nullptr;
};

portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
Flash s_flash;
lv_timer_t *s_timer = nullptr;

void paint(bool red)
{
    s_flash.red = red;
    lv_obj_set_style_bg_color(s_flash.overlay, hex(red ? kRedHex : 0xFFFFFF), 0);
    const lv_color_t fg = hex(red ? 0xFFFFFF : kDarkRedHex);
    lv_obj_set_style_text_color(s_flash.from, fg, 0);
    lv_obj_set_style_text_color(s_flash.to, fg, 0);
}

void hide_flash()
{
    if (s_flash.overlay != nullptr) {
        lv_obj_delete(s_flash.overlay);
    }
    s_flash.overlay = s_flash.from = s_flash.to = nullptr;
    s_flash.shown = false;
    s_flash.shown_id[0] = '\0';
}

void on_flash_pressed(lv_event_t *)
{
    if (!s_flash.shown) {
        return;
    }
    ESP_LOGI(TAG, "view: alert %s acknowledged at the screen", s_flash.shown_id);
    alert_client_ack(s_flash.shown_id);
    taskENTER_CRITICAL(&s_mux);
    if (strcmp(s_flash.alert.id, s_flash.shown_id) == 0) {
        s_flash.want = false;
    }
    taskEXIT_CRITICAL(&s_mux);
    hide_flash();
}

void show_flash(const alert_flash &a, uint32_t now_ms)
{
    if (s_flash.overlay == nullptr) {
        lv_obj_t *o = lv_obj_create(lv_layer_top());
        lv_obj_remove_style_all(o);
        lv_obj_set_size(o, lv_pct(100), lv_pct(100));
        lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
        lv_obj_add_flag(o, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
        lv_obj_set_flex_flow(o, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_flex_align(o, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_all(o, ui_px(24), 0);
        lv_obj_set_style_pad_row(o, ui_px(16), 0);
        lv_obj_add_event_cb(o, on_flash_pressed, LV_EVENT_PRESSED, nullptr);
        s_flash.from = text(o, &RELAY_UI_FONT_ALERT, 0xFFFFFF);
        s_flash.to = text(o, &RELAY_UI_FONT_LARGE, 0xFFFFFF);
        for (lv_obj_t *l : {s_flash.from, s_flash.to}) {
            lv_obj_set_width(l, lv_pct(100));
            lv_obj_set_style_text_align(l, LV_TEXT_ALIGN_CENTER, 0);
            lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
            lv_obj_remove_flag(l, LV_OBJ_FLAG_CLICKABLE);
        }
        s_flash.overlay = o;
    }
    set_ascii(s_flash.from, a.from[0] != '\0' ? a.from : "Someone");
    char line[kAlertNameBytes + 48];
    snprintf(line, sizeof line, "needs %s - tap anywhere", a.to[0] != '\0' ? a.to : "you");
    set_ascii(s_flash.to, line);
    lv_obj_move_foreground(s_flash.overlay);
    snprintf(s_flash.shown_id, sizeof s_flash.shown_id, "%s", a.id);
    s_flash.started_ms = now_ms;
    s_flash.shown = true;
    paint(true);
    ESP_LOGI(TAG, "view: flashing for alert %s", a.id);
}

void on_flash_tick(lv_timer_t *)
{
    const uint32_t now = lv_tick_get();
    taskENTER_CRITICAL(&s_mux);
    const bool changed = s_flash.changed;
    const bool want = s_flash.want;
    const alert_flash a = s_flash.alert;
    s_flash.changed = false;
    taskEXIT_CRITICAL(&s_mux);
    if (changed) {
        if (want) {
            show_flash(a, now);
        } else if (s_flash.shown) {
            ESP_LOGI(TAG, "view: alert %s over", s_flash.shown_id);
            hide_flash();
        }
    }
    if (!s_flash.shown) {
        return;
    }
    const uint32_t elapsed = now - s_flash.started_ms;
    if (alert_flash_expired(a, elapsed)) {
        // Relay has gone quiet: its auto-stop has passed, so this one stops by itself.
        ESP_LOGW(TAG, "view: alert %s ran past its auto-stop with no word from Relay", s_flash.shown_id);
        taskENTER_CRITICAL(&s_mux);
        s_flash.want = false;
        taskEXIT_CRITICAL(&s_mux);
        hide_flash();
        return;
    }
    const bool red = alert_flash_red(elapsed, a.hz);
    if (red != s_flash.red) {
        paint(red);
    }
}

}  // namespace

void alert_view_build(int pane, lv_obj_t *box, int slot, int w, int h)
{
    if (pane < 0 || pane > 1) {
        return;
    }
    APane &p = s_p[pane];
    p = APane();
    p.slot = slot;
    p.w = w;
    p.h = h;
    p.box = box;
    lv_obj_set_style_bg_color(box, hex(kBgHex), 0);
    lv_obj_set_style_bg_opa(box, LV_OPA_COVER, 0);

    const int top = kCaptionHeaderH + ui_px(8);
    const int bottom = ui_px(40);
    p.list = plain_box(box);
    lv_obj_add_flag(p.list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(p.list, LV_DIR_VER);
    lv_obj_set_pos(p.list, kPad, top);
    lv_obj_set_size(p.list, w - 2 * kPad, h - top - bottom);
    lv_obj_set_flex_flow(p.list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(p.list, kRowGap, 0);

    p.empty = text(box, &RELAY_UI_FONT_MEDIUM, kDimHex);
    lv_obj_set_width(p.empty, w - 2 * kPad);
    lv_label_set_long_mode(p.empty, LV_LABEL_LONG_WRAP);
    lv_obj_set_pos(p.empty, kPad, top + ui_px(12));

    p.message = text(box, &RELAY_UI_FONT_SMALL, kDimHex);
    lv_obj_set_width(p.message, w - 2 * kPad);
    lv_obj_set_pos(p.message, kPad, h - bottom + ui_px(10));
    lv_label_set_text(p.message, "");
    p.built = true;
    alert_view_tick(pane, lv_tick_get());
}

void alert_view_tick(int pane, uint32_t now_ms)
{
    if (pane < 0 || pane > 1 || !s_p[pane].built) {
        return;
    }
    APane &p = s_p[pane];
    const uint32_t rev = captions_revision(p.slot);
    if (rev != p.revision) {
        p.revision = rev;
        captions_read_alerts(p.slot, copy_board, nullptr);
        if (s_snap.changes != p.drawn_changes || s_snap.count != p.row_count) {
            p.drawn_changes = s_snap.changes;
            build_rows(p);
            p.drawn_second = 0xFFFFFFFF;
        }
        draw_empty(p);
    }
    const uint32_t second = now_ms / 1000;
    if (second != p.drawn_second) {
        p.drawn_second = second;
        draw_words(p, now_ms);
    }
    const alert_client_state st = alert_client_get();
    if (st.revision != p.client_rev || (st.at_ms != 0 && now_ms - st.at_ms < 4500)) {
        p.client_rev = st.revision;
        draw_message(p, now_ms);
    }
}

void alert_view_teardown()
{
    for (APane &p : s_p) {
        p.built = false;
    }
}

void alert_view_start()
{
    screen_lock();
    if (s_timer == nullptr) {
        s_timer = lv_timer_create(on_flash_tick, kFlashTickMs, nullptr);
    }
    screen_unlock();
}

void alert_view_flash(const alert_flash &alert)
{
    taskENTER_CRITICAL(&s_mux);
    s_flash.alert = alert;
    s_flash.want = true;
    s_flash.changed = true;
    taskEXIT_CRITICAL(&s_mux);
}

void alert_view_flash_end(const char *id)
{
    taskENTER_CRITICAL(&s_mux);
    if (id != nullptr && strcmp(s_flash.alert.id, id) == 0 && s_flash.want) {
        s_flash.want = false;
        s_flash.changed = true;
    }
    taskEXIT_CRITICAL(&s_mux);
}

void alert_view_report()
{
    for (int i = 0; i < 2; ++i) {
        if (s_p[i].built) {
            ESP_LOGI(TAG, "view: report: pane %d alerts, %d buttons", i + 1, s_p[i].row_count);
        }
    }
    taskENTER_CRITICAL(&s_mux);
    const bool want = s_flash.want;
    char id[kAlertFlashIdBytes];
    memcpy(id, s_flash.alert.id, sizeof id);
    taskEXIT_CRITICAL(&s_mux);
    if (want) {
        ESP_LOGI(TAG, "view: report: flashing for alert %s", id);
    }
}
