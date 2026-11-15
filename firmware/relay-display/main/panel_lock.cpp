#include "panel_lock.h"

#include <atomic>

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "settings.h"

namespace {

const char *TAG = "panel_lock";

// Held only for copies of a few bytes, never across NVS or drawing.
portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
lock_state s_state;
std::atomic<uint32_t> s_rev{0};
std::atomic<uint32_t> s_idle_s{0};

}  // namespace

void panel_lock_start()
{
    panel_policy saved;
    if (settings_load_lock(&saved)) {
        taskENTER_CRITICAL(&s_mux);
        s_state.set(saved);
        taskEXIT_CRITICAL(&s_mux);
        ESP_LOGI(TAG, "lock: %s%s (saved)", saved.lock ? "locked" : "unlocked", saved.pin[0] ? ", with a PIN" : "");
    }
}

void panel_lock_set(const panel_policy &policy, const char *how)
{
    taskENTER_CRITICAL(&s_mux);
    const bool same = lock_policy_equal(policy, s_state.policy);
    if (!same) {
        s_state.set(policy);
    }
    taskEXIT_CRITICAL(&s_mux);
    if (same) {
        return;  // it comes on every check-in
    }
    s_rev.fetch_add(1);
    ESP_LOGI(TAG, "lock: %s%s, %u s to relock (%s)", policy.lock ? "locked" : "unlocked",
             policy.pin[0] ? ", with a PIN" : "", static_cast<unsigned>(policy.unlock_s), how);
    const esp_err_t err = settings_save_lock(policy);
    if (err != ESP_OK) {
        ESP_LOGW(TAG, "lock: not saved (%s); it holds until the panel restarts", esp_err_to_name(err));
    }
}

void panel_lock_tick(uint32_t idle_ms)
{
    s_idle_s = idle_ms / 1000;
    taskENTER_CRITICAL(&s_mux);
    const bool was = s_state.refuses();
    s_state.tick(idle_ms);
    const bool now = s_state.refuses();
    taskEXIT_CRITICAL(&s_mux);
    if (now != was) {
        s_rev.fetch_add(1);
        ESP_LOGI(TAG, "lock: locked again after %u s with no touch", static_cast<unsigned>(idle_ms / 1000));
    }
}

bool panel_lock_refuses()
{
    taskENTER_CRITICAL(&s_mux);
    const bool r = s_state.refuses();
    taskEXIT_CRITICAL(&s_mux);
    return r;
}

bool panel_lock_has_pin()
{
    taskENTER_CRITICAL(&s_mux);
    const bool r = s_state.has_pin();
    taskEXIT_CRITICAL(&s_mux);
    return r;
}

lock_try panel_lock_try(const char *pin, uint32_t now)
{
    taskENTER_CRITICAL(&s_mux);
    const lock_try r = s_state.try_pin(pin, now);
    taskEXIT_CRITICAL(&s_mux);
    if (r == lock_try::unlocked) {
        s_rev.fetch_add(1);
        ESP_LOGI(TAG, "lock: unlocked at the screen with the PIN");
    } else if (r == lock_try::wrong || r == lock_try::wait) {
        ESP_LOGW(TAG, "lock: a wrong PIN at the screen%s", r == lock_try::wait ? "; the keypad waits a minute" : "");
    }
    return r;
}

uint32_t panel_lock_wait_left_ms(uint32_t now)
{
    taskENTER_CRITICAL(&s_mux);
    const uint32_t r = s_state.wait_left_ms(now);
    taskEXIT_CRITICAL(&s_mux);
    return r;
}

uint32_t panel_lock_revision()
{
    return s_rev.load();
}

uint32_t panel_lock_idle_s()
{
    return s_idle_s.load();
}
