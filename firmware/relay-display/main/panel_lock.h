#pragma once

// The operator's lock on this panel's screen (#126). The rules are in `lock_format.h` (native checks); this file keeps
// the policy Relay last sent, saves it in NVS so a panel that restarts while Relay is away stays locked, and answers
// the menu (`caption_view.cpp`), which asks before every change made at the screen.
//
// Relay's side (`panel_link.cpp`) calls `panel_lock_set` from its own tasks; everything else is the screen's and is
// called on the LVGL task.

#include <cstdint>

#include "lock_format.h"

// Read the saved policy. Call once, before the screen and the link start.
void panel_lock_start();

// Relay's word, from a check-in reply or the stream. Saved only when it changed.
void panel_lock_set(const panel_policy &policy, const char *how);

// The screen's tick: `idle_ms` is how long the screen has gone untouched. Locks again after an unlock that has gone
// unused for the unlock time.
void panel_lock_tick(uint32_t idle_ms);

// Whether a change at the screen is refused now.
bool panel_lock_refuses();

// Whether the operator set a PIN, so the menu offers the keypad.
bool panel_lock_has_pin();

// The keypad's OK. `now` is LVGL's tick.
lock_try panel_lock_try(const char *pin, uint32_t now);

// How long the keypad still waits after too many wrong PINs, in ms; 0 when it does not.
uint32_t panel_lock_wait_left_ms(uint32_t now);

// Counts every change to the lock, so the menu draws itself again when Relay locks or unlocks it.
uint32_t panel_lock_revision();

// How long the screen has gone untouched, in seconds, as the last tick saw it: what the check-in reports.
uint32_t panel_lock_idle_s();
