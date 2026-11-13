#pragma once

// The panel's side of Relay's panel registry (app/panels.py), with no ESP-IDF in it, so `test_native.cpp`
// checks it on a laptop. `panel_link.cpp` owns the sockets and the tasks.
//
// **What goes over the wire**, plain HTTP on the viewer port, no login (panels do not pair):
//
//   POST /api/panels/checkin          every 30 s: who this panel is and what it shows
//        {"id":"<32 hex>","name":"Panel 1a2b","version":"0.1.0","board":"crowpanel-adv7","rotation":0,
//         "scripts":["latin"],"feeds":[{"stream":"translation","lang":"SPANISH"}],"ip":"192.168.1.50",
//         "uptime_s":42,"rssi":-58,"feed_rev":0,"rotation_rev":0}
//   -> {"ok":true,"name":"Lobby","interval_s":30,"set_feed":{"type":"set_feed","rev":3,"feeds":[..]},
//       "set_rotation":{"type":"set_rotation","rev":2,"deg":180}}
//
//   GET /api/panels/<id>/events       kept open; Relay writes to it the moment the operator acts
//   -> data: {"type":"set_feed","rev":3,"feeds":[{"stream":"transcription"}]}
//   -> data: {"type":"update_available","version":"0.2.0","size":..,"sha256":"..","path":"/api/panels/firmware/0.2.0.bin","now":false}
//   -> data: {"type":"service_mode","mode":"both","service_type":"7","key":"<32 hex>"}   (#81; also in a reply's `service`)
//   -> data: {"type":"set_rotation","rev":2,"deg":180}   (#122; also in a reply's `set_rotation`)
//   -> data: {"type":"reboot"}         (#122; on the stream only, never in a reply)
//   -> data: {"type":"alert","id":"<16 hex>","from":"Drums","to":"FOH booth","ends_in_s":120,"hz":1}   (#121)
//   -> data: {"type":"alert_end","id":"<16 hex>","why":"seen"}   (#121; seen, missed or cancelled)
//   -> : ping                          every 15 s of quiet
//
// A feed is {"stream":"transcription"}, {"stream":"translation","lang":"SPANISH"} or, since #122,
// {"stream":"service","service_type":"7"}: the Planning Center service, which the panel keeps as a pick with the
// service type id where a translation keeps its language. Since #121 also {"stream":"alert"}: the alert buttons.
//
// **The rotation follows the feed's rule: the newest change wins, from either side.** The operator's carries a `rev`,
// and the panel reports the last one it applied (`rotation_rev`), so Relay sends it only until it is applied. A turn
// made at the screen (the menu's "Turn 180°") does not move `rotation_rev`, so the next check-in does not undo it.
// Either way the panel saves the angle and restarts, because its screen and touch layer are set up once, at boot; it
// keeps `rotation_rev` in its own storage for the same reason.
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

#include "alert_format.h"
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
    char board[24];      // RELAY_BOARD_ID; empty leaves it out
    int rotation;        // 0, 90, 180 or 270, what the screen shows
    char ip[16];         // dotted quad, empty when unknown
    uint32_t uptime_s;
    bool has_rssi;
    int rssi;            // dBm
    uint32_t feed_rev;   // the last operator feed it applied; 0 when none
    uint32_t rotation_rev;  // the last operator rotation it applied; 0 when none
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
    char num[64];
    bool ok = raw("{\"id\":\"") && val(st.id) && raw("\",\"name\":\"") && val(name) && raw("\",\"version\":\"") &&
              val(st.version) && raw("\"");
    if (ok && st.board[0] != '\0') {
        ok = raw(",\"board\":\"") && val(st.board) && raw("\"");
    }
    snprintf(num, sizeof num, ",\"rotation\":%d", panel_rotation_valid(st.rotation) ? st.rotation : 0);
    ok = ok && raw(num) && raw(",\"scripts\":[\"latin\"],\"feeds\":[");
    bool any = false;
    for (int i = 0; ok && i < st.feed_count && i < kPanelFeedsMax; ++i) {
        const feed_pick &f = st.feeds[i];
        if (f.stream != kFeedTranscription && f.stream != kFeedTranslation && f.stream != kFeedAlert &&
            f.stream != kFeedLyrics && f.stream != kFeedTimers && f.stream != kFeedStatus && !(f.stream == kFeedService && f.lang[0] != '\0')) {
            continue;
        }
        ok = raw(any ? "," : "");
        any = true;
        if (f.stream == kFeedTranscription) {
            ok = ok && raw("{\"stream\":\"transcription\"}");
        } else if (f.stream == kFeedService) {
            ok = ok && raw("{\"stream\":\"service\",\"service_type\":\"") && val(f.lang) && raw("\"}");
        } else if (f.stream == kFeedAlert) {
            ok = ok && raw("{\"stream\":\"alert\"}");
        } else if (f.stream == kFeedLyrics) {
            ok = ok && raw("{\"stream\":\"lyrics\"}");
        } else if (f.stream == kFeedTimers) {
            ok = ok && raw("{\"stream\":\"timers\"}");
        } else if (f.stream == kFeedStatus) {
            ok = ok && raw("{\"stream\":\"status\"}");
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
    snprintf(num, sizeof num, ",\"feed_rev\":%lu,\"rotation_rev\":%lu}", static_cast<unsigned long>(st.feed_rev),
             static_cast<unsigned long>(st.rotation_rev));
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

// Read the `feeds` array of a `set_feed` object at `obj`. Each entry is {"stream":"transcription"},
// {"stream":"translation","lang":".."} or {"stream":"service","service_type":"<digits>"}. False for anything else: no
// feed, three feeds, an unknown stream, a translation with no language, or a service type that is not a Planning
// Center id. A language is not checked against what is live, nor a service against what is offered: the panel's feed
// rules do that.
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
        } else if (strcmp(stream, "service") == 0) {
            // A type longer than the pick holds would be cut into another type, so it is refused: the quoted
            // value's own length is checked, as `read_string` stops quietly at the buffer's end.
            const char *t = caption_json::get(item, e, "service_type");
            const char *t_end = t != nullptr ? caption_json::skip_value(t, e) : nullptr;
            if (t_end == nullptr || static_cast<size_t>(t_end - t) > sizeof lang + 1 ||
                !caption_json::read_string(t, e, lang, sizeof lang, &n) || n == 0) {
                return false;
            }
            for (size_t i = 0; i < n; ++i) {
                if (lang[i] < '0' || lang[i] > '9') {
                    return false;
                }
            }
            pick = feed_pick_make(kFeedService, lang);
        } else if (strcmp(stream, "alert") == 0) {
            pick = feed_pick_make(kFeedAlert, nullptr);
        } else if (strcmp(stream, "lyrics") == 0) {
            pick = feed_pick_make(kFeedLyrics, nullptr);
        } else if (strcmp(stream, "timers") == 0) {
            pick = feed_pick_make(kFeedTimers, nullptr);
        } else if (strcmp(stream, "status") == 0) {
            pick = feed_pick_make(kFeedStatus, nullptr);
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

// The operator's rotation (#122): {"rev":2,"deg":180}. The angle is 0, 90, 180 or 270 and nothing else.
struct panel_set_rotation {
    uint32_t rev = 0;
    int deg = 0;
};

inline bool panel_read_set_rotation(const char *obj, const char *e, panel_set_rotation *out)
{
    uint64_t rev = 0;
    uint64_t deg = 0;
    const char *r = caption_json::get(obj, e, "rev");
    const char *d = caption_json::get(obj, e, "deg");
    if (r == nullptr || !caption_json::read_uint(r, e, &rev) || rev > 0xFFFFFFFFull || d == nullptr ||
        !caption_json::read_uint(d, e, &deg) || deg > 270 || !panel_rotation_valid(static_cast<int>(deg))) {
        return false;
    }
    out->rev = static_cast<uint32_t>(rev);
    out->deg = static_cast<int>(deg);
    return true;
}

// What one message from Relay asks of the panel.
enum class panel_message : uint8_t {
    none,              // not something the panel acts on (an unknown type, or unreadable)
    set_feed,          // `feed` is filled in
    update_available,  // a firmware update is waiting; `update` is it when it is one the panel will act on
    service_mode,      // the operator set the panel's service mode (#81); `service` is it
    set_rotation,      // the operator set the screen's rotation (#122); `rotation` is it, 0, 90, 180 or 270
    reboot,            // the operator asked the panel to restart (#122)
    alert,             // flash the screen until someone taps it (#121); `alert` is it
    alert_end,         // the alert `alert_end_id` is over: seen elsewhere, not seen in time, or stopped (#121)
};

struct panel_event {
    panel_message kind = panel_message::none;
    panel_set_feed feed = {};
    update_format::Order update = {};
    panel_service service = {};
    panel_set_rotation rotation = {};
    alert_flash alert = {};
    char alert_end_id[kAlertFlashIdBytes] = {};
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
    } else if (strcmp(type, "service_mode") == 0) {
        if (panel_service_parse(json, e, &ev.service)) {
            ev.kind = panel_message::service_mode;
        }
    } else if (strcmp(type, "set_rotation") == 0) {
        if (panel_read_set_rotation(json, e, &ev.rotation)) {
            ev.kind = panel_message::set_rotation;
        }
    } else if (strcmp(type, "reboot") == 0) {
        ev.kind = panel_message::reboot;
    } else if (strcmp(type, "alert") == 0) {
        if (alert_flash_parse(json, e, &ev.alert)) {
            ev.kind = panel_message::alert;
        }
    } else if (strcmp(type, "alert_end") == 0) {
        if (alert_end_parse(json, e, ev.alert_end_id, sizeof ev.alert_end_id)) {
            ev.kind = panel_message::alert_end;
        }
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
    // The panel's service mode (#81). A Relay from before it sends none, which leaves the mode as it was.
    bool has_service = false;
    panel_service service = {};
    // The operator's rotation (#122), while the panel has not applied its revision.
    bool has_rotation = false;
    panel_set_rotation rotation = {};
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
    const char *service = caption_json::get(json, e, "service");
    if (service != nullptr) {
        r.has_service = panel_service_parse(service, e, &r.service);
    }
    const char *rotation = caption_json::get(json, e, "set_rotation");
    r.has_rotation = rotation != nullptr && panel_read_set_rotation(rotation, e, &r.rotation);
    return r;
}

// Whether a `set_feed` is to be applied. One the panel already applied is not (the same one reaches it twice, in a
// check-in reply and on the stream). Any other revision is, including a lower one: the operator's revisions
// restart if Relay's configuration was lost, and the newest word is the one just received.
inline bool panel_should_apply(uint32_t rev, uint32_t applied)
{
    return rev != applied;
}

// Whether a rotation means a restart: it is not the one the panel booted with.
inline bool panel_rotation_needs_restart(int wanted, int booted)
{
    return panel_rotation_valid(wanted) && wanted != booted;
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
