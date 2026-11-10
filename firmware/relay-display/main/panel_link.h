#pragma once

// The panel's link to Relay's panel registry: it checks in every 30 s, so Relay lists it (name, version, what it
// shows, address, uptime, Wi-Fi signal) and shows it offline when it stops, and it keeps `GET /api/panels/<id>/events`
// open so a feed the operator sets in Relay's admin arrives at once and is applied (`feeds_apply`).
//
// The rules (the check-in body, the two requests, the messages Relay sends, which `set_feed` to apply) are in
// `panel_link_format.h`, which `test_native.cpp` checks on a laptop. This file is the two tasks and their sockets.
//
// **Plain HTTP, no pairing, by decision.** The check-in says what any device on the LAN can already see, and the
// stream carries a feed choice and the panel's service mode. A panel the operator lets control the service is also
// sent its control key there (#80): it stops casual misuse, not someone reading the network. Relay bounds what a stray client can do (`app/panels.py`);
// control stays under `/api/admin/*` on Relay's HTTPS port, which the panel never calls.
//
// **A panel that has not found Relay does nothing here.** The address is the one the caption feeds use (a typed
// one, or what discovery found), so a Relay that moves is followed, and a panel with no address stays quiet.

#include <cstdint>

#include "esp_err.h"
#include "service_format.h"
#include "settings_format.h"

// Start the two tasks. Call after `feeds_start`. Returns at once. Needs the panel's ID, so a settings blob without one
// starts nothing.
esp_err_t panel_link_start(const relay_settings_t &settings);

// The console's `panel`: log the link's state in one line.
void panel_link_report();

// The panel's Planning Center service mode as Relay last said it (off until it has), and a count of its changes.
panel_service panel_link_service();
uint32_t panel_link_service_revision();
