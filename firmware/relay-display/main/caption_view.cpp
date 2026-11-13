#include "caption_view.h"

#include <cstdio>
#include <cstring>

#include "caption_view_format.h"
#include "captions.h"
#include "config.h"
#include "display.h"
#include "ui_fonts.h"
#include "feeds.h"
#include "esp_log.h"
#include "panel_link.h"
#include "screen.h"
#include "alert_view.h"
#include "lyrics_view.h"
#include "status_view.h"
#include "timers_view.h"
#include "service_view.h"

extern "C" {
}

namespace {

const char *TAG = "view";

constexpr uint32_t kTickMs = 50;

// Colours: the screen's near-black ground, a header a step lighter, white text, and three signal colours.
constexpr uint32_t kBgHex = 0x101010;
constexpr uint32_t kHeaderHex = 0x1E1E1E;
constexpr uint32_t kDimHex = 0x8C8C8C;
constexpr uint32_t kGoodHex = 0x3DD68C;
constexpr uint32_t kBusyHex = 0xF5A524;
constexpr uint32_t kBadHex = 0xF0524F;
constexpr uint32_t kJumpHex = 0x2F6FED;

// One column of captions: a header, the list of lines and its Jump button. A view holds one, or two side by side;
// each follows live, scrolls and jumps on its own.
struct Pane {
    bool built = false;
    bool service = false;   // the Planning Center service (`service_view.cpp`) instead of captions
    bool alert = false;     // the alert buttons (`alert_view.cpp`, #121) instead of captions
    bool lyrics = false;    // ProPresenter's lyrics (`lyrics_view.cpp`, #138) instead of captions
    bool timers = false;    // ProPresenter's timers (`timers_view.cpp`)
    bool status = false;    // the status view (`status_view.cpp`, #224)
    int slot = 0;
    int x = 0;
    int y = 0;
    int w = kLcdWidth;
    int h = kLcdHeight;

    lv_obj_t *box = nullptr;
    lv_obj_t *header = nullptr;
    lv_obj_t *list = nullptr;
    lv_obj_t *title = nullptr;
    lv_obj_t *dot = nullptr;
    lv_obj_t *state_label = nullptr;
    lv_obj_t *empty = nullptr;
    lv_obj_t *notice = nullptr;
    lv_obj_t *jump = nullptr;

    caption_follow follow;
    // The lines on the glass, oldest first, one per child of `list`.
    caption_shown_line shown[kCaptionLines] = {};
    int shown_count = 0;

    uint32_t revision = 0;
    bool synced = false;
    caption_state state = caption_state::off;
    uint8_t shown_stream = 0xFF;  // what the header's title was made from, so it is made again when that changes
    char shown_lang[sizeof(relay_settings_t::feed_lang)] = {};
    bool notice_shown = false;

    // Set while this code moves the list, so its scroll events are not taken for a finger's.
    bool programmatic = false;
};

// The menu that picks what the panes show: a card over the captions with One feed / Two feeds, which pane (when two),
// and the feeds on offer.
struct Menu {
    bool open = false;          // wanted open: survives the view being built again for a change of layout
    int pane = 0;               // the pane a pick is for
    bool dirty = false;         // rebuild the card's contents on the next tick
    lv_obj_t *button = nullptr; // the header button that opens it
    lv_obj_t *scrim = nullptr;
    lv_obj_t *card = nullptr;
    int turn_armed = -1;        // a rotation tapped once (0, 90, 180, 270); a second tap restarts the panel at it
    feed_pick rows[kFeedOptionsMax + 1];
};

struct View {
    bool built = false;
    bool split = false;
    int count = 1;
    Pane pane[2];
    Menu menu;
    lv_obj_t *root = nullptr;
    lv_timer_t *timer = nullptr;
    uint32_t feeds_rev = 0;
    bool rebuild_requested = false;
};

constexpr int kMenuButtonW = ui_px(64);
constexpr int kMenuButtonH = ui_px(44);
// Room a header leaves for the menu button at its right edge.
constexpr int kMenuReserve = kMenuButtonW + ui_px(20);
constexpr int kNoticeH = ui_px(32);

View s_v;
feeds_view s_fv;  // the last state read from `feeds.h`; kept off the stack
lv_style_t s_line_style;
bool s_style_ready = false;

lv_color_t hex(uint32_t rgb)
{
    return lv_color_hex(rgb);
}

// Whether any pointer has a finger on it now.
bool finger_down()
{
    for (lv_indev_t *i = lv_indev_get_next(nullptr); i != nullptr; i = lv_indev_get_next(i)) {
        if (lv_indev_get_type(i) == LV_INDEV_TYPE_POINTER && lv_indev_get_state(i) == LV_INDEV_STATE_PRESSED) {
            return true;
        }
    }
    return false;
}

int px_below(Pane &p)
{
    lv_obj_update_layout(p.list);
    return static_cast<int>(lv_obj_get_scroll_bottom(p.list));
}

void scroll_to_live(Pane &p)
{
    const int below = px_below(p);
    if (below <= 0) {
        return;
    }
    p.programmatic = true;
    lv_obj_scroll_by(p.list, 0, -below, LV_ANIM_OFF);
    p.programmatic = false;
}

// **Nothing is set to the value it already has.** LVGL 9.2 redraws on every `lv_label_set_text`, local style set and
// un-hide, changed or not, and these run on the 50 ms tick: a header refreshed each tick was four strips redrawn
// and a frame-buffer flip twenty times a second on a still screen (77 flushes/s, 40% CPU in the diag log), and
// that traffic on the PSRAM bus is what the panel's bounce-buffer refill loses to: flickering bands.
void set_text(lv_obj_t *label, const char *words)
{
    if (strcmp(lv_label_get_text(label), words) != 0) {
        lv_label_set_text(label, words);
    }
}

void set_bg(lv_obj_t *o, lv_color_t c)
{
    if (!lv_color_eq(lv_obj_get_style_bg_color(o, 0), c)) {
        lv_obj_set_style_bg_color(o, c, 0);
    }
}

void set_shown(lv_obj_t *o, bool shown)
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

void show_jump(Pane &p, bool shown)
{
    set_shown(p.jump, shown);
}

void log_following(const Pane &p, const char *why)
{
    if (s_v.count > 1) {
        ESP_LOGI(TAG, "view: pane %d %s (%s)", p.slot + 1, p.follow.following() ? "following live" : "paused", why);
    } else {
        ESP_LOGI(TAG, "view: %s (%s)", p.follow.following() ? "following live" : "paused", why);
    }
}

// ---------------------------------------------------------------------------
// The lines
// ---------------------------------------------------------------------------

void add_line(Pane &p, const caption_line &line)
{
    lv_obj_t *label = lv_label_create(p.list);
    lv_obj_add_style(label, &s_line_style, 0);
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(label, lv_pct(100));
    lv_label_set_text(label, line.text);
    p.shown[p.shown_count++] = {line.seq, caption_text_hash(line.text)};
}

void clear_lines(Pane &p)
{
    lv_obj_clean(p.list);
    p.shown_count = 0;
}

// Delete the `n` oldest lines. A view that is paused keeps what it shows where it is: the lines above the
// finger's place go, so the content under it moves up by their height, and the scroll is moved back by that.
void drop_front(Pane &p, int n)
{
    if (n <= 0) {
        return;
    }
    lv_obj_update_layout(p.list);
    const int row_gap = kCaptionLineGap;
    int removed = 0;
    for (int i = 0; i < n; ++i) {
        removed += static_cast<int>(lv_obj_get_height(lv_obj_get_child(p.list, i))) + row_gap;
    }
    const int y = static_cast<int>(lv_obj_get_scroll_y(p.list));
    for (int i = 0; i < n; ++i) {
        lv_obj_delete(lv_obj_get_child(p.list, 0));
    }
    for (int i = n; i < p.shown_count; ++i) {
        p.shown[i - n] = p.shown[i];
    }
    p.shown_count -= n;
    if (!p.follow.following()) {
        lv_obj_update_layout(p.list);
        lv_obj_scroll_to_y(p.list, y > removed ? y - removed : 0, LV_ANIM_OFF);
    }
}

void read_feed_now(Pane &p, const caption_feed *feed, caption_state state)
{
    p.state = state;
    if (feed == nullptr) {
        clear_lines(p);
        return;
    }
    const caption_buffer &buf = feed->lines();
    lv_obj_update_layout(p.list);
    const caption_plan plan = caption_plan_update(p.shown, p.shown_count, buf);
    if (plan.rebuild) {
        // A snapshot, a reset or another feed replaced what the list held, and where the finger had it is no
        // longer anywhere: show live.
        clear_lines(p);
        for (int i = 0; i < buf.size(); ++i) {
            add_line(p, buf.at(i));
        }
        if (!p.follow.following()) {
            p.follow.reset(lv_tick_get());
            log_following(p, "the lines were replaced");
        }
        return;
    }
    drop_front(p, plan.drop_front);
    if (plan.append_from >= 0) {
        for (int i = plan.append_from; i < buf.size(); ++i) {
            add_line(p, buf.at(i));
        }
    }
}

// Under the feed's lock, from `captions_read`. **Everything here moves the list, and none of it is a finger.**
// Deleting the oldest lines shrinks the content, and LVGL then pulls a scroll position that is past the new end
// back to it and says so with a scroll event, which looked like a drag to the bottom and put a paused view
// back to live. So the scroll events from this call are not taken for the user's.
void read_feed(const caption_feed *feed, caption_state state, void *ctx)
{
    Pane &p = *static_cast<Pane *>(ctx);
    p.programmatic = true;
    read_feed_now(p, feed, state);
    p.programmatic = false;
}

// ---------------------------------------------------------------------------
// The header
// ---------------------------------------------------------------------------

lv_color_t tone_colour(caption_tone t)
{
    switch (t) {
    case caption_tone::good: return hex(kGoodHex);
    case caption_tone::busy: return hex(kBusyHex);
    case caption_tone::bad: return hex(kBadHex);
    case caption_tone::idle: break;
    }
    return hex(kDimHex);
}

// The list sits under the header, and under the notice when there is one.
void place_list(Pane &p)
{
    const int top = kCaptionHeaderH + (p.notice_shown ? kNoticeH : 0);
    lv_obj_set_pos(p.list, 0, top);
    lv_obj_set_size(p.list, p.w, p.h - top);
    lv_obj_align(p.empty, LV_ALIGN_CENTER, 0, top / 2);
}

// A service pane's header: the service's name and the connection. Its body is `service_view.cpp`'s.
void refresh_service_header(Pane &p)
{
    char title[48];
    if (!service_view_title(p.slot, title, sizeof title)) {
        snprintf(title, sizeof title, "Service");
    }
    if (strcmp(lv_label_get_text(p.title), title) != 0) {
        lv_label_set_text(p.title, title);
    }
    set_text(p.state_label, caption_state_label(p.state));
    set_bg(p.dot, tone_colour(caption_state_tone(p.state)));
}

// An Alert pane's header: "Alerts" and the connection. Its body is `alert_view.cpp`'s.
void refresh_alert_header(Pane &p)
{
    if (strcmp(lv_label_get_text(p.title), "Alerts") != 0) {
        lv_label_set_text(p.title, "Alerts");
    }
    set_text(p.state_label, caption_state_label(p.state));
    set_bg(p.dot, tone_colour(caption_state_tone(p.state)));
}

// A ProPresenter pane's header: its title and the connection. Its body is `lyrics_view.cpp`'s or `timers_view.cpp`'s.
void refresh_pp_header(Pane &p, const char *title)
{
    if (strcmp(lv_label_get_text(p.title), title) != 0) {
        lv_label_set_text(p.title, title);
    }
    set_text(p.state_label, caption_state_label(p.state));
    set_bg(p.dot, tone_colour(caption_state_tone(p.state)));
}

void refresh_header(Pane &p)
{
    char lang[sizeof p.shown_lang] = {};
    const uint8_t stream = captions_feed_choice(p.slot, lang, sizeof lang);
    if (stream != p.shown_stream || strcmp(lang, p.shown_lang) != 0) {
        const bool first = p.shown_stream == 0xFF;
        p.shown_stream = stream;
        snprintf(p.shown_lang, sizeof p.shown_lang, "%s", lang);
        char title[48];
        caption_feed_title(stream, lang, title, sizeof title);
        lv_label_set_text(p.title, title);
        if (!first) {
            // Another feed is another list: it opens at live.
            p.follow.reset(lv_tick_get());
            show_jump(p, false);
        }
    }
    set_text(p.state_label, caption_state_label(p.state));
    set_bg(p.dot, tone_colour(caption_state_tone(p.state)));
    set_text(p.empty, caption_empty_text(p.state));
    set_shown(p.empty, p.shown_count == 0);
}

// The strip under the header that says a language is not live, while its pane shows the transcription.
void refresh_notice(Pane &p, int pane_index)
{
    if (p.service || p.alert || p.lyrics || p.timers) {
        return;   // a service, alert, lyrics or timers pane is never a fallback
    }
    char note[64] = {};
    const bool want = s_fv.shown[pane_index].fallback;
    if (want) {
        feed_fallback_note(s_fv.saved[pane_index], note, sizeof note);
    }
    const bool show = want && note[0] != '\0';
    if (show) {
        lv_label_set_text(lv_obj_get_child(p.notice, 0), note);
    }
    if (show != p.notice_shown) {
        p.notice_shown = show;
        if (show) {
            lv_obj_remove_flag(p.notice, LV_OBJ_FLAG_HIDDEN);
        } else {
            lv_obj_add_flag(p.notice, LV_OBJ_FLAG_HIDDEN);
        }
        place_list(p);
    }
}

// ---------------------------------------------------------------------------
// Following, and the finger
// ---------------------------------------------------------------------------

void on_scroll(lv_event_t *e)
{
    Pane &p = *static_cast<Pane *>(lv_event_get_user_data(e));
    if (p.programmatic) {
        return;
    }
    const bool at_bottom = caption_at_bottom(static_cast<int>(lv_obj_get_scroll_bottom(p.list)));
    if (p.follow.user_scrolled(at_bottom, lv_tick_get())) {
        log_following(p, at_bottom ? "scrolled to the bottom" : "scrolled back");
        show_jump(p, !p.follow.following());
    }
}

void on_jump(lv_event_t *e)
{
    Pane &p = *static_cast<Pane *>(lv_event_get_user_data(e));
    p.follow.jump(lv_tick_get());
    scroll_to_live(p);
    show_jump(p, false);
    log_following(p, "jump to live");
}

void tick_pane(Pane &p, uint32_t now, bool touching)
{
    if (!p.built) {
        return;
    }
    if (p.service) {
        p.state = captions_state(p.slot);
        service_view_tick(p.slot, now, touching);
        refresh_service_header(p);
        return;
    }
    if (p.alert) {
        p.state = captions_state(p.slot);
        alert_view_tick(p.slot, now);
        refresh_alert_header(p);
        return;
    }
    if (p.lyrics) {
        p.state = captions_state(p.slot);
        lyrics_view_tick(p.slot, now);
        refresh_pp_header(p, "Lyrics");
        return;
    }
    if (p.timers) {
        p.state = captions_state(p.slot);
        timers_view_tick(p.slot, now);
        refresh_pp_header(p, "Timers");
        return;
    }
    if (p.status) {
        p.state = captions_state(p.slot);
        status_view_tick(p.slot, now);
        refresh_pp_header(p, "Status");
        return;
    }
    const uint32_t rev = captions_revision(p.slot);
    if (!p.synced || rev != p.revision) {
        p.revision = rev;
        p.synced = true;
        captions_read(p.slot, read_feed, &p);
        refresh_header(p);
    }
    if (p.follow.tick(now)) {
        log_following(p, "no touch for a minute");
    }
    // Following means the newest line is in view, unless a finger is on the list: it is not moved under it.
    if (p.follow.following() && !touching) {
        scroll_to_live(p);
    }
    show_jump(p, !p.follow.following());
}

// ---------------------------------------------------------------------------
// The picture
// ---------------------------------------------------------------------------

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

void build_header(Pane &p, bool last)
{
    p.header = plain_box(p.box);
    lv_obj_set_pos(p.header, 0, 0);
    lv_obj_set_size(p.header, p.w, kCaptionHeaderH);
    lv_obj_set_style_bg_color(p.header, hex(kHeaderHex), 0);
    lv_obj_set_style_bg_opa(p.header, LV_OPA_COVER, 0);

    // The menu button sits over the last pane's right edge, so that header leaves room for it.
    const int reserve = last ? kMenuReserve : 0;
    const bool narrow = p.w < kCaptionNarrowW;

    p.title = text(p.header, &RELAY_UI_FONT_MEDIUM, 0xFFFFFF);
    lv_label_set_long_mode(p.title, LV_LABEL_LONG_CLIP);
    lv_obj_set_width(p.title, narrow ? p.w - kCaptionPadX - ui_px(44) - reserve : p.w / 2);
    lv_obj_align(p.title, LV_ALIGN_LEFT_MID, kCaptionPadX, 0);

    // The indicator: a dot and the connection in words (the dot alone on a half-width pane), a row that sizes
    // to what it holds.
    lv_obj_t *status = plain_box(p.header);
    lv_obj_set_size(status, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(status, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(status, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(status, ui_px(12), 0);
    lv_obj_align(status, LV_ALIGN_RIGHT_MID, -(narrow ? ui_px(14) : kCaptionPadX) - reserve, 0);

    p.dot = plain_box(status);
    lv_obj_set_size(p.dot, ui_px(16), ui_px(16));
    lv_obj_set_style_radius(p.dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(p.dot, LV_OPA_COVER, 0);
    p.state_label = text(status, &RELAY_UI_FONT_SMALL, 0xD0D0D0);
    if (narrow) {
        lv_obj_add_flag(p.state_label, LV_OBJ_FLAG_HIDDEN);
    }
}

void build_list(Pane &p)
{
    p.list = plain_box(p.box);
    lv_obj_add_flag(p.list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(p.list, LV_OBJ_FLAG_SCROLL_ELASTIC);
    lv_obj_remove_flag(p.list, LV_OBJ_FLAG_SCROLL_CHAIN);
    lv_obj_set_scroll_dir(p.list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(p.list, LV_SCROLLBAR_MODE_ACTIVE);
    lv_obj_set_flex_flow(p.list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_left(p.list, kCaptionPadX, 0);
    lv_obj_set_style_pad_right(p.list, kCaptionPadX, 0);
    lv_obj_set_style_pad_top(p.list, kCaptionPadY, 0);
    lv_obj_set_style_pad_bottom(p.list, kCaptionPadY, 0);
    lv_obj_set_style_pad_row(p.list, kCaptionLineGap, 0);
    // `remove_style_all` took the theme's scrollbar with it: a thin bar that shows how far back the list is.
    lv_obj_set_style_bg_color(p.list, hex(0x707070), LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_opa(p.list, LV_OPA_COVER, LV_PART_SCROLLBAR);
    lv_obj_set_style_width(p.list, ui_px(6), LV_PART_SCROLLBAR);
    lv_obj_set_style_radius(p.list, ui_px(3), LV_PART_SCROLLBAR);
    lv_obj_add_event_cb(p.list, on_scroll, LV_EVENT_SCROLL, &p);

    p.empty = text(p.box, &RELAY_UI_FONT_MEDIUM, kDimHex);
    lv_label_set_long_mode(p.empty, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(p.empty, p.w - 2 * kCaptionPadX);
    lv_obj_set_style_text_align(p.empty, LV_TEXT_ALIGN_CENTER, 0);

    // The notice that a language is not live, between the header and the list.
    p.notice = plain_box(p.box);
    lv_obj_set_pos(p.notice, 0, kCaptionHeaderH);
    lv_obj_set_size(p.notice, p.w, kNoticeH);
    lv_obj_set_style_bg_color(p.notice, hex(0x4A3300), 0);
    lv_obj_set_style_bg_opa(p.notice, LV_OPA_COVER, 0);
    lv_obj_t *note = text(p.notice, &RELAY_UI_FONT_SMALL, kBusyHex);
    lv_label_set_long_mode(note, LV_LABEL_LONG_CLIP);
    lv_obj_set_width(note, p.w - 2 * kCaptionPadX);
    lv_obj_align(note, LV_ALIGN_LEFT_MID, kCaptionPadX, 0);
    lv_obj_add_flag(p.notice, LV_OBJ_FLAG_HIDDEN);
    p.notice_shown = false;
    place_list(p);
}

void build_jump(Pane &p)
{
    p.jump = lv_button_create(p.box);
    lv_obj_remove_style_all(p.jump);
    lv_obj_set_size(p.jump, kCaptionJumpW, kCaptionJumpH);
    lv_obj_align(p.jump, LV_ALIGN_BOTTOM_MID, 0, -kCaptionJumpMargin);
    lv_obj_set_style_bg_color(p.jump, hex(kJumpHex), 0);
    lv_obj_set_style_bg_opa(p.jump, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(p.jump, kCaptionJumpH / 2, 0);
    lv_obj_set_style_border_color(p.jump, hex(0xFFFFFF), 0);
    lv_obj_set_style_border_width(p.jump, 2, 0);
    lv_obj_set_style_bg_color(p.jump, hex(0x1F52C2), LV_STATE_PRESSED);
    lv_obj_t *label = text(p.jump, &RELAY_UI_FONT_MEDIUM, 0xFFFFFF);
    lv_label_set_text(label, LV_SYMBOL_DOWN "  Jump to live");
    lv_obj_center(label);
    lv_obj_add_event_cb(p.jump, on_jump, LV_EVENT_CLICKED, &p);
    lv_obj_add_flag(p.jump, LV_OBJ_FLAG_HIDDEN);
}

void build_pane(lv_obj_t *root, Pane &p, int index, bool split)
{
    p = Pane();
    p.slot = index;
    const feed_pane_rect r = feed_pane_rect_for(split, index, display_width(), display_height());
    p.x = r.x;
    p.y = r.y;
    p.w = r.w;
    p.h = r.h;
    p.box = plain_box(root);
    lv_obj_set_pos(p.box, p.x, p.y);
    lv_obj_set_size(p.box, p.w, p.h);
    // The menu button sits at the top right of the screen, over the header of the pane there: the right one side by
    // side, the top one stacked.
    const bool under_menu = !split || index == (r.stacked ? 0 : 1);
    p.service = s_fv.shown[index].pick.stream == kFeedService;
    if (p.service) {
        service_view_build(index, p.box, index, p.w, p.h);
        build_header(p, under_menu);
        p.built = true;
        return;
    }
    p.alert = s_fv.shown[index].pick.stream == kFeedAlert;
    if (p.alert) {
        alert_view_build(index, p.box, index, p.w, p.h);
        build_header(p, under_menu);
        p.built = true;
        return;
    }
    p.lyrics = s_fv.shown[index].pick.stream == kFeedLyrics;
    if (p.lyrics) {
        lyrics_view_build(index, p.box, index, p.w, p.h);
        build_header(p, under_menu);
        p.built = true;
        return;
    }
    p.timers = s_fv.shown[index].pick.stream == kFeedTimers;
    if (p.timers) {
        timers_view_build(index, p.box, index, p.w, p.h);
        build_header(p, under_menu);
        p.built = true;
        return;
    }
    p.status = s_fv.shown[index].pick.stream == kFeedStatus;
    if (p.status) {
        status_view_build(index, p.box, index, p.w, p.h);
        build_header(p, under_menu);
        p.built = true;
        return;
    }
    build_list(p);
    build_header(p, under_menu);
    build_jump(p);
    p.follow.reset(lv_tick_get());
    p.built = true;
}

// ---------------------------------------------------------------------------
// The menu
// ---------------------------------------------------------------------------

constexpr int kCardW = ui_px(620);
constexpr int kCardH = ui_px(440);

void menu_close()
{
    s_v.menu.open = false;
    s_v.menu.turn_armed = -1;
    if (s_v.menu.scrim != nullptr) {
        lv_obj_delete_async(s_v.menu.scrim);
    }
    s_v.menu.scrim = s_v.menu.card = nullptr;
    ESP_LOGI(TAG, "view: menu closed");
}

void on_menu_close(lv_event_t *)
{
    menu_close();
}

void on_menu_scrim(lv_event_t *e)
{
    // A tap on the dark around the card closes it; a tap on the card reaches the card's own children.
    if (lv_event_get_target(e) == s_v.menu.scrim) {
        menu_close();
    }
}

// The screen's rotation (#122). A turn restarts the panel, so it takes two taps on the same angle: the first asks,
// the second turns.
void on_menu_turn(lv_event_t *e)
{
    const int deg = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
    if (deg == display_rotation()) {
        s_v.menu.turn_armed = -1;
        s_v.menu.dirty = true;
        return;
    }
    if (s_v.menu.turn_armed != deg) {
        s_v.menu.turn_armed = deg;
        s_v.menu.dirty = true;
        return;
    }
    ESP_LOGI(TAG, "view: rotation %d confirmed", deg);
    panel_link_turn_from_screen(deg);
}

void on_menu_mode(lv_event_t *e)
{
    s_v.menu.turn_armed = -1;
    const bool two = lv_event_get_user_data(e) != nullptr;
    s_v.menu.pane = 0;
    feeds_set_split(two);
    s_v.menu.dirty = true;
}

void on_menu_pane(lv_event_t *e)
{
    s_v.menu.turn_armed = -1;
    s_v.menu.pane = static_cast<int>(reinterpret_cast<intptr_t>(lv_event_get_user_data(e)));
    s_v.menu.dirty = true;
}

void on_menu_row(lv_event_t *e)
{
    const feed_pick *pick = static_cast<const feed_pick *>(lv_event_get_user_data(e));
    s_v.menu.turn_armed = -1;
    feeds_choose(s_v.menu.pane, *pick);
    s_v.menu.dirty = true;
}

lv_obj_t *menu_button(lv_obj_t *parent, const char *label, int w, int h, bool selected, lv_event_cb_t cb, void *arg)
{
    lv_obj_t *b = lv_button_create(parent);
    lv_obj_remove_style_all(b);
    lv_obj_set_size(b, w, h);
    lv_obj_set_style_bg_color(b, hex(selected ? kJumpHex : 0x303030), 0);
    lv_obj_set_style_bg_opa(b, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(b, ui_px(10), 0);
    lv_obj_set_style_bg_color(b, hex(0x1F52C2), LV_STATE_PRESSED);
    lv_obj_t *l = text(b, &RELAY_UI_FONT_MEDIUM, 0xFFFFFF);
    lv_label_set_text(l, label);
    lv_obj_center(l);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, arg);
    return b;
}

// The card's contents, made again from the feeds' state. Done from the tick, never from inside a button's own
// event, so no object is deleted under its own callback.
void menu_rebuild()
{
    Menu &m = s_v.menu;
    if (m.card == nullptr) {
        return;
    }
    m.dirty = false;
    lv_obj_clean(m.card);
    if (m.pane >= s_fv.split + 1) {
        m.pane = 0;
    }

    lv_obj_t *top = plain_box(m.card);
    lv_obj_set_size(top, lv_pct(100), ui_px(48));
    lv_obj_t *title = text(top, &RELAY_UI_FONT_MEDIUM, 0xFFFFFF);
    lv_label_set_text(title, "Show on screen");
    lv_obj_align(title, LV_ALIGN_LEFT_MID, 0, 0);
    lv_obj_t *done = menu_button(top, "Done", ui_px(110), ui_px(48), false, on_menu_close, nullptr);
    lv_obj_set_ext_click_area(done, 8);
    lv_obj_align(done, LV_ALIGN_RIGHT_MID, 0, 0);

    // The screen's rotation: the one shown is highlighted; another, tapped, turns red and asks for a second tap.
    lv_obj_t *turn = plain_box(m.card);
    lv_obj_set_size(turn, lv_pct(100), ui_px(48));
    lv_obj_set_flex_flow(turn, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(turn, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(turn, ui_px(10), 0);
    lv_obj_t *turn_label = text(turn, &RELAY_UI_FONT_MEDIUM, kDimHex);
    lv_label_set_text(turn_label, LV_SYMBOL_REFRESH);
    static const int kTurns[] = {0, 90, 180, 270};
    static const char *const kTurnWords[] = {"0", "90", "180", "270"};
    for (int i = 0; i < 4; ++i) {
        const bool armed = m.turn_armed == kTurns[i];
        lv_obj_t *b = menu_button(turn, armed ? "Sure?" : kTurnWords[i], ui_px(118), ui_px(48),
                                  kTurns[i] == display_rotation(), on_menu_turn,
                                  reinterpret_cast<void *>(static_cast<intptr_t>(kTurns[i])));
        if (armed) {
            lv_obj_set_style_bg_color(b, hex(0xB3261E), 0);
        }
    }

    lv_obj_t *mode = plain_box(m.card);
    lv_obj_set_size(mode, lv_pct(100), ui_px(52));
    lv_obj_set_flex_flow(mode, LV_FLEX_FLOW_ROW);
    lv_obj_set_style_pad_column(mode, ui_px(12), 0);
    menu_button(mode, "One feed", ui_px(200), ui_px(52), !s_fv.split, on_menu_mode, nullptr);
    menu_button(mode, "Two feeds", ui_px(200), ui_px(52), s_fv.split, on_menu_mode, reinterpret_cast<void *>(1));

    if (s_fv.split) {
        lv_obj_t *tabs = plain_box(m.card);
        lv_obj_set_size(tabs, lv_pct(100), ui_px(44));
        lv_obj_set_flex_flow(tabs, LV_FLEX_FLOW_ROW);
        lv_obj_set_style_pad_column(tabs, ui_px(12), 0);
        menu_button(tabs, "Left", ui_px(140), ui_px(44), m.pane == 0, on_menu_pane, reinterpret_cast<void *>(0));
        menu_button(tabs, "Right", ui_px(140), ui_px(44), m.pane == 1, on_menu_pane, reinterpret_cast<void *>(1));
    }

    lv_obj_t *list = plain_box(m.card);
    lv_obj_set_width(list, lv_pct(100));
    lv_obj_set_flex_grow(list, 1);
    lv_obj_add_flag(list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_scroll_dir(list, LV_DIR_VER);
    lv_obj_set_flex_flow(list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_row(list, ui_px(8), 0);

    const feed_pick &saved = s_fv.saved[m.pane];
    const feed_pick current = saved.stream == kFeedNone ? feed_pick_make(kFeedTranscription, "") : saved;
    int rows = 0;
    auto add_row = [&](const feed_pick &pick, const char *label, bool dim) {
        m.rows[rows] = pick;
        const bool checked = feed_pick_same(pick, current);
        lv_obj_t *b = menu_button(list, "", lv_pct(100), ui_px(56), checked, on_menu_row, &m.rows[rows]);
        lv_obj_t *l = lv_obj_get_child(b, 0);
        lv_label_set_text(l, label);
        lv_obj_set_style_text_color(l, hex(dim ? kDimHex : 0xFFFFFF), 0);
        lv_obj_align(l, LV_ALIGN_LEFT_MID, ui_px(18), 0);
        if (checked) {
            lv_obj_t *tick = text(b, &RELAY_UI_FONT_MEDIUM, 0xFFFFFF);
            lv_label_set_text(tick, LV_SYMBOL_OK);
            lv_obj_align(tick, LV_ALIGN_RIGHT_MID, -ui_px(18), 0);
        }
        ++rows;
    };
    for (int i = 0; i < s_fv.options.count && rows < kFeedOptionsMax; ++i) {
        add_row(s_fv.options.items[i].pick, s_fv.options.items[i].label, false);
    }
    // What was chosen and is not live now is listed, dim and ticked, so the menu says what is saved.
    if (s_fv.options.known && current.stream == kFeedTranslation && !s_fv.options.has(current)) {
        char words[kFeedLabelBytes] = {};
        feed_language_words(current.lang, words, sizeof words);
        char label[kFeedLabelBytes + 16];
        snprintf(label, sizeof label, "%s (not live)", words);
        add_row(current, label, true);
    }
    if (rows == 0) {
        lv_obj_t *none = text(list, &RELAY_UI_FONT_MEDIUM, kDimHex);
        lv_label_set_text(none, s_fv.options.known ? "Nothing is live" : "Waiting for Relay");
    }
    ESP_LOGI(TAG, "view: menu %s, %d on offer, pane %d", s_fv.split ? "two feeds" : "one feed", rows, m.pane + 1);
}

void menu_open()
{
    Menu &m = s_v.menu;
    m.open = true;
    if (m.scrim != nullptr) {
        return;
    }
    m.scrim = plain_box(s_v.root);
    lv_obj_set_size(m.scrim, display_width(), display_height());
    lv_obj_set_pos(m.scrim, 0, 0);
    lv_obj_set_style_bg_color(m.scrim, hex(0x000000), 0);
    lv_obj_set_style_bg_opa(m.scrim, LV_OPA_70, 0);
    lv_obj_add_flag(m.scrim, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_add_event_cb(m.scrim, on_menu_scrim, LV_EVENT_CLICKED, nullptr);

    m.card = plain_box(m.scrim);
    lv_obj_set_size(m.card, kCardW, kCardH);
    lv_obj_center(m.card);
    lv_obj_set_style_bg_color(m.card, hex(kHeaderHex), 0);
    lv_obj_set_style_bg_opa(m.card, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(m.card, ui_px(16), 0);
    lv_obj_set_style_border_color(m.card, hex(0x555555), 0);
    lv_obj_set_style_border_width(m.card, 2, 0);
    lv_obj_set_style_pad_all(m.card, ui_px(16), 0);
    lv_obj_set_style_pad_row(m.card, ui_px(10), 0);
    lv_obj_set_flex_flow(m.card, LV_FLEX_FLOW_COLUMN);
    // A tap on the card is the card's, not the dark around it.
    lv_obj_add_flag(m.card, LV_OBJ_FLAG_CLICKABLE);
    m.pane = 0;
    m.turn_armed = -1;
    menu_rebuild();
    ESP_LOGI(TAG, "view: menu opened");
}

void on_menu_button(lv_event_t *)
{
    menu_open();
}

void build_menu_button(lv_obj_t *root)
{
    Menu &m = s_v.menu;
    m.button = lv_button_create(root);
    lv_obj_remove_style_all(m.button);
    lv_obj_set_size(m.button, kMenuButtonW, kMenuButtonH);
    // The drawn button shrinks with the scale; the touch target around it stays about the old size.
    lv_obj_set_ext_click_area(m.button, 12);
    lv_obj_align(m.button, LV_ALIGN_TOP_RIGHT, -ui_px(12), (kCaptionHeaderH - kMenuButtonH) / 2);
    lv_obj_set_style_bg_color(m.button, hex(0x303030), 0);
    lv_obj_set_style_bg_opa(m.button, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(m.button, ui_px(10), 0);
    lv_obj_set_style_bg_color(m.button, hex(0x1F52C2), LV_STATE_PRESSED);
    lv_obj_t *l = text(m.button, &RELAY_UI_FONT_MEDIUM, 0xFFFFFF);
    lv_label_set_text(l, LV_SYMBOL_LIST);
    lv_obj_center(l);
    lv_obj_add_event_cb(m.button, on_menu_button, LV_EVENT_CLICKED, nullptr);
    m.scrim = m.card = nullptr;
}

// ---------------------------------------------------------------------------
// The tick
// ---------------------------------------------------------------------------

// Take in a change to the choice or to what is live. A change between one feed and two is a different picture:
// the screen is asked to build the view again, once.
void sync_feeds()
{
    if (feeds_revision() == s_v.feeds_rev) {
        return;
    }
    feeds_snapshot(&s_fv);
    s_v.feeds_rev = s_fv.revision;
    // A pane going between captions and the service is a different picture, as one feed and two are.
    bool kind_changed = false;
    for (int i = 0; i < s_v.count; ++i) {
        kind_changed = kind_changed || (s_fv.shown[i].pick.stream == kFeedService) != s_v.pane[i].service ||
                       (s_fv.shown[i].pick.stream == kFeedAlert) != s_v.pane[i].alert ||
                       (s_fv.shown[i].pick.stream == kFeedLyrics) != s_v.pane[i].lyrics ||
                       (s_fv.shown[i].pick.stream == kFeedTimers) != s_v.pane[i].timers ||
                       (s_fv.shown[i].pick.stream == kFeedStatus) != s_v.pane[i].status;
    }
    if (s_fv.split != s_v.split || kind_changed) {
        if (!s_v.rebuild_requested) {
            s_v.rebuild_requested = true;
            screen_show_captions();
        }
        return;
    }
    for (int i = 0; i < s_v.count; ++i) {
        refresh_notice(s_v.pane[i], i);
    }
    if (s_v.menu.open) {
        s_v.menu.dirty = true;
    }
}

void on_tick(lv_timer_t *)
{
    if (!s_v.built) {
        return;
    }
    const uint32_t now = lv_tick_get();
    const bool touching = finger_down();
    sync_feeds();
    for (int i = 0; i < s_v.count; ++i) {
        if (touching) {
            s_v.pane[i].follow.touched(now);
        }
        tick_pane(s_v.pane[i], now, touching);
    }
    if (s_v.menu.dirty && s_v.menu.open) {
        menu_rebuild();
    }
}

}  // namespace

void caption_view_build(lv_obj_t *root)
{
    caption_view_teardown();
    if (!s_style_ready) {
        lv_style_init(&s_line_style);
        lv_style_set_text_font(&s_line_style, &RELAY_UI_FONT_CAPTION);
        lv_style_set_text_color(&s_line_style, lv_color_white());
        lv_style_set_pad_all(&s_line_style, 0);
        s_style_ready = true;
    }
    lv_obj_set_style_bg_color(root, hex(kBgHex), 0);
    feeds_snapshot(&s_fv);
    s_v.feeds_rev = s_fv.revision;
    s_v.root = root;
    s_v.split = s_fv.split;
    s_v.count = s_v.split ? 2 : 1;
    s_v.rebuild_requested = false;

    for (int i = 0; i < s_v.count; ++i) {
        build_pane(root, s_v.pane[i], i, s_v.split);
    }
    if (s_v.split) {
        // A rule between the two panes.
        const feed_pane_rect first = feed_pane_rect_for(true, 0, display_width(), display_height());
        lv_obj_t *rule = plain_box(root);
        if (first.stacked) {
            lv_obj_set_pos(rule, 0, first.h);
            lv_obj_set_size(rule, display_width(), kFeedPaneDividerPx);
        } else {
            lv_obj_set_pos(rule, first.w, 0);
            lv_obj_set_size(rule, kFeedPaneDividerPx, display_height());
        }
        lv_obj_set_style_bg_color(rule, hex(0x3A3A3A), 0);
        lv_obj_set_style_bg_opa(rule, LV_OPA_COVER, 0);
    }
    build_menu_button(root);
    for (int i = 0; i < s_v.count; ++i) {
        refresh_notice(s_v.pane[i], i);
    }
    s_v.built = true;
    s_v.timer = lv_timer_create(on_tick, kTickMs, nullptr);
    on_tick(s_v.timer);  // the first picture is not a tick late
    if (s_v.menu.open) {
        // The menu was open when the layout changed: it goes up again over the new panes.
        menu_open();
    }
    for (int i = 0; i < s_v.count; ++i) {
        ESP_LOGI(TAG, "view: caption view up for slot %d", i);
    }
}

void caption_view_teardown()
{
    service_view_teardown();
    alert_view_teardown();
    lyrics_view_teardown();
    timers_view_teardown();
    status_view_teardown();
    if (s_v.timer != nullptr) {
        lv_timer_delete(s_v.timer);
        s_v.timer = nullptr;
    }
    s_v.built = false;
    for (Pane &p : s_v.pane) {
        p.built = false;
        p.box = p.header = p.list = p.title = p.dot = p.state_label = p.empty = p.notice = p.jump = nullptr;
    }
    s_v.menu.button = s_v.menu.scrim = s_v.menu.card = nullptr;
    s_v.root = nullptr;
}

void caption_view_report()
{
    screen_lock();
    if (!s_v.built) {
        ESP_LOGI(TAG, "view: not on screen");
    } else {
        for (int i = 0; i < s_v.count; ++i) {
            Pane &p = s_v.pane[i];
            if (p.service || p.alert || p.lyrics || p.timers || p.status) {
                continue;   // service_view_report, alert_view_report and the ProPresenter views', below
            }
            ESP_LOGI(TAG, "%s: %s, %d lines, %d px below, jump button %s", i == 0 ? "view" : "view: split, pane 2",
                     p.follow.following() ? "following live" : "paused", p.shown_count, px_below(p),
                     lv_obj_has_flag(p.jump, LV_OBJ_FLAG_HIDDEN) ? "hidden" : "shown");
        }
        service_view_report();
        alert_view_report();
        lyrics_view_report();
        timers_view_report();
        status_view_report();
        ESP_LOGI(TAG, "view: menu %s, %s", s_v.menu.open ? "open" : "closed",
                 s_v.split ? "two feeds" : "one feed");
    }
    screen_unlock();
}
