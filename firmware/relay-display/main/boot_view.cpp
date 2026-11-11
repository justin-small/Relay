#include "boot_view.h"

LV_IMAGE_DECLARE(relay_boot_bubble);
LV_IMAGE_DECLARE(relay_boot_word);

namespace {

// The lockup is 384 by 112 pixels on every board, centred. These are the places of its parts inside it, and they are
// the numbers `brand/panel_assets.py` cut the two images with.
constexpr int kLockW = 384;
constexpr int kLockH = 112;
constexpr int kStemX = 3;
constexpr int kStemW = 23;
constexpr int kStemTop = 10;
constexpr int kStemBottom = 103;
constexpr int kBubbleX = 35;
constexpr int kBubbleY = 10;
constexpr int kBubbleRise = 14;  // the bubble starts this far down and settles upwards
constexpr int kLineX = 51;
constexpr int kLineH = 10;
constexpr int kLine1Y = 28;
constexpr int kLine1W = 43;
constexpr int kLine2Y = 47;
constexpr int kLine2W = 27;
constexpr int kWordX = 142;
constexpr int kWordW = 242;

// The app's colours: the ground `screen.cpp` paints, off-white, and the green that means live.
constexpr uint32_t kGround = 0x101010;
constexpr uint32_t kInk = 0xF4F4F5;
constexpr uint32_t kLive = 0x4ADE80;

lv_obj_t *plain(lv_obj_t *parent, int x, int y, int w, int h)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_CLICKABLE);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    return o;
}

lv_obj_t *pill(lv_obj_t *parent, int x, int y, int w, int h, uint32_t colour)
{
    lv_obj_t *o = plain(parent, x, y, w, h);
    lv_obj_set_style_bg_color(o, lv_color_hex(colour), 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, LV_RADIUS_CIRCLE, 0);
    return o;
}

// An alpha-only image takes its colour from the recolour.
lv_obj_t *tinted(lv_obj_t *parent, const lv_image_dsc_t *src, int x, int y, uint32_t colour)
{
    lv_obj_t *o = lv_image_create(parent);
    lv_image_set_src(o, src);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_style_image_recolor(o, lv_color_hex(colour), 0);
    lv_obj_set_style_image_recolor_opa(o, LV_OPA_COVER, 0);
    return o;
}

void set_stem(void *o, int32_t h)
{
    lv_obj_set_height(static_cast<lv_obj_t *>(o), h);
    lv_obj_set_y(static_cast<lv_obj_t *>(o), kStemBottom - h);
}

void set_y(void *o, int32_t y) { lv_obj_set_y(static_cast<lv_obj_t *>(o), y); }

void set_image_opa(void *o, int32_t v)
{
    lv_obj_set_style_image_opa(static_cast<lv_obj_t *>(o), static_cast<lv_opa_t>(v), 0);
}

// A width, and the object is shown from its first step: a line or the word is not there until it starts.
void set_width(void *o, int32_t w)
{
    lv_obj_remove_flag(static_cast<lv_obj_t *>(o), LV_OBJ_FLAG_HIDDEN);
    lv_obj_set_width(static_cast<lv_obj_t *>(o), w);
}

lv_anim_t run(lv_obj_t *o, lv_anim_exec_xcb_t set, int from, int to, uint32_t delay_ms, uint32_t ms,
              lv_anim_path_cb_t path)
{
    lv_anim_t a;
    lv_anim_init(&a);
    lv_anim_set_var(&a, o);
    lv_anim_set_exec_cb(&a, set);
    lv_anim_set_values(&a, from, to);
    lv_anim_set_delay(&a, delay_ms);
    lv_anim_set_duration(&a, ms);
    lv_anim_set_path_cb(&a, path);
    return a;
}

}  // namespace

void boot_view_build(lv_obj_t *root)
{
    lv_obj_t *lock = plain(root, 0, 0, kLockW, kLockH);
    lv_obj_center(lock);

    // The upright: a dot that grows upwards from the baseline.
    lv_obj_t *stem = pill(lock, kStemX, kStemBottom - kStemW, kStemW, kStemW, kInk);
    lv_anim_t a = run(stem, set_stem, kStemW, kStemBottom - kStemTop, 0, 460, lv_anim_path_ease_out);
    lv_anim_start(&a);

    // The bubble: it fades in while it settles upwards a little past its place and back.
    lv_obj_t *bubble = tinted(lock, &relay_boot_bubble, kBubbleX, kBubbleY + kBubbleRise, kLive);
    lv_obj_set_style_image_opa(bubble, LV_OPA_TRANSP, 0);
    a = run(bubble, set_image_opa, LV_OPA_TRANSP, LV_OPA_COVER, 370, 300, lv_anim_path_ease_out);
    lv_anim_start(&a);
    a = run(bubble, set_y, kBubbleY + kBubbleRise, kBubbleY, 370, 560, lv_anim_path_overshoot);
    lv_anim_start(&a);

    // The two caption lines: cut out of the bubble in the ground's colour, each written from the left.
    lv_obj_t *line1 = pill(lock, kLineX, kLine1Y, kLineH, kLineH, kGround);
    lv_obj_add_flag(line1, LV_OBJ_FLAG_HIDDEN);
    a = run(line1, set_width, kLineH, kLine1W, 960, 280, lv_anim_path_ease_out);
    lv_anim_start(&a);

    // The second line is written again and again for as long as the picture is up: the panel is working.
    lv_obj_t *line2 = pill(lock, kLineX, kLine2Y, kLineH, kLineH, kGround);
    lv_obj_add_flag(line2, LV_OBJ_FLAG_HIDDEN);
    a = run(line2, set_width, kLineH, kLine2W, 1200, 260, lv_anim_path_ease_out);
    lv_anim_set_playback_delay(&a, 1100);
    lv_anim_set_playback_duration(&a, 220);
    lv_anim_set_repeat_delay(&a, 260);
    lv_anim_set_repeat_count(&a, LV_ANIM_REPEAT_INFINITE);
    lv_anim_start(&a);

    // The word: the whole image stands still in a window that opens from the left, so it is written as it appears.
    lv_obj_t *window = plain(lock, kWordX, 0, 0, kLockH);
    lv_obj_add_flag(window, LV_OBJ_FLAG_HIDDEN);
    tinted(window, &relay_boot_word, 0, 0, kInk);
    a = run(window, set_width, 0, kWordW, 1330, 970, lv_anim_path_ease_in_out);
    lv_anim_start(&a);
}
