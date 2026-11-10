#pragma once

// The Planning Center service on the panel (#82), with no LVGL or ESP-IDF in it, so `test_native.cpp` checks it on a
// laptop. `captions.cpp` holds the state for a caption slot pointed at the Service feed; `service_view.cpp` draws it.
//
// **What goes over the wire**, plain HTTP on the viewer port like the captions (app/servicefeed.py):
//
//   GET /stream?stream=service&type=<service type id>
//   -> data: {"type":"service","service_type":"7","followed":true,"name":"Sunday AM",
//             "plan":{"id":"700","title":"..","series_title":"..","dates":".."},
//             "items":[{"id":"1","title":"..","type":"song","length":240,"position":"during",
//                       "key":"G","arrangement":"..","sequence":["Verse 1","Chorus 1"],
//                       "notes":[{"category":"Vocals","content":".."}]}, ..],
//             "notes":false,"current_item":"3","next_item":"4","item_started_at":..,"item_length_s":240,
//             "item_left_s":12.5,"service_started_at":..,"service_over_s":30.0,
//             "live":true,"controller":"Dana Ortiz","reachable":true,"updated_at":..,"server_time":..}
//   -> data: {"type":"service_tick","service_type":"7","server_time":..,"updated_at":..}   every 15 s
//
// **Each `service` message is the whole state**: it replaces what the panel held. Timers are sent as they stood at
// `server_time`, and the panel counts them down itself from the moment it read them:
//     item_left(t) = item_left_s - t
//     over(t)      = service_over_s + max(0, t - max(0, item_left_s))
// with t the seconds since the message arrived. The service only falls further behind once the current item has run
// past its length.
//
// The panel's **service mode** comes from Relay too: in a check-in reply's `service`, and as a `service_mode` event on
// the panel's own stream. Off (the default) offers no Service feed in the menu.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

#include "caption_format.h"

// ---------------------------------------------------------------------------
// Text for LVGL's own Montserrat fonts
// ---------------------------------------------------------------------------

// **Only the caption font draws accents.** The item titles use it; the smaller text (the key and sequence, the
// footer, the header) is drawn with LVGL's Montserrat, which holds ASCII alone, so it is folded to it here: an
// accented Latin letter loses its accent ("José" reads "Jose"), anything else becomes "?". `in` is already through
// caption_fold.
inline size_t service_ascii(const char *in, char *out, size_t cap)
{
    // U+00C0..U+00FF without their accents; '?' where there is no plain letter.
    static const char kLatin1[] = "AAAAAAACEEEEIIIIDNOOOOO?OUUUUYPsaaaaaaaceeeeiiiidnooooo?ouuuuypy";
    if (cap == 0) {
        return 0;
    }
    size_t o = 0;
    const auto *p = reinterpret_cast<const unsigned char *>(in);
    const auto *end = p + strlen(in);
    while (p < end && o + 1 < cap) {
        size_t used = 0;
        const int32_t cp = caption_utf8_next(p, end, &used);
        if (used == 0) {
            break;
        }
        p += used;
        char c = '?';
        if (cp >= 0x20 && cp < 0x7F) {
            c = static_cast<char>(cp);
        } else if (cp >= 0xC0 && cp <= 0xFF) {
            c = kLatin1[cp - 0xC0];
        } else if (cp >= 0x100 && cp <= 0x17F) {
            // Latin Extended-A comes in upper/lower pairs of one base letter, in this order.
            static const char kExtA[] = "AaAaAaCcCcCcCcDdDdEeEeEeEeEeGgGgGgGgHhHhIiIiIiIiIiJjJjKkkLlLlLlLlLlNnNnNnnNnOoOoOoOoRrRrRrSsSsSsSsTtTtTtUuUuUuUuUuUuWwYyYZzZzZzs";
            c = kExtA[cp - 0x100];
        } else if (cp == 0x2018 || cp == 0x2019) {
            c = '\'';
        } else if (cp == 0x201C || cp == 0x201D) {
            c = '"';
        } else if (cp == 0x2013 || cp == 0x2014) {
            c = '-';
        }
        out[o++] = c;
    }
    out[o] = '\0';
    return o;
}

// ---------------------------------------------------------------------------
// The panel's service mode
// ---------------------------------------------------------------------------

enum class service_mode : uint8_t { off, show, control, both };

constexpr size_t kServiceTypeBytes = 21;   // a Planning Center id: up to 20 digits
constexpr size_t kServiceKeyBytes = 33;    // 32 hex digits

struct panel_service {
    service_mode mode = service_mode::off;
    char type[kServiceTypeBytes] = {};     // the service type the operator set, "" for the panel's own pick
    char key[kServiceKeyBytes] = {};       // the control key, "" when the panel may not control
};

inline bool service_mode_shows(service_mode m)
{
    return m == service_mode::show || m == service_mode::both;
}

inline bool service_mode_controls(service_mode m)
{
    return m == service_mode::control || m == service_mode::both;
}

inline bool service_id_ok(const char *s)
{
    size_t n = 0;
    for (; s[n] != '\0'; ++n) {
        if (s[n] < '0' || s[n] > '9' || n >= kServiceTypeBytes - 1) {
            return false;
        }
    }
    return n > 0;
}

// Read a `service` object ({"mode":"both","service_type":"7"|null,"key":".."}). False, and `*out` untouched, for
// anything that is not one: a mode the panel does not know reads as nothing changed, not as off.
inline bool panel_service_parse(const char *obj, const char *e, panel_service *out)
{
    panel_service got;
    char mode[12] = {};
    size_t n = 0;
    const char *m = caption_json::get(obj, e, "mode");
    if (m == nullptr || !caption_json::read_string(m, e, mode, sizeof mode, &n)) {
        return false;
    }
    if (strcmp(mode, "off") == 0) {
        got.mode = service_mode::off;
    } else if (strcmp(mode, "show") == 0) {
        got.mode = service_mode::show;
    } else if (strcmp(mode, "control") == 0) {
        got.mode = service_mode::control;
    } else if (strcmp(mode, "both") == 0) {
        got.mode = service_mode::both;
    } else {
        return false;
    }
    const char *t = caption_json::get(obj, e, "service_type");
    if (t != nullptr && caption_json::read_string(t, e, got.type, sizeof got.type, &n) && !service_id_ok(got.type)) {
        got.type[0] = '\0';
    }
    const char *k = caption_json::get(obj, e, "key");
    if (k != nullptr && service_mode_controls(got.mode)) {
        if (!caption_json::read_string(k, e, got.key, sizeof got.key, &n) || n != kServiceKeyBytes - 1) {
            got.key[0] = '\0';
        }
    }
    *out = got;
    return true;
}

inline bool panel_service_same(const panel_service &a, const panel_service &b)
{
    return a.mode == b.mode && strcmp(a.type, b.type) == 0 && strcmp(a.key, b.key) == 0;
}

// ---------------------------------------------------------------------------
// The service feeds Relay offers (the `service` array of `targets`)
// ---------------------------------------------------------------------------

constexpr int kServiceOffersMax = 8;
constexpr size_t kServiceLabelBytes = 32;

struct service_offer {
    char type[kServiceTypeBytes] = {};
    char label[kServiceLabelBytes] = {};
};

struct service_offers {
    int count = 0;
    service_offer items[kServiceOffersMax];
};

// `{"service":[{"service_type":"7","label":"Sunday AM"},..]}`. A message with no `service` array offers none.
inline service_offers service_offers_parse(const char *json, size_t len)
{
    service_offers out;
    const char *e = json + len;
    caption_json::array_iter it;
    const char *arr = caption_json::get(json, e, "service");
    if (arr == nullptr || !caption_json::array_begin(arr, e, &it)) {
        return out;
    }
    while (out.count < kServiceOffersMax) {
        const char *item = caption_json::array_next(&it);
        if (item == nullptr) {
            break;
        }
        service_offer o;
        size_t n = 0;
        char raw[96] = {};
        const char *t = caption_json::get(item, e, "service_type");
        if (t == nullptr || !caption_json::read_string(t, e, o.type, sizeof o.type, &n) || !service_id_ok(o.type)) {
            continue;
        }
        const char *l = caption_json::get(item, e, "label");
        if (l != nullptr && caption_json::read_string(l, e, raw, sizeof raw, &n)) {
            caption_fold(raw, strlen(raw), o.label, sizeof o.label);
        }
        if (o.label[0] == '\0') {
            snprintf(o.label, sizeof o.label, "Service");
        }
        out.items[out.count++] = o;
    }
    return out;
}

// ---------------------------------------------------------------------------
// The service itself
// ---------------------------------------------------------------------------

constexpr int kServiceItemsMax = 48;
constexpr size_t kServiceTitleBytes = 72;
constexpr size_t kServiceKeyNameBytes = 8;
constexpr size_t kServiceSequenceBytes = 72;
constexpr size_t kServiceNotesBytes = 240;
constexpr size_t kServiceNameBytes = 48;
constexpr size_t kServiceIdBytes = 21;

enum class service_item_type : uint8_t { item, song, header, media };
enum class service_position : uint8_t { pre, during, post };

struct service_item {
    char id[kServiceIdBytes] = {};
    char title[kServiceTitleBytes] = {};
    service_item_type type = service_item_type::item;
    service_position position = service_position::during;
    uint32_t length_s = 0;
    char key[kServiceKeyNameBytes] = {};
    char sequence[kServiceSequenceBytes] = {};   // "Verse 1, Chorus 1, Bridge"
    char notes[kServiceNotesBytes] = {};         // "Vocals: Ask Sam to lead\nBand: ..", "" without notes
};

// What a `service` message said, plus when it arrived.
struct service_state {
    bool known = false;             // a `service` message has been read
    bool followed = false;          // Relay follows this service type
    char name[kServiceNameBytes] = {};
    bool has_plan = false;
    char plan_title[kServiceTitleBytes] = {};
    char plan_dates[kServiceNameBytes] = {};
    int count = 0;                  // items read (at most kServiceItemsMax)
    int total = 0;                  // items Relay sent
    service_item items[kServiceItemsMax];
    int current = -1;               // index into `items`, or -1
    int next = -1;
    bool timing = false;            // item_left_s was sent: an item is running
    double item_left_s = 0;
    uint32_t item_length_s = 0;
    bool has_over = false;
    double service_over_s = 0;
    bool live = false;
    bool relay_controls = false;    // the controller is Relay's own Planning Center person: Release, not Take control
    char controller[kServiceNameBytes] = {};
    bool reachable = true;
    bool notes_allowed = false;
    double server_time = 0;
    double updated_at = 0;          // 0: never
    uint32_t received_ms = 0;       // the panel's clock when the message (or the last tick) was read
    uint32_t changes = 0;           // counts `service` messages applied
};

namespace service_detail {

inline void folded(const char *obj, const char *e, const char *key, char *out, size_t cap)
{
    out[0] = '\0';
    const char *v = caption_json::get(obj, e, key);
    if (v == nullptr) {
        return;
    }
    char raw[kServiceNotesBytes * 2] = {};
    size_t n = 0;
    if (caption_json::read_string(v, e, raw, sizeof raw, &n)) {
        caption_fold(raw, strlen(raw), out, cap);
    }
}

inline bool number(const char *obj, const char *e, const char *key, double *out)
{
    const char *g = caption_json::get(obj, e, key);
    if (g == nullptr) {
        return false;
    }
    const char *v = caption_json::ws(g, e);
    if (v >= e || !(*v == '-' || (*v >= '0' && *v <= '9'))) {
        return false;
    }
    char buf[32];
    size_t n = 0;
    while (v + n < e && n + 1 < sizeof buf &&
           (v[n] == '-' || v[n] == '+' || v[n] == '.' || v[n] == 'e' || v[n] == 'E' || (v[n] >= '0' && v[n] <= '9'))) {
        buf[n] = v[n];
        ++n;
    }
    buf[n] = '\0';
    char *end = nullptr;
    const double d = strtod(buf, &end);
    if (end == buf) {
        return false;
    }
    *out = d;
    return true;
}

inline bool flag(const char *obj, const char *e, const char *key, bool fallback)
{
    bool b = fallback;
    const char *v = caption_json::get(obj, e, key);
    if (v != nullptr) {
        caption_json::read_bool(v, e, &b);
    }
    return b;
}

// Append `s` to `out` (with `sep` before it unless `out` is empty), cut to fit.
inline void join(char *out, size_t cap, const char *sep, const char *s)
{
    size_t o = strlen(out);
    if (o > 0) {
        for (const char *c = sep; *c != '\0' && o + 1 < cap; ++c) {
            out[o++] = *c;
        }
    }
    for (const char *c = s; *c != '\0' && o + 1 < cap; ++c) {
        out[o++] = *c;
    }
    out[o] = '\0';
}

inline void read_item(const char *obj, const char *e, service_item *it)
{
    char id[kServiceIdBytes + 8] = {};
    size_t n = 0;
    const char *v = caption_json::get(obj, e, "id");
    if (v != nullptr && caption_json::read_string(v, e, id, sizeof id, &n) && n < kServiceIdBytes) {
        memcpy(it->id, id, n + 1);
    }
    folded(obj, e, "title", it->title, sizeof it->title);
    char word[12] = {};
    v = caption_json::get(obj, e, "type");
    if (v != nullptr && caption_json::read_string(v, e, word, sizeof word, &n)) {
        it->type = strcmp(word, "song") == 0     ? service_item_type::song
                   : strcmp(word, "header") == 0 ? service_item_type::header
                   : strcmp(word, "media") == 0  ? service_item_type::media
                                                 : service_item_type::item;
    }
    v = caption_json::get(obj, e, "position");
    if (v != nullptr && caption_json::read_string(v, e, word, sizeof word, &n)) {
        it->position = strcmp(word, "pre") == 0    ? service_position::pre
                       : strcmp(word, "post") == 0 ? service_position::post
                                                   : service_position::during;
    }
    double length = 0;
    if (number(obj, e, "length", &length) && length > 0) {
        it->length_s = length > 86400 ? 86400u : static_cast<uint32_t>(length);
    }
    folded(obj, e, "key", it->key, sizeof it->key);
    caption_json::array_iter ai;
    const char *seq = caption_json::get(obj, e, "sequence");
    if (seq != nullptr && caption_json::array_begin(seq, e, &ai)) {
        for (const char *s = caption_json::array_next(&ai); s != nullptr; s = caption_json::array_next(&ai)) {
            char raw[48] = {};
            char part[32] = {};
            if (caption_json::read_string(s, e, raw, sizeof raw, &n)) {
                caption_fold(raw, strlen(raw), part, sizeof part);
                if (part[0] != '\0') {
                    join(it->sequence, sizeof it->sequence, ", ", part);
                }
            }
        }
    }
    const char *notes = caption_json::get(obj, e, "notes");
    if (notes != nullptr && caption_json::array_begin(notes, e, &ai)) {
        for (const char *note = caption_json::array_next(&ai); note != nullptr; note = caption_json::array_next(&ai)) {
            char cat[32] = {};
            char content[kServiceNotesBytes] = {};
            folded(note, e, "category", cat, sizeof cat);
            folded(note, e, "content", content, sizeof content);
            if (content[0] == '\0') {
                continue;
            }
            char line[kServiceNotesBytes] = {};
            join(line, sizeof line, "", cat);
            join(line, sizeof line, ": ", content);
            join(it->notes, sizeof it->notes, "\n", line);
        }
    }
}

inline int index_of(const service_state &s, const char *obj, const char *e, const char *key)
{
    char id[kServiceIdBytes + 8] = {};
    size_t n = 0;
    const char *v = caption_json::get(obj, e, key);
    if (v == nullptr || !caption_json::read_string(v, e, id, sizeof id, &n) || n == 0) {
        return -1;
    }
    for (int i = 0; i < s.count; ++i) {
        if (strcmp(s.items[i].id, id) == 0) {
            return i;
        }
    }
    return -1;
}

}  // namespace service_detail

// Empty `s` as a new one is, without a temporary (see `service_apply`). Every field is plain data.
inline void service_reset(service_state *s)
{
    memset(static_cast<void *>(s), 0, sizeof *s);
    s->current = -1;
    s->next = -1;
    s->reachable = true;
    for (service_item &it : s->items) {
        it.type = service_item_type::item;
        it.position = service_position::during;
    }
}

// What one message from the Service feed did.
enum class service_change : uint8_t { ignored, malformed, state, tick };

// Apply one event's JSON to `s`. `now_ms` is the panel's clock, the start of the countdown. A `service` message
// replaces everything; a `service_tick` only moves the clock reading and `updated_at`.
inline service_change service_apply(service_state *s, const char *json, size_t len, uint32_t now_ms)
{
    namespace d = service_detail;
    const char *e = json + len;
    char type[16] = {};
    size_t n = 0;
    const char *t = caption_json::get(json, e, "type");
    if (t == nullptr || !caption_json::read_string(t, e, type, sizeof type, &n)) {
        return service_change::malformed;
    }
    if (strcmp(type, "service_tick") == 0) {
        double server = 0;
        if (!s->known || !d::number(json, e, "server_time", &server)) {
            return service_change::ignored;
        }
        // The timers are the last message's, counted from when it arrived: a tick only says Relay is there and
        // when Planning Center last answered, so the clock reading is not reset.
        double updated = 0;
        if (d::number(json, e, "updated_at", &updated)) {
            s->updated_at = updated;
        }
        s->server_time = server - (static_cast<double>(now_ms - s->received_ms) / 1000.0);
        return service_change::tick;
    }
    if (strcmp(type, "service") != 0) {
        return service_change::ignored;
    }
    // Read into the state in place, cleared field by field: **a `service_state` is tens of kilobytes, so it is never
    // made on a task's stack** (`*s = service_state()` makes a temporary, which overran the caption task's). A message
    // is whole or not applied (a cut `data:` line never reaches here), so there is no half-read state to fear.
    const uint32_t changes = s->changes;
    service_reset(s);
    s->changes = changes + 1;
    s->known = true;
    s->received_ms = now_ms;
    s->followed = d::flag(json, e, "followed", false);
    d::folded(json, e, "name", s->name, sizeof s->name);
    const char *plan = caption_json::get(json, e, "plan");
    const char *pv = plan != nullptr ? caption_json::ws(plan, e) : nullptr;
    if (pv != nullptr && pv < e && *pv == '{') {
        s->has_plan = true;
        d::folded(plan, e, "title", s->plan_title, sizeof s->plan_title);
        d::folded(plan, e, "dates", s->plan_dates, sizeof s->plan_dates);
        if (s->plan_title[0] == '\0') {
            d::folded(plan, e, "series_title", s->plan_title, sizeof s->plan_title);
        }
    }
    caption_json::array_iter it;
    const char *items = caption_json::get(json, e, "items");
    if (items != nullptr && caption_json::array_begin(items, e, &it)) {
        for (const char *obj = caption_json::array_next(&it); obj != nullptr; obj = caption_json::array_next(&it)) {
            ++s->total;
            if (s->count < kServiceItemsMax) {
                d::read_item(obj, e, &s->items[s->count++]);
            }
        }
    }
    s->current = d::index_of(*s, json, e, "current_item");
    s->next = d::index_of(*s, json, e, "next_item");
    double v = 0;
    if (d::number(json, e, "item_left_s", &v)) {
        s->timing = true;
        s->item_left_s = v;
    }
    if (d::number(json, e, "item_length_s", &v) && v > 0) {
        s->item_length_s = static_cast<uint32_t>(v);
    }
    if (d::number(json, e, "service_over_s", &v)) {
        s->has_over = true;
        s->service_over_s = v;
    }
    s->live = d::flag(json, e, "live", false);
    s->relay_controls = d::flag(json, e, "relay_controls", false);
    d::folded(json, e, "controller", s->controller, sizeof s->controller);
    s->reachable = d::flag(json, e, "reachable", true);
    s->notes_allowed = d::flag(json, e, "notes", false);
    d::number(json, e, "server_time", &s->server_time);
    d::number(json, e, "updated_at", &s->updated_at);
    return service_change::state;
}

// ---------------------------------------------------------------------------
// Timers
// ---------------------------------------------------------------------------

inline double service_elapsed_s(const service_state &s, uint32_t now_ms)
{
    return static_cast<double>(now_ms - s.received_ms) / 1000.0;
}

// The current item's time left at `now_ms` (negative once it has run over).
inline double service_item_left(const service_state &s, uint32_t now_ms)
{
    return s.item_left_s - service_elapsed_s(s, now_ms);
}

// How far behind plan the service is at `now_ms` (negative: ahead).
inline double service_over(const service_state &s, uint32_t now_ms)
{
    const double t = service_elapsed_s(s, now_ms);
    const double grace = s.item_left_s > 0 ? s.item_left_s : 0;
    return s.service_over_s + (t > grace ? t - grace : 0);
}

// Seconds as "4:05" or "1:02:03", rounded toward zero; the sign is the caller's to word.
inline size_t service_clock(double seconds, char *out, size_t cap)
{
    if (seconds < 0) {
        seconds = -seconds;
    }
    const uint32_t s = seconds > 359999 ? 359999u : static_cast<uint32_t>(seconds);
    const unsigned h = s / 3600, m = (s / 60) % 60, sec = s % 60, mins = s / 60;
    const int n = s >= 3600 ? snprintf(out, cap, "%u:%02u:%02u", h, m, sec) : snprintf(out, cap, "%u:%02u", mins, sec);
    return n > 0 ? static_cast<size_t>(n) : 0;
}

// "3:20 left" or "1:05 over" for the current item; "" when nothing is running.
inline size_t service_item_left_words(const service_state &s, uint32_t now_ms, char *out, size_t cap)
{
    out[0] = '\0';
    if (!s.timing || s.current < 0) {
        return 0;
    }
    const double left = service_item_left(s, now_ms);
    char clock[16];
    service_clock(left, clock, sizeof clock);
    const int n = snprintf(out, cap, "%s %s", clock, left < 0 ? "over" : "left");
    return n > 0 ? static_cast<size_t>(n) : 0;
}

// "Service 2:10 behind" / "Service 0:45 ahead" / "Service on time"; "" when the service has not started.
inline size_t service_over_words(const service_state &s, uint32_t now_ms, char *out, size_t cap)
{
    out[0] = '\0';
    if (!s.has_over) {
        return 0;
    }
    const double over = service_over(s, now_ms);
    if (over > -30 && over < 30) {
        return static_cast<size_t>(snprintf(out, cap, "Service on time"));
    }
    char clock[16];
    service_clock(over, clock, sizeof clock);
    const int n = snprintf(out, cap, "Service %s %s", clock, over > 0 ? "behind" : "ahead");
    return n > 0 ? static_cast<size_t>(n) : 0;
}

// An item's own length as "4:00", "" for none.
inline size_t service_length_words(const service_item &it, char *out, size_t cap)
{
    out[0] = '\0';
    if (it.length_s == 0 || it.type == service_item_type::header) {
        return 0;
    }
    return service_clock(it.length_s, out, cap);
}

// The second line of a song's row: "Key G · Verse 1, Chorus 1"; "" for anything else.
inline size_t service_song_words(const service_item &it, char *out, size_t cap)
{
    out[0] = '\0';
    if (it.type != service_item_type::song) {
        return 0;
    }
    if (it.key[0] != '\0') {
        snprintf(out, cap, "Key %s", it.key);
    }
    if (it.sequence[0] != '\0') {
        service_detail::join(out, cap, " - ", it.sequence);
    }
    return strlen(out);
}

// ---------------------------------------------------------------------------
// What the footer says
// ---------------------------------------------------------------------------

enum class service_tone : uint8_t { good, idle, bad };

struct service_status {
    service_tone tone = service_tone::idle;
    char text[96] = {};
};

// The footer's words, worst news first: Relay does not follow it, Planning Center is unreachable, nothing planned
// today, not live, or who is controlling with the over/under.
inline service_status service_footer(const service_state &s, uint32_t now_ms)
{
    service_status st;
    if (!s.known) {
        snprintf(st.text, sizeof st.text, "Waiting for Relay");
        return st;
    }
    if (!s.followed) {
        st.tone = service_tone::bad;
        snprintf(st.text, sizeof st.text, "Relay does not follow this service");
        return st;
    }
    if (!s.reachable) {
        st.tone = service_tone::bad;
        snprintf(st.text, sizeof st.text, "Planning Center unreachable");
        return st;
    }
    if (!s.has_plan) {
        snprintf(st.text, sizeof st.text, "No service planned today");
        return st;
    }
    if (!s.live) {
        snprintf(st.text, sizeof st.text, "Not live");
        return st;
    }
    st.tone = service_tone::good;
    char over[48] = {};
    service_over_words(s, now_ms, over, sizeof over);
    if (s.controller[0] != '\0') {
        char who[kServiceNameBytes + 16] = {};
        service_detail::join(who, sizeof who, "", s.controller);
        service_detail::join(who, sizeof who, "", " is controlling");
        service_detail::join(st.text, sizeof st.text, "", over);
        service_detail::join(st.text, sizeof st.text, " - ", who);
    } else {
        service_detail::join(st.text, sizeof st.text, "", over[0] != '\0' ? over : "Live");
    }
    return st;
}

// The index the order of service should keep in view: the current item, else the next, else the first.
inline int service_focus(const service_state &s)
{
    if (s.current >= 0) {
        return s.current;
    }
    if (s.next >= 0) {
        return s.next;
    }
    return s.count > 0 ? 0 : -1;
}

// ---------------------------------------------------------------------------
// The LIVE buttons (#83)
// ---------------------------------------------------------------------------
//
//   POST /api/panels/<id>/service/<next|previous|take_control|release>
//        {"key":"<the panel's control key>","type":"<service type id>"}
//   -> 200 {"ok":true,"service":{..}}            the new state also arrives on the Service feed
//   -> 4xx/5xx {"ok":false,"error":"not_controlling"}
//
// Relay presses the button in Planning Center as its token's owner (app/pccontrol.py). **One press at a time**: a
// second tap while the first is unanswered is ignored, so a double tap cannot skip two items.

enum class service_action : uint8_t { next, previous, take_control, release };

inline const char *service_action_path(service_action a)
{
    switch (a) {
    case service_action::next: return "next";
    case service_action::previous: return "previous";
    case service_action::take_control: return "take_control";
    case service_action::release: return "release";
    }
    return "next";
}

// The request. Returns its length, or 0 when it does not fit, `id` is not a panel ID, `type` not a service type or
// `key` not a control key.
inline size_t service_action_request(char *out, size_t cap, const char *host, uint16_t port, const char *id,
                                     service_action action, const char *key, const char *type, const char *version)
{
    if (strlen(id) != 32 || strlen(key) != kServiceKeyBytes - 1 || !service_id_ok(type)) {
        return 0;
    }
    for (const char *c = id; *c != '\0'; ++c) {
        if (!((*c >= '0' && *c <= '9') || (*c >= 'a' && *c <= 'f'))) {
            return 0;
        }
    }
    for (const char *c = key; *c != '\0'; ++c) {
        if (!((*c >= '0' && *c <= '9') || (*c >= 'a' && *c <= 'f'))) {
            return 0;
        }
    }
    char body[96];
    const int bn = snprintf(body, sizeof body, "{\"key\":\"%s\",\"type\":\"%s\"}", key, type);
    if (bn <= 0 || static_cast<size_t>(bn) >= sizeof body) {
        return 0;
    }
    char host_header[80];
    if (port == 80) {
        snprintf(host_header, sizeof host_header, "%s", host);
    } else {
        snprintf(host_header, sizeof host_header, "%s:%u", host, static_cast<unsigned>(port));
    }
    const int n = snprintf(out, cap,
                           "POST /api/panels/%s/service/%s HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Content-Type: application/json\r\n"
                           "Content-Length: %d\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n"
                           "%s",
                           id, service_action_path(action), host_header, bn, version, body);
    if (n <= 0 || static_cast<size_t>(n) >= cap) {
        return 0;
    }
    return static_cast<size_t>(n);
}

// How a press went, as the panel words it.
enum class service_result : uint8_t {
    none,             // nothing pressed yet
    sending,          // waiting for Relay
    done,             // Relay pressed it; the change arrives on the feed
    not_allowed,      // this panel may not control, or its key is not Relay's
    too_fast,
    not_controlling,  // Next / Previous before Take control
    no_service,       // no plan today, or a service type Relay does not follow
    unreachable,      // Planning Center did not answer
    no_relay,         // Relay did not answer
    refused,          // anything else
};

// Relay's answer: the HTTP status and the body's `error` code.
inline service_result service_result_from(int status, const char *body, size_t len)
{
    if (status == 0) {
        return service_result::no_relay;
    }
    if (status == 200) {
        return service_result::done;
    }
    char code[24] = {};
    size_t n = 0;
    const char *e = body + len;
    const char *v = caption_json::get(body, e, "error");
    if (v != nullptr) {
        caption_json::read_string(v, e, code, sizeof code, &n);
    }
    if (strcmp(code, "not_allowed") == 0 || strcmp(code, "bad_key") == 0) {
        return service_result::not_allowed;
    }
    if (strcmp(code, "too_fast") == 0) {
        return service_result::too_fast;
    }
    if (strcmp(code, "not_controlling") == 0) {
        return service_result::not_controlling;
    }
    if (strcmp(code, "no_plan") == 0 || strcmp(code, "not_followed") == 0 || strcmp(code, "not_connected") == 0) {
        return service_result::no_service;
    }
    if (strcmp(code, "unreachable") == 0) {
        return service_result::unreachable;
    }
    if (status == 404) {
        return service_result::not_allowed;   // Relay does not know this panel (it restarted): a check-in cures it
    }
    return service_result::refused;
}

// The words under the buttons: "" for none and done (the item changing says it), else what went wrong.
inline const char *service_result_words(service_result r)
{
    switch (r) {
    case service_result::none:
    case service_result::done: return "";
    case service_result::sending: return "Sending...";
    case service_result::not_allowed: return "Not allowed";
    case service_result::too_fast: return "Too fast: try again";
    case service_result::not_controlling: return "Take control first";
    case service_result::no_service: return "No service to control";
    case service_result::unreachable: return "Planning Center unreachable";
    case service_result::no_relay: return "Relay not reachable";
    case service_result::refused: return "Planning Center said no";
    }
    return "";
}

// The confirmation Take control asks for: "Take control from Dana Ortiz?" when someone else has it, "" when nobody
// does (then it is pressed at once). ASCII, for Montserrat.
inline size_t service_take_question(const service_state &s, char *out, size_t cap)
{
    out[0] = '\0';
    if (!s.live || s.relay_controls || s.controller[0] == '\0' || cap == 0) {
        return 0;
    }
    char who[kServiceNameBytes] = {};
    service_ascii(s.controller, who, sizeof who);
    service_detail::join(out, cap, "", "Take control from ");
    service_detail::join(out, cap, "", who);
    service_detail::join(out, cap, "", "?");
    return strlen(out);
}
