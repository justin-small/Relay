#pragma once

// Finding Relay on the LAN: the rules, with no ESP-IDF in them. `discovery.cpp` browses mDNS and calls these;
// `test_native.cpp` compiles this header with g++ and checks them.
//
// Relay announces `_relay-captions._tcp` (from inside the app, `app/announce.py`) with the viewer port,
// and a TXT record holding `v` (Relay's version), `name` (the install name) and `path`.
// The panel connects to the first answer and remembers its install name. With more than one Relay on the network it
// then stays with the one it first chose, and a Relay of another name is never taken in its place.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

constexpr const char *kRelayService = "_relay-captions";
constexpr const char *kRelayProto = "_tcp";

// An install name: 32 printable ASCII characters and the terminator.
constexpr size_t kRelayNameCap = 33;
// Answers kept from one browse.
constexpr int kRelayFoundMax = 8;

struct relay_found {
    uint8_t ip[4];
    uint16_t port;
    char name[kRelayNameCap];
};

// Make `out` the install name `in` stands for: printable ASCII only (anything else becomes one `?`, so two names
// that differ only outside it still differ from a blank), outer spaces trimmed, at most `cap - 1` characters.
// The panel stores and compares names in this form.
inline void relay_name_clean(const char *in, char *out, size_t cap)
{
    if (cap == 0) {
        return;
    }
    size_t n = 0;
    bool in_run = false;
    for (const char *p = in != nullptr ? in : ""; *p != '\0' && n + 1 < cap; ++p) {
        const unsigned char c = static_cast<unsigned char>(*p);
        if (c >= 0x20 && c <= 0x7E) {
            out[n++] = static_cast<char>(c);
            in_run = false;
        } else if (c >= 0x80 && (c & 0xC0) == 0x80) {
            continue;  // a continuation byte belongs to the `?` already written
        } else if (!in_run) {
            out[n++] = '?';
            in_run = true;
        }
    }
    out[n] = '\0';
    while (n > 0 && out[n - 1] == ' ') {
        out[--n] = '\0';
    }
    size_t lead = 0;
    while (out[lead] == ' ') {
        ++lead;
    }
    if (lead > 0) {
        memmove(out, out + lead, n - lead + 1);
    }
}

// An answer a panel can connect to: an address that is not 0.0.0.0 or the loopback, and a port.
inline bool relay_found_usable(const relay_found &f)
{
    const bool zero = f.ip[0] == 0 && f.ip[1] == 0 && f.ip[2] == 0 && f.ip[3] == 0;
    return !zero && f.ip[0] != 127 && f.port != 0;
}

// `http://a.b.c.d`, with `:port` unless it is 80, the form the Relay address is stored in (`settings_format.h`).
inline bool relay_url_make(const relay_found &f, char *out, size_t cap)
{
    if (!relay_found_usable(f)) {
        return false;
    }
    const int n = f.port == 80
                      ? snprintf(out, cap, "http://%u.%u.%u.%u", f.ip[0], f.ip[1], f.ip[2], f.ip[3])
                      : snprintf(out, cap, "http://%u.%u.%u.%u:%u", f.ip[0], f.ip[1], f.ip[2], f.ip[3],
                                 static_cast<unsigned>(f.port));
    return n > 0 && static_cast<size_t>(n) < cap;
}

// Which answer to connect to, or -1 for none.
//   * With a remembered name (`saved` not empty): the first answer of that name. A Relay of any other name is
//     not taken, so a panel that chose one Relay does not move to another when the first is off.
//   * With none remembered: the first usable answer, and the caller remembers its name.
inline int relay_pick(const relay_found *list, int count, const char *saved)
{
    const bool want = saved != nullptr && saved[0] != '\0';
    for (int i = 0; i < count; ++i) {
        if (!relay_found_usable(list[i])) {
            continue;
        }
        if (!want || strcmp(list[i].name, saved) == 0) {
            return i;
        }
    }
    return -1;
}
