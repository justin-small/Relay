#include "board_i2c.h"

#include "config.h"
#include "driver/gpio.h"
#include "esp_log.h"

namespace {

const char *TAG = "i2c";

i2c_master_bus_handle_t s_bus = nullptr;

}  // namespace

i2c_master_bus_handle_t board_i2c_init()
{
    if (s_bus != nullptr) {
        return s_bus;
    }

    i2c_master_bus_config_t cfg = {};
    cfg.i2c_port = I2C_NUM_0;
    cfg.sda_io_num = static_cast<gpio_num_t>(kPinI2cSda);
    cfg.scl_io_num = static_cast<gpio_num_t>(kPinI2cScl);
    cfg.clk_source = I2C_CLK_SRC_DEFAULT;
    // Glitches shorter than this many clocks are filtered out, which keeps a noisy
    // line near the panel from reading as a start or a stop.
    cfg.glitch_ignore_cnt = 7;
    // **The internal pull-ups stay off.** They are tens of kilohms, too weak for a
    // 400 kHz bus, so the bus relies on pull-ups on the board, which it is expected to
    // carry. Those are not in the Elecrow sources this was written from, so they are a
    // bench item: a scan that finds nothing at 0x30 is the first sign they are missing.
    cfg.flags.enable_internal_pullup = false;

    const esp_err_t err = i2c_new_master_bus(&cfg, &s_bus);
    if (err != ESP_OK) {
        // A null handle is kept as null, so the next call tries again rather than
        // returning a bus that was never started.
        s_bus = nullptr;
        ESP_LOGW(TAG, "bus: could not start the I2C controller (%s)", esp_err_to_name(err));
    }
    return s_bus;
}

esp_err_t board_i2c_add_device(uint8_t address, i2c_master_dev_handle_t *out)
{
    if (s_bus == nullptr) {
        return ESP_ERR_INVALID_STATE;
    }
    i2c_device_config_t dev = {};
    dev.dev_addr_length = I2C_ADDR_BIT_LEN_7;
    dev.device_address = address;
    dev.scl_speed_hz = kI2cSpeedHz;
    return i2c_master_bus_add_device(s_bus, &dev, out);
}
