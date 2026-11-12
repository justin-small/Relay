#pragma once

// The panel's caption feeds: up to two connections to Relay's `GET /stream?...&finals=1`, each with
// its own task, socket and 200-line buffer. A feed resumes with `?last_id=` after a drop, so only the
// lines it missed are fetched, and backs off from 1 s to 30 s while Relay is away.
//
// The rules (the HTTP and event-stream readers, the JSON fields, the text folding, the buffer and what
// each message does to it) are in `caption_format.h`, which `test_native.cpp` checks on a laptop. This
// file is the socket and the task.
//
// **Every read of a feed's lines is under its lock**, through `captions_read`. The caption view
// (`caption_view.cpp`) builds its lines from inside that call, and the client task waits for it. The lock
// order is the screen's, then a feed's: the client task never takes the screen's.

#include <cstddef>
#include <cstdint>

#include "alert_format.h"
#include "caption_format.h"
#include "esp_err.h"
#include "pp_format.h"
#include "service_format.h"

constexpr int kCaptionSlots = 2;

// Make the two slots and their tasks. Nothing connects until a feed is set.
esp_err_t captions_start();

// Show `stream` (kFeedTranscription, kFeedTranslation, kFeedService, kFeedAlert, kFeedLyrics or kFeedTimers; kFeedNone turns the slot off) from the Relay
// at `relay_url` (`http://host[:port]`) in `slot`. A different feed from the one the slot has starts
// over with an empty buffer; the same feed again changes nothing. Not saved: the settings are.
esp_err_t captions_set_feed(int slot, const char *relay_url, uint8_t stream, const char *lang);

caption_state captions_state(int slot);

// The feed the slot was asked for: kFeedNone, kFeedTranscription or kFeedTranslation, with the translation's
// language (as Relay names it) copied into `lang`. For the view's header.
uint8_t captions_feed_choice(int slot, char *lang, size_t lang_cap);

// Counts every change to the slot: a line, a rebuild, a reset, a new target set or a new state. The
// view compares it with the one it last drew.
uint32_t captions_revision(int slot);

// Call `fn` with the slot's feed under its lock. `feed` is null while the slot is off.
void captions_read(int slot, void (*fn)(const caption_feed *feed, caption_state state, void *ctx), void *ctx);

// Call `fn` with the slot's Planning Center service under its lock (`service_format.h`). `service` is null unless the
// slot is on the Service feed (then `lang` is the service type id). The same lock and order as `captions_read`.
void captions_read_service(int slot, void (*fn)(const service_state *service, caption_state state, void *ctx),
                           void *ctx);

// Call `fn` with the slot's alert buttons under its lock (`alert_format.h`, #121). `alerts` is null unless the slot is
// on the Alert feed. The same lock and order as `captions_read`.
void captions_read_alerts(int slot, void (*fn)(const alert_board *alerts, caption_state state, void *ctx), void *ctx);

// Call `fn` with the slot's ProPresenter lyrics, or timers, under its lock (`pp_format.h`, #138). `lyrics` (`timers`) is null
// unless the slot is on that feed. The same lock and order as `captions_read`.
void captions_read_lyrics(int slot, void (*fn)(const lyrics_state *lyrics, caption_state state, void *ctx), void *ctx);
void captions_read_timers(int slot, void (*fn)(const timers_state *timers, caption_state state, void *ctx), void *ctx);

// This panel's id, which the timers feed asks Relay for its own timers by. Call once at boot, before any feed is set.
void captions_set_panel_id(const char *id);

// Told after each change, from the slot's task, outside the lock. One listener; null removes it.
void captions_set_listener(void (*fn)(int slot, void *ctx), void *ctx);

// Ask the Relay at `relay_url` what is live: `GET /api/targets`, the JSON copied into `out` (`cap` bytes, `kCaptionTargetsMax`
// is enough). Blocks for up to a few seconds, so it is for a task of its own. False when the panel has no address,
// Relay does not answer 200, or the answer does not fit. Relay's stream says what is live only when it changes, so this
// is how a panel learns it when it connects.
bool captions_fetch_targets(const char *relay_url, char *out, size_t cap);

// For the panel link (`panel_link.cpp`), which talks to Relay over plain HTTP the way the feeds do: whether the panel has an
// address, a TCP connection to `host:port` (the descriptor, or -1; receives time out after a second so a task can look
// at its own state between reads), and a write of all of `n` bytes.
bool captions_has_address();
int captions_connect(const char *host, uint16_t port);
bool captions_send(int fd, const char *p, size_t n);

// The console's `captions <slot>`: the state and every line, one per log line.
void captions_dump(int slot);
