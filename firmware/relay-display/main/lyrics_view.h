#pragma once

// ProPresenter's lyrics on the glass (#138): a pane on the Lyrics feed (`kFeedLyrics`) under the header
// `caption_view.cpp` draws. The current slide's lines are large and centred, the next slide's first words dim
// under them, and the part ("Verse 1", "Chorus") and the song's title small along the foot. With no song up, a
// blank slide, or ProPresenter or Relay away, the pane is quiet: a few dim words and never the last slide's text.
//
// The rules (parsing, folding, what the idle words are, which of three sizes fits) are in `pp_format.h`, checked on a
// laptop. **Everything that draws runs on the LVGL task under `screen_lock`** and redraws only when what the pane shows
// has changed (`lyrics_state::changes`), not when Relay repeats itself.

#include <cstdint>

#include "lvgl.h"
#include "pp_format.h"

// Build the Lyrics pane `pane` (0 or 1) in `box`, a column `w` x `h`, reading caption slot `slot`.
void lyrics_view_build(int pane, lv_obj_t *box, int slot, int w, int h);

// Take in what changed. Cheap when nothing did.
void lyrics_view_tick(int pane, uint32_t now_ms);

// Stop drawing both panes; the objects go with the screen.
void lyrics_view_teardown();

// The console's `view`: what each lyrics pane shows.
void lyrics_view_report();
