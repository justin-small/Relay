#pragma once

#include "esp_err.h"
#include "settings_format.h"

// Setup mode for the Relay panel: the setup network, its page, the attempt to
// join the network a person typed, the save, and the close. It is kept apart from the
// Relay side on purpose, so a network can be configured before the panel can reach
// Relay at all.

// Start the board's network from its stored settings, once the panel is up. A stored
// Wi-Fi network is joined, and the station keeps retrying it in the background. With
// no stored network the setup network opens, and stays open until a configuration
// succeeds. The serial console's `recover` command is started here too.
esp_err_t provision_start(const relay_settings_t &settings);

// Open the setup network now: a new passphrase is drawn on the panel, the page is
// served, and the station keeps any stored network until a new one succeeds. Recovery
// (`recover` on the serial console, or a five-second hold of BOOT in `button.cpp`) calls this
// with `recovery` set. A recovery with a stored network closes the setup network again after
// fifteen minutes with no use. Callers may be on different tasks: opens are serialised inside.
esp_err_t provision_open_setup(bool recovery);

// Whether the setup network is open. The caption stream waits while it is, so it does not open
// a connection over a network the board is about to close.
bool provision_setup_open();

// A copy of the stored settings, as last read or saved. Readers never write it.
void provision_stored_settings(relay_settings_t *out);
