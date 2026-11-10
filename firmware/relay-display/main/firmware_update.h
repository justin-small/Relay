#pragma once

// The panel's own firmware update from Relay: what Relay asks for, the download over plain HTTP into the spare
// slot, the signature check before that slot can boot, the wait for a quiet moment, and the proof that a new
// image works. The decisions are in `update_format.h` and `ota_format.h`.
//
// **Images travel over plain HTTP on the LAN, so the signature check is what keeps a stranger's image off the
// panel.** It is `esp_ota_end`, against the public key this firmware was built with
// (CONFIG_SECURE_SIGNED_ON_UPDATE_NO_SECURE_BOOT). The slot is never made bootable before it passes, and the
// bootloader is told about the slot only at the moment the panel restarts.

#include "esp_err.h"
#include "update_format.h"

// Read the boot's state (is this a new image on approval; did the bootloader take the last one back) and start
// the update task. `settings_readable` is the one half of the proof known at boot. Called once from `app_main`
// after settings are loaded.
esp_err_t firmware_update_start(bool settings_readable);

// What Relay asked, from a check-in reply or the event stream. A not-present order is the ask withdrawn: a
// verified image waiting for its restart is dropped.
void firmware_update_offer(const update_format::Order &order);

// Relay answered a check-in with a 200 that parsed: the proof a new image needs. Confirms a pending image.
void firmware_update_reached_relay();

// A caption line arrived: the quiet clock starts again.
void firmware_update_note_line();
