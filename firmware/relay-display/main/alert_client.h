#pragma once

// Alerts' client (#121): sends a tap on an alert button (`POST /api/alerts/<id>/send`) and acknowledges the alert
// flashing on this panel (`POST /api/alerts/ack`). One task, one request at a time; the newest tap of each kind is
// the one sent. The rules (the requests) are in `alert_format.h`. What the tap did arrives on the Alert feed, so
// the result kept here is only for wording a failure.

#include <cstdint>

#include "esp_err.h"
#include "settings_format.h"

enum class alert_send_result : uint8_t { none, sending, sent, refused, unreachable };

struct alert_client_state {
    alert_send_result result = alert_send_result::none;
    uint32_t at_ms = 0;
    uint32_t revision = 0;
};

// Start the task. Needs the panel's ID.
esp_err_t alert_client_start(const relay_settings_t &settings);

// Send (or stop) the alert on button `button` (8 hex). False when the task is not running or it is not an id.
bool alert_client_send(const char *button);

// Acknowledge alert `alert_id` (16 hex) as seen on this panel. False when the task is not running.
bool alert_client_ack(const char *alert_id);

alert_client_state alert_client_get();
