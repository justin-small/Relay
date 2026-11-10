#include "board_panel.h"

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
#include "panel_format.h"
#include "sdkconfig.h"

// The CrowPanel Advance 7.0's panel and backlight (`board_panel.h`): the ESP32-S3's RGB interface with its
// frame buffer in PSRAM, and an STC8 on the I2C bus that switches the light. Pins and timing are in
// `boards/crowpanel_adv7.h`.

namespace {

const char *TAG = "display";

esp_lcd_panel_handle_t s_panel = nullptr;
volatile uint32_t s_frames_done = 0;

// **Runs in the interrupt**, with the cache possibly off, so it sits in IRAM
// (`CONFIG_LCD_RGB_ISR_IRAM_SAFE`). A frame-complete event is one increment.
bool IRAM_ATTR on_frame_complete(esp_lcd_panel_handle_t, const esp_lcd_rgb_panel_event_data_t *, void *)
{
    // An assignment, not `++`: C++20 deprecates the increment of a volatile.
    s_frames_done = s_frames_done + 1;
    return false;
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

}  // namespace

esp_err_t board_backlight_init()
{
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
}

esp_err_t board_backlight(bool on)
{
    const esp_err_t err = stc8_send(on ? kBacklight.on : kBacklight.off);
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "backlight: the STC8 did not take the %s command (%s)", on ? "on" : "off",
                 esp_err_to_name(err));
    }
    return err;
}

esp_err_t board_panel_init(int rotation_deg, esp_lcd_panel_handle_t *out)
{
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
    // **Two frame buffers, in PSRAM** (768 KB each). With one, the panel read the buffer while a redraw was
    // written into it, and a scrolling list showed old and new rows in the same frame (the caption list
    // "jumped"). `display.cpp` now draws into the buffer that is not being scanned and flips.
    cfg.num_fbs = 2;
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

    // **No hardware mirror.** The driver's mirror applies only to a bitmap it copies in, and a flip hands it
    // one of its own buffers, so `display.cpp` turns the picture itself (`kPanelTurnsInSoftware`).
    const bool flip = panel_rotation_mirrors(rotation_deg);

    ESP_LOGI(TAG, "panel: %dx%d RGB565, pclk %d Hz, two frame buffers, rotation %d (turned in software)",
             kLcdWidth, kLcdHeight, static_cast<int>(CONFIG_RELAY_PCLK_HZ), flip ? 180 : 0);
    *out = s_panel;
    return ESP_OK;
}

esp_err_t board_panel_frame_buffers(void **a, void **b)
{
    if (s_panel == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_lcd_rgb_panel_get_frame_buffer(s_panel, 2, a, b);
}

uint32_t board_panel_frames_done()
{
    return s_frames_done;
}

namespace {
esp_timer_handle_t s_resync_timer = nullptr;

void resync_now(void *)
{
    if (s_panel != nullptr) {
        esp_lcd_rgb_panel_restart(s_panel);
    }
}
}  // namespace

// **A restart after a write, not one every frame** (2026-10-28). `CONFIG_LCD_RGB_RESTART_IN_VSYNC` restarts the
// DMA in every blank, which put the drift right and made the panel flicker as soon as a phone joined: the Wi-Fi
// interrupt delays the VSYNC interrupt, and a restart that comes late re-sends the start of a frame, so a busy
// radio showed a shifted frame many times a second. A restart only after a flash write costs at most one shifted
// frame per write, and the writes are rare.
void board_panel_resync()
{
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
}

esp_err_t board_panel_set_pclk(uint32_t hz)
{
    if (s_panel == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    return esp_lcd_rgb_panel_set_pclk(s_panel, hz);
}
