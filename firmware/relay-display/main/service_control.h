#pragma once

// The LIVE buttons' client (#83): Next, Previous, Take control and Release, sent to Relay
// (`POST /api/panels/<id>/service/<action>` with the panel's control key), which presses them in Planning Center as
// its token's owner. The panel never holds the Planning Center token.
//
// **One press at a time.** A press made while one is waiting for Relay is refused here, so a double tap cannot skip
// two items. The result (`service_result`) is kept for the view to word; the change itself arrives on the Service
// feed, so the item on the glass moves when Relay confirms, not when the finger lifts.
//
// The rules (the request, the answer, the words) are in `service_format.h`. This file is the task and its socket.

#include <cstdint>

#include "esp_err.h"
#include "service_format.h"
#include "settings_format.h"

struct service_control_state {
    service_result result = service_result::none;
    service_action action = service_action::next;
    uint32_t at_ms = 0;        // when the result was set (the panel's clock)
    uint32_t revision = 0;     // counts every change
};

// Start the task. Needs the panel's ID. Call after `panel_link_start`.
esp_err_t service_control_start(const relay_settings_t &settings);

// Press `action` for service type `type`. False, and nothing sent, while a press is still waiting for Relay or the
// panel has no control key.
bool service_control_press(service_action action, const char *type);

service_control_state service_control_get();
