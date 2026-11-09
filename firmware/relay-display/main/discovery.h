#pragma once

// Finding Relay on the LAN with no address typed in. Relay announces itself over mDNS (`discovery_format.h` says
// what and how); this file browses for it once the panel has an address and points the caption feeds at the answer
// (`feeds_use_relay`).
//
// **A typed Relay address always wins.** Discovery runs only when the settings hold none, so a LAN that blocks
// multicast is served by the setup page's field, as before. The Relay address that is found is not stored (a DHCP
// lease moves it); the install name is, so a panel stays with the Relay it first chose. The browse repeats, and a
// Relay that comes back at another address is followed.

#include "esp_err.h"
#include "settings_format.h"

// Start the browsing task, if `settings` has no Relay address. Call after `feeds_start`. Returns at once.
esp_err_t discovery_start(const relay_settings_t &settings);
