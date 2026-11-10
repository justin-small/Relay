#include "screen.h"

#include "caption_view.h"

#include <cstdio>
#include <cstring>

#include "config.h"
#include "diag.h"
#include "display.h"
#include "esp_heap_caps.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"
#include "lvgl.h"
#include "sdkconfig.h"

namespace {

const char *TAG = "screen";

constexpr int kMarginX = 32;
// The gap between two lines of a stack, the same as `kPanelTextGap` in `panel_format.h`.
constexpr int kLineGap = 18;
// The draw buffer is this many rows of the panel. Sixteen rows is 25.6 KB and a full screen is thirty flushes, which
// the board's PSRAM does not notice and the emulator, which has no PSRAM, has to fit in internal RAM.
constexpr int kDrawRows = 16;
constexpr uint32_t kTaskStackBytes = 8192;

// The QR code on the left of the setup screen: a white field and the code inside it.
constexpr int kQrBox = 264;
constexpr int kQrPx = 232;
constexpr int kQrColumnGap = 40;

// Colours: a near-black ground, white text, and a dimmer tone for the small lines.
constexpr uint8_t kBg = 0x10;

SemaphoreHandle_t s_lock = nullptr;
lv_display_t *s_disp = nullptr;
uint16_t *s_draw_buf = nullptr;
bool s_active = false;  // LVGL flushes to the panel only while this is set

enum class Request : uint8_t { none, lines, captions, bench };
Request s_request = Request::none;
// The lines a request names, copied by the caller: a caller's own buffers are gone by the time the task draws.
constexpr int kLineChars = 64;
char s_line_text[kPanelTextMaxLines][kLineChars];
int s_line_scale[kPanelTextMaxLines];
int s_line_count = 0;
// Sized for the setup network's join code: at most about 90 characters with every character of a 32-byte name escaped.
char s_line_qr[160] = "";
char s_bench_version[16] = "";

// PSRAM when the module has it, and internal RAM when it does not. The emulator's module has no PSRAM, and the
// screen must still start there.
void *alloc_buffer(size_t bytes)
{
    void *p = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM);
    if (p == nullptr) {
        p = heap_caps_malloc(bytes, MALLOC_CAP_8BIT);
    }
    return p;
}

uint32_t tick_ms()
{
    return static_cast<uint32_t>(esp_timer_get_time() / 1000);
}

// Everything the screen draws goes through here. A partial area, in the panel's logical coordinates.
void flush(lv_display_t *disp, const lv_area_t *area, uint8_t *px_map)
{
#if CONFIG_RELAY_DEV_DIAG
    const int64_t flush_start = esp_timer_get_time();
#endif
    if (s_active) {
        // **The stride is the area's width, not the panel's.** LVGL lays out each partial area as its own rows,
        // `area width` pixels a row. A panel-wide stride is right only for an area the full width of the panel, and
        // a narrow one smeared the rows beside it on the glass.
        const int w = area->x2 - area->x1 + 1;
        display_write_rect(area->x1, area->y1, area->x2, area->y2, reinterpret_cast<const uint16_t *>(px_map), w);
        // Shown together by `lvgl_task`, after the pass.
    }
#if CONFIG_RELAY_DEV_DIAG
    diag_note_flush(esp_timer_get_time() - flush_start);
#endif
    lv_display_flush_ready(disp);
}

// The face for a line's scale: the scales the provisioning screens ask for (3 small, 4 medium, 5 and up large).
const lv_font_t *font_for_scale(int scale)
{
    if (scale <= 3) {
        return &lv_font_montserrat_20;
    }
    if (scale == 4) {
        return &lv_font_montserrat_28;
    }
    return &lv_font_montserrat_40;
}

int line_height(int scale)
{
    return lv_font_get_line_height(font_for_scale(scale));
}

lv_obj_t *label(lv_obj_t *parent, int x, int y, int w, const char *text, int scale, lv_text_align_t align)
{
    lv_obj_t *l = lv_label_create(parent);
    lv_label_set_long_mode(l, LV_LABEL_LONG_CLIP);
    lv_obj_set_pos(l, x, y);
    lv_obj_set_width(l, w);
    lv_obj_set_style_pad_all(l, 0, 0);
    lv_obj_set_style_text_font(l, font_for_scale(scale), 0);
    lv_obj_set_style_text_color(l, lv_color_white(), 0);
    lv_obj_set_style_text_align(l, align, 0);
    lv_label_set_text(l, text);
    return l;
}

lv_obj_t *box(lv_obj_t *parent, int x, int y, int w, int h, lv_color_t fill, int radius)
{
    lv_obj_t *o = lv_obj_create(parent);
    lv_obj_remove_style_all(o);
    lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_set_pos(o, x, y);
    lv_obj_set_size(o, w, h);
    lv_obj_set_style_bg_color(o, fill, 0);
    lv_obj_set_style_bg_opa(o, LV_OPA_COVER, 0);
    lv_obj_set_style_radius(o, radius, 0);
    return o;
}

lv_obj_t *fresh_root()
{
    lv_obj_t *root = lv_screen_active();
    lv_obj_clean(root);
    lv_obj_set_style_bg_color(root, lv_color_make(kBg, kBg, kBg), 0);
    lv_obj_set_style_bg_opa(root, LV_OPA_COVER, 0);
    return root;
}

// The QR code on the left, and its caption under it. False when the text does not fit a QR code, and then nothing is
// left on the screen.
bool draw_qr(lv_obj_t *root, const char *payload)
{
    const int caption_h = line_height(3);
    const int y = (kLcdHeight - kQrBox - kLineGap - caption_h) / 2;
    lv_obj_t *field = box(root, kMarginX, y, kQrBox, kQrBox, lv_color_white(), 8);
    lv_obj_t *qr = lv_qrcode_create(field);
    lv_qrcode_set_size(qr, kQrPx);
    lv_qrcode_set_dark_color(qr, lv_color_black());
    lv_qrcode_set_light_color(qr, lv_color_white());
    if (lv_qrcode_update(qr, payload, std::strlen(payload)) != LV_RESULT_OK) {
        ESP_LOGW(TAG, "qr: the join code did not fit a QR code; the screen shows the lines alone");
        lv_obj_delete(field);
        return false;
    }
    lv_obj_center(qr);
    label(root, kMarginX, y + kQrBox + kLineGap, kQrBox, "Scan to join", 3, LV_TEXT_ALIGN_CENTER);
    return true;
}

// A stack of lines, centred vertically in the column [x, x + w).
void draw_stack(lv_obj_t *root, int x, int w)
{
    int total = 0;
    for (int i = 0; i < s_line_count; ++i) {
        total += line_height(s_line_scale[i]);
    }
    total += kLineGap * (s_line_count - 1);
    int y = (kLcdHeight - total) / 2;
    for (int i = 0; i < s_line_count; ++i) {
        label(root, x, y, w, s_line_text[i], s_line_scale[i], LV_TEXT_ALIGN_CENTER);
        y += line_height(s_line_scale[i]) + kLineGap;
    }
}

// Make LVGL repaint the whole panel. Needed once after the bench picture, which was written raw.
void activate()
{
    if (!s_active) {
        s_active = true;
        lv_obj_invalidate(lv_screen_active());
    }
}

void draw_lines_now()
{
    caption_view_teardown();
    lv_obj_t *root = fresh_root();
    if (s_line_qr[0] != '\0' && draw_qr(root, s_line_qr)) {
        const int x = kMarginX + kQrBox + kQrColumnGap;
        draw_stack(root, x, kLcdWidth - kMarginX - x);
    } else {
        draw_stack(root, kMarginX, kLcdWidth - 2 * kMarginX);
    }
    activate();
}

// The task that draws. Every picture is built here, on this task's stack: the callers (the boot, the setup network's
// handlers) run on stacks of a few kilobytes. They ask for a picture with a request, and this task draws what was
// asked for, so a burst of requests draws the last one.
void lvgl_task(void *)
{
    for (;;) {
        screen_lock();
        switch (s_request) {
        case Request::lines:
            draw_lines_now();
            break;
        case Request::captions:
            caption_view_teardown();
            caption_view_build(fresh_root());
            activate();
            break;
        case Request::bench:
            // The bench picture is written raw, and LVGL stops flushing until something is drawn again.
            caption_view_teardown();
            s_active = false;
            display_show_test_screen(s_bench_version);
            break;
        case Request::none:
            break;
        }
        s_request = Request::none;
        const uint32_t next = lv_timer_handler();
        // **Everything LVGL flushed in this pass is shown now, once.** Not on the flush LVGL calls the last: a
        // redraw that never got that flag was written to the back buffer and never shown (a removed pane stayed
        // up and a new feed stayed blank). A pass with nothing written is a no-op.
        display_present();
        screen_unlock();
        const uint32_t wait = next < 5 ? 5 : (next > 33 ? 33 : next);
        vTaskDelay(pdMS_TO_TICKS(wait));
    }
}

}  // namespace

esp_err_t screen_start()
{
    s_lock = xSemaphoreCreateRecursiveMutex();
    if (s_lock == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    lv_init();
    lv_tick_set_cb(tick_ms);
    s_disp = lv_display_create(kLcdWidth, kLcdHeight);
    if (s_disp == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    // Partial rendering into a buffer of a few rows. Each flush is the area that changed, so a label's text redraws its
    // own rectangle and not the panel.
    const size_t bytes = static_cast<size_t>(kLcdWidth) * kDrawRows * sizeof(uint16_t);
    s_draw_buf = static_cast<uint16_t *>(alloc_buffer(bytes));
    if (s_draw_buf == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    lv_display_set_buffers(s_disp, s_draw_buf, nullptr, static_cast<uint32_t>(bytes), LV_DISPLAY_RENDER_MODE_PARTIAL);
    lv_display_set_flush_cb(s_disp, flush);
    lv_obj_set_style_bg_color(lv_screen_active(), lv_color_make(kBg, kBg, kBg), 0);

    if (xTaskCreate(lvgl_task, "lvgl", kTaskStackBytes, nullptr, 3, nullptr) != pdPASS) {
        return ESP_ERR_NO_MEM;
    }
    ESP_LOGI(TAG, "lvgl: up; internal RAM free %u bytes (largest block %u), PSRAM free %u bytes",
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_8BIT)),
             static_cast<unsigned>(heap_caps_get_largest_free_block(MALLOC_CAP_8BIT)),
             static_cast<unsigned>(heap_caps_get_free_size(MALLOC_CAP_SPIRAM)));
    return ESP_OK;
}

void screen_lock()
{
    if (s_lock != nullptr) {
        xSemaphoreTakeRecursive(s_lock, portMAX_DELAY);
    }
}

void screen_unlock()
{
    if (s_lock != nullptr) {
        xSemaphoreGiveRecursive(s_lock);
    }
}

esp_err_t screen_show_lines(const panel_text_line *lines, int count, const char *qr)
{
    if (lines == nullptr || count <= 0) {
        return ESP_ERR_INVALID_ARG;
    }
    screen_lock();
    s_line_count = 0;
    for (int i = 0; i < count && i < kPanelTextMaxLines; ++i) {
        std::snprintf(s_line_text[s_line_count], kLineChars, "%s", lines[i].text);
        s_line_scale[s_line_count] = lines[i].scale;
        ++s_line_count;
    }
    std::snprintf(s_line_qr, sizeof s_line_qr, "%s", qr != nullptr ? qr : "");
    s_request = Request::lines;
    screen_unlock();
    return ESP_OK;
}

esp_err_t screen_show_captions()
{
    screen_lock();
    s_request = Request::captions;
    screen_unlock();
    return ESP_OK;
}

esp_err_t screen_show_bench(const char *version)
{
    screen_lock();
    std::snprintf(s_bench_version, sizeof s_bench_version, "%s", version != nullptr ? version : "");
    s_request = Request::bench;
    screen_unlock();
    return ESP_OK;
}
