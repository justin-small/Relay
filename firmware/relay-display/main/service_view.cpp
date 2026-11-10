// The Planning Center service in a pane. `service_view.h` says what it shows; the rules are in `service_format.h`.

#include "service_view.h"

#include <cstdio>
#include <cstring>
#include <initializer_list>
#include <new>

#include "caption_view_format.h"
#include "captions.h"
#include "config.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "panel_link.h"
#include "screen.h"
#include "service_control.h"
#include "service_format.h"

extern "C" {
LV_FONT_DECLARE(relay_caption_36);
}

namespace {

const char *TAG = "view";

constexpr uint32_t kBgHex = 0x101010;
constexpr uint32_t kNowHex = 0x181818;
constexpr uint32_t kDimHex = 0x8C8C8C;
constexpr uint32_t kGoodHex = 0x3DD68C;
constexpr uint32_t kBadHex = 0xF0524F;
constexpr uint32_t kCurrentHex = 0x1F3A70;
constexpr uint32_t kJumpHex = 0x2F6FED;

constexpr int kNowH = 116;
constexpr int kFooterH = 40;
constexpr int kBarH = 84;          // the LIVE buttons under the order of service
constexpr int kBarBigH = 200;      // the same, filling the pane, in "Control only"
constexpr uint32_t kResultShowMs = 4000;
constexpr int kPad = kCaptionPadX;

struct SPane {
    bool built = false;
    int slot = 0;
    int w = kLcdWidth;
    lv_obj_t *box = nullptr;
    lv_obj_t *now_title = nullptr;
    lv_obj_t *now_timer = nullptr;
    lv_obj_t *next_title = nullptr;
    lv_obj_t *list = nullptr;
    lv_obj_t *empty = nullptr;
    lv_obj_t *jump = nullptr;
    lv_obj_t *dot = nullptr;
    lv_obj_t *footer = nullptr;
    lv_obj_t *scrim = nullptr;      // the notes card, while it is up
    lv_obj_t *rows[kServiceItemsMax] = {};
    int row_count = 0;

    service_state *snap = nullptr;  // the last state copied from the slot (PSRAM where there is any)
    bool has_snap = false;
    uint32_t revision = 0;
    bool synced = false;
    uint32_t drawn_changes = 0xFFFFFFFF;  // the `service` message the rows were built from
    int drawn_focus = -2;
    char drawn_timer[24] = {};
    char drawn_footer[sizeof(service_status::text)] = {};
    service_tone drawn_tone = service_tone::idle;

    // The LIVE buttons (#83), when the panel's service mode lets it control.
    bool controls = false;
    bool shows = true;              // the order of service ("Control only" leaves it out)
    uint32_t mode_rev = 0;          // panel_link_service_revision() when built: a change rebuilds the view
    bool rebuild_asked = false;
    lv_obj_t *btn_prev = nullptr;
    lv_obj_t *btn_next = nullptr;
    lv_obj_t *btn_take = nullptr;
    lv_obj_t *take_label = nullptr;
    lv_obj_t *message = nullptr;
    lv_obj_t *confirm = nullptr;    // "Take control from ..?", while it is up
    uint32_t drawn_ctrl_rev = 0xFFFFFFFF;
    bool drawn_release = false;
    char drawn_message[48] = {};

    caption_follow follow;          // following = the current item is kept in view
    bool programmatic = false;
    bool scroll_due = false;        // the current item moved, or following resumed: bring it into view
    int note_item = -1;             // the row whose notes are up
};

SPane s_p[2];

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

// Montserrat holds ASCII alone (`service_ascii`).
void set_ascii(lv_obj_t *label, const char *s)
{
    char out[sizeof(service_item::notes)];
    service_ascii(s, out, sizeof out);
    lv_label_set_text(label, out);
}

void copy_state(const service_state *service, caption_state, void *ctx)
{
    SPane &p = *static_cast<SPane *>(ctx);
    p.has_snap = service != nullptr;
    if (service != nullptr) {
        memcpy(static_cast<void *>(p.snap), service, sizeof *service);
    }
}

// ---------------------------------------------------------------------------
// Following the current item
// ---------------------------------------------------------------------------

void show_jump(SPane &p, bool shown)
{
    if (shown) {
        lv_obj_remove_flag(p.jump, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(p.jump, LV_OBJ_FLAG_HIDDEN);
    }
}

void scroll_to_focus(SPane &p)
{
    const int focus = p.has_snap ? service_focus(*p.snap) : -1;
    if (focus < 0 || focus >= p.row_count) {
        return;
    }
    lv_obj_update_layout(p.list);
    // The current item near the top, with the one before it peeking in above when there is room.
    int y = static_cast<int>(lv_obj_get_y(p.rows[focus])) - kCaptionPadY;
    if (focus > 0) {
        y -= static_cast<int>(lv_obj_get_height(p.rows[focus - 1])) / 2;
    }
    p.programmatic = true;
    lv_obj_scroll_to_y(p.list, y > 0 ? y : 0, LV_ANIM_OFF);
    p.programmatic = false;
}

void on_scroll(lv_event_t *e)
{
    SPane &p = *static_cast<SPane *>(lv_event_get_user_data(e));
    if (p.programmatic) {
        return;
    }
    // Any scroll by a finger pauses: the order of service is read up and down, there is no "bottom" to go back to.
    if (p.follow.user_scrolled(false, lv_tick_get())) {
        ESP_LOGI(TAG, "view: service pane %d paused (scrolled)", static_cast<int>(&p - s_p) + 1);
        show_jump(p, true);
    }
}

void on_jump(lv_event_t *e)
{
    SPane &p = *static_cast<SPane *>(lv_event_get_user_data(e));
    p.follow.jump(lv_tick_get());
    scroll_to_focus(p);
    show_jump(p, false);
    ESP_LOGI(TAG, "view: service pane %d following (jump to now)", static_cast<int>(&p - s_p) + 1);
}

// ---------------------------------------------------------------------------
// The notes card
// ---------------------------------------------------------------------------

void notes_close(SPane &p)
{
    if (p.scrim != nullptr) {
        lv_obj_delete_async(p.scrim);
        p.scrim = nullptr;
        p.note_item = -1;
        ESP_LOGI(TAG, "view: service notes closed");
    }
}

void on_notes_scrim(lv_event_t *e)
{
    SPane &p = *static_cast<SPane *>(lv_event_get_user_data(e));
    notes_close(p);
}

void notes_open(SPane &p, int index)
{
    if (!p.has_snap || index < 0 || index >= p.snap->count || p.snap->items[index].notes[0] == '\0') {
        return;
    }
    notes_close(p);
    const service_item &it = p.snap->items[index];
    // Over the whole glass: a pane in the split view is too narrow for a paragraph.
    p.scrim = plain_box(lv_obj_get_parent(p.box));
    lv_obj_set_pos(p.scrim, 0, 0);
    lv_obj_set_size(p.scrim, kLcdWidth, kLcdHeight);
    lv_obj_set_style_bg_color(p.scrim, hex(0x000000), 0);
    lv_obj_set_style_bg_opa(p.scrim, LV_OPA_70, 0);
    lv_obj_add_flag(p.scrim, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(p.scrim, on_notes_scrim, LV_EVENT_CLICKED, &p);

    lv_obj_t *card = plain_box(p.scrim);
    lv_obj_set_size(card, 640, LV_SIZE_CONTENT);
    lv_obj_set_style_max_height(card, 420, 0);
    lv_obj_center(card);
    lv_obj_set_style_bg_color(card, hex(0x1E1E1E), 0);
    lv_obj_set_style_bg_opa(card, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(card, 16, 0);
    lv_obj_set_style_border_color(card, hex(0x555555), 0);
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_set_style_pad_all(card, 20, 0);
    lv_obj_set_style_pad_row(card, 12, 0);
    lv_obj_set_flex_flow(card, LV_FLEX_FLOW_COLUMN);
    lv_obj_add_flag(card, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_t *title = text(card, &lv_font_montserrat_28, 0xFFFFFF);
    lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
    lv_obj_set_width(title, lv_pct(100));
    set_ascii(title, it.title);
    lv_obj_t *body = text(card, &lv_font_montserrat_20, 0xD0D0D0);
    lv_label_set_long_mode(body, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(body, lv_pct(100));
    set_ascii(body, it.notes);
    lv_obj_t *hint = text(card, &lv_font_montserrat_20, kDimHex);
    lv_label_set_text(hint, "Tap to close");
    p.note_item = index;
    ESP_LOGI(TAG, "view: service notes open for item %d", index + 1);
}

// A row's user data is its pane and index: (pane << 8) | index.
void on_row(lv_event_t *e)
{
    const int tag = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
    notes_open(s_p[(tag >> 8) & 1], tag & 0xFF);
}

// ---------------------------------------------------------------------------
// The LIVE buttons
// ---------------------------------------------------------------------------

SPane &pane_of(lv_event_t *e)
{
    return s_p[static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e))) & 1];
}

void press(SPane &p, service_action action)
{
    char type[kServiceTypeBytes] = {};
    if (captions_feed_choice(p.slot, type, sizeof type) != kFeedService) {
        return;
    }
    service_control_press(action, type);   // refused while the last press waits: a double tap is one press
}

void confirm_close(SPane &p)
{
    if (p.confirm != nullptr) {
        lv_obj_delete_async(p.confirm);
        p.confirm = nullptr;
    }
}

void on_confirm_yes(lv_event_t *e)
{
    SPane &p = pane_of(e);
    confirm_close(p);
    ESP_LOGI(TAG, "view: take control confirmed");
    press(p, service_action::take_control);
}

void on_confirm_no(lv_event_t *e)
{
    SPane &p = pane_of(e);
    confirm_close(p);
    ESP_LOGI(TAG, "view: take control cancelled");
}

lv_obj_t *button(lv_obj_t *parent, const char *label, uint32_t colour, lv_event_cb_t cb, intptr_t tag)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_style_bg_color(b, hex(colour), 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(b, 14, 0);
    lv_obj_set_style_bg_color(b, hex(0x1F52C2), LV_STATE_PRESSED);
    lv_obj_set_style_bg_opa(b, LV_OPA_50, LV_STATE_DISABLED);
    lv_obj_t *l = text(b, &lv_font_montserrat_28, 0xFFFFFF);
    lv_label_set_text(l, label);
    lv_obj_center(l);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, reinterpret_cast<void *>(tag));
    return b;
}

void confirm_open(SPane &p, const char *question)
{
    confirm_close(p);
    const intptr_t tag = &p - s_p;
    p.confirm = plain_box(lv_obj_get_parent(p.box));
    lv_obj_set_pos(p.confirm, 0, 0);
    lv_obj_set_size(p.confirm, kLcdWidth, kLcdHeight);
    lv_obj_set_style_bg_color(p.confirm, hex(0x000000), 0);
    lv_obj_set_style_bg_opa(p.confirm, LV_OPA_70, 0);
    lv_obj_add_flag(p.confirm, LV_OBJ_FLAG_CLICKABLE);   // a tap outside the card does nothing
    lv_obj_t *card = plain_box(p.confirm);
    lv_obj_set_size(card, 600, 220);
    lv_obj_center(card);
    lv_obj_set_style_bg_color(card, hex(0x1E1E1E), 0);
    lv_obj_set_style_bg_opa(card, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(card, 16, 0);
    lv_obj_set_style_border_color(card, hex(0x555555), 0);
    lv_obj_set_style_border_width(card, 2, 0);
    lv_obj_t *q = text(card, &lv_font_montserrat_28, 0xFFFFFF);
    lv_label_set_long_mode(q, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(q, 560);
    lv_obj_set_style_text_align(q, LV_TEXT_ALIGN_CENTER, 0);
    lv_label_set_text(q, question);
    lv_obj_align(q, LV_ALIGN_TOP_MID, 0, 28);
    lv_obj_t *yes = button(card, "Take control", kBadHex, on_confirm_yes, tag);
    lv_obj_set_size(yes, 250, 72);
    lv_obj_align(yes, LV_ALIGN_BOTTOM_LEFT, 30, -24);
    lv_obj_t *no = button(card, "Cancel", 0x303030, on_confirm_no, tag);
    lv_obj_set_size(no, 250, 72);
    lv_obj_align(no, LV_ALIGN_BOTTOM_RIGHT, -30, -24);
    ESP_LOGI(TAG, "view: confirm \"%s\"", question);
}

void on_prev(lv_event_t *e)
{
    press(pane_of(e), service_action::previous);
}

void on_next(lv_event_t *e)
{
    press(pane_of(e), service_action::next);
}

void on_take(lv_event_t *e)
{
    SPane &p = pane_of(e);
    if (p.has_snap && p.snap->relay_controls) {
        press(p, service_action::release);
        return;
    }
    char question[96] = {};
    if (p.has_snap && service_take_question(*p.snap, question, sizeof question) > 0) {
        confirm_open(p, question);   // it takes LIVE from whoever has it
        return;
    }
    press(p, service_action::take_control);
}

// The bar: Previous and Next, large, and Take control (Release once Relay has it), with a line for how the last
// press went. In "Control only" it fills the pane under the current and next item.
void build_bar(SPane &p, int top, int height)
{
    const intptr_t tag = &p - s_p;
    lv_obj_t *bar = plain_box(p.box);
    lv_obj_set_pos(bar, 0, top);
    lv_obj_set_size(bar, p.w, height);
    lv_obj_set_style_bg_color(bar, hex(kNowHex), 0);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);
    const bool big = height >= kBarBigH;
    const int gap = 12;
    const int bh = big ? 120 : 64;
    const int take_w = p.w < kLcdWidth ? 120 : 220;
    const int arrow_w = (p.w - 2 * kPad - take_w - 2 * gap) / 2;
    p.btn_prev = button(bar, LV_SYMBOL_PREV "  Previous", 0x303030, on_prev, tag);
    p.btn_next = button(bar, "Next  " LV_SYMBOL_NEXT, kJumpHex, on_next, tag);
    p.btn_take = button(bar, "Take control", 0x5A3A00, on_take, tag);
    p.take_label = lv_obj_get_child(p.btn_take, 0);
    if (p.w < kLcdWidth) {
        // A half-width pane: arrows only, and a short word for control.
        lv_label_set_text(lv_obj_get_child(p.btn_prev, 0), LV_SYMBOL_PREV);
        lv_label_set_text(lv_obj_get_child(p.btn_next, 0), LV_SYMBOL_NEXT);
    }
    lv_obj_set_size(p.btn_prev, arrow_w, bh);
    lv_obj_set_size(p.btn_next, arrow_w, bh);
    lv_obj_set_size(p.btn_take, take_w, bh);
    const int y = big ? 16 : (height - bh) / 2 - 6;
    lv_obj_set_pos(p.btn_prev, kPad, y);
    lv_obj_set_pos(p.btn_next, kPad + arrow_w + gap, y);
    lv_obj_set_pos(p.btn_take, kPad + 2 * arrow_w + 2 * gap, y);
    p.message = text(bar, &lv_font_montserrat_20, kBadHex);
    lv_label_set_long_mode(p.message, LV_LABEL_LONG_DOT);
    lv_obj_set_width(p.message, p.w - 2 * kPad);
    lv_obj_set_pos(p.message, kPad, y + bh + 2);
}

// Keep the buttons and the message in step with the last press and with who has control.
void draw_controls(SPane &p, uint32_t now_ms)
{
    if (!p.controls || p.btn_take == nullptr) {
        return;
    }
    const bool release = p.has_snap && p.snap->relay_controls;
    const bool narrow = p.w < kLcdWidth;
    if (release != p.drawn_release || p.drawn_ctrl_rev == 0xFFFFFFFF) {
        p.drawn_release = release;
        lv_label_set_text(p.take_label, release ? "Release" : (narrow ? "Take" : "Take control"));
    }
    const service_control_state st = service_control_get();
    const bool sending = st.result == service_result::sending;
    const char *words = "";
    if (sending || now_ms - st.at_ms < kResultShowMs) {
        words = service_result_words(st.result);
    }
    if (st.revision != p.drawn_ctrl_rev || strcmp(words, p.drawn_message) != 0) {
        p.drawn_ctrl_rev = st.revision;
        snprintf(p.drawn_message, sizeof p.drawn_message, "%s", words);
        lv_label_set_text(p.message, words);
        lv_obj_set_style_text_color(p.message, hex(sending ? kDimHex : kBadHex), 0);
        for (lv_obj_t *b : {p.btn_prev, p.btn_next, p.btn_take}) {
            if (sending) {
                lv_obj_add_state(b, LV_STATE_DISABLED);
            } else {
                lv_obj_remove_state(b, LV_STATE_DISABLED);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// The order of service
// ---------------------------------------------------------------------------

void build_rows(SPane &p)
{
    lv_obj_clean(p.list);
    p.row_count = 0;
    const service_state &s = *p.snap;
    for (int i = 0; i < s.count; ++i) {
        const service_item &it = s.items[i];
        lv_obj_t *row = plain_box(p.list);
        p.rows[p.row_count++] = row;
        lv_obj_set_width(row, lv_pct(100));
        lv_obj_set_height(row, LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(row, LV_FLEX_FLOW_COLUMN);
        lv_obj_set_style_pad_hor(row, 12, 0);
        lv_obj_set_style_pad_ver(row, it.type == service_item_type::header ? 2 : 6, 0);
        lv_obj_set_style_radius(row, 8, 0);
        if (i == s.current) {
            lv_obj_set_style_bg_color(row, hex(kCurrentHex), 0);
            lv_obj_set_style_bg_opa(row, LV_OPA_COVER, 0);
        }
        if (it.type == service_item_type::header) {
            // A section break: small, dim, with room above it.
            lv_obj_set_style_margin_top(row, i == 0 ? 0 : 10, 0);
            lv_obj_t *h = text(row, &lv_font_montserrat_20, kDimHex);
            lv_label_set_long_mode(h, LV_LABEL_LONG_DOT);
            lv_obj_set_width(h, lv_pct(100));
            set_ascii(h, it.title[0] != '\0' ? it.title : "Section");
            continue;
        }
        lv_obj_t *top = plain_box(row);
        // An `lv_obj` is clickable by default, and a press goes to the deepest clickable object under it: the row
        // has the tap, so this box does not take it.
        lv_obj_remove_flag(top, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_set_size(top, lv_pct(100), LV_SIZE_CONTENT);
        lv_obj_set_flex_flow(top, LV_FLEX_FLOW_ROW);
        lv_obj_set_flex_align(top, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
        lv_obj_set_style_pad_column(top, 12, 0);
        lv_obj_t *title = text(top, &relay_caption_36, i < s.current ? kDimHex : 0xFFFFFF);
        lv_label_set_long_mode(title, LV_LABEL_LONG_DOT);
        lv_obj_set_flex_grow(title, 1);
        lv_label_set_text(title, it.title[0] != '\0' ? it.title : "(untitled)");
        char len[24] = {};
        service_length_words(it, len, sizeof len);
        const bool notes = s.notes_allowed && it.notes[0] != '\0';
        if (len[0] != '\0' || notes) {
            char right[40];
            snprintf(right, sizeof right, "%s%s%s", notes ? LV_SYMBOL_FILE : "", notes && len[0] ? "  " : "", len);
            lv_obj_t *r = text(top, &lv_font_montserrat_20, kDimHex);
            lv_label_set_text(r, right);
        }
        char song[sizeof(service_item::sequence) + 24] = {};
        if (service_song_words(it, song, sizeof song) > 0) {
            lv_obj_t *l = text(row, &lv_font_montserrat_20, 0xB0B0B0);
            lv_label_set_long_mode(l, LV_LABEL_LONG_WRAP);
            lv_obj_set_width(l, lv_pct(100));
            set_ascii(l, song);
        }
        if (notes) {
            lv_obj_add_flag(row, LV_OBJ_FLAG_CLICKABLE);
            const intptr_t tag = (static_cast<intptr_t>(&p - s_p) << 8) | i;
            lv_obj_add_event_cb(row, on_row, LV_EVENT_SHORT_CLICKED, reinterpret_cast<void *>(tag));
        }
    }
    if (s.total > s.count) {
        lv_obj_t *more = text(p.list, &lv_font_montserrat_20, kDimHex);
        char words[64];
        snprintf(words, sizeof words, "and %d more items", s.total - s.count);
        lv_label_set_text(more, words);
    }
}

// The big "now" block and the empty-list words.
void draw_now(SPane &p, uint32_t now_ms)
{
    const service_state &s = *p.snap;
    const bool have = p.has_snap && s.known;
    const char *now_title = "";
    if (have && s.current >= 0) {
        now_title = s.items[s.current].title;
    } else if (have && s.has_plan) {
        now_title = s.live ? "" : (s.plan_title[0] != '\0' ? s.plan_title : "Today's service");
    }
    lv_label_set_text(p.now_title, now_title);
    char next[sizeof(service_item::title) + 8] = {};
    if (have && s.next >= 0) {
        snprintf(next, sizeof next, "%s", s.items[s.next].title);
    }
    lv_label_set_text(p.next_title, next);
    lv_obj_t *next_word = lv_obj_get_child(lv_obj_get_parent(p.next_title), 0);
    if (next[0] != '\0') {
        lv_obj_remove_flag(next_word, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(next_word, LV_OBJ_FLAG_HIDDEN);
    }

    const char *empty = nullptr;
    if (!have) {
        empty = caption_empty_text(caption_state::connecting);
    } else if (!s.followed) {
        empty = "Relay does not follow this service";
    } else if (!s.has_plan) {
        empty = "No service planned today";
    } else if (s.count == 0) {
        empty = "The plan has no items";
    }
    if (empty != nullptr && (p.shows || !have || !s.followed || !s.has_plan)) {
        set_ascii(p.empty, empty);
        lv_obj_remove_flag(p.empty, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(p.empty, LV_OBJ_FLAG_HIDDEN);
    }
    (void)now_ms;
}

// The timer and the footer, which move with the clock. Labels are set only when their words change.
void draw_clock(SPane &p, uint32_t now_ms)
{
    char timer[24] = {};
    bool over = false;
    if (p.has_snap && p.snap->known) {
        if (p.snap->timing && p.snap->current >= 0) {
            const double left = service_item_left(*p.snap, now_ms);
            over = left < 0;
            char clock[16];
            service_clock(left, clock, sizeof clock);
            snprintf(timer, sizeof timer, "%s%s", over ? "+" : "", clock);
        }
    }
    if (strcmp(timer, p.drawn_timer) != 0) {
        snprintf(p.drawn_timer, sizeof p.drawn_timer, "%s", timer);
        lv_label_set_text(p.now_timer, timer);
        lv_obj_set_style_text_color(p.now_timer, hex(over ? kBadHex : 0xFFFFFF), 0);
    }
    service_status st;
    if (p.has_snap) {
        st = service_footer(*p.snap, now_ms);
    } else {
        snprintf(st.text, sizeof st.text, "Waiting for Relay");
    }
    if (strcmp(st.text, p.drawn_footer) != 0 || st.tone != p.drawn_tone) {
        snprintf(p.drawn_footer, sizeof p.drawn_footer, "%s", st.text);
        p.drawn_tone = st.tone;
        set_ascii(p.footer, st.text);
        const uint32_t c = st.tone == service_tone::good ? kGoodHex : st.tone == service_tone::bad ? kBadHex : kDimHex;
        lv_obj_set_style_bg_color(p.dot, hex(c), 0);
        lv_obj_set_style_text_color(p.footer, hex(st.tone == service_tone::bad ? kBadHex : 0xD0D0D0), 0);
    }
}

}  // namespace

void service_view_build(int pane, lv_obj_t *box, int slot, int w)
{
    if (pane < 0 || pane > 1) {
        return;
    }
    SPane &p = s_p[pane];
    service_state *snap = p.snap;
    p = SPane();
    if (snap == nullptr) {
        void *mem = heap_caps_malloc_prefer(sizeof(service_state), 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT,
                                            MALLOC_CAP_DEFAULT);
        if (mem == nullptr) {
            ESP_LOGE(TAG, "view: no memory for the service");
            return;
        }
        snap = new (mem) service_state();
    }
    p.snap = snap;
    p.slot = slot;
    p.w = w;
    p.box = box;
    const panel_service mode = panel_link_service();
    p.mode_rev = panel_link_service_revision();
    p.controls = service_mode_controls(mode.mode) && mode.key[0] != '\0';
    p.shows = !(mode.mode == service_mode::control && p.controls);
    lv_obj_set_style_bg_color(box, hex(kBgHex), 0);
    const bool narrow = w < kLcdWidth;
    const int timer_w = narrow ? 120 : 200;

    // Now.
    lv_obj_t *now = plain_box(box);
    lv_obj_set_pos(now, 0, kCaptionHeaderH);
    lv_obj_set_size(now, w, kNowH);
    lv_obj_set_style_bg_color(now, hex(kNowHex), 0);
    lv_obj_set_style_bg_opa(now, LV_OPA_COVER, 0);
    p.now_title = text(now, &relay_caption_36, 0xFFFFFF);
    lv_label_set_long_mode(p.now_title, LV_LABEL_LONG_DOT);
    lv_obj_set_width(p.now_title, w - 2 * kPad - timer_w);
    lv_obj_set_pos(p.now_title, kPad, 10);
    p.now_timer = text(now, &lv_font_montserrat_40, 0xFFFFFF);
    lv_obj_set_width(p.now_timer, timer_w);
    lv_obj_set_style_text_align(p.now_timer, LV_TEXT_ALIGN_RIGHT, 0);
    lv_obj_align(p.now_timer, LV_ALIGN_TOP_RIGHT, -kPad, 10);
    lv_obj_t *next_row = plain_box(now);
    lv_obj_set_size(next_row, w - 2 * kPad, LV_SIZE_CONTENT);
    lv_obj_set_pos(next_row, kPad, 64);
    lv_obj_set_flex_flow(next_row, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(next_row, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(next_row, 12, 0);
    lv_obj_t *next_word = text(next_row, &lv_font_montserrat_20, kDimHex);
    lv_label_set_text(next_word, "NEXT");
    p.next_title = text(next_row, &relay_caption_36, 0xB0B0B0);
    lv_label_set_long_mode(p.next_title, LV_LABEL_LONG_DOT);
    lv_obj_set_flex_grow(p.next_title, 1);

    // The order of service.
    const int list_top = kCaptionHeaderH + kNowH;
    const int bar_h = !p.controls ? 0 : p.shows ? kBarH : kLcdHeight - list_top - kFooterH;
    p.list = plain_box(box);
    lv_obj_set_pos(p.list, 0, list_top);
    lv_obj_set_size(p.list, w, kLcdHeight - list_top - kFooterH - bar_h);
    if (!p.shows) {
        lv_obj_add_flag(p.list, LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_add_flag(p.list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(p.list, LV_OBJ_FLAG_SCROLL_ELASTIC);
    lv_obj_remove_flag(p.list, LV_OBJ_FLAG_SCROLL_CHAIN);
    lv_obj_set_scroll_dir(p.list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(p.list, LV_SCROLLBAR_MODE_ACTIVE);
    lv_obj_set_flex_flow(p.list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_hor(p.list, kPad - 12, 0);
    lv_obj_set_style_pad_ver(p.list, kCaptionPadY, 0);
    lv_obj_set_style_pad_row(p.list, 4, 0);
    lv_obj_set_style_bg_color(p.list, hex(0x707070), LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_opa(p.list, LV_OPA_COVER, LV_PART_SCROLLBAR);
    lv_obj_set_style_width(p.list, 6, LV_PART_SCROLLBAR);
    lv_obj_add_event_cb(p.list, on_scroll, LV_EVENT_SCROLL, &p);

    p.empty = text(box, &lv_font_montserrat_28, kDimHex);
    lv_label_set_long_mode(p.empty, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(p.empty, w - 2 * kPad);
    lv_obj_set_style_text_align(p.empty, LV_TEXT_ALIGN_CENTER, 0);
    lv_obj_align(p.empty, LV_ALIGN_TOP_MID, 0, list_top + 60);

    p.jump = lv_button_create(box);
    lv_obj_remove_style_all(p.jump);
    lv_obj_set_size(p.jump, narrow ? 220 : kCaptionJumpW, kCaptionJumpH);
    lv_obj_align(p.jump, LV_ALIGN_BOTTOM_MID, 0, -kFooterH - bar_h - 12);
    lv_obj_set_style_bg_color(p.jump, hex(kJumpHex), 0);
    lv_obj_set_style_bg_opa(p.jump, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(p.jump, kCaptionJumpH / 2, 0);
    lv_obj_set_style_border_color(p.jump, hex(0xFFFFFF), 0);
    lv_obj_set_style_border_width(p.jump, 2, 0);
    lv_obj_set_style_bg_color(p.jump, hex(0x1F52C2), LV_STATE_PRESSED);
    lv_obj_t *jl = text(p.jump, &lv_font_montserrat_28, 0xFFFFFF);
    lv_label_set_text(jl, LV_SYMBOL_UP "  Jump to now");
    lv_obj_center(jl);
    lv_obj_add_event_cb(p.jump, on_jump, LV_EVENT_CLICKED, &p);
    lv_obj_add_flag(p.jump, LV_OBJ_FLAG_HIDDEN);

    // The footer.
    lv_obj_t *foot = plain_box(box);
    lv_obj_set_pos(foot, 0, kLcdHeight - kFooterH);
    lv_obj_set_size(foot, w, kFooterH);
    lv_obj_set_style_bg_color(foot, hex(0x1E1E1E), 0);
    lv_obj_set_style_bg_opa(foot, LV_OPA_COVER, 0);
    lv_obj_set_flex_flow(foot, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(foot, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_hor(foot, kPad, 0);
    lv_obj_set_style_pad_column(foot, 10, 0);
    p.dot = plain_box(foot);
    lv_obj_set_size(p.dot, 14, 14);
    lv_obj_set_style_radius(p.dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(p.dot, LV_OPA_COVER, 0);
    p.footer = text(foot, &lv_font_montserrat_20, 0xD0D0D0);
    lv_label_set_long_mode(p.footer, LV_LABEL_LONG_DOT);
    lv_obj_set_flex_grow(p.footer, 1);

    if (p.controls) {
        build_bar(p, kLcdHeight - kFooterH - bar_h, bar_h);
    }

    p.follow.reset(lv_tick_get());
    p.built = true;
    ESP_LOGI(TAG, "view: service pane %d up for slot %d%s", pane + 1, slot,
             !p.controls ? "" : p.shows ? ", with controls" : ", controls only");
}

void service_view_tick(int pane, uint32_t now_ms, bool touching)
{
    if (pane < 0 || pane > 1 || !s_p[pane].built) {
        return;
    }
    SPane &p = s_p[pane];
    if (touching) {
        p.follow.touched(now_ms);
    }
    const uint32_t rev = captions_revision(p.slot);
    if (!p.synced || rev != p.revision) {
        p.revision = rev;
        p.synced = true;
        captions_read_service(p.slot, copy_state, &p);
        const uint32_t changes = p.has_snap ? p.snap->changes : 0;
        if (changes != p.drawn_changes) {
            p.drawn_changes = changes;
            if (p.note_item >= 0) {
                notes_close(p);   // the item under it may have gone
            }
            // A paused list keeps where the finger left it; a following one is put on the current item below.
            lv_obj_update_layout(p.list);
            const int kept_y = static_cast<int>(lv_obj_get_scroll_y(p.list));
            if (p.has_snap) {
                build_rows(p);
                if (!p.follow.following()) {
                    lv_obj_update_layout(p.list);
                    p.programmatic = true;
                    lv_obj_scroll_to_y(p.list, kept_y, LV_ANIM_OFF);
                    p.programmatic = false;
                }
            } else {
                lv_obj_clean(p.list);
                p.row_count = 0;
            }
            draw_now(p, now_ms);
            // The rows were made again, so the list is back at its top: put the current item in view again.
            p.scroll_due = true;
            const int focus = p.has_snap ? service_focus(*p.snap) : -1;
            if (focus != p.drawn_focus) {
                p.drawn_focus = focus;
                ESP_LOGI(TAG, "view: service pane %d, current item %d of %d", pane + 1,
                         p.has_snap ? p.snap->current + 1 : 0, p.has_snap ? p.snap->count : 0);
            }
        }
    }
    if (p.follow.tick(now_ms)) {
        ESP_LOGI(TAG, "view: service pane %d following (no touch for a minute)", pane + 1);
        p.scroll_due = true;
    }
    // Following keeps the current item in view, unless a finger is on the list: it is not moved under it.
    if (p.scroll_due && p.follow.following() && !touching) {
        p.scroll_due = false;
        scroll_to_focus(p);
    }
    show_jump(p, p.shows && !p.follow.following());
    draw_clock(p, now_ms);
    draw_controls(p, now_ms);
    // The operator changed the panel's service mode: the buttons come or go, so the picture is made again.
    if (panel_link_service_revision() != p.mode_rev && !p.rebuild_asked) {
        p.rebuild_asked = true;
        screen_show_captions();
    }
}

bool service_view_title(int pane, char *out, size_t cap)
{
    if (pane < 0 || pane > 1 || !s_p[pane].built || !s_p[pane].has_snap || !s_p[pane].snap->known ||
        s_p[pane].snap->name[0] == '\0') {
        return false;
    }
    service_ascii(s_p[pane].snap->name, out, cap);
    return true;
}

void service_view_teardown()
{
    for (SPane &p : s_p) {
        p.built = false;
        p.box = p.now_title = p.now_timer = p.next_title = p.list = p.empty = p.jump = p.dot = p.footer = nullptr;
        p.scrim = nullptr;
        p.row_count = 0;
        p.note_item = -1;
        p.btn_prev = p.btn_next = p.btn_take = p.take_label = p.message = p.confirm = nullptr;
    }
}

void service_view_report()
{
    for (int i = 0; i < 2; ++i) {
        SPane &p = s_p[i];
        if (!p.built) {
            continue;
        }
        const service_state *s = p.has_snap ? p.snap : nullptr;
        // Where the current item's row is on the glass (its centre), or -1: what a test taps to open its notes.
        int row_y = -1;
        const int focus = s != nullptr ? service_focus(*s) : -1;
        if (focus >= 0 && focus < p.row_count) {
            lv_area_t a;
            lv_obj_update_layout(p.list);
            lv_obj_get_coords(p.rows[focus], &a);
            row_y = (a.y1 + a.y2) / 2;
        }
        if (p.controls && p.btn_take != nullptr) {
            lv_area_t a, b, c;
            lv_obj_get_coords(p.btn_prev, &a);
            lv_obj_get_coords(p.btn_next, &b);
            lv_obj_get_coords(p.btn_take, &c);
            ESP_LOGI(TAG, "view: service pane %d buttons: previous %d %d, next %d %d, %s %d %d, message \"%s\"%s", i + 1,
                     static_cast<int>((a.x1 + a.x2) / 2), static_cast<int>((a.y1 + a.y2) / 2),
                     static_cast<int>((b.x1 + b.x2) / 2), static_cast<int>((b.y1 + b.y2) / 2),
                     lv_label_get_text(p.take_label), static_cast<int>((c.x1 + c.x2) / 2),
                     static_cast<int>((c.y1 + c.y2) / 2), p.drawn_message,
                     p.confirm != nullptr ? ", confirm up" : "");
        }
        ESP_LOGI(TAG, "view: service pane %d, %s, %d items, current %d, next %d, timer \"%s\", footer \"%s\", %s, "
                      "row at %d%s",
                 i + 1, s != nullptr && s->known ? "known" : "waiting", s != nullptr ? s->count : 0,
                 s != nullptr ? s->current + 1 : 0, s != nullptr ? s->next + 1 : 0, p.drawn_timer, p.drawn_footer,
                 p.follow.following() ? "following" : "paused", row_y, p.note_item >= 0 ? ", notes open" : "");
    }
}
