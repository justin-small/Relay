#include "display.h"

#include <cstring>

#include "board_panel.h"
#include "config.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_lcd_panel_ops.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "panel_format.h"
#include "sdkconfig.h"

#if CONFIG_RELAY_QEMU
#include "esp_lcd_qemu_rgb.h"
#endif

namespace {

const char *TAG = "display";

// The strip is the unit of drawing (`kStripRows` in `panel_format.h`). Each strip is
// painted into this buffer and handed to the driver, which copies it into the PSRAM
// frame buffer.
uint16_t s_strip[kStripRows * kLcdWidth];

// The one panel this firmware drives: the board's (`board_panel.h`), or the emulator's virtual one.
esp_lcd_panel_handle_t s_panel = nullptr;

// **A panel that cannot mirror has the turn made here**. The emulator's virtual panel is one
// (`esp_lcd_panel_mirror` returns ESP_ERR_NOT_SUPPORTED), and so is a board that says so in its header
// (`kPanelTurnsInSoftware`). Physical pixel (x, y) is given the logical pixel at (W-1-x, H-1-y), which is
// what the RGB driver's mirror does on the CrowPanel, so the picture is the same on all three. On a board
// whose driver mirrors, the constant is false and the compiler drops the turn.
#if CONFIG_RELAY_QEMU
constexpr bool kSoftwareTurn = true;
#else
constexpr bool kSoftwareTurn = kPanelTurnsInSoftware;
#endif
bool s_software_mirror = false;
// The rotation the picture is written at (`panel_format.h`). 90 and 270 are always turned here, on every board:
// no driver turns a picture a quarter.
int s_rotation = 0;

// **Two frame buffers, when the board keeps two** (`board_panel_frame_buffers`). Strips are copied into the
// back buffer, which the panel is not scanning, and `display_present` flips to it, so a redraw is seen whole.
// On a board with one buffer, and on the emulator, strips go straight to the driver as before.
//
// After a flip the old front buffer is the new back one and is stale by the area just presented. It is brought
// up to date from the new front before anything is drawn into it (`settle`), which also waits for the panel
// to finish scanning the old front: the driver switches buffers at the end of the frame being scanned, and
// the next frame-complete event is that end.
uint16_t *s_fb[2] = {nullptr, nullptr};
int s_back = 1;  // the buffer drawn into; the driver starts out scanning buffer 0
bool s_flip_pending = false;
uint32_t s_flip_mark = 0;

struct box {
    int x1 = kLcdWidth, y1 = kLcdHeight, x2 = 0, y2 = 0;  // x2 and y2 are exclusive
    bool empty() const { return x2 <= x1 || y2 <= y1; }
    void add(int ax1, int ay1, int ax2, int ay2)
    {
        if (ax1 < x1) x1 = ax1;
        if (ay1 < y1) y1 = ay1;
        if (ax2 > x2) x2 = ax2;
        if (ay2 > y2) y2 = ay2;
    }
};
box s_dirty;  // drawn into the back buffer since the last flip
box s_stale;  // what the last flip changed, which the new back buffer has yet to receive

void copy_box(uint16_t *to, const uint16_t *from, const box &b)
{
    for (int y = b.y1; y < b.y2; ++y) {
        std::memcpy(to + y * kLcdWidth + b.x1, from + y * kLcdWidth + b.x1,
                    static_cast<size_t>(b.x2 - b.x1) * sizeof(uint16_t));
    }
}

#if !CONFIG_RELAY_QEMU
void settle()
{
    if (!s_flip_pending) {
        return;
    }
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(100);
    while (board_panel_frames_done() - s_flip_mark < 1) {
        if (static_cast<int32_t>(xTaskGetTickCount() - deadline) >= 0) {
            ESP_LOGW(TAG, "panel: no frame ended within 100 ms of a flip; drawing on");
            break;
        }
        vTaskDelay(1);
    }
    if (!s_stale.empty()) {
        copy_box(s_fb[s_back], s_fb[s_back ^ 1], s_stale);
        s_stale = box();
    }
    s_flip_pending = false;
}
#else
void settle() {}
#endif

// Put the strip `[x1, x2) x [y1, y2)` on the panel's own (physical) coordinates.
esp_err_t put(int x1, int y1, int x2, int y2, const uint16_t *pixels)
{
    if (s_fb[0] == nullptr) {
        return esp_lcd_panel_draw_bitmap(s_panel, x1, y1, x2, y2, pixels);
    }
    settle();
    const int w = x2 - x1;
    for (int y = y1; y < y2; ++y) {
        std::memcpy(s_fb[s_back] + y * kLcdWidth + x1, pixels + (y - y1) * w, static_cast<size_t>(w) * sizeof(uint16_t));
    }
    s_dirty.add(x1, y1, x2, y2);
    return ESP_OK;
}

esp_err_t present()
{
    if (s_fb[0] == nullptr || s_dirty.empty()) {
        return ESP_OK;
    }
    // A whole buffer handed to the driver is a flip, not a copy.
    ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(s_panel, 0, 0, kLcdWidth, kLcdHeight, s_fb[s_back]), TAG, "flip");
    s_stale = s_dirty;
    s_dirty = box();
    s_back ^= 1;
    s_flip_pending = true;
#if !CONFIG_RELAY_QEMU
    s_flip_mark = board_panel_frames_done();
#endif
    return ESP_OK;
}

// Paint the whole panel from `pixel(x, y)`, one strip at a time.
template <typename Pixel>
esp_err_t paint_rows(Pixel pixel)
{
    for (int i = 0; i < strip_count(kLcdHeight, kStripRows); ++i) {
        const strip_span span = strip_at(i, kLcdHeight, kStripRows);
        render_strip(pixel, span.y0, span.rows, kLcdWidth, s_strip);
        ESP_RETURN_ON_ERROR(put(0, span.y0, kLcdWidth, span.y0 + span.rows, s_strip), TAG, "draw strip at row %d",
                            span.y0);
    }
    return present();
}

template <typename Pixel>
esp_err_t paint(Pixel pixel)
{
    if (kSoftwareTurn && s_software_mirror) {
        return paint_rows([pixel](int x, int y) { return pixel(kLcdWidth - 1 - x, kLcdHeight - 1 - y); });
    }
    return paint_rows(pixel);
}

#if !CONFIG_RELAY_QEMU
// Wait until the panel has completed `frames` more frames, or give up after
// `timeout_ms`. The comparison is on the signed difference of tick counts, so a
// tick counter that wraps does not end the wait early.
esp_err_t wait_for_frames(uint32_t frames, uint32_t timeout_ms)
{
    const uint32_t start = board_panel_frames_done();
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    while (board_panel_frames_done() - start < frames) {
        if (static_cast<int32_t>(xTaskGetTickCount() - deadline) >= 0) {
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    return ESP_OK;
}

#endif

}  // namespace

esp_err_t display_backlight_init()
{
#if CONFIG_RELAY_QEMU
    // The emulator has no backlight, so there is nothing to switch (`Kconfig.projbuild`).
    return ESP_OK;
#else
    return board_backlight_init();
#endif
}

esp_err_t display_backlight(bool on)
{
#if CONFIG_RELAY_QEMU
    (void)on;
    return ESP_OK;
#else
    return board_backlight(on);
#endif
}

esp_err_t display_init(int rotation_deg)
{
#if CONFIG_RELAY_QEMU
    // **The emulator's panel**. Created with the virtual RGB panel, which takes
    // the same strips as the driver below and keeps its own frame buffer. It has no
    // pixel clock, no timings, no mirror and no frame-complete event, so none of those
    // are configured. See `Kconfig.projbuild`.
    esp_lcd_rgb_qemu_config_t qemu = {};
    qemu.width = kLcdWidth;
    qemu.height = kLcdHeight;
    qemu.bpp = RGB_QEMU_BPP_16;
    ESP_RETURN_ON_ERROR(esp_lcd_new_rgb_qemu(&qemu, &s_panel), TAG, "virtual panel create");
    s_software_mirror = panel_rotation_mirrors(rotation_deg);
    s_rotation = panel_rotation_valid(rotation_deg) ? rotation_deg : 0;
    ESP_LOGI(TAG, "panel: QEMU virtual panel %dx%d RGB565, rotation %d (turned in software)",
             kLcdWidth, kLcdHeight, s_rotation);
    return ESP_OK;
#else
    s_rotation = panel_rotation_valid(rotation_deg) ? rotation_deg : 0;
    // A quarter turn is never the driver's: it is told 0 and the turn is made here.
    ESP_RETURN_ON_ERROR(board_panel_init(panel_rotation_portrait(s_rotation) ? 0 : s_rotation, &s_panel), TAG, "panel");
    s_software_mirror = kSoftwareTurn && panel_rotation_mirrors(rotation_deg);
    void *a = nullptr;
    void *b = nullptr;
    if (board_panel_frame_buffers(&a, &b) == ESP_OK && a != nullptr && b != nullptr) {
        s_fb[0] = static_cast<uint16_t *>(a);
        s_fb[1] = static_cast<uint16_t *>(b);
        s_back = 1;
        ESP_LOGI(TAG, "panel: drawing into the back of two frame buffers");
    }
    return ESP_OK;
#endif
}

void display_resync()
{
#if !CONFIG_RELAY_QEMU
    board_panel_resync();
#endif
}

esp_err_t display_set_pclk(uint32_t hz)
{
#if CONFIG_RELAY_QEMU
    (void)hz;
    return ESP_OK;
#else
    return board_panel_set_pclk(hz);
#endif
}

uint32_t display_frames_done()
{
#if CONFIG_RELAY_QEMU
    return 0;
#else
    return board_panel_frames_done();
#endif
}

esp_err_t display_fill(uint16_t rgb565)
{
    if (s_panel == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    return paint([rgb565](int, int) { return rgb565; });
}

esp_err_t display_show_test_screen(const char *version)
{
    if (s_panel == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    const int version_chars = static_cast<int>(strlen(version));
    ESP_RETURN_ON_ERROR(paint([version, version_chars](int x, int y) {
        return test_screen_pixel(x, y, version, version_chars);
    }), TAG, "test screen");

#if !CONFIG_RELAY_QEMU
    // The backlight waits for two complete frames. One frame can be the one that
    // was already scanning when the screen was painted; two are the whole picture.
    // The virtual panel has no frame event: each draw returns once it has copied
    // the strip, so the picture is whole when the paint returns.
    const esp_err_t waited = wait_for_frames(2, 500);
    if (waited != ESP_OK) {
        ESP_LOGE(TAG, "panel: no frame completed in 500 ms; the backlight stays off");
        return waited;
    }
#endif
    ESP_RETURN_ON_ERROR(display_backlight(true), TAG, "backlight on");
    ESP_LOGI(TAG, "panel: test screen shown, backlight on");
    return ESP_OK;
}

esp_err_t display_write_rect(int x1, int y1, int x2, int y2, const uint16_t *pixels, int stride)
{
    if (s_panel == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    if (x1 < 0 || y1 < 0 || x2 >= display_width() || y2 >= display_height() || x2 < x1 || y2 < y1 ||
        stride < x2 - x1 + 1) {
        return ESP_ERR_INVALID_ARG;
    }
    if (panel_rotation_portrait(s_rotation)) {
        // A quarter turn: the glass rectangle is the picture's turned on its side, each glass strip read pixel by
        // pixel from where the turn puts it (`panel_logical_x`, `panel_logical_y`).
        const int ax = panel_physical_x(x1, y1, s_rotation), bx = panel_physical_x(x2, y2, s_rotation);
        const int ay = panel_physical_y(x1, y1, s_rotation), by = panel_physical_y(x2, y2, s_rotation);
        const int px1 = ax < bx ? ax : bx, px2 = ax < bx ? bx : ax;
        const int py1 = ay < by ? ay : by, py2 = ay < by ? by : ay;
        const int pw = px2 - px1 + 1;
        for (int py0 = py1; py0 <= py2; py0 += kStripRows) {
            const int rows = (py2 - py0 + 1) < kStripRows ? (py2 - py0 + 1) : kStripRows;
            for (int r = 0; r < rows; ++r) {
                for (int c = 0; c < pw; ++c) {
                    const int lx = panel_logical_x(px1 + c, py0 + r, s_rotation);
                    const int ly = panel_logical_y(px1 + c, py0 + r, s_rotation);
                    s_strip[r * pw + c] = pixels[(ly - y1) * stride + (lx - x1)];
                }
            }
            ESP_RETURN_ON_ERROR(put(px1, py0, px1 + pw, py0 + rows, s_strip), TAG, "draw rect at row %d", py0);
        }
        return ESP_OK;
    }
    const int w = x2 - x1 + 1;
    if (kSoftwareTurn && s_software_mirror) {
        // The physical rectangle is the logical one turned over both axes, and each physical strip is read
        // from the logical pixel the turn puts there (see `paint`). Columns run the other way too.
        const int px1 = kLcdWidth - 1 - x2;
        const int py1 = kLcdHeight - 1 - y2;
        const int py2 = kLcdHeight - 1 - y1;
        for (int py0 = py1; py0 <= py2; py0 += kStripRows) {
            const int rows = (py2 - py0 + 1) < kStripRows ? (py2 - py0 + 1) : kStripRows;
            for (int r = 0; r < rows; ++r) {
                const int ly = kLcdHeight - 1 - (py0 + r);
                for (int c = 0; c < w; ++c) {
                    const int lx = kLcdWidth - 1 - (px1 + c);
                    s_strip[r * w + c] = pixels[(ly - y1) * stride + (lx - x1)];
                }
            }
            ESP_RETURN_ON_ERROR(put(px1, py0, px1 + w, py0 + rows, s_strip), TAG, "draw rect at row %d", py0);
        }
        return ESP_OK;
    }
    for (int y0 = 0; y0 < y2 - y1 + 1; y0 += kStripRows) {
        const int rows = (y2 - y1 + 1 - y0) < kStripRows ? (y2 - y1 + 1 - y0) : kStripRows;
        for (int r = 0; r < rows; ++r) {
            std::memcpy(s_strip + r * w, pixels + (y0 + r) * stride, static_cast<size_t>(w) * sizeof(uint16_t));
        }
        ESP_RETURN_ON_ERROR(put(x1, y1 + y0, x2 + 1, y1 + y0 + rows, s_strip), TAG, "draw rect at row %d", y1 + y0);
    }
    return ESP_OK;
}

int display_width()
{
    return panel_logical_width(s_rotation);
}

int display_height()
{
    return panel_logical_height(s_rotation);
}

int display_rotation()
{
    return s_rotation;
}

esp_err_t display_present()
{
    return present();
}
