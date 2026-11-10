#pragma once

// The panel's side of Relay's panel registry (app/panels.py), with no ESP-IDF in it, so `test_native.cpp`
// checks it on a laptop. `panel_link.cpp` owns the sockets and the tasks.
//
// **What goes over the wire**, plain HTTP on the viewer port, no login (panels do not pair):
//
//   POST /api/panels/checkin          every 30 s: who this panel is and what it shows
//        {"id":"<32 hex>","name":"Panel 1a2b","version":"0.1.0","scripts":["latin"],
//         "feeds":[{"stream":"translation","lang":"SPANISH"}],"ip":"192.168.1.50","uptime_s":42,
//         "rssi":-58,"feed_rev":0}
//   -> {"ok":true,"name":"Lobby","interval_s":30,"set_feed":{"type":"set_feed","rev":3,"feeds":[..]}}
//
//   GET /api/panels/<id>/events       kept open; Relay writes to it the moment the operator acts
//   -> data: {"type":"set_feed","rev":3,"feeds":[{"stream":"transcription"}]}
//   -> data: {"type":"update_available","version":"0.2.0","size":..,"sha256":"..","path":"/api/panels/firmware/0.2.0.bin","now":false}
//   -> : ping                          every 15 s of quiet
//
// **`rev` is how Relay and the panel agree what has been applied.** The operator's feed carries a revision
// that rises with each setting. The panel tells Relay the last one it applied (`feed_rev`), and Relay sends a
// `set_feed` only while the panel has not, so a choice made at the screen afterwards is not undone by the next
// check-in. A `set_feed` the panel has already applied (the same one arrives in a check-in reply and on the
// stream) is ignored.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "caption_format.h"
#include "feed_format.h"
#include "settings_format.h"
#include "update_format.h"

constexpr uint32_t kPanelCheckinMs = 30000;       // between check-ins
constexpr uint32_t kPanelCheckinRetryMs = 8000;   // after one that failed
constexpr size_t kPanelBodyMax = 640;             // what a check-in body can be; Relay refuses over 2048
constexpr size_t kPanelReplyMax = 1024;           // what a check-in reply can be before it is cut
constexpr size_t kPanelNameMax = 32;
constexpr int kPanelFeedsMax = 2;

// ---------------------------------------------------------------------------
// What the panel says about itself
// ---------------------------------------------------------------------------

struct panel_status {
    char id[33];
    char version[16];
    char ip[16];         // dotted quad, empty when unknown
    uint32_t uptime_s;
    bool has_rssi;
    int rssi;            // dBm
    uint32_t feed_rev;   // the last operator feed it applied; 0 when none
    int feed_count;      // 0..2, what is shown now
    feed_pick feeds[kPanelFeedsMax];
};

// The name a panel gives itself until the operator names it in Relay: "Panel" and the first four characters of
// its ID, which is also what Relay shows for a panel that sent none.
inline size_t panel_default_name(const char *id, char *out, size_t cap)
{
    const int n = snprintf(out, cap, "Panel %.4s", id);
    return n > 0 && static_cast<size_t>(n) < cap ? static_cast<size_t>(n) : 0;
}

// Append `s` to `out` as the inside of a JSON string. Control characters and the two that need it are escaped.
inline bool panel_json_put(char *out, size_t cap, size_t *o, const char *s)
{
    for (; *s != '\0'; ++s) {
        const unsigned char c = static_cast<unsigned char>(*s);
        char piece[8];
        size_t n = 0;
        if (c == '"' || c == '\\') {
            piece[n++] = '\\';
            piece[n++] = static_cast<char>(c);
        } else if (c < 0x20) {
            n = static_cast<size_t>(snprintf(piece, sizeof piece, "\\u%04x", c));
        } else {
            piece[n++] = static_cast<char>(c);
        }
        if (*o + n >= cap) {
            return false;
        }
        memcpy(out + *o, piece, n);
        *o += n;
    }
    out[*o] = '\0';
    return true;
}

// Append `s` to `out` exactly as it is: the JSON's own punctuation.
inline bool panel_raw_put(char *out, size_t cap, size_t *o, const char *s)
{
    const size_t n = strlen(s);
    if (*o + n >= cap) {
        return false;
    }
    memcpy(out + *o, s, n + 1);
    *o += n;
    return true;
}

// The check-in body. Returns its length, or 0 when it does not fit.
inline size_t panel_checkin_body(char *out, size_t cap, const panel_status &st)
{
    if (cap == 0) {
        return 0;
    }
    char name[kPanelNameMax];
    if (panel_default_name(st.id, name, sizeof name) == 0) {
        return 0;
    }
    size_t o = 0;
    out[0] = '\0';
    auto raw = [&](const char *s) { return panel_raw_put(out, cap, &o, s); };
    auto val = [&](const char *s) { return panel_json_put(out, cap, &o, s); };
    char num[40];
    bool ok = raw("{\"id\":\"") && val(st.id) && raw("\",\"name\":\"") && val(name) && raw("\",\"version\":\"") &&
              val(st.version) && raw("\",\"scripts\":[\"latin\"],\"feeds\":[");
    bool any = false;
    for (int i = 0; ok && i < st.feed_count && i < kPanelFeedsMax; ++i) {
        const feed_pick &f = st.feeds[i];
        if (f.stream != kFeedTranscription && f.stream != kFeedTranslation) {
            continue;
        }
        ok = raw(any ? "," : "");
        any = true;
        if (f.stream == kFeedTranscription) {
            ok = ok && raw("{\"stream\":\"transcription\"}");
        } else {
            ok = ok && raw("{\"stream\":\"translation\",\"lang\":\"") && val(f.lang) && raw("\"}");
        }
    }
    ok = ok && raw("]");
    if (ok && st.ip[0] != '\0') {
        ok = raw(",\"ip\":\"") && val(st.ip) && raw("\"");
    }
    snprintf(num, sizeof num, ",\"uptime_s\":%lu", static_cast<unsigned long>(st.uptime_s));
    ok = ok && raw(num);
    if (ok && st.has_rssi) {
        snprintf(num, sizeof num, ",\"rssi\":%d", st.rssi);
        ok = raw(num);
    }
    snprintf(num, sizeof num, ",\"feed_rev\":%lu}", static_cast<unsigned long>(st.feed_rev));
    ok = ok && raw(num);
    return ok ? o : 0;
}

// ---------------------------------------------------------------------------
// The two requests
// ---------------------------------------------------------------------------

inline void panel_host_header(char *out, size_t cap, const char *host, uint16_t port)
{
    if (port == 80) {
        snprintf(out, cap, "%s", host);
    } else {
        snprintf(out, cap, "%s:%u", host, static_cast<unsigned>(port));
    }
}

// `POST /api/panels/checkin` with `body`. Returns the length, or 0 when it does not fit.
inline size_t panel_checkin_request(char *out, size_t cap, const char *host, uint16_t port, const char *body,
                                    size_t body_len, const char *version)
{
    char host_header[160];
    panel_host_header(host_header, sizeof host_header, host, port);
    const int n = snprintf(out, cap,
                           "POST /api/panels/checkin HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Content-Type: application/json\r\n"
                           "Content-Length: %u\r\n"
                           "Accept: application/json\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n"
                           "%s",
                           host_header, static_cast<unsigned>(body_len), version, body);
    if (n <= 0 || static_cast<size_t>(n) >= cap) {
        return 0;
    }
    return static_cast<size_t>(n);
}

// `GET /api/panels/<id>/events`. Returns the length, or 0 when it does not fit or `id` is not a panel ID.
inline size_t panel_events_request(char *out, size_t cap, const char *host, uint16_t port, const char *id,
                                   const char *version)
{
    if (!settings_panel_id_valid(id)) {
        return 0;
    }
    char host_header[160];
    panel_host_header(host_header, sizeof host_header, host, port);
    const int n = snprintf(out, cap,
                           "GET /api/panels/%s/events HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Accept: text/event-stream\r\n"
                           "Cache-Control: no-cache\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n",
                           id, host_header, version);
    if (n <= 0 || static_cast<size_t>(n) >= cap) {
        return 0;
    }
    return static_cast<size_t>(n);
}

// `POST /api/panels/<id>/update` with `body` (update_format::report_body). Returns the length, or 0.
inline size_t panel_update_report_request(char *out, size_t cap, const char *host, uint16_t port, const char *id,
                                          const char *body, size_t body_len, const char *version)
{
    if (!settings_panel_id_valid(id)) {
        return 0;
    }
    char host_header[160];
    panel_host_header(host_header, sizeof host_header, host, port);
    const int n = snprintf(out, cap,
                           "POST /api/panels/%s/update HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Content-Type: application/json\r\n"
                           "Content-Length: %u\r\n"
                           "Accept: application/json\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n"
                           "%s",
                           id, host_header, static_cast<unsigned>(body_len), version, body);
    return n > 0 && static_cast<size_t>(n) < cap ? static_cast<size_t>(n) : 0;
}

// `GET <path>`: the firmware image Relay offered. `path` has been through update_format::path_ok.
inline size_t panel_image_request(char *out, size_t cap, const char *host, uint16_t port, const char *path,
                                  const char *version)
{
    if (!update_format::path_ok(path)) {
        return 0;
    }
    char host_header[160];
    panel_host_header(host_header, sizeof host_header, host, port);
    const int n = snprintf(out, cap,
                           "GET %s HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Accept: application/octet-stream\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n",
                           path, host_header, version);
    return n > 0 && static_cast<size_t>(n) < cap ? static_cast<size_t>(n) : 0;
}

// ---------------------------------------------------------------------------
// What Relay tells the panel
// ---------------------------------------------------------------------------

struct panel_set_feed {
    uint32_t rev;
    int count;  // 1 or 2
    feed_pick feeds[kPanelFeedsMax];
};

// Read the `feeds` array of a `set_feed` object at `obj`. Each entry is {"stream":"transcription"} or
// {"stream":"translation","lang":".."}. False for anything else: no feed, three feeds, an unknown stream, or a
// translation with no language. A language is not checked against what is live: the panel's feed rules do that.
inline bool panel_read_set_feed(const char *obj, const char *e, panel_set_feed *out)
{
    uint64_t rev = 0;
    const char *r = caption_json::get(obj, e, "rev");
    if (r == nullptr || !caption_json::read_uint(r, e, &rev) || rev > 0xFFFFFFFFull) {
        return false;
    }
    caption_json::array_iter it;
    const char *feeds = caption_json::get(obj, e, "feeds");
    if (feeds == nullptr || !caption_json::array_begin(feeds, e, &it)) {
        return false;
    }
    panel_set_feed got = {};
    got.rev = static_cast<uint32_t>(rev);
    while (const char *item = caption_json::array_next(&it)) {
        if (got.count >= kPanelFeedsMax) {
            return false;
        }
        char stream[16];
        char lang[kFeedLangBytes];
        size_t n = 0;
        const char *s = caption_json::get(item, e, "stream");
        if (s == nullptr || !caption_json::read_string(s, e, stream, sizeof stream, &n)) {
            return false;
        }
        feed_pick pick;
        if (strcmp(stream, "transcription") == 0) {
            pick = feed_pick_make(kFeedTranscription, nullptr);
        } else if (strcmp(stream, "translation") == 0) {
            const char *l = caption_json::get(item, e, "lang");
            if (l == nullptr || !caption_json::read_string(l, e, lang, sizeof lang, &n) || n == 0 ||
                n >= sizeof lang) {
                return false;
            }
            pick = feed_pick_make(kFeedTranslation, lang);
        } else {
            return false;
        }
        got.feeds[got.count++] = pick;
    }
    if (got.count == 0) {
        return false;
    }
    *out = got;
    return true;
}

// A firmware update Relay asks for (`app/panels.py`, `_update_message`): in a check-in reply's `update`, and
// at the top level of an `update_available` event. `present` is false when the object is missing a field or
// one is not something the panel will act on (a version that is not X.Y.Z, a path that leaves the origin,
// a checksum that is not 64 lowercase hex digits, a size over the slot); nothing partial is ever returned.
inline update_format::Order panel_read_update(const char *obj, const char *e, uint32_t slot_bytes)
{
    update_format::Order o = {};
    size_t n = 0;
    uint64_t size = 0;
    bool now = false;
    const char *v = caption_json::get(obj, e, "version");
    const char *h = caption_json::get(obj, e, "sha256");
    const char *p = caption_json::get(obj, e, "path");
    const char *z = caption_json::get(obj, e, "size");
    if (v == nullptr || h == nullptr || p == nullptr || z == nullptr ||
        !caption_json::read_string(v, e, o.version, sizeof o.version, &n) ||
        !caption_json::read_string(h, e, o.sha256, sizeof o.sha256, &n) ||
        !caption_json::read_string(p, e, o.path, sizeof o.path, &n) || !caption_json::read_uint(z, e, &size) ||
        size > 0xFFFFFFFFull) {
        return update_format::Order{};
    }
    const char *w = caption_json::get(obj, e, "now");
    if (w != nullptr && caption_json::read_bool(w, e, &now)) {
        o.now = now;
    }
    o.size = static_cast<uint32_t>(size);
    o.present = update_format::order_ok(o, slot_bytes);
    if (!o.present) {
        return update_format::Order{};
    }
    return o;
}

// What one message from Relay asks of the panel.
enum class panel_message : uint8_t {
    none,              // not something the panel acts on (an unknown type, or unreadable)
    set_feed,          // `feed` is filled in
    update_available,  // a firmware update is waiting; `update` is it when it is one the panel will act on
};

struct panel_event {
    panel_message kind = panel_message::none;
    panel_set_feed feed = {};
    update_format::Order update = {};
};

// One event from the stream (the JSON of a `data:` line).
inline panel_event panel_parse_event(const char *json, size_t len, uint32_t slot_bytes = 0x1E0000)
{
    panel_event ev;
    const char *e = json + len;
    char type[24];
    size_t n = 0;
    const char *t = caption_json::get(json, e, "type");
    if (t == nullptr || !caption_json::read_string(t, e, type, sizeof type, &n)) {
        return ev;
    }
    if (strcmp(type, "set_feed") == 0) {
        if (panel_read_set_feed(json, e, &ev.feed)) {
            ev.kind = panel_message::set_feed;
        }
    } else if (strcmp(type, "update_available") == 0) {
        ev.kind = panel_message::update_available;
        ev.update = panel_read_update(json, e, slot_bytes);
    }
    return ev;
}

// A check-in reply's body: whether Relay accepted it, the `set_feed` it carries if any, and the name it gave
// the panel (`name_len` is 0 when it sent none).
struct panel_reply {
    bool ok = false;
    bool has_feed = false;
    panel_set_feed feed = {};
    char name[kPanelNameMax] = {};
    // The firmware update Relay is asking for, if it is asking. A reply with none is the ask withdrawn.
    update_format::Order update = {};
};

inline panel_reply panel_parse_reply(const char *json, size_t len, uint32_t slot_bytes = 0x1E0000)
{
    panel_reply r;
    const char *e = json + len;
    const char *ok = caption_json::get(json, e, "ok");
    bool flag = false;
    if (ok == nullptr || !caption_json::read_bool(ok, e, &flag) || !flag) {
        return r;
    }
    r.ok = true;
    size_t n = 0;
    const char *name = caption_json::get(json, e, "name");
    if (name != nullptr) {
        caption_json::read_string(name, e, r.name, sizeof r.name, &n);
    }
    const char *feed = caption_json::get(json, e, "set_feed");
    if (feed != nullptr && panel_read_set_feed(feed, e, &r.feed)) {
        r.has_feed = true;
    }
    const char *update = caption_json::get(json, e, "update");
    if (update != nullptr) {
        r.update = panel_read_update(update, e, slot_bytes);
    }
    return r;
}

// Whether a `set_feed` is to be applied. One the panel already applied is not (the same one reaches it twice, in a
// check-in reply and on the stream). Any other revision is, including a lower one: the operator's revisions
// restart if Relay's configuration was lost, and the newest word is the one just received.
inline bool panel_should_apply(uint32_t rev, uint32_t applied)
{
    return rev != applied;
}

// How long to wait before opening the event stream again after `failures` in a row, in ms. A 404 is Relay saying it
// does not know this panel (it restarted), which a check-in cures, so it is retried soon and not backed off.
inline uint32_t panel_events_wait_ms(uint32_t failures, int http_status)
{
    if (http_status == 404) {
        return 2000;
    }
    return caption_backoff_ms(failures);
}
