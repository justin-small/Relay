#include "display.h"

#include <cstring>

#include "board_format.h"
#include "board_i2c.h"
#include "config.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_log.h"
#include "esp_timer.h"
#include "esp_lcd_panel_ops.h"
#include "esp_lcd_panel_rgb.h"
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

// The one panel this firmware drives, and the frame-completion count the backlight
// waits on.
esp_lcd_panel_handle_t s_panel = nullptr;
volatile uint32_t s_frames_done = 0;

#if CONFIG_RELAY_QEMU
// **The emulated panel cannot mirror, so the turn is made here**. The virtual
// panel's `esp_lcd_panel_mirror` returns ESP_ERR_NOT_SUPPORTED. Physical pixel (x, y)
// is given the logical pixel at (W-1-x, H-1-y), which is what the driver's mirror does
// on a real panel, so the picture on the emulator is the picture on the board.
bool s_software_mirror = false;
#endif

// Paint the whole panel from `pixel(x, y)`, one strip at a time.
template <typename Pixel>
esp_err_t paint_rows(Pixel pixel)
{
    for (int i = 0; i < strip_count(kLcdHeight, kStripRows); ++i) {
        const strip_span span = strip_at(i, kLcdHeight, kStripRows);
        render_strip(pixel, span.y0, span.rows, kLcdWidth, s_strip);
        ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(s_panel, 0, span.y0, kLcdWidth, span.y0 + span.rows, s_strip),
                            TAG, "draw strip at row %d", span.y0);
    }
    return ESP_OK;
}

template <typename Pixel>
esp_err_t paint(Pixel pixel)
{
#if CONFIG_RELAY_QEMU
    if (s_software_mirror) {
        return paint_rows([pixel](int x, int y) { return pixel(kLcdWidth - 1 - x, kLcdHeight - 1 - y); });
    }
#endif
    return paint_rows(pixel);
}

#if !CONFIG_RELAY_QEMU
// **Runs in the interrupt**, with the cache possibly off, so it sits in IRAM
// (`CONFIG_LCD_RGB_ISR_IRAM_SAFE`). A frame-complete event is one increment.
bool IRAM_ATTR on_frame_complete(esp_lcd_panel_handle_t, const esp_lcd_rgb_panel_event_data_t *, void *)
{
    // An assignment, not `++`: C++20 deprecates the increment of a volatile.
    s_frames_done = s_frames_done + 1;
    return false;
}

// Wait until the panel has completed `frames` more frames, or give up after
// `timeout_ms`. The comparison is on the signed difference of tick counts, so a
// tick counter that wraps does not end the wait early.
esp_err_t wait_for_frames(uint32_t frames, uint32_t timeout_ms)
{
    const uint32_t start = s_frames_done;
    const TickType_t deadline = xTaskGetTickCount() + pdMS_TO_TICKS(timeout_ms);
    while (s_frames_done - start < frames) {
        if (static_cast<int32_t>(xTaskGetTickCount() - deadline) >= 0) {
            return ESP_ERR_TIMEOUT;
        }
        vTaskDelay(pdMS_TO_TICKS(2));
    }
    return ESP_OK;
}

// **The backlight is an STC8H1K28 on the board's I2C bus, not a GPIO**. The
// pair of bytes is the one for this board's revision, from `config.h`.
#if CONFIG_RELAY_BOARD_ADV_V12
constexpr stc8_backlight_commands kBacklight = stc8_backlight_for(true);
#else
constexpr stc8_backlight_commands kBacklight = stc8_backlight_for(false);
#endif

// The STC8's device handle, once `display_backlight_init` has added it to the bus. Null
// until then, and stays null on a board whose STC8 is not there.
i2c_master_dev_handle_t s_stc8 = nullptr;

// **One command is one byte written to 0x30, with nothing before it.** The STC8 has no
// register map, so there is no address byte to send first (`config.h`).
esp_err_t stc8_send(uint8_t command)
{
    if (s_stc8 == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    // A 100 ms timeout is far longer than one byte takes at 400 kHz. It bounds a bus
    // that is stuck low, so the boot cannot wait on it forever.
    return i2c_master_transmit(s_stc8, &command, 1, 100);
}
#endif

}  // namespace

esp_err_t display_backlight_init()
{
#if CONFIG_RELAY_QEMU
    // The emulator has no STC8, so there is nothing to switch (`Kconfig.projbuild`).
    return ESP_OK;
#else
    // **A missing STC8 is logged and does not stop the boot.** `app_main` checks this
    // return with ESP_ERROR_CHECK, which would reset the board into the same failure on
    // every boot. The panel can still draw without a backlight, and the log says why the
    // screen is dark. So every failure here is logged and the function returns ESP_OK.
    if (board_i2c_init() == nullptr) {
        ESP_LOGE(TAG, "backlight: no I2C bus, so the STC8 cannot be reached; the screen stays dark");
        return ESP_OK;
    }
    if (board_i2c_add_device(kStc8Addr, &s_stc8) != ESP_OK) {
        ESP_LOGE(TAG, "backlight: could not add the STC8 at 0x%02x to the bus; the screen stays dark", kStc8Addr);
        return ESP_OK;
    }
    // **The off command goes first.** The panel is meant to be dark for the rest of the
    // boot. Whether the STC8 keeps its last command across a
    // reset of this chip is not confirmed; the bench check is a flash of light during reset.
    const esp_err_t off = stc8_send(kBacklight.off);
    if (off != ESP_OK) {
        ESP_LOGE(TAG, "backlight: the STC8 at 0x%02x did not answer (%s); the screen stays dark",
                 kStc8Addr, esp_err_to_name(off));
    }
    return ESP_OK;
#endif
}

esp_err_t display_backlight(bool on)
{
#if CONFIG_RELAY_QEMU
    (void)on;
    return ESP_OK;
#else
    const esp_err_t err = stc8_send(on ? kBacklight.on : kBacklight.off);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "backlight: the STC8 did not take the %s command (%s)", on ? "on" : "off",
                 esp_err_to_name(err));
    }
    return err;
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
    ESP_LOGI(TAG, "panel: QEMU virtual panel %dx%d RGB565, rotation %d (turned in software)",
             kLcdWidth, kLcdHeight, s_software_mirror ? 180 : 0);
    return ESP_OK;
#else
    esp_lcd_rgb_panel_config_t cfg = {};
    cfg.clk_src = LCD_CLK_SRC_DEFAULT;
    cfg.timings.pclk_hz = CONFIG_RELAY_PCLK_HZ;
    cfg.timings.h_res = kLcdWidth;
    cfg.timings.v_res = kLcdHeight;
    cfg.timings.hsync_pulse_width = kHsyncPulse;
    cfg.timings.hsync_back_porch = kHsyncBack;
    cfg.timings.hsync_front_porch = kHsyncFront;
    cfg.timings.vsync_pulse_width = kVsyncPulse;
    cfg.timings.vsync_back_porch = kVsyncBack;
    cfg.timings.vsync_front_porch = kVsyncFront;
    // **The sync polarities follow the ESP-IDF example for this panel at 16 MHz**, which
    // sets the data to be clocked on the falling edge of PCLK and leaves the sync lines at
    // their zero flags, idle high. Elecrow's Arduino examples set the same panel's polarity
    // through LovyanGFX, and that library stores the opposite idle level, so the two families
    // disagree. This firmware drives esp_lcd directly, as the ESP-IDF example does, so it
    // takes that example's flags. A picture that is shifted or fails to lock is the first thing
    // to try with the two sync flags set to 1. PCLK idles low here, as in that example; the
    // PlatformIO example sets it high, which is the other bench item.
    cfg.timings.flags.pclk_active_neg = 1;
    cfg.timings.flags.hsync_idle_low = 0;
    cfg.timings.flags.vsync_idle_low = 0;
    cfg.timings.flags.de_idle_high = 0;
    cfg.timings.flags.pclk_idle_high = 0;

    cfg.data_width = 16;
    cfg.bits_per_pixel = 16;
    // **One frame buffer, in PSRAM.** The cost of one buffer is tearing on a redraw, since the panel
    // reads the buffer while it is written. A second buffer fixes that for 768 KB more PSRAM, and it
    // is the change to make if the bench shows tearing on a live screen.
    cfg.num_fbs = 1;
    // A bounce buffer in internal RAM that the driver fills from the PSRAM frame buffer, ten lines as
    // shipped. With a frame buffer present the driver does the
    // copy itself, so strips are still painted as before.
    cfg.bounce_buffer_size_px = CONFIG_RELAY_BOUNCE_LINES * kLcdWidth;
    cfg.flags.fb_in_psram = 1;

    cfg.hsync_gpio_num = kPinHsync;
    cfg.vsync_gpio_num = kPinVsync;
    cfg.de_gpio_num = kPinDe;
    cfg.pclk_gpio_num = kPinPclk;
    cfg.disp_gpio_num = -1;  // no display-enable line; the backlight is separate
    for (int i = 0; i < 16; ++i) {
        cfg.data_gpio_nums[i] = kPinData[i];
    }

    ESP_RETURN_ON_ERROR(esp_lcd_new_rgb_panel(&cfg, &s_panel), TAG, "panel create");

    esp_lcd_rgb_panel_event_callbacks_t cbs = {};
    cbs.on_frame_buf_complete = on_frame_complete;
    ESP_RETURN_ON_ERROR(esp_lcd_rgb_panel_register_event_callbacks(s_panel, &cbs, nullptr),
                        TAG, "frame callback");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG, "panel init");

    // Mirroring is set after init and before anything is drawn, so no frame is
    // ever painted unmirrored and then flipped. For 0 degrees both axes stay clear.
    const bool flip = panel_rotation_mirrors(rotation_deg);
    ESP_RETURN_ON_ERROR(esp_lcd_panel_mirror(s_panel, flip, flip), TAG, "mirror");

    ESP_LOGI(TAG, "panel: %dx%d RGB565, pclk %d Hz, rotation %d",
             kLcdWidth, kLcdHeight, static_cast<int>(CONFIG_RELAY_PCLK_HZ), flip ? 180 : 0);
    return ESP_OK;
#endif
}

#if !CONFIG_RELAY_QEMU
namespace {
esp_timer_handle_t s_resync_timer = nullptr;

void resync_now(void *)
{
    if (s_panel != nullptr) {
        esp_lcd_rgb_panel_restart(s_panel);
    }
}
}  // namespace
#endif

// **A restart after a write, not one every frame** (2026-10-28). `CONFIG_LCD_RGB_RESTART_IN_VSYNC` restarts the
// DMA in every blank, which put the drift right and made the panel flicker as soon as a phone joined: the Wi-Fi
// interrupt delays the VSYNC interrupt, and a restart that comes late re-sends the start of a frame, so a busy
// radio showed a shifted frame many times a second. A restart only after a flash write costs at most one shifted
// frame per write, and the writes are rare.
void display_resync()
{
#if !CONFIG_RELAY_QEMU
    if (s_panel == nullptr) {
        return;
    }
    resync_now(nullptr);
    if (s_resync_timer == nullptr) {
        const esp_timer_create_args_t args = {.callback = resync_now, .arg = nullptr,
                                              .dispatch_method = ESP_TIMER_TASK, .name = "panel_resync",
                                              .skip_unhandled_events = true};
        if (esp_timer_create(&args, &s_resync_timer) != ESP_OK) {
            return;
        }
    }
    esp_timer_stop(s_resync_timer);
    esp_timer_start_once(s_resync_timer, 500 * 1000);
#endif
}

esp_err_t display_set_pclk(uint32_t hz)
{
#if CONFIG_RELAY_QEMU
    (void)hz;
    return ESP_OK;
#else
    if (s_panel == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_lcd_rgb_panel_set_pclk(s_panel, hz);
#endif
}

uint32_t display_frames_done()
{
    return s_frames_done;
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
    if (x1 < 0 || y1 < 0 || x2 >= kLcdWidth || y2 >= kLcdHeight || x2 < x1 || y2 < y1 || stride < x2 - x1 + 1) {
        return ESP_ERR_INVALID_ARG;
    }
    const int w = x2 - x1 + 1;
#if CONFIG_RELAY_QEMU
    if (s_software_mirror) {
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
            ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(s_panel, px1, py0, px1 + w, py0 + rows, s_strip),
                                TAG, "draw rect at row %d", py0);
        }
        return ESP_OK;
    }
#endif
    for (int y0 = 0; y0 < y2 - y1 + 1; y0 += kStripRows) {
        const int rows = (y2 - y1 + 1 - y0) < kStripRows ? (y2 - y1 + 1 - y0) : kStripRows;
        for (int r = 0; r < rows; ++r) {
            std::memcpy(s_strip + r * w, pixels + (y0 + r) * stride, static_cast<size_t>(w) * sizeof(uint16_t));
        }
        ESP_RETURN_ON_ERROR(esp_lcd_panel_draw_bitmap(s_panel, x1, y1 + y0, x2 + 1, y1 + y0 + rows, s_strip),
                            TAG, "draw rect at row %d", y1 + y0);
    }
    return ESP_OK;
}
