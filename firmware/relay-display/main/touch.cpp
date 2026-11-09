#include "touch.h"

#include <cstring>
#include <initializer_list>

#include "board_i2c.h"
#include "config.h"
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "lvgl.h"
#include "screen.h"
#include "sdkconfig.h"

namespace {

const char *TAG = "touch";

int s_rotation_deg = 0;
touch_source *s_source = nullptr;

// What LVGL is told between reads. LVGL polls on its own timer and wants a state every time, so
// the last known one is kept for the polls that find nothing new, and for a read that failed: a
// failed read lifts the finger, because a press that never ends would hold a button down forever.
touch_raw s_last = {};

void read_cb(lv_indev_t *, lv_indev_data_t *data)
{
    touch_report report = touch_report::none;
    touch_raw raw = s_last;
    if (s_source == nullptr || s_source->read(&report, &raw) != ESP_OK) {
        raw.pressed = false;
        report = touch_report::released;
    }
    if (report != touch_report::none) {
        const bool changed = raw.pressed != s_last.pressed;
        s_last = raw;
        if (changed) {
            // Down and up only, not every sample of a drag. Quiet on the board, where a person is
            // using the screen; the emulator suite reads these lines.
#if CONFIG_RELAY_QEMU
            ESP_LOGI(TAG, "pointer %s at (%d, %d)", raw.pressed ? "down" : "up",
                     touch_logical_x(raw.x, s_rotation_deg), touch_logical_y(raw.y, s_rotation_deg));
#else
            ESP_LOGD(TAG, "pointer %s", raw.pressed ? "down" : "up");
#endif
        }
    }
    data->point.x = touch_logical_x(s_last.x, s_rotation_deg);
    data->point.y = touch_logical_y(s_last.y, s_rotation_deg);
    data->state = s_last.pressed ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
}

esp_err_t register_pointer()
{
    lv_indev_t *indev = lv_indev_create();
    if (indev == nullptr) {
        return ESP_ERR_NO_MEM;
    }
    lv_indev_set_type(indev, LV_INDEV_TYPE_POINTER);
    lv_indev_set_read_cb(indev, read_cb);
    return ESP_OK;
}

#if CONFIG_RELAY_QEMU

// The emulator's finger: set from the console task and read by LVGL's task, so the pair is
// guarded. A report is `pressed` or `released` once per change and `none` after that, as the
// controller's ready bit behaves.
portMUX_TYPE s_inject_lock = portMUX_INITIALIZER_UNLOCKED;
touch_raw s_inject = {};
bool s_inject_fresh = false;

class injected_touch : public touch_source {
public:
    esp_err_t read(touch_report *report, touch_raw *out) override
    {
        taskENTER_CRITICAL(&s_inject_lock);
        const bool fresh = s_inject_fresh;
        const touch_raw now = s_inject;
        s_inject_fresh = false;
        taskEXIT_CRITICAL(&s_inject_lock);
        if (!fresh) {
            *report = touch_report::none;
            return ESP_OK;
        }
        *out = now;
        *report = now.pressed ? touch_report::pressed : touch_report::released;
        return ESP_OK;
    }
};

injected_touch s_injected;

#else

class gt911_touch : public touch_source {
public:
    // Find the controller at either of its two addresses and read its product ID, so a bus that
    // answers with something else is not taken for it. Returns the reason when it is not found.
    esp_err_t begin()
    {
        i2c_master_bus_handle_t bus = board_i2c_init();
        if (bus == nullptr) {
            return ESP_ERR_INVALID_STATE;
        }
        // **Which address answers depends on the INT level when the controller left reset**, which
        // this firmware does not drive. Both are tried so the log says which this board has.
        for (const uint8_t addr : {kGt911AddrPrimary, kGt911AddrAlternate}) {
            if (i2c_master_probe(bus, addr, 50) != ESP_OK) {
                continue;
            }
            if (board_i2c_add_device(addr, &dev_) != ESP_OK) {
                return ESP_ERR_NO_MEM;
            }
            uint8_t id[4] = {};
            const esp_err_t err = read_reg(kGt911RegProductId, id, sizeof id);
            if (err != ESP_OK) {
                ESP_LOGW(TAG, "gt911: 0x%02x answered but its product ID could not be read (%s)", addr,
                         esp_err_to_name(err));
                return err;
            }
            // The ID is ASCII: "911" then a NUL. Anything else is some other device at this address.
            if (std::memcmp(id, "911", 3) != 0) {
                ESP_LOGW(TAG, "gt911: 0x%02x answered with ID %02x %02x %02x %02x, not a GT911", addr, id[0], id[1],
                         id[2], id[3]);
                return ESP_ERR_NOT_FOUND;
            }
            ESP_LOGI(TAG, "gt911: found at 0x%02x%s", addr, addr == kGt911AddrPrimary ? "" : " (the alternate address)");
            return ESP_OK;
        }
        ESP_LOGW(TAG, "gt911: no answer at 0x%02x or 0x%02x; the screen shows captions without touch",
                 kGt911AddrPrimary, kGt911AddrAlternate);
        return ESP_ERR_NOT_FOUND;
    }

    esp_err_t read(touch_report *report, touch_raw *out) override
    {
        uint8_t buf[kGt911ReadBytes] = {};
        esp_err_t err = read_reg(kGt911RegStatus, buf, sizeof buf);
        if (err != ESP_OK) {
            return err;
        }
        *report = gt911_decode(buf, out);
        if (buf[0] & 0x80) {
            // **The ready bit is cleared by writing the status register**, and the controller does not
            // produce its next report until it is. Done for every ready report, including the ones
            // that were ignored.
            const uint8_t clear[3] = {static_cast<uint8_t>(kGt911RegStatus >> 8),
                                      static_cast<uint8_t>(kGt911RegStatus & 0xFF), 0};
            err = i2c_master_transmit(dev_, clear, sizeof clear, 20);
            if (err != ESP_OK) {
                return err;
            }
        }
        return ESP_OK;
    }

private:
    esp_err_t read_reg(uint16_t reg, uint8_t *buf, size_t len)
    {
        const uint8_t addr[2] = {static_cast<uint8_t>(reg >> 8), static_cast<uint8_t>(reg & 0xFF)};
        return i2c_master_transmit_receive(dev_, addr, sizeof addr, buf, len, 20);
    }

    i2c_master_dev_handle_t dev_ = nullptr;
};

gt911_touch s_gt911;

#endif

}  // namespace

esp_err_t touch_start(int rotation_deg)
{
    s_rotation_deg = rotation_deg;
#if CONFIG_RELAY_QEMU
    s_source = &s_injected;
    ESP_LOGI(TAG, "touch: emulator input, rotation %d", panel_rotation_mirrors(rotation_deg) ? 180 : 0);
#else
    const esp_err_t found = s_gt911.begin();
    if (found != ESP_OK) {
        return found;
    }
    s_source = &s_gt911;
    ESP_LOGI(TAG, "touch: gt911 up, rotation %d", panel_rotation_mirrors(rotation_deg) ? 180 : 0);
#endif
    screen_lock();
    const esp_err_t err = register_pointer();
    screen_unlock();
    return err;
}

void touch_inject(int x, int y, bool pressed)
{
#if CONFIG_RELAY_QEMU
    taskENTER_CRITICAL(&s_inject_lock);
    s_inject.pressed = pressed;
    s_inject.x = x;
    s_inject.y = y;
    s_inject_fresh = true;
    taskEXIT_CRITICAL(&s_inject_lock);
#else
    (void)x;
    (void)y;
    (void)pressed;
#endif
}
