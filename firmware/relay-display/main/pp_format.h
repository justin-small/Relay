#pragma once

// ProPresenter on the panel (#138), with no LVGL or ESP-IDF in it, so `test_native.cpp` checks it on a laptop.
// `captions.cpp` holds the state for a caption slot pointed at one of these feeds; `lyrics_view.cpp` and
// `timers_view.cpp` draw it.
//
// **What goes over the wire**, plain HTTP on the viewer port like the captions (app/modules/propresenter/propresenter.py):
//
//   GET /stream?stream=lyrics&for=panel
//   -> data: {"type":"lyrics","enabled":true,"live":true,"song":true,"title":"Great Song","part":"Verse 1",
//             "index":3,"text":"line one\nline two","next":"line three","rev":12,"server_time":1760000000.0}
//
//   GET /stream?stream=pptimers&for=panel&panel=<this panel's id>
//   -> data: {"type":"pptimers","enabled":true,"live":true,"amber_s":120,"red_s":30,
//             "timers":[{"id":"195531B6-...","name":"Segment Countdown","state":"running","dir":"down",
//                        "value":298.0,"level":"ok"}],"rev":12,"server_time":1760000000.0}
//
// **Each message is the whole state**, so a reconnecting panel needs nothing else, and each is repeated at Relay's
// 15 s heartbeat. The panel therefore decides what is *new* by what it shows, not by `rev` (which moves whenever
// anything at all changes in ProPresenter, a running timer included): a repeat that says what the glass already
// says changes nothing and redraws nothing. A still screen must not be redrawn (README, "Caption view").
//
// **The timers are counted here.** A running timer's `value` is the seconds it showed as of the message; the panel
// counts it from the moment the message arrived, down for `"dir":"down"` and up for `"up"`, so it ticks smoothly
// with no request a second. The colour (`pp_timer_level`) is worked out from the counted value and the thresholds,
// so a timer goes amber and red on its own between messages. A message that is not heard for a while
// (`kPpTimersStaleMs`) is no longer believed: the digits go grey and say so.
//
// The panel's menu offers Lyrics and Timers from Relay's `targets` message (`pp_offers_parse`):
//   "propresenter":{"lyrics":true,"timers":true}

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "caption_format.h"

// ---------------------------------------------------------------------------
// What Relay offers
// ---------------------------------------------------------------------------

struct pp_offers {
    bool lyrics = false;
    bool timers = false;
};

// Read the `propresenter` object of a `targets` message. Neither is on offer when it is not there (an older Relay,
// or ProPresenter not set up).
inline pp_offers pp_offers_parse(const char *json, size_t len)
{
    pp_offers out;
    const char *e = json + len;
    const char *obj = caption_json::get(json, e, "propresenter");
    if (obj == nullptr) {
        return out;
    }
    bool b = false;
    const char *v = caption_json::get(obj, e, "lyrics");
    out.lyrics = v != nullptr && caption_json::read_bool(v, e, &b) && b;
    v = caption_json::get(obj, e, "timers");
    out.timers = v != nullptr && caption_json::read_bool(v, e, &b) && b;
    return out;
}

// A signed decimal ("298.0", "-3.5", "12") in tenths, rounded toward zero. Not an exponent; not a string.
inline bool pp_read_tenths(const char *p, const char *e, int32_t *out)
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
    int64_t whole = 0;
    while (p < e && *p >= '0' && *p <= '9') {
        whole = whole * 10 + (*p - '0');
        if (whole > 1000000) {   // far past any timer: not a number to trust
            return false;
        }
        ++p;
    }
    int tenth = 0;
    if (p < e && *p == '.') {
        ++p;
        if (p < e && *p >= '0' && *p <= '9') {
            tenth = *p - '0';
        }
        while (p < e && *p >= '0' && *p <= '9') {
            ++p;
        }
    }
    if (p < e && (*p == 'e' || *p == 'E')) {
        return false;
    }
    const int64_t v = whole * 10 + tenth;
    *out = static_cast<int32_t>(neg ? -v : v);
    return true;
}

// ---------------------------------------------------------------------------
// Lyrics
// ---------------------------------------------------------------------------

constexpr size_t kLyricsTextBytes = 400;    // the slide's lines, folded, one per line
constexpr size_t kLyricsNextBytes = 160;
constexpr size_t kLyricsTitleBytes = 64;
constexpr size_t kLyricsPartBytes = 32;
constexpr size_t kLyricsRawBytes = 480;     // a slide's lines as written on the wire, before folding

struct lyrics_state {
    bool known = false;     // a message has been read
    bool enabled = false;   // the operator has the lyrics on for panels
    bool live = false;      // a song (or a presentation, when Relay is not holding to songs) is up
    char title[kLyricsTitleBytes] = {};
    char part[kLyricsPartBytes] = {};
    char text[kLyricsTextBytes] = {};   // lines separated by '\n'
    char next[kLyricsNextBytes] = {};
    uint32_t changes = 0;   // counts every message that changed what the pane shows
};

enum class lyrics_change : uint8_t { ignored, state, same, malformed };

// Fold a slide's lines for the panel's font: each line on its own (caption_fold would join them), empty lines
// dropped, '\n' between. Cut with "..." where it does not fit `cap`. Returns the length.
inline size_t lyrics_fold_lines(const char *in, size_t len, char *out, size_t cap)
{
    if (cap == 0) {
        return 0;
    }
    size_t o = 0;
    bool cut = false;
    size_t start = 0;
    while (start <= len && !cut) {
        size_t end = start;
        while (end < len && in[end] != '\n' && in[end] != '\r') {
            ++end;
        }
        char line[kLyricsRawBytes];
        const size_t n = caption_fold(in + start, end - start, line, sizeof line);
        if (n > 0) {
            const size_t need = n + (o > 0 ? 1 : 0);
            if (o + need + 1 > cap) {
                cut = true;
            } else {
                if (o > 0) {
                    out[o++] = '\n';
                }
                memcpy(out + o, line, n);
                o += n;
            }
        }
        start = end + 1;
    }
    if (cut) {
        // Back off to a character boundary with room for "...".
        while (o > 0 && o + 4 > cap) {
            --o;
            while (o > 0 && (static_cast<unsigned char>(out[o]) & 0xC0) == 0x80) {
                --o;
            }
        }
        while (o > 0 && (out[o - 1] == ' ' || out[o - 1] == '\n')) {
            --o;
        }
        if (o + 4 <= cap) {
            memcpy(out + o, "...", 3);
            o += 3;
        }
    }
    out[o] = '\0';
    return o;
}

// Whether the folded text is plain ASCII, which LVGL's own large Montserrat draws. Anything else is an accent
// (Spanish and French lyrics) and needs the caption font.
inline bool pp_ascii_only(const char *s)
{
    for (; *s != '\0'; ++s) {
        if (static_cast<unsigned char>(*s) >= 0x80) {
            return false;
        }
    }
    return true;
}

inline void lyrics_clear(lyrics_state *s)
{
    s->known = false;
    s->enabled = false;
    s->live = false;
    s->title[0] = s->part[0] = s->text[0] = s->next[0] = '\0';
}

namespace pp_detail {

inline bool read_text(const char *obj, const char *e, const char *key, char *out, size_t cap)
{
    out[0] = '\0';
    char raw[160];
    const char *v = caption_json::get(obj, e, key);
    size_t n = 0;
    if (v == nullptr || !caption_json::read_string(v, e, raw, sizeof raw, &n)) {
        return false;
    }
    caption_fold(raw, strnlen(raw, sizeof raw), out, cap);
    return true;
}

}  // namespace pp_detail

// Read one message of the lyrics feed into `*s`, in place (a lyrics_state is about 700 bytes: it is neither
// returned nor built whole on a task's stack). A message that is not `lyrics` is ignored; one that is not JSON the
// panel reads leaves the state as it was. `state` when what the pane shows changed, `same` when the message says
// what the glass already says (a heartbeat, a new `rev`).
inline lyrics_change lyrics_apply(lyrics_state *s, const char *json, size_t len)
{
    const char *e = json + len;
    const char *p = caption_json::ws(json, e);
    if (p == nullptr || p >= e || *p != '{' || caption_json::skip_value(p, e) == nullptr) {
        return lyrics_change::malformed;
    }
    char type[16];
    size_t n = 0;
    const char *t = caption_json::get(p, e, "type");
    if (t == nullptr || !caption_json::read_string(t, e, type, sizeof type, &n) || strcmp(type, "lyrics") != 0) {
        return lyrics_change::ignored;
    }
    bool enabled = false;
    bool live = false;
    const char *v = caption_json::get(p, e, "enabled");
    if (v == nullptr || !caption_json::read_bool(v, e, &enabled)) {
        return lyrics_change::malformed;
    }
    v = caption_json::get(p, e, "live");
    if (v != nullptr) {
        caption_json::read_bool(v, e, &live);
    }
    live = live && enabled;

    // The slide's text is read into the state only if it differs, so a repeat costs a compare and no write.
    char title[kLyricsTitleBytes] = {};
    char part[kLyricsPartBytes] = {};
    char next[kLyricsNextBytes] = {};
    char text[kLyricsTextBytes] = {};
    if (live) {
        pp_detail::read_text(p, e, "title", title, sizeof title);
        pp_detail::read_text(p, e, "part", part, sizeof part);
        const char *tv = caption_json::get(p, e, "text");
        char raw[kLyricsRawBytes];
        if (tv != nullptr && caption_json::read_string(tv, e, raw, sizeof raw, &n)) {
            lyrics_fold_lines(raw, strnlen(raw, sizeof raw), text, sizeof text);
        }
        const char *nv = caption_json::get(p, e, "next");
        if (nv != nullptr && caption_json::read_string(nv, e, raw, sizeof raw, &n)) {
            // The next slide as one dim line: its lines run together (caption_fold turns a newline into a space).
            caption_fold(raw, strnlen(raw, sizeof raw), next, sizeof next);
        }
    }
    const bool same = s->known && s->enabled == enabled && s->live == live && strcmp(s->title, title) == 0 &&
                      strcmp(s->part, part) == 0 && strcmp(s->text, text) == 0 && strcmp(s->next, next) == 0;
    if (same) {
        return lyrics_change::same;
    }
    s->known = true;
    s->enabled = enabled;
    s->live = live;
    memcpy(s->title, title, sizeof s->title);
    memcpy(s->part, part, sizeof s->part);
    memcpy(s->text, text, sizeof s->text);
    memcpy(s->next, next, sizeof s->next);
    ++s->changes;
    return lyrics_change::state;
}

// What the pane says in place of lyrics, or null when it has lyrics to show. Quiet by design: a blank slide, no
// presentation, or ProPresenter being down is an empty screen with a few dim words, never the last slide's text.
// `refused` is Relay answering the feed with an error (it is off for panels, or ProPresenter is not set up);
// `relay_down` is no connection to Relay.
inline const char *lyrics_idle_text(const lyrics_state &s, bool refused, bool relay_down)
{
    if (refused) {
        return "Relay is not offering lyrics.\nSwitch them on for panels under ProPresenter in Relay admin.";
    }
    if (relay_down) {
        return "Relay is not running";
    }
    if (!s.known) {
        return "Connecting to Relay";
    }
    if (!s.enabled) {
        return "Lyrics are switched off for panels";
    }
    if (!s.live) {
        return "No song on screen";
    }
    if (s.text[0] == '\0') {
        return "";   // a blank slide: nothing, no words
    }
    return nullptr;
}

// ---- fitting the lyrics to the pane ----------------------------------------------------------------------------

// The three sizes lyrics are drawn at, largest first: LVGL's Montserrat 48 and the UI's large tier (ASCII only),
// and the caption font (which draws accents). `ascii` is whether the text is plain ASCII.
constexpr int kLyricsTiers = 3;

// How many rows `text` takes in a column `width_px` wide at a font of `px`, with the average letter taken as 0.58 of
// the font size and a tenth extra for words that do not break where they would fit.
inline int pp_wrapped_rows(const char *text, int px, int width_px)
{
    const int per_row = static_cast<int>(width_px / (px * 0.58 * 1.1));
    if (per_row < 1) {
        return 1000;
    }
    int rows = 0;
    size_t start = 0;
    const size_t len = strlen(text);
    while (start <= len) {
        size_t end = start;
        while (end < len && text[end] != '\n') {
            ++end;
        }
        const int n = static_cast<int>(end - start);
        rows += n == 0 ? 1 : (n + per_row - 1) / per_row;
        start = end + 1;
    }
    return rows;
}

// The tier for `text` in a `w` x `h` area: 0 is the largest, kLyricsTiers - 1 the smallest. `px` holds each tier's
// font size; a tier that cannot draw this text (non-ASCII with a Montserrat) is skipped. The largest that fits wins,
// and the smallest allowed is used when none does (the label then wraps and is clipped, not shrunk).
inline int lyrics_pick_tier(const char *text, bool ascii, const int px[kLyricsTiers], int w, int h)
{
    const int first = ascii ? 0 : kLyricsTiers - 1;
    for (int t = first; t < kLyricsTiers; ++t) {
        const int rows = pp_wrapped_rows(text, px[t], w);
        if (rows * px[t] * 1.25 <= h) {
            return t;
        }
    }
    return kLyricsTiers - 1;
}

// ---------------------------------------------------------------------------
// Timers
// ---------------------------------------------------------------------------

constexpr int kPpTimersMax = 4;
constexpr size_t kPpTimerIdBytes = 40;
constexpr size_t kPpTimerNameBytes = 52;
constexpr uint32_t kPpTimersStaleMs = 40 * 1000;   // Relay repeats the state every 15 s

enum class pp_timer_state : uint8_t { stopped, running, complete, overrunning, overran };

struct pp_timer {
    char id[kPpTimerIdBytes] = {};
    char name[kPpTimerNameBytes] = {};
    pp_timer_state state = pp_timer_state::stopped;
    bool down = true;         // a countdown (or to a time of day); false is elapsed time, counting up
    int32_t value_ds = 0;     // tenths of a second, as of `timers_state::at_ms`; negative while overrun
};

struct timers_state {
    bool known = false;
    bool enabled = false;
    bool live = false;
    int amber_s = 120;
    int red_s = 30;
    int count = 0;
    pp_timer timers[kPpTimersMax];
    uint32_t at_ms = 0;       // the panel's clock when the last message arrived, which the values count on from
    uint32_t changes = 0;     // counts every message that changed what the pane's rows are made of
};

enum class timers_change : uint8_t { ignored, state, same, malformed };

inline pp_timer_state pp_timer_state_from(const char *s)
{
    if (strcmp(s, "running") == 0) {
        return pp_timer_state::running;
    }
    if (strcmp(s, "complete") == 0) {
        return pp_timer_state::complete;
    }
    if (strcmp(s, "overrunning") == 0) {
        return pp_timer_state::overrunning;
    }
    if (strcmp(s, "overran") == 0) {
        return pp_timer_state::overran;
    }
    return pp_timer_state::stopped;
}

inline bool pp_timer_counting(pp_timer_state s)
{
    return s == pp_timer_state::running || s == pp_timer_state::overrunning;
}

inline void timers_clear(timers_state *s)
{
    s->known = false;
    s->enabled = false;
    s->live = false;
    s->count = 0;
}

// Read one message of the timers feed into `*s` in place. `now_ms` is the panel's clock: the values count on from
// it. Every message re-anchors the count (a timer someone changed in ProPresenter is corrected by the next one),
// but `state` is returned, and `changes` moves, only when the rows themselves are different: a timer, its name,
// its state or direction, the thresholds, or a value that is not counting. The rows are not remade for a repeat.
inline timers_change timers_apply(timers_state *s, const char *json, size_t len, uint32_t now_ms)
{
    const char *e = json + len;
    const char *p = caption_json::ws(json, e);
    if (p == nullptr || p >= e || *p != '{' || caption_json::skip_value(p, e) == nullptr) {
        return timers_change::malformed;
    }
    char type[16];
    size_t n = 0;
    const char *t = caption_json::get(p, e, "type");
    if (t == nullptr || !caption_json::read_string(t, e, type, sizeof type, &n) || strcmp(type, "pptimers") != 0) {
        return timers_change::ignored;
    }
    bool enabled = false;
    bool live = false;
    const char *v = caption_json::get(p, e, "enabled");
    if (v == nullptr || !caption_json::read_bool(v, e, &enabled)) {
        return timers_change::malformed;
    }
    v = caption_json::get(p, e, "live");
    if (v != nullptr) {
        caption_json::read_bool(v, e, &live);
    }
    live = live && enabled;
    uint64_t amber = 120;
    uint64_t red = 30;
    v = caption_json::get(p, e, "amber_s");
    if (v != nullptr) {
        caption_json::read_uint(v, e, &amber);
    }
    v = caption_json::get(p, e, "red_s");
    if (v != nullptr) {
        caption_json::read_uint(v, e, &red);
    }
    if (amber > 86399) {
        amber = 86399;
    }
    if (red > amber) {
        red = amber;
    }

    bool changed = !s->known || s->enabled != enabled || s->live != live || s->amber_s != static_cast<int>(amber) ||
                   s->red_s != static_cast<int>(red);
    int count = 0;
    pp_timer rows[kPpTimersMax];
    caption_json::array_iter it;
    const char *list = caption_json::get(p, e, "timers");
    if (live && list != nullptr && caption_json::array_begin(list, e, &it)) {
        while (count < kPpTimersMax) {
            const char *item = caption_json::array_next(&it);
            if (item == nullptr) {
                break;
            }
            pp_timer &r = rows[count];
            char state[16] = {};
            char dir[8] = {};
            char name[160] = {};
            const char *iv = caption_json::get(item, e, "id");
            size_t id_len = 0;
            if (iv == nullptr || !caption_json::read_string(iv, e, r.id, sizeof r.id, &id_len) || id_len == 0 ||
                id_len >= sizeof r.id) {
                continue;
            }
            iv = caption_json::get(item, e, "name");
            if (iv != nullptr && caption_json::read_string(iv, e, name, sizeof name, &n)) {
                caption_fold(name, strnlen(name, sizeof name), r.name, sizeof r.name);
            }
            iv = caption_json::get(item, e, "state");
            if (iv != nullptr) {
                caption_json::read_string(iv, e, state, sizeof state, &n);
            }
            r.state = pp_timer_state_from(state);
            iv = caption_json::get(item, e, "dir");
            if (iv != nullptr) {
                caption_json::read_string(iv, e, dir, sizeof dir, &n);
            }
            r.down = strcmp(dir, "up") != 0;
            iv = caption_json::get(item, e, "value");
            if (iv == nullptr || !pp_read_tenths(iv, e, &r.value_ds)) {
                continue;   // a timer with no time is not drawn
            }
            ++count;
        }
    }
    if (count != s->count) {
        changed = true;
    }
    for (int i = 0; i < count && !changed; ++i) {
        const pp_timer &a = s->timers[i];
        const pp_timer &b = rows[i];
        changed = strcmp(a.id, b.id) != 0 || strcmp(a.name, b.name) != 0 || a.state != b.state || a.down != b.down ||
                  (!pp_timer_counting(b.state) && a.value_ds != b.value_ds);
    }
    s->known = true;
    s->enabled = enabled;
    s->live = live;
    s->amber_s = static_cast<int>(amber);
    s->red_s = static_cast<int>(red);
    s->count = count;
    for (int i = 0; i < count; ++i) {
        s->timers[i] = rows[i];
    }
    s->at_ms = now_ms;
    if (changed) {
        ++s->changes;
        return timers_change::state;
    }
    return timers_change::same;
}

// ---- counting -----------------------------------------------------------------------------------------------------

// The seconds a timer shows `now_ms`, counted from where the message left it: a running countdown goes down, a
// running elapsed timer goes up, anything else stays. Whole seconds, toward zero, so a countdown never shows more
// time than there is and an overrun never shows less. A clock that has gone backwards counts as no time.
inline int32_t pp_timer_seconds(const pp_timer &t, uint32_t at_ms, uint32_t now_ms)
{
    int64_t ds = t.value_ds;
    if (pp_timer_counting(t.state)) {
        const int64_t since_ds = static_cast<int32_t>(now_ms - at_ms) > 0 ? (now_ms - at_ms) / 100 : 0;
        ds += t.down ? -since_ds : since_ds;
    }
    return static_cast<int32_t>(ds / 10);   // C++ truncates toward zero
}

// "5:00", "12:34", "1:02:03", "-0:05": minutes and seconds under an hour, the minus sign while overrun, never more than
// "9:59:59". `-0:00`
// is never shown: a countdown that has just gone past zero with less than a second over shows "0:00".
inline size_t pp_timer_format(int32_t secs, char *out, size_t cap)
{
    if (cap == 0) {
        return 0;
    }
    const bool neg = secs < 0;
    uint32_t a = static_cast<uint32_t>(neg ? -static_cast<int64_t>(secs) : secs);
    if (a > 9 * 3600 + 59 * 60 + 59) {
        a = 9 * 3600 + 59 * 60 + 59;   // "-9:59:59" is the longest text, the eight cells of the timers view
    }
    const uint32_t h = a / 3600;
    const uint32_t m = (a / 60) % 60;
    const uint32_t s = a % 60;
    int n;
    if (h > 0) {
        n = snprintf(out, cap, "%s%u:%02u:%02u", neg ? "-" : "", static_cast<unsigned>(h), static_cast<unsigned>(m),
                     static_cast<unsigned>(s));
    } else {
        n = snprintf(out, cap, "%s%u:%02u", neg ? "-" : "", static_cast<unsigned>(m), static_cast<unsigned>(s));
    }
    return n > 0 ? (static_cast<size_t>(n) < cap ? static_cast<size_t>(n) : cap - 1) : 0;
}

enum class pp_level : uint8_t { ok, amber, red, over };

// The colour state of a timer showing `secs`. A countdown is amber at `amber_s` seconds left or fewer, red at
// `red_s` or fewer, and over once it has run past zero (or is overrunning). A countdown that finished without
// overrunning (`complete`) is red. An elapsed timer counts up and is never anything but ok. The thresholds are
// inclusive, so "amber at 2:00" means the digits show amber from 2:00.
inline pp_level pp_timer_level(const pp_timer &t, int32_t secs, int amber_s, int red_s)
{
    if (!t.down) {
        return pp_level::ok;
    }
    if (t.state == pp_timer_state::overrunning || t.state == pp_timer_state::overran || secs < 0) {
        return pp_level::over;
    }
    if (t.state == pp_timer_state::complete || secs <= red_s) {
        return pp_level::red;
    }
    return secs <= amber_s ? pp_level::amber : pp_level::ok;
}

// The digits' colour as 0xRRGGBB. A timer that is not running and is not in a warning is dim, so a screen of
// stopped timers is quiet and a running one stands out. `stale` (Relay has not been heard from) greys everything.
inline uint32_t pp_level_hex(pp_level level, bool running, bool stale)
{
    if (stale) {
        return 0x6A6A6A;
    }
    switch (level) {
    case pp_level::red:
    case pp_level::over: return 0xF0524F;
    case pp_level::amber: return 0xF5B342;
    case pp_level::ok: break;
    }
    return running ? 0xFFFFFF : 0x9A9A9A;
}

inline const char *pp_timer_state_words(pp_timer_state s, bool down)
{
    switch (s) {
    case pp_timer_state::running: return down ? "Running" : "Counting up";
    case pp_timer_state::complete: return "Finished";
    case pp_timer_state::overrunning: return "Overrunning";
    case pp_timer_state::overran: return "Overran";
    case pp_timer_state::stopped: break;
    }
    return "Stopped";
}

// Whether the last message is too old to count on: Relay repeats the state every 15 s, so 40 s of nothing is a
// connection that has died (the client reopens it after 45 s of silence).
inline bool pp_timers_stale(uint32_t at_ms, uint32_t now_ms)
{
    return static_cast<int32_t>(now_ms - at_ms) > static_cast<int32_t>(kPpTimersStaleMs);
}

// What the pane says in place of timers, or null when it has rows. As with the lyrics: a few dim words.
inline const char *timers_idle_text(const timers_state &s, bool refused, bool relay_down)
{
    if (refused) {
        return "Relay is not offering timers.\nSet up ProPresenter in Relay admin.";
    }
    if (relay_down) {
        return "Relay is not running";
    }
    if (!s.known) {
        return "Connecting to Relay";
    }
    if (!s.enabled) {
        return "ProPresenter is not set up in Relay";
    }
    if (!s.live) {
        return "ProPresenter is not answering";
    }
    if (s.count == 0) {
        return "No timers to show";
    }
    return nullptr;
}

// ---- the digits -----------------------------------------------------------------------------------------------------

// The digit sizes, largest first: the two generated fonts (`main/fonts/relay_digits_*.c`, tools/gen_digits_font.sh) and
// LVGL's Montserrat 48. `line` is the font's line height; the widest digit, the colon and the minus sign are each
// given a fixed cell (the glyphs are proportional, and a ticking digit must not move its neighbours).
struct pp_digit_font {
    int px;
    int line;
    int digit_w;
    int colon_w;
    int minus_w;
};

constexpr int kPpDigitFonts = 3;
constexpr pp_digit_font kPpDigitFont[kPpDigitFonts] = {
    {140, 102, 94, 32, 54},
    {80, 58, 54, 19, 31},
    {48, 52, 34, 13, 19},
};

// The width of `text` ("12:34", "-0:05") laid out in cells.
inline int pp_timer_width(const char *text, const pp_digit_font &f)
{
    int w = 0;
    for (const char *c = text; *c != '\0'; ++c) {
        w += *c == ':' ? f.colon_w : *c == '-' ? f.minus_w : f.digit_w;
    }
    return w;
}

// The longest text a pane's timers are laid out for: "-00:00" (an overrun under an hour), or "-0:00:00" when any
// timer is an hour or more. The font is chosen for this once, when the rows are made, so it never changes while a
// timer counts; the cells (below) are fixed width, so the digits do not move either.
inline const char *pp_timers_sample(const timers_state &s)
{
    for (int i = 0; i < s.count; ++i) {
        const int32_t ds = s.timers[i].value_ds;
        if (ds >= 36000 || ds <= -36000) {
            return "-0:00:00";
        }
    }
    return "-00:00";
}

// The largest digit font for `count` timers in a `w` x `h` area whose widest text is `sample`. Each timer gets an equal
// row, of which `name_h` goes to its name; the digits have the rest. Returns an index into kPpDigitFont, the smallest
// when nothing fits.
inline int pp_digit_pick(int count, const char *sample, int w, int h, int name_h)
{
    if (count < 1) {
        count = 1;
    }
    const int row = h / count;
    for (int i = 0; i < kPpDigitFonts; ++i) {
        const pp_digit_font &f = kPpDigitFont[i];
        if (f.line + name_h <= row && pp_timer_width(sample, f) <= w) {
            return i;
        }
    }
    return kPpDigitFonts - 1;
}

// Where each character of `text` sits in a row `w` wide, centred: its left edge and its cell's width. Returns the
// number of characters placed.
inline int pp_timer_cells(const char *text, const pp_digit_font &f, int w, int *x, int *cell_w, int cap)
{
    int total = pp_timer_width(text, f);
    int at = (w - total) / 2;
    if (at < 0) {
        at = 0;
    }
    int n = 0;
    for (const char *c = text; *c != '\0' && n < cap; ++c, ++n) {
        const int cw = *c == ':' ? f.colon_w : *c == '-' ? f.minus_w : f.digit_w;
        x[n] = at;
        cell_w[n] = cw;
        at += cw;
    }
    return n;
}
