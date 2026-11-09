#include "caption_view.h"

#include <cstdio>
#include <cstring>

#include "caption_view_format.h"
#include "captions.h"
#include "config.h"
#include "esp_log.h"
#include "screen.h"

extern "C" {
LV_FONT_DECLARE(relay_caption_36);
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

struct View {
    bool built = false;
    int slot = 0;

    lv_obj_t *list = nullptr;
    lv_obj_t *title = nullptr;
    lv_obj_t *dot = nullptr;
    lv_obj_t *state_label = nullptr;
    lv_obj_t *empty = nullptr;
    lv_obj_t *jump = nullptr;
    lv_timer_t *timer = nullptr;

    caption_follow follow;
    // The lines on the glass, oldest first, one per child of `list`.
    caption_shown_line shown[kCaptionLines] = {};
    int shown_count = 0;

    uint32_t revision = 0;
    bool synced = false;
    caption_state state = caption_state::off;
    uint8_t shown_stream = 0xFF;  // what the header's title was made from, so it is made again when that changes
    char shown_lang[sizeof(relay_settings_t::feed_lang)] = {};

    // Set while this code moves the list, so its scroll events are not taken for a finger's.
    bool programmatic = false;
};

View s_v;
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

int px_below()
{
    lv_obj_update_layout(s_v.list);
    return static_cast<int>(lv_obj_get_scroll_bottom(s_v.list));
}

void scroll_to_live()
{
    const int below = px_below();
    if (below <= 0) {
        return;
    }
    s_v.programmatic = true;
    lv_obj_scroll_by(s_v.list, 0, -below, LV_ANIM_OFF);
    s_v.programmatic = false;
}

void show_jump(bool shown)
{
    if (shown) {
        lv_obj_remove_flag(s_v.jump, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_v.jump, LV_OBJ_FLAG_HIDDEN);
    }
}

void log_following(const char *why)
{
    ESP_LOGI(TAG, "view: %s (%s)", s_v.follow.following() ? "following live" : "paused", why);
}

// ---------------------------------------------------------------------------
// The lines
// ---------------------------------------------------------------------------

void add_line(const caption_line &line)
{
    lv_obj_t *label = lv_label_create(s_v.list);
    lv_obj_add_style(label, &s_line_style, 0);
    lv_label_set_long_mode(label, LV_LABEL_LONG_WRAP);
    lv_obj_set_width(label, lv_pct(100));
    lv_label_set_text(label, line.text);
    s_v.shown[s_v.shown_count++] = {line.seq, caption_text_hash(line.text)};
}

void clear_lines()
{
    lv_obj_clean(s_v.list);
    s_v.shown_count = 0;
}

// Delete the `n` oldest lines. A view that is paused keeps what it shows where it is: the lines above the
// finger's place go, so the content under it moves up by their height, and the scroll is moved back by that.
void drop_front(int n)
{
    if (n <= 0) {
        return;
    }
    lv_obj_update_layout(s_v.list);
    const int row_gap = kCaptionLineGap;
    int removed = 0;
    for (int i = 0; i < n; ++i) {
        removed += static_cast<int>(lv_obj_get_height(lv_obj_get_child(s_v.list, i))) + row_gap;
    }
    const int y = static_cast<int>(lv_obj_get_scroll_y(s_v.list));
    for (int i = 0; i < n; ++i) {
        lv_obj_delete(lv_obj_get_child(s_v.list, 0));
    }
    for (int i = n; i < s_v.shown_count; ++i) {
        s_v.shown[i - n] = s_v.shown[i];
    }
    s_v.shown_count -= n;
    if (!s_v.follow.following()) {
        lv_obj_update_layout(s_v.list);
        lv_obj_scroll_to_y(s_v.list, y > removed ? y - removed : 0, LV_ANIM_OFF);
    }
}

void read_feed_now(const caption_feed *feed, caption_state state);

// Under the feed's lock, from `captions_read`. **Everything here moves the list, and none of it is a finger.**
// Deleting the oldest lines shrinks the content, and LVGL then pulls a scroll position that is past the new end
// back to it and says so with a scroll event, which looked like a drag to the bottom and put a paused view
// back to live. So the scroll events from this call are not taken for the user's.
void read_feed(const caption_feed *feed, caption_state state, void *)
{
    s_v.programmatic = true;
    read_feed_now(feed, state);
    s_v.programmatic = false;
}

void read_feed_now(const caption_feed *feed, caption_state state)
{
    s_v.state = state;
    if (feed == nullptr) {
        clear_lines();
        return;
    }
    const caption_buffer &buf = feed->lines();
    lv_obj_update_layout(s_v.list);
    const caption_plan plan = caption_plan_update(s_v.shown, s_v.shown_count, buf);
    if (plan.rebuild) {
        // A snapshot, a reset or another feed replaced what the list held, and where the finger had it is no
        // longer anywhere: show live.
        clear_lines();
        for (int i = 0; i < buf.size(); ++i) {
            add_line(buf.at(i));
        }
        if (!s_v.follow.following()) {
            s_v.follow.reset(lv_tick_get());
            log_following("the lines were replaced");
        }
        return;
    }
    drop_front(plan.drop_front);
    if (plan.append_from >= 0) {
        for (int i = plan.append_from; i < buf.size(); ++i) {
            add_line(buf.at(i));
        }
    }
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

void refresh_header()
{
    char lang[sizeof s_v.shown_lang] = {};
    const uint8_t stream = captions_feed_choice(s_v.slot, lang, sizeof lang);
    if (stream != s_v.shown_stream || strcmp(lang, s_v.shown_lang) != 0) {
        s_v.shown_stream = stream;
        snprintf(s_v.shown_lang, sizeof s_v.shown_lang, "%s", lang);
        char title[48];
        caption_feed_title(stream, lang, title, sizeof title);
        lv_label_set_text(s_v.title, title);
    }
    lv_label_set_text(s_v.state_label, caption_state_label(s_v.state));
    lv_obj_set_style_bg_color(s_v.dot, tone_colour(caption_state_tone(s_v.state)), 0);
    lv_label_set_text(s_v.empty, caption_empty_text(s_v.state));
    if (s_v.shown_count == 0) {
        lv_obj_remove_flag(s_v.empty, LV_OBJ_FLAG_HIDDEN);
    } else {
        lv_obj_add_flag(s_v.empty, LV_OBJ_FLAG_HIDDEN);
    }
}

// ---------------------------------------------------------------------------
// Following, and the finger
// ---------------------------------------------------------------------------

void on_scroll(lv_event_t *)
{
    if (s_v.programmatic) {
        return;
    }
    const bool at_bottom = caption_at_bottom(static_cast<int>(lv_obj_get_scroll_bottom(s_v.list)));
    if (s_v.follow.user_scrolled(at_bottom, lv_tick_get())) {
        log_following(at_bottom ? "scrolled to the bottom" : "scrolled back");
        show_jump(!s_v.follow.following());
    }
}

void on_jump(lv_event_t *)
{
    s_v.follow.jump(lv_tick_get());
    scroll_to_live();
    show_jump(false);
    log_following("jump to live");
}

void on_tick(lv_timer_t *)
{
    if (!s_v.built) {
        return;
    }
    const uint32_t now = lv_tick_get();
    const bool touching = finger_down();
    if (touching) {
        s_v.follow.touched(now);
    }
    const uint32_t rev = captions_revision(s_v.slot);
    if (!s_v.synced || rev != s_v.revision) {
        s_v.revision = rev;
        s_v.synced = true;
        captions_read(s_v.slot, read_feed, nullptr);
        refresh_header();
    }
    if (s_v.follow.tick(now)) {
        log_following("no touch for a minute");
    }
    // Following means the newest line is in view, unless a finger is on the list: it is not moved under it.
    if (s_v.follow.following() && !touching) {
        scroll_to_live();
    }
    show_jump(!s_v.follow.following());
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

void build_header(lv_obj_t *root)
{
    lv_obj_t *bar = plain_box(root);
    lv_obj_set_pos(bar, 0, 0);
    lv_obj_set_size(bar, kLcdWidth, kCaptionHeaderH);
    lv_obj_set_style_bg_color(bar, hex(kHeaderHex), 0);
    lv_obj_set_style_bg_opa(bar, LV_OPA_COVER, 0);

    s_v.title = text(bar, &lv_font_montserrat_28, 0xFFFFFF);
    lv_label_set_long_mode(s_v.title, LV_LABEL_LONG_CLIP);
    lv_obj_set_width(s_v.title, kLcdWidth / 2);
    lv_obj_align(s_v.title, LV_ALIGN_LEFT_MID, kCaptionPadX, 0);

    // The indicator: a dot and the connection in words, a row that sizes to what it holds.
    lv_obj_t *status = plain_box(bar);
    lv_obj_set_size(status, LV_SIZE_CONTENT, LV_SIZE_CONTENT);
    lv_obj_set_flex_flow(status, LV_FLEX_FLOW_ROW);
    lv_obj_set_flex_align(status, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_CENTER, LV_FLEX_ALIGN_CENTER);
    lv_obj_set_style_pad_column(status, 12, 0);
    lv_obj_align(status, LV_ALIGN_RIGHT_MID, -kCaptionPadX, 0);

    s_v.dot = plain_box(status);
    lv_obj_set_size(s_v.dot, 16, 16);
    lv_obj_set_style_radius(s_v.dot, LV_RADIUS_CIRCLE, 0);
    lv_obj_set_style_bg_opa(s_v.dot, LV_OPA_COVER, 0);
    s_v.state_label = text(status, &lv_font_montserrat_20, 0xD0D0D0);
}

void build_list(lv_obj_t *root)
{
    s_v.list = plain_box(root);
    lv_obj_set_pos(s_v.list, 0, kCaptionHeaderH);
    lv_obj_set_size(s_v.list, kLcdWidth, kLcdHeight - kCaptionHeaderH);
    lv_obj_add_flag(s_v.list, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(s_v.list, LV_OBJ_FLAG_SCROLL_ELASTIC);
    lv_obj_remove_flag(s_v.list, LV_OBJ_FLAG_SCROLL_CHAIN);
    lv_obj_set_scroll_dir(s_v.list, LV_DIR_VER);
    lv_obj_set_scrollbar_mode(s_v.list, LV_SCROLLBAR_MODE_ACTIVE);
    lv_obj_set_flex_flow(s_v.list, LV_FLEX_FLOW_COLUMN);
    lv_obj_set_style_pad_left(s_v.list, kCaptionPadX, 0);
    lv_obj_set_style_pad_right(s_v.list, kCaptionPadX, 0);
    lv_obj_set_style_pad_top(s_v.list, kCaptionPadY, 0);
    lv_obj_set_style_pad_bottom(s_v.list, kCaptionPadY, 0);
    lv_obj_set_style_pad_row(s_v.list, kCaptionLineGap, 0);
    // `remove_style_all` took the theme's scrollbar with it: a thin bar that shows how far back the list is.
    lv_obj_set_style_bg_color(s_v.list, hex(0x707070), LV_PART_SCROLLBAR);
    lv_obj_set_style_bg_opa(s_v.list, LV_OPA_COVER, LV_PART_SCROLLBAR);
    lv_obj_set_style_width(s_v.list, 6, LV_PART_SCROLLBAR);
    lv_obj_set_style_radius(s_v.list, 3, LV_PART_SCROLLBAR);
    lv_obj_add_event_cb(s_v.list, on_scroll, LV_EVENT_SCROLL, nullptr);

    s_v.empty = text(root, &lv_font_montserrat_28, kDimHex);
    lv_obj_align(s_v.empty, LV_ALIGN_CENTER, 0, kCaptionHeaderH / 2);
}

void build_jump(lv_obj_t *root)
{
    s_v.jump = lv_button_create(root);
    lv_obj_remove_style_all(s_v.jump);
    lv_obj_set_size(s_v.jump, kCaptionJumpW, kCaptionJumpH);
    lv_obj_align(s_v.jump, LV_ALIGN_BOTTOM_MID, 0, -kCaptionJumpMargin);
    lv_obj_set_style_bg_color(s_v.jump, hex(kJumpHex), 0);
    lv_obj_set_style_bg_opa(s_v.jump, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(s_v.jump, kCaptionJumpH / 2, 0);
    lv_obj_set_style_border_color(s_v.jump, hex(0xFFFFFF), 0);
    lv_obj_set_style_border_width(s_v.jump, 2, 0);
    lv_obj_set_style_bg_color(s_v.jump, hex(0x1F52C2), LV_STATE_PRESSED);
    lv_obj_t *label = text(s_v.jump, &lv_font_montserrat_28, 0xFFFFFF);
    lv_label_set_text(label, LV_SYMBOL_DOWN "  Jump to live");
    lv_obj_center(label);
    lv_obj_add_event_cb(s_v.jump, on_jump, LV_EVENT_CLICKED, nullptr);
    lv_obj_add_flag(s_v.jump, LV_OBJ_FLAG_HIDDEN);
}

}  // namespace

void caption_view_build(lv_obj_t *root, int slot)
{
    caption_view_teardown();
    if (!s_style_ready) {
        lv_style_init(&s_line_style);
        lv_style_set_text_font(&s_line_style, &relay_caption_36);
        lv_style_set_text_color(&s_line_style, lv_color_white());
        lv_style_set_pad_all(&s_line_style, 0);
        s_style_ready = true;
    }
    lv_obj_set_style_bg_color(root, hex(kBgHex), 0);
    s_v.slot = slot;
    s_v.shown_count = 0;
    s_v.synced = false;
    s_v.revision = 0;
    s_v.state = caption_state::off;
    s_v.shown_stream = 0xFF;
    s_v.shown_lang[0] = '\0';
    s_v.programmatic = false;
    s_v.follow.reset(lv_tick_get());

    build_list(root);
    build_header(root);
    build_jump(root);
    s_v.built = true;
    s_v.timer = lv_timer_create(on_tick, kTickMs, nullptr);
    on_tick(s_v.timer);  // the first picture is not a tick late
    ESP_LOGI(TAG, "view: caption view up for slot %d", slot);
}

void caption_view_teardown()
{
    if (s_v.timer != nullptr) {
        lv_timer_delete(s_v.timer);
        s_v.timer = nullptr;
    }
    s_v.built = false;
    s_v.list = s_v.title = s_v.dot = s_v.state_label = s_v.empty = s_v.jump = nullptr;
}

void caption_view_report()
{
    screen_lock();
    if (!s_v.built) {
        ESP_LOGI(TAG, "view: not on screen");
    } else {
        ESP_LOGI(TAG, "view: %s, %d lines, %d px below, jump button %s",
                 s_v.follow.following() ? "following live" : "paused", s_v.shown_count, px_below(),
                 lv_obj_has_flag(s_v.jump, LV_OBJ_FLAG_HIDDEN) ? "hidden" : "shown");
    }
    screen_unlock();
}
