#pragma once

// Alerts (#121), the panel's side, with no LVGL or ESP-IDF in it, so `test_native.cpp` checks it on a laptop.
// `alert_view.cpp` draws the pane and the flash; `alert_client.cpp` sends.
//
// **Two jobs, one feature.**
//
//  * **Sending**: a pane on the Alert feed (`kFeedAlert`) shows Relay's alert buttons from `GET /stream?stream=alert`,
//    which carries every button whole in each message:
//      data: {"type":"alerts","buttons":[{"id":"0677be7d","name":"FOH booth","state":"on","from":"Drums",
//             "seen_on":"","age_s":3},..]}
//    A tap is `POST /api/alerts/<id>/send` with {"panel":"<this panel's id>"}; Relay names the sender by the panel's
//    name. A tap on a button that is flashing stops it.
//  * **Being flashed**: any panel a button lists is told on its event stream (`panel_link_format.h`):
//      data: {"type":"alert","id":"<16 hex>","from":"Drums","to":"FOH booth","ends_in_s":120,"hz":1}
//      data: {"type":"alert_end","id":"<16 hex>","why":"seen"}
//    The whole screen flashes red and white, once a second, until someone taps it (`POST /api/alerts/ack` with
//    {"id":..,"panel":..}), Relay ends it, or `ends_in_s` passes with no word from Relay.
//
// The flash rate is Relay's to say but never faster than twice a second, whatever it says: above three a second,
// flashing is a seizure risk.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "caption_format.h"

constexpr int kAlertButtonsMax = 16;
constexpr size_t kAlertIdBytes = 9;        // a button id: 8 hex
constexpr size_t kAlertFlashIdBytes = 17;  // an alert id: 16 hex
constexpr size_t kAlertNameBytes = 64;     // up to 32 characters, in UTF-8
constexpr uint32_t kAlertFlashMaxHz = 2;
constexpr uint32_t kAlertEndsMaxS = 600;

enum class alert_button_state : uint8_t { idle, on, seen, missed, cancelled };

struct alert_button {
    char id[kAlertIdBytes] = {};
    char name[kAlertNameBytes] = {};
    alert_button_state state = alert_button_state::idle;
    char from[kAlertNameBytes] = {};
    char seen_on[kAlertNameBytes] = {};
    uint32_t age_s = 0;  // how long ago its alert started or ended, when the message was sent
};

struct alert_board {
    bool known = false;  // a message has been read
    int count = 0;
    alert_button buttons[kAlertButtonsMax];
    uint32_t changes = 0;   // counts every message that changed what the pane shows
    uint32_t at_ms = 0;     // when the last message arrived (the panel's clock), so ages go on counting
};

enum class alert_change : uint8_t { ignored, state, tick, malformed };

inline bool alert_hex_ok(const char *s, size_t len)
{
    if (strlen(s) != len) {
        return false;
    }
    for (const char *c = s; *c != '\0'; ++c) {
        if (!((*c >= '0' && *c <= '9') || (*c >= 'a' && *c <= 'f'))) {
            return false;
        }
    }
    return true;
}

inline alert_button_state alert_state_from(const char *s)
{
    if (strcmp(s, "on") == 0) {
        return alert_button_state::on;
    }
    if (strcmp(s, "seen") == 0) {
        return alert_button_state::seen;
    }
    if (strcmp(s, "missed") == 0) {
        return alert_button_state::missed;
    }
    if (strcmp(s, "cancelled") == 0) {
        return alert_button_state::cancelled;
    }
    return alert_button_state::idle;
}

// Whether two buttons show the same thing: everything but the age, which moves on every message.
inline bool alert_button_same(const alert_button &x, const alert_button &y)
{
    return strcmp(x.id, y.id) == 0 && strcmp(x.name, y.name) == 0 && x.state == y.state &&
           strcmp(x.from, y.from) == 0 && strcmp(x.seen_on, y.seen_on) == 0;
}

// Empty a board in place. **A board is about 3 KB: never build one on a task's stack** (the caption task has 6 KB),
// so it is neither returned nor assigned from a temporary.
inline void alert_board_clear(alert_board *b)
{
    b->known = false;
    b->count = 0;
}

// Read one message of the Alert feed into `*board`. A message that is not `alerts` is ignored; one that is not JSON
// the panel reads leaves the board as it was. A button with no id or name is skipped, and more than
// `kAlertButtonsMax` are cut. `now_ms` is the panel's clock. Read into the board in place, one button at a time.
inline alert_change alert_apply(alert_board *board, const char *json, size_t len, uint32_t now_ms)
{
    const char *e = json + len;
    const char *p = caption_json::ws(json, e);
    if (p == nullptr || p >= e || *p != '{' || caption_json::skip_value(p, e) == nullptr) {
        return alert_change::malformed;
    }
    char type[16];
    size_t n = 0;
    const char *t = caption_json::get(p, e, "type");
    if (t == nullptr || !caption_json::read_string(t, e, type, sizeof type, &n) || strcmp(type, "alerts") != 0) {
        return alert_change::ignored;
    }
    caption_json::array_iter it;
    const char *list = caption_json::get(p, e, "buttons");
    if (list == nullptr || !caption_json::array_begin(list, e, &it)) {
        return alert_change::malformed;
    }
    bool changed = !board->known;
    int count = 0;
    while (const char *item = caption_json::array_next(&it)) {
        if (count >= kAlertButtonsMax) {
            break;
        }
        alert_button b;
        char state[16] = {};
        const char *v = caption_json::get(item, e, "id");
        if (v == nullptr || !caption_json::read_string(v, e, b.id, sizeof b.id, &n) ||
            !alert_hex_ok(b.id, kAlertIdBytes - 1)) {
            continue;
        }
        v = caption_json::get(item, e, "name");
        if (v == nullptr || !caption_json::read_string(v, e, b.name, sizeof b.name, &n) || n == 0) {
            continue;
        }
        v = caption_json::get(item, e, "state");
        if (v != nullptr && caption_json::read_string(v, e, state, sizeof state, &n)) {
            b.state = alert_state_from(state);
        }
        v = caption_json::get(item, e, "from");
        if (v != nullptr) {
            caption_json::read_string(v, e, b.from, sizeof b.from, &n);
        }
        v = caption_json::get(item, e, "seen_on");
        if (v != nullptr) {
            caption_json::read_string(v, e, b.seen_on, sizeof b.seen_on, &n);
        }
        uint64_t age = 0;
        v = caption_json::get(item, e, "age_s");
        if (v != nullptr && caption_json::read_uint(v, e, &age)) {
            b.age_s = age > 0xFFFFFFull ? 0xFFFFFFu : static_cast<uint32_t>(age);
        }
        if (count >= board->count || !alert_button_same(b, board->buttons[count])) {
            changed = true;
        }
        board->buttons[count++] = b;
    }
    changed = changed || count != board->count;
    board->known = true;
    board->count = count;
    board->changes += changed ? 1 : 0;
    board->at_ms = now_ms;
    return changed ? alert_change::state : alert_change::tick;
}

// What a button's second line says, in ASCII words (the panel's fonts have no accents; names are folded by the
// caller). Empty for a button with nothing to say. `age_s` is how long ago, as of now.
inline size_t alert_button_words(const alert_button &b, uint32_t age_s, char *out, size_t cap)
{
    if (cap == 0) {
        return 0;
    }
    char ago[24];
    if (age_s < 10) {
        snprintf(ago, sizeof ago, "just now");
    } else if (age_s < 60) {
        snprintf(ago, sizeof ago, "%u s ago", static_cast<unsigned>(age_s));
    } else {
        snprintf(ago, sizeof ago, "%u min ago", static_cast<unsigned>((age_s + 30) / 60));
    }
    int n = 0;
    switch (b.state) {
    case alert_button_state::on:
        n = b.from[0] != '\0' ? snprintf(out, cap, "Flashing - from %s - tap to stop", b.from)
                              : snprintf(out, cap, "Flashing - tap to stop");
        break;
    case alert_button_state::seen:
        n = snprintf(out, cap, "Seen on %s - %s", b.seen_on[0] != '\0' ? b.seen_on : "a screen", ago);
        break;
    case alert_button_state::missed:
        n = snprintf(out, cap, "Not seen - %s", ago);
        break;
    case alert_button_state::cancelled:
        n = snprintf(out, cap, "Stopped - %s", ago);
        break;
    case alert_button_state::idle:
        out[0] = '\0';
        return 0;
    }
    if (n < 0) {
        out[0] = '\0';
        return 0;
    }
    return static_cast<size_t>(n) < cap ? static_cast<size_t>(n) : cap - 1;
}

// ---------------------------------------------------------------------------
// Being flashed
// ---------------------------------------------------------------------------

struct alert_flash {
    char id[kAlertFlashIdBytes] = {};
    char from[kAlertNameBytes] = {};
    char to[kAlertNameBytes] = {};
    uint32_t ends_in_s = 0;  // how long until Relay's auto-stop, when it was sent
    uint32_t hz = 1;         // flashes a second: 1 or 2
};

// The `alert` object at `obj` (an event-stream message). False unless it has an alert id; the rest is defaulted.
inline bool alert_flash_parse(const char *obj, const char *e, alert_flash *out)
{
    alert_flash f;
    size_t n = 0;
    const char *v = caption_json::get(obj, e, "id");
    if (v == nullptr || !caption_json::read_string(v, e, f.id, sizeof f.id, &n) ||
        !alert_hex_ok(f.id, kAlertFlashIdBytes - 1)) {
        return false;
    }
    v = caption_json::get(obj, e, "from");
    if (v != nullptr) {
        caption_json::read_string(v, e, f.from, sizeof f.from, &n);
    }
    v = caption_json::get(obj, e, "to");
    if (v != nullptr) {
        caption_json::read_string(v, e, f.to, sizeof f.to, &n);
    }
    uint64_t num = 0;
    v = caption_json::get(obj, e, "ends_in_s");
    f.ends_in_s = v != nullptr && caption_json::read_uint(v, e, &num) && num > 0
                      ? static_cast<uint32_t>(num > kAlertEndsMaxS ? kAlertEndsMaxS : num)
                      : kAlertEndsMaxS;
    v = caption_json::get(obj, e, "hz");
    f.hz = v != nullptr && caption_json::read_uint(v, e, &num) && num >= 1
               ? static_cast<uint32_t>(num > kAlertFlashMaxHz ? kAlertFlashMaxHz : num)
               : 1;
    *out = f;
    return true;
}

// The `alert_end` object's alert id. False when there is none.
inline bool alert_end_parse(const char *obj, const char *e, char *id, size_t cap)
{
    size_t n = 0;
    const char *v = caption_json::get(obj, e, "id");
    return v != nullptr && cap >= kAlertFlashIdBytes && caption_json::read_string(v, e, id, cap, &n) &&
           alert_hex_ok(id, kAlertFlashIdBytes - 1);
}

// Whether the flash shows red (true) or white at `elapsed_ms` since it began: half of each period each.
inline bool alert_flash_red(uint32_t elapsed_ms, uint32_t hz)
{
    if (hz < 1) {
        hz = 1;
    }
    if (hz > kAlertFlashMaxHz) {
        hz = kAlertFlashMaxHz;
    }
    const uint32_t period = 1000 / hz;
    return (elapsed_ms % period) < period / 2;
}

// Whether a flash that began `elapsed_ms` ago has outlived Relay's auto-stop, with a few seconds' grace for an
// `alert_end` on its way. Only for a panel that has lost Relay: Relay's word is the one that ends it otherwise.
inline bool alert_flash_expired(const alert_flash &f, uint32_t elapsed_ms)
{
    return elapsed_ms / 1000 >= f.ends_in_s + 5;
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

inline size_t alert_post(char *out, size_t cap, const char *host, uint16_t port, const char *path, const char *body,
                         const char *version)
{
    char host_header[80];
    if (port == 80) {
        snprintf(host_header, sizeof host_header, "%s", host);
    } else {
        snprintf(host_header, sizeof host_header, "%s:%u", host, static_cast<unsigned>(port));
    }
    const int n = snprintf(out, cap,
                           "POST %s HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Content-Type: application/json\r\n"
                           "Content-Length: %u\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n"
                           "%s",
                           path, host_header, static_cast<unsigned>(strlen(body)), version, body);
    if (n <= 0 || static_cast<size_t>(n) >= cap) {
        return 0;
    }
    return static_cast<size_t>(n);
}

// `POST /api/alerts/<button>/send` as this panel. 0 when an id is not one or it does not fit.
inline size_t alert_send_request(char *out, size_t cap, const char *host, uint16_t port, const char *button,
                                 const char *panel_id, const char *version)
{
    if (!alert_hex_ok(button, kAlertIdBytes - 1) || !alert_hex_ok(panel_id, 32)) {
        return 0;
    }
    char path[48];
    char body[64];
    snprintf(path, sizeof path, "/api/alerts/%s/send", button);
    snprintf(body, sizeof body, "{\"panel\":\"%s\"}", panel_id);
    return alert_post(out, cap, host, port, path, body, version);
}

// `POST /api/alerts/ack` for the alert flashing on this panel. 0 when an id is not one or it does not fit.
inline size_t alert_ack_request(char *out, size_t cap, const char *host, uint16_t port, const char *alert_id,
                                const char *panel_id, const char *version)
{
    if (!alert_hex_ok(alert_id, kAlertFlashIdBytes - 1) || !alert_hex_ok(panel_id, 32)) {
        return 0;
    }
    char body[96];
    snprintf(body, sizeof body, "{\"id\":\"%s\",\"panel\":\"%s\"}", alert_id, panel_id);
    return alert_post(out, cap, host, port, "/api/alerts/ack", body, version);
}
