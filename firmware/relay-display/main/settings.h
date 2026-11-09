#pragma once

#include "esp_err.h"
#include "settings_format.h"

// The panel's stored configuration. The struct, its layout and the rules for reading a
// blob back are in `settings_format.h`, which has no ESP-IDF in it and is what
// `test_native.cpp` checks. This header is the NVS side.

// Fill `out` with the defaults, then with whatever is stored. A blob that is missing,
// short, from another schema or unreadable leaves the defaults in place and logs why. An
// error comes back only when NVS itself refuses to open.
esp_err_t settings_load(relay_settings_t *out);

// Write the whole blob in one NVS entry and commit it.
esp_err_t settings_save(const relay_settings_t *in);

// Save the feed choice (`feed_stream`, `feed_lang`, `feed2_stream`, `feed2_lang`, `feed_split` of `in`) into the stored
// blob and leave every other field as stored: the choice is saved from the feeds task while the setup page may be
// saving a network. An error comes back if the blob cannot be written.
esp_err_t settings_save_feeds(const relay_settings_t *in);

// Copy the stored feed choice into `*cand`, so a save of a whole blob made from an older copy (the setup page's)
// does not put an old choice back.
void settings_adopt_feeds(relay_settings_t *cand);

// The defaults: nothing configured, so the panel opens its setup network and does not
// connect to anything. The rotation starts at the Kconfig value, and the address is DHCP.
void settings_defaults(relay_settings_t *out);

// Make the panel ID if there is none yet: 16 random bytes from the hardware generator, as
// 32 hex characters, saved at once. Called at boot after `settings_load`. `*created` says
// whether a new one was made.
esp_err_t settings_ensure_panel_id(relay_settings_t *settings, bool *created);

// **The factory reset: every stored value, then nothing.** Stops the radio and erases the
// whole NVS partition (this file's blob and Wi-Fi's own copy of the network). The caller
// restarts: a board left running on erased storage would write its in-memory state straight
// back. The panel ID goes with it, so the panel is new to Relay afterwards.
esp_err_t settings_factory_reset();
