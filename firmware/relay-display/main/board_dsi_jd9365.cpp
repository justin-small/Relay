#include "board_panel.h"

#include "config.h"
#include "driver/ledc.h"
#include "esp_attr.h"
#include "esp_check.h"
#include "esp_err.h"
#include "esp_lcd_jd9365.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_lcd_panel_ops.h"
#include "esp_ldo_regulator.h"
#include "esp_log.h"
#include "panel_format.h"
#include "sdkconfig.h"

// The Waveshare ESP32-P4 10.1" board's panel and backlight (`board_panel.h`): a JD9365 on two-lane MIPI-DSI,
// with its frame buffer in PSRAM, and a PWM pin for the light. The values are in `boards/waveshare_p4_10_1.h`.
//
// **Written from Waveshare's board support package and not yet run on a board.** The order of the calls is
// theirs (`bsp_display_new_with_handles`): power the DSI pads, make the bus, make the command channel, make the
// panel with the vendor's start-up table, reset, init, display on. What the bench has to confirm is in README.md.

// The JD9365's start-up table for this panel (`boards/waveshare_p4_10_1_init.c`, which says why it is C).
extern "C" {
extern const jd9365_lcd_init_cmd_t relay_jd9365_init[];
extern const uint16_t relay_jd9365_init_count;
}

namespace {

const char *TAG = "display";

esp_lcd_panel_handle_t s_panel = nullptr;
volatile uint32_t s_frames_done = 0;

// The backlight's PWM: its own timer and channel, which nothing else in this firmware uses.
constexpr ledc_mode_t kLedcMode = LEDC_LOW_SPEED_MODE;
constexpr ledc_timer_t kLedcTimer = LEDC_TIMER_1;
constexpr ledc_channel_t kLedcChannel = LEDC_CHANNEL_1;
bool s_backlight_ready = false;

// **Runs in the interrupt.** One frame scanned out of the frame buffer is one increment, which is what
// `display.cpp` waits on before it lights the panel.
bool IRAM_ATTR on_refresh_done(esp_lcd_panel_handle_t, esp_lcd_dpi_panel_event_data_t *, void *)
{
    // An assignment, not `++`: C++20 deprecates the increment of a volatile.
    s_frames_done = s_frames_done + 1;
    return false;
}

}  // namespace

esp_err_t board_backlight_init()
{
    // **A backlight that cannot be set up is logged and does not stop the boot**, for the reason the
    // CrowPanel's is not: `app_main` checks this return, and a reset would only reach the same failure.
    ledc_timer_config_t timer = {};
    timer.speed_mode = kLedcMode;
    timer.duty_resolution = LEDC_TIMER_10_BIT;
    timer.timer_num = kLedcTimer;
    timer.freq_hz = kBacklightPwmHz;
    timer.clk_cfg = LEDC_AUTO_CLK;
    esp_err_t err = ledc_timer_config(&timer);
    if (err == ESP_OK) {
        // **Duty 0 from the first edge**, so the pin is driven low as it becomes an output and the panel is
        // dark for the rest of the boot.
        ledc_channel_config_t channel = {};
        channel.gpio_num = kPinBacklight;
        channel.speed_mode = kLedcMode;
        channel.channel = kLedcChannel;
        channel.intr_type = LEDC_INTR_DISABLE;
        channel.timer_sel = kLedcTimer;
        channel.duty = 0;
        channel.hpoint = 0;
        err = ledc_channel_config(&channel);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "backlight: the PWM on GPIO%d could not be set up (%s); the screen stays dark", kPinBacklight,
                 esp_err_to_name(err));
        return ESP_OK;
    }
    s_backlight_ready = true;
    return ESP_OK;
}

esp_err_t board_backlight(bool on)
{
    if (!s_backlight_ready) {
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = ledc_set_duty(kLedcMode, kLedcChannel, on ? kBacklightDutyMax : 0);
    if (err == ESP_OK) {
        err = ledc_update_duty(kLedcMode, kLedcChannel);
    }
    if (err != ESP_OK) {
        ESP_LOGE(TAG, "backlight: the PWM did not take %s (%s)", on ? "on" : "off", esp_err_to_name(err));
    }
    return err;
}

esp_err_t board_panel_init(int rotation_deg, esp_lcd_panel_handle_t *out)
{
    // The DSI pads' supply first: until it is on, the bus cannot leave "no power".
    static esp_ldo_channel_handle_t phy_power = nullptr;
    esp_ldo_channel_config_t ldo = {};
    ldo.chan_id = kDsiPhyLdoChannel;
    ldo.voltage_mv = kDsiPhyLdoMillivolts;
    ESP_RETURN_ON_ERROR(esp_ldo_acquire_channel(&ldo, &phy_power), TAG, "DSI pad power");

    esp_lcd_dsi_bus_handle_t bus = nullptr;
    esp_lcd_dsi_bus_config_t bus_cfg = {};
    bus_cfg.bus_id = 0;
    bus_cfg.num_data_lanes = kDsiLanes;
    bus_cfg.phy_clk_src = MIPI_DSI_PHY_CLK_SRC_DEFAULT;
    bus_cfg.lane_bit_rate_mbps = kDsiLaneMbps;
    ESP_RETURN_ON_ERROR(esp_lcd_new_dsi_bus(&bus_cfg, &bus), TAG, "DSI bus");

    // The channel the start-up table is sent over: eight-bit commands and parameters, which is the JD9365's.
    esp_lcd_panel_io_handle_t io = nullptr;
    esp_lcd_dbi_io_config_t dbi = {};
    dbi.virtual_channel = 0;
    dbi.lcd_cmd_bits = 8;
    dbi.lcd_param_bits = 8;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_io_dbi(bus, &dbi, &io), TAG, "DSI command channel");

    // The video stream: the JD9365 driver's timing for 800 x 1280 at 60 Hz, in RGB565, which is what the strips
    // `display.cpp` paints are. **One frame buffer, in PSRAM**, as on the CrowPanel: 800 x 1280 x 16 bits is
    // 2,048,000 bytes of this chip's 32 MB. A redraw can tear with one; a second is the change if the bench shows it.
    esp_lcd_dpi_panel_config_t dpi = JD9365_800_1280_PANEL_60HZ_DPI_CONFIG(LCD_COLOR_PIXEL_FORMAT_RGB565);
    dpi.num_fbs = 1;

    jd9365_vendor_config_t vendor = {};
    vendor.init_cmds = relay_jd9365_init;
    vendor.init_cmds_size = relay_jd9365_init_count;
    vendor.mipi_config.dsi_bus = bus;
    vendor.mipi_config.dpi_config = &dpi;
    vendor.mipi_config.lane_num = kDsiLanes;

    esp_lcd_panel_dev_config_t dev = {};
    dev.reset_gpio_num = kPinLcdReset;
    dev.rgb_ele_order = LCD_RGB_ELEMENT_ORDER_RGB;
    dev.bits_per_pixel = 16;
    dev.vendor_config = &vendor;
    ESP_RETURN_ON_ERROR(esp_lcd_new_panel_jd9365(io, &dev, &s_panel), TAG, "panel create");

    esp_lcd_dpi_panel_event_callbacks_t cbs = {};
    cbs.on_refresh_done = on_refresh_done;
    ESP_RETURN_ON_ERROR(esp_lcd_dpi_panel_register_event_callbacks(s_panel, &cbs, nullptr), TAG, "frame callback");

    ESP_RETURN_ON_ERROR(esp_lcd_panel_reset(s_panel), TAG, "panel reset");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_init(s_panel), TAG, "panel init");
    ESP_RETURN_ON_ERROR(esp_lcd_panel_disp_on_off(s_panel, true), TAG, "panel on");

    // No mirror here: `kPanelTurnsInSoftware` is true for this board, so `display.cpp` makes the turn.
    ESP_LOGI(TAG, "panel: %dx%d RGB565 over %d-lane DSI at %u Mbit/s, rotation %d (turned in software)", kLcdWidth,
             kLcdHeight, kDsiLanes, static_cast<unsigned>(kDsiLaneMbps), panel_rotation_mirrors(rotation_deg) ? 180 : 0);
    *out = s_panel;
    return ESP_OK;
}

uint32_t board_panel_frames_done()
{
    return s_frames_done;
}

// Nothing to put back: the picture's position is the DSI host's, and a late read of the frame buffer does not
// move it the way it does on the RGB interface. If the bench shows otherwise, this is where the fix goes.
void board_panel_resync()
{
}

esp_err_t board_panel_set_pclk(uint32_t)
{
    return ESP_ERR_NOT_SUPPORTED;
}

esp_err_t board_panel_frame_buffers(void **, void **)
{
    return ESP_ERR_NOT_SUPPORTED;
}
