#pragma once

// What the panel shows: one feed full screen or two side by side, the choice a person made at the screen (or the
// operator will make from Relay admin), and what to do when a chosen language stops being live.
//
// The rules (the offered list, the fall-back to the transcription, the panes' geometry) are in `feed_format.h`,
// checked on a laptop. This file keeps the choice, saves it, reads the live languages from the caption feeds'
// `targets` messages and points the two caption slots (`captions.h`) at what each pane shows. Pane 0 is
// caption slot 0, and pane 1 is slot 1.
//
// **The choice is saved in NVS** (`feed_stream`/`feed_lang` for pane 0, `feed2_*` and `feed_split` for pane 1) by this file's
// own task, not by the caller, so a tap never waits for flash. **Whichever change is newest wins, from either side**: a
// person at the screen and the operator's setting (`feeds_choose`, `feeds_set_split`) are the same call, and the
// last one made is what is shown and saved.

#include "esp_err.h"
#include "feed_format.h"
#include "settings_format.h"

struct feeds_view {
    bool split;              // two panes
    feed_pick saved[2];      // each pane's own choice, which stays when its language is not live
    feed_resolved shown[2];  // what each pane is pointed at now
    feed_options options;    // what the menu offers; `options.known` is false until Relay has said
    uint32_t revision;       // counts every change to any of this
};

// Read the saved choice from `settings` and start the task that keeps the caption slots on it. With no Relay
// address in the settings nothing is shown until `feeds_use_relay`. Call after `captions_start`.
esp_err_t feeds_start(const relay_settings_t &settings);

// A copy of the current state.
void feeds_snapshot(feeds_view *out);

// `revision` of the state now, without copying it.
uint32_t feeds_revision();

// One pane's choice. Saved, and shown at once. `pane` is 0 or 1.
void feeds_choose(int pane, const feed_pick &pick);

// One feed or two. Turning two on with nothing chosen for the second pane gives it the first live translation.
void feeds_set_split(bool split);

// Point the panel at a Relay now, without saving the address, and take the slots back from the console's `feed`.
// The bench and the emulator suite use it; a panel finds Relay from its settings.
void feeds_use_relay(const char *url);

// The Relay address the panel uses now (typed, or found), copied into `out`. False when it has none.
bool feeds_relay_url(char *out, size_t cap);

// The operator's setting, from Relay: one or two feeds, as one change. `count` 1 is one feed full screen with `picks[0]`;
// 2 is two side by side. Saved and shown at once, and the newest change wins, as with a choice at the screen.
void feeds_apply(const feed_pick *picks, int count);

// The console's `feed` sets the slots by hand. The task leaves them alone from then on.
void feeds_go_manual();

// The console's `feeds`: log the state in one line.
void feeds_report();
