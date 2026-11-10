#pragma once

// The decisions for updating the panel from Relay, and nothing that touches flash, the network or a
// task. `firmware_update.cpp` does the work; this says what the work may be. No ESP-IDF in it, so
// `test_native.cpp` checks it on a laptop. Adapted from the VXP room panel's `update_format.h`.
//
// **The words are Relay's.** `state_word` and `error_word` are the strings `app/panels.py` keeps in
// `UPDATE_STATES` and `UPDATE_ERRORS`, and `tests/test_panels.py` holds the two sides to each other. A
// word Relay does not know is dropped on its side, so a mismatch here would be a silent failure.
//
// What goes over the wire, on the viewer port, plain HTTP, no login:
//
//   Relay -> panel   {"version":"0.2.0","size":1048576,"sha256":"<64 hex>","path":"/api/panels/firmware/0.2.0.bin",
//                     "now":false}   as the check-in reply's `update`, and as an `update_available` event
//                     with a `type`. No `update` in a reply is the ask withdrawn.
//   panel -> Relay   POST /api/panels/<id>/update  {"state":"downloading","version":"0.2.0","progress":40}
//                    states: downloading, ready, restarting, failed (with `error`), installed, rolled_back
//   panel <- Relay   GET <path>   the image, with Content-Length

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "ota_format.h"

#ifndef CONFIG_RELAY_UPDATE_CONFIRM_S
#define CONFIG_RELAY_UPDATE_CONFIRM_S 600
#endif
#ifndef CONFIG_RELAY_UPDATE_QUIET_S
#define CONFIG_RELAY_UPDATE_QUIET_S 120
#endif

namespace update_format {

constexpr size_t kVersionBytes = 33;
constexpr size_t kShaBytes = 65;
constexpr size_t kPathBytes = 129;
// One flash sector: the unit `esp_ota_write` erases in with sequential writes.
constexpr size_t kChunkBytes = 4096;

// **Proof.** A new image has this long after boot to reach Relay with a 200 before the bootloader is
// asked to take it back. The clock is the boot's, so a panel that never reaches Relay does not keep a bad
// image for ever.
constexpr uint32_t kConfirmSeconds = CONFIG_RELAY_UPDATE_CONFIRM_S;

// **The quiet moment.** A panel is mid-sentence in a room. It restarts when no caption line has arrived
// for this long, so the restart lands in a pause and not under a speaker. The operator's `now` skips the
// wait and nothing else.
constexpr uint32_t kQuietSeconds = CONFIG_RELAY_UPDATE_QUIET_S;

// A download that has not finished in this long is a trickle, not a link.
constexpr uint32_t kMaxDownloadSeconds = 20 * 60;

// How often a download reports its progress to Relay, in percent.
constexpr unsigned kProgressStepPercent = 10;

enum class update_state : uint8_t { idle, downloading, ready, restarting, failed };
enum class update_error : uint8_t { none, download, signature, version, compatible, install, rollback };

inline const char *state_word(update_state s)
{
    switch (s) {
    case update_state::downloading:
        return "downloading";
    case update_state::ready:
        return "ready";
    case update_state::restarting:
        return "restarting";
    case update_state::failed:
        return "failed";
    case update_state::idle:
        break;
    }
    return "";
}

inline const char *error_word(update_error e)
{
    switch (e) {
    case update_error::download:
        return "download";
    case update_error::signature:
        return "signature";
    case update_error::version:
        return "version";
    case update_error::compatible:
        return "compatible";
    case update_error::install:
        return "install";
    case update_error::rollback:
        return "rollback";
    case update_error::none:
        break;
    }
    return "";
}

// What an image head's verdict means to Relay: `compatible` is the word for a file that is not for this
// hardware, `version` for one that does not go forward, and `download` for bytes that are not an image.
inline update_error error_for(ota_verdict v)
{
    switch (v) {
    case ota_verdict::accept:
        return update_error::none;
    case ota_verdict::merged_image:
        return update_error::compatible;
    case ota_verdict::older:
        return update_error::version;
    case ota_verdict::not_an_image:
        break;
    }
    return update_error::download;
}

// `X.Y.Z` and nothing else.
inline bool version_ok(const char *v)
{
    if (v == nullptr) {
        return false;
    }
    size_t n = 0;
    while (n < kVersionBytes && v[n] != '\0') {
        if (!((v[n] >= '0' && v[n] <= '9') || v[n] == '.')) {
            return false;
        }
        ++n;
    }
    if (n == 0 || n >= kVersionBytes) {
        return false;
    }
    unsigned parsed[3] = {0, 0, 0};
    return ota_parse_version(v, n + 1, parsed);
}

// A path the panel joins to the Relay address it already holds. A path and nothing more: a scheme, a
// second host or a `..` segment would send the panel somewhere Relay's offer was not meant to name.
// `//` and `..` are refused anywhere, a stricter rule than Relay needs and one nothing legitimate hits.
inline bool path_ok(const char *p)
{
    if (p == nullptr || p[0] != '/') {
        return false;
    }
    size_t n = 0;
    while (n < kPathBytes && p[n] != '\0') {
        const char c = p[n];
        if (c < 0x21 || c > 0x7e || c == '\\' || c == '?' || c == '#' || c == '%') {
            return false;
        }
        if (n + 1 < kPathBytes && p[n + 1] != '\0') {
            if ((c == '/' && p[n + 1] == '/') || (c == '.' && p[n + 1] == '.')) {
                return false;
            }
        }
        ++n;
    }
    return n > 0 && n < kPathBytes;
}

// 64 lowercase hex digits.
inline bool sha256_ok(const char *h)
{
    if (h == nullptr) {
        return false;
    }
    for (size_t i = 0; i < 64; ++i) {
        if (!((h[i] >= '0' && h[i] <= '9') || (h[i] >= 'a' && h[i] <= 'f'))) {
            return false;
        }
    }
    return h[64] == '\0';
}

// What Relay asked, once read. `size` is bounded by the slot before anything is written, so an offer
// cannot make the panel write past its partition.
struct Order {
    bool present;
    bool now;
    uint32_t size;
    char version[kVersionBytes];
    char sha256[kShaBytes];
    char path[kPathBytes];
};

inline bool order_ok(const Order &o, uint32_t slot_bytes)
{
    return version_ok(o.version) && sha256_ok(o.sha256) && path_ok(o.path) && o.size >= kOtaHeadBytes &&
           o.size <= slot_bytes;
}

// The wait after the `failures`th failure in a row: none for the first. A failed update is reported to
// Relay, which ends the ask, so this only bounds a panel that is offered the same version again at once.
inline uint32_t retry_seconds(uint32_t failures)
{
    if (failures == 0) {
        return 0;
    }
    const uint32_t shift = failures - 1 > 6 ? 6 : failures - 1;
    const uint32_t s = 60u << shift;
    return s > 1800u ? 1800u : s;
}

inline bool quiet_moment(uint32_t seconds_since_line)
{
    return seconds_since_line >= kQuietSeconds;
}

// Whether a verified image may be booted now. `now` skips the wait and nothing else.
inline bool may_restart(bool staged, bool operator_now, bool quiet)
{
    return staged && (operator_now || quiet);
}

// Whether the last boot took the attempted image back. An attempt is recorded just before the restart. The
// boot that follows is either that version on approval, or the image the panel ran before with the attempt
// still on record: the bootloader took the new image back. A boot with no attempt was not an update.
inline bool attempt_was_taken_back(const char *attempt, const char *running, bool pending)
{
    if (attempt == nullptr || attempt[0] == '\0' || running == nullptr || pending) {
        return false;
    }
    return std::strcmp(attempt, running) != 0;
}

// The body of `POST /api/panels/<id>/update`. `error` is `none` unless the state is `failed`. Returns the
// length or 0 when it does not fit.
inline size_t report_body(char *out, size_t cap, const char *state, const char *version, unsigned progress,
                          const char *error)
{
    int n;
    if (error != nullptr && error[0] != '\0') {
        n = std::snprintf(out, cap, "{\"state\":\"%s\",\"version\":\"%s\",\"error\":\"%s\"}", state, version, error);
    } else {
        n = std::snprintf(out, cap, "{\"state\":\"%s\",\"version\":\"%s\",\"progress\":%u}", state, version,
                          progress > 100 ? 100u : progress);
    }
    return n > 0 && static_cast<size_t>(n) < cap ? static_cast<size_t>(n) : 0;
}

}  // namespace update_format
