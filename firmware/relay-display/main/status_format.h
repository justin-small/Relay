#pragma once

// The Status feed (#224): Relay's health for the sound desk and the booth. A pane on `kFeedStatus` reads
//
//   GET /stream?stream=status
//
//   {"type":"status","running":true,"started_at":1794590000.5,
//    "input":{"state":"ok","dbfs":-18},
//    "languages":["Spanish","French"],"viewers":37,"on_air":true,
//    "streams":[{"source":"obs","label":"OBS Studio","on_air":true,"health":"ok",
//                "live_since":1794591000.0,"viewers":null}],
//    "server_time":1794592520.1}
//
// Each message is the whole state (app/streamstatus.py). Relay sends one when something shown changes and again
// every 5 s, so the panel needs no silence handling of its own beyond `status_stale`. The times are Relay's clock
// (epoch seconds): the panel keeps how long each has run as of the message, and counts on from its own clock, so
// the elapsed times tick between messages and a panel clock that is wrong does not matter.
//
// `relay_state` is parsed in place into a small struct (well under 1 KB), never built on a task stack copy-by-value
// chain. Labels are kept as Relay sent them (UTF-8); the view folds them to ASCII for the small fonts.

#include <cstdint>
#include <cstdio>
#include <cstring>

#include "caption_format.h"

constexpr int kStatusStreamsMax = 4;      // a pane has room for a few, and no church streams to more at once
constexpr int kStatusLangsMax = 6;
constexpr size_t kStatusLabelBytes = 25;  // Relay sends at most 24 characters
constexpr uint32_t kStatusStaleMs = 20000;  // four missed messages

enum class status_input : uint8_t { off, lost, clipping, silent, ok };
enum class status_health : uint8_t { idle, ok, warn, error };
enum class status_air : uint8_t { unknown, off, on };

struct status_stream {
    char label[kStatusLabelBytes] = {};
    status_air air = status_air::unknown;
    status_health health = status_health::idle;
    int32_t live_s = -1;    // how long it had been live at `status_state::at_ms`, -1 when not live or not said
    int32_t viewers = -1;   // -1 when the provider gives no count
};

struct status_state {
    bool known = false;
    bool running = false;
    int32_t run_s = -1;     // how long captions had been running at `at_ms`, -1 when stopped
    status_input input = status_input::off;
    int dbfs = -60;
    int lang_count = 0;
    char langs[kStatusLangsMax][kStatusLabelBytes] = {};
    int32_t viewers = 0;
    status_air on_air = status_air::unknown;
    int stream_count = 0;
    status_stream streams[kStatusStreamsMax];
    uint32_t at_ms = 0;     // the panel's clock when the last message arrived
    uint32_t changes = 0;   // counts every message that changed something shown other than the clocks and the level
};

enum class status_change : uint8_t { ignored, state, same, malformed };

inline void status_clear(status_state *s)
{
    s->known = false;
    s->running = false;
    s->run_s = -1;
    s->input = status_input::off;
    s->dbfs = -60;
    s->lang_count = 0;
    s->viewers = 0;
    s->on_air = status_air::unknown;
    s->stream_count = 0;
}

// Seconds from a JSON number ("1794592520.1", "12", "-3"): the whole part only. False for anything else (null, a
// string, an exponent). Epoch seconds do not fit the tenths `pp_read_tenths` reads, hence this.
inline bool status_read_seconds(const char *p, const char *e, int64_t *out)
{
    if (p == nullptr || p >= e) {
        return false;
    }
    bool neg = false;
    if (*p == '-') {
        neg = true;
        ++p;
    }
    if (p >= e || *p < '0' || *p > '9') {
        return false;
    }
    int64_t v = 0;
    int digits = 0;
    while (p < e && *p >= '0' && *p <= '9') {
        if (++digits > 15) {
            return false;
        }
        v = v * 10 + (*p - '0');
        ++p;
    }
    if (p < e && *p == '.') {
        ++p;
        while (p < e && *p >= '0' && *p <= '9') {
            ++p;
        }
    }
    if (p < e && (*p == 'e' || *p == 'E')) {
        return false;
    }
    *out = neg ? -v : v;
    return true;
}

inline status_input status_input_from(const char *s)
{
    if (strcmp(s, "ok") == 0) {
        return status_input::ok;
    }
    if (strcmp(s, "silent") == 0) {
        return status_input::silent;
    }
    if (strcmp(s, "clipping") == 0) {
        return status_input::clipping;
    }
    if (strcmp(s, "lost") == 0) {
        return status_input::lost;
    }
    return status_input::off;
}

inline status_health status_health_from(const char *s)
{
    if (strcmp(s, "ok") == 0) {
        return status_health::ok;
    }
    if (strcmp(s, "warn") == 0) {
        return status_health::warn;
    }
    if (strcmp(s, "error") == 0) {
        return status_health::error;
    }
    return status_health::idle;
}

// true, false, or anything else (null, missing) as unknown.
inline status_air status_air_read(const char *v, const char *e)
{
    bool b = false;
    if (v == nullptr || !caption_json::read_bool(v, e, &b)) {
        return status_air::unknown;
    }
    return b ? status_air::on : status_air::off;
}

// Seconds between `since` and `now` (both Relay's clock), or -1 when `since` is missing.
inline int32_t status_elapsed_at(const char *obj, const char *e, const char *key, int64_t now)
{
    int64_t since = 0;
    const char *v = caption_json::get(obj, e, key);
    if (v == nullptr || !status_read_seconds(v, e, &since)) {
        return -1;
    }
    int64_t d = now - since;
    if (d < 0) {
        d = 0;
    }
    if (d > 99 * 3600) {
        d = 99 * 3600;
    }
    return static_cast<int32_t>(d);
}

inline int32_t status_count(const char *v, const char *e)
{
    uint64_t n = 0;
    if (v == nullptr || !caption_json::read_uint(v, e, &n)) {
        return -1;
    }
    return n > 9999999 ? 9999999 : static_cast<int32_t>(n);
}

// Read one message of the Status feed into `*s` in place. `now_ms` is the panel's clock. Every message re-anchors
// the elapsed times and the level; `state` is returned, and `changes` moves, only when something else shown changed.
inline status_change status_apply(status_state *s, const char *json, size_t len, uint32_t now_ms)
{
    const char *e = json + len;
    const char *p = caption_json::ws(json, e);
    if (p == nullptr || p >= e || *p != '{' || caption_json::skip_value(p, e) == nullptr) {
        return status_change::malformed;
    }
    char word[16];
    size_t n = 0;
    const char *t = caption_json::get(p, e, "type");
    if (t == nullptr || !caption_json::read_string(t, e, word, sizeof word, &n) || strcmp(word, "status") != 0) {
        return status_change::ignored;
    }
    int64_t now = 0;
    const char *v = caption_json::get(p, e, "server_time");
    if (v == nullptr || !status_read_seconds(v, e, &now)) {
        return status_change::malformed;
    }

    // Read into a fresh struct (a few hundred bytes: the callers' are statics or heap), then compare.
    status_state next;
    next.known = true;
    bool b = false;
    v = caption_json::get(p, e, "running");
    next.running = v != nullptr && caption_json::read_bool(v, e, &b) && b;
    next.run_s = next.running ? status_elapsed_at(p, e, "started_at", now) : -1;
    const char *in = caption_json::get(p, e, "input");
    if (in != nullptr) {
        v = caption_json::get(in, e, "state");
        if (v != nullptr && caption_json::read_string(v, e, word, sizeof word, &n)) {
            next.input = status_input_from(word);
        }
        int64_t db = -60;
        v = caption_json::get(in, e, "dbfs");
        if (v != nullptr && status_read_seconds(v, e, &db)) {
            next.dbfs = db < -60 ? -60 : db > 0 ? 0 : static_cast<int>(db);
        }
    }
    caption_json::array_iter it;
    const char *list = caption_json::get(p, e, "languages");
    if (list != nullptr && caption_json::array_begin(list, e, &it)) {
        while (next.lang_count < kStatusLangsMax) {
            const char *item = caption_json::array_next(&it);
            if (item == nullptr) {
                break;
            }
            if (caption_json::read_string(item, e, next.langs[next.lang_count], kStatusLabelBytes, &n) && n > 0) {
                ++next.lang_count;
            }
        }
    }
    const int32_t viewers = status_count(caption_json::get(p, e, "viewers"), e);
    next.viewers = viewers < 0 ? 0 : viewers;
    next.on_air = status_air_read(caption_json::get(p, e, "on_air"), e);
    list = caption_json::get(p, e, "streams");
    if (list != nullptr && caption_json::array_begin(list, e, &it)) {
        while (next.stream_count < kStatusStreamsMax) {
            const char *item = caption_json::array_next(&it);
            if (item == nullptr) {
                break;
            }
            if (*caption_json::ws(item, e) != '{') {
                continue;
            }
            status_stream &st = next.streams[next.stream_count];
            v = caption_json::get(item, e, "label");
            if (v == nullptr || !caption_json::read_string(v, e, st.label, sizeof st.label, &n) || n == 0) {
                continue;
            }
            st.air = status_air_read(caption_json::get(item, e, "on_air"), e);
            v = caption_json::get(item, e, "health");
            if (v != nullptr && caption_json::read_string(v, e, word, sizeof word, &n)) {
                st.health = status_health_from(word);
            }
            st.live_s = st.air == status_air::on ? status_elapsed_at(item, e, "live_since", now) : -1;
            st.viewers = status_count(caption_json::get(item, e, "viewers"), e);
            ++next.stream_count;
        }
    }

    // What counts as a change: all but the clocks and the exact level.
    bool changed = !s->known || s->running != next.running || s->input != next.input ||
                   s->lang_count != next.lang_count || s->viewers != next.viewers || s->on_air != next.on_air ||
                   s->stream_count != next.stream_count || (s->run_s < 0) != (next.run_s < 0);
    for (int i = 0; !changed && i < next.lang_count; ++i) {
        changed = strcmp(s->langs[i], next.langs[i]) != 0;
    }
    for (int i = 0; !changed && i < next.stream_count; ++i) {
        const status_stream &a = s->streams[i];
        const status_stream &c = next.streams[i];
        changed = strcmp(a.label, c.label) != 0 || a.air != c.air || a.health != c.health || a.viewers != c.viewers ||
                  (a.live_s < 0) != (c.live_s < 0);
    }
    next.at_ms = now_ms;
    next.changes = s->changes + (changed ? 1 : 0);
    *s = next;
    return changed ? status_change::state : status_change::same;
}

// ---- what the pane says ---------------------------------------------------------------------------------------------

inline bool status_stale(uint32_t at_ms, uint32_t now_ms)
{
    return static_cast<int32_t>(now_ms - at_ms) > static_cast<int32_t>(kStatusStaleMs);
}

// `base_s` seconds as of `at_ms`, counted on to `now_ms`. -1 stays -1.
inline int32_t status_seconds(int32_t base_s, uint32_t at_ms, uint32_t now_ms)
{
    if (base_s < 0) {
        return -1;
    }
    const int32_t since = static_cast<int32_t>(now_ms - at_ms);
    return base_s + (since > 0 ? since / 1000 : 0);
}

// "4:05", "1:02:03". Nothing for -1.
inline void status_elapsed_text(int32_t secs, char *out, size_t cap)
{
    if (cap == 0) {
        return;
    }
    if (secs < 0) {
        out[0] = '\0';
        return;
    }
    const int h = secs / 3600;
    const int m = (secs / 60) % 60;
    const int sec = secs % 60;
    if (h > 0) {
        snprintf(out, cap, "%d:%02d:%02d", h, m, sec);
    } else {
        snprintf(out, cap, "%d:%02d", m, sec);
    }
}

inline const char *status_input_words(status_input i)
{
    switch (i) {
    case status_input::ok: return "OK";
    case status_input::silent: return "Silent";
    case status_input::clipping: return "Clipping";
    case status_input::lost: return "Lost";
    case status_input::off: break;
    }
    return "Off";
}

inline const char *status_health_words(status_health h)
{
    switch (h) {
    case status_health::ok: return "healthy";
    case status_health::warn: return "needs a look";
    case status_health::error: return "trouble";
    case status_health::idle: break;
    }
    return "not connected";
}

// The pane's colours: on air red, a problem amber or red, fine green, nothing going grey.
enum class status_tone : uint8_t { idle, good, warn, bad, live };

inline status_tone status_input_tone(status_input i)
{
    switch (i) {
    case status_input::ok: return status_tone::good;
    case status_input::silent: return status_tone::warn;
    case status_input::clipping:
    case status_input::lost: return status_tone::bad;
    case status_input::off: break;
    }
    return status_tone::idle;
}

inline status_tone status_health_tone(status_health h)
{
    switch (h) {
    case status_health::ok: return status_tone::good;
    case status_health::warn: return status_tone::warn;
    case status_health::error: return status_tone::bad;
    case status_health::idle: break;
    }
    return status_tone::idle;
}

// The livestream line: "ON AIR", "OFF AIR", or "NOT WATCHED" with no stream module on.
inline const char *status_air_words(const status_state &s)
{
    if (s.stream_count == 0) {
        return "NOT WATCHED";
    }
    if (s.on_air == status_air::on) {
        return "ON AIR";
    }
    if (s.on_air == status_air::off) {
        return "OFF AIR";
    }
    return "UNKNOWN";
}

// How long the stream that is on air has been, as of the message: the first source that says it is live.
inline int32_t status_air_live_s(const status_state &s)
{
    for (int i = 0; i < s.stream_count; ++i) {
        if (s.streams[i].air == status_air::on && s.streams[i].live_s >= 0) {
            return s.streams[i].live_s;
        }
    }
    return -1;
}

// One stream's row, after its label: "live 25:20 - healthy - 142 watching". ASCII; `out` at least 64 bytes.
inline void status_stream_text(const status_stream &st, uint32_t at_ms, uint32_t now_ms, char *out, size_t cap)
{
    char when[16];
    if (st.air == status_air::on) {
        char t[12];
        status_elapsed_text(status_seconds(st.live_s, at_ms, now_ms), t, sizeof t);
        snprintf(when, sizeof when, "live%s%s", t[0] != '\0' ? " " : "", t);
    } else {
        snprintf(when, sizeof when, "%s", st.air == status_air::off ? "off air" : "-");
    }
    if (st.viewers >= 0) {
        snprintf(out, cap, "%s - %s - %ld watching", when, status_health_words(st.health), static_cast<long>(st.viewers));
    } else {
        snprintf(out, cap, "%s - %s", when, status_health_words(st.health));
    }
}

// What the pane says in place of the status, or null when it has one to show.
inline const char *status_idle_text(const status_state &s, bool refused, bool relay_down)
{
    if (refused) {
        return "This Relay has no status view.\nUpdate Relay.";
    }
    if (relay_down) {
        return "Relay is not running";
    }
    if (!s.known) {
        return "Connecting to Relay";
    }
    return nullptr;
}
