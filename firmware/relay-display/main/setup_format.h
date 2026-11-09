#pragma once

// The decisions behind the setup network, with no ESP-IDF in it: the names
// and passphrase the setup network shows, the form a person submits and whether its
// values are usable, the Relay address, and the reason a Wi-Fi attempt failed.
// `provision.cpp` runs the radio and the page; `test_native.cpp` checks everything
// here on a laptop.
//
// Every check returns its message as a string the page and the panel can both show,
// and none of them takes a value from a log line or a page. The passphrase is generated
// here from bytes the caller supplies, so the native test can fix those bytes.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

#include "captive.h"
#include "form.h"
#include "settings_format.h"

// ---------------------------------------------------------------------------
// The setup network's name, address and passphrase
// ---------------------------------------------------------------------------

// `Relay-Panel-` and the last two bytes of the Wi-Fi MAC in upper-case hex: sixteen
// characters, and a seventeenth for the terminator. The MAC is on the board's label,
// so an installer can match the name to the device.
constexpr char kSetupSsidPrefix[] = "Relay-Panel-";

inline void setup_ssid_for_mac(const uint8_t mac[6], char out[17])
{
    static const char kHex[] = "0123456789ABCDEF";
    memcpy(out, kSetupSsidPrefix, sizeof kSetupSsidPrefix - 1);
    char *p = out + sizeof kSetupSsidPrefix - 1;
    for (int i = 4; i < 6; ++i) {
        *p++ = kHex[(mac[i] >> 4) & 0x0F];
        *p++ = kHex[mac[i] & 0x0F];
    }
    *p = '\0';
}

// The page's address. The access point's own address is fixed by ESP-IDF's default
// access-point configuration, and it is the one a browser is told to open.
constexpr char kSetupPageAddress[] = "http://192.168.4.1/";
// `kSetupHostAddress`, `setup_peer_allowed` and `setup_dns_answer` are shared with the I/O
// board's access point, in `firmware/components/relay_captive/include/captive.h`.

// **The setup network as a QR code a phone's camera joins** (`WIFI:T:WPA;S:<name>;P:<password>;;`), the
// form iOS and Android both read. It carries nothing the panel does not already print beside it. `\`, `;`,
// `,`, `:` and `"` in a name or password are escaped with a backslash, as the format asks; the generated
// name and passphrase hold none of them, but the escape keeps the function true for any text. False when
// `out` is too small, and then `out` holds nothing usable.
inline bool setup_wifi_qr(const char *ssid, const char *password, char *out, size_t size)
{
    size_t o = 0;
    const auto put = [&](char c) {
        if (o + 1 >= size) {
            return false;
        }
        out[o++] = c;
        return true;
    };
    const auto text = [&](const char *t, bool escape) {
        for (; *t != '\0'; ++t) {
            if (escape && std::strchr("\\;,:\"", *t) != nullptr && !put('\\')) {
                return false;
            }
            if (!put(*t)) {
                return false;
            }
        }
        return true;
    };
    if (size == 0) {
        return false;
    }
    const bool ok = text("WIFI:T:WPA;S:", false) && text(ssid, true) && text(";P:", false) &&
                    text(password, true) && text(";;", false);
    out[ok ? o : 0] = '\0';
    return ok;
}

// The page's socket is dual-stack, so a client's IPv4 address reaches `getpeername` as an
// IPv4-mapped IPv6 address, ::ffff:a.b.c.d, with family AF_INET6. On the bench every request was
// refused as "address family 10" until this was read. Returns false for any other IPv6 address;
// otherwise `out` is the IPv4 address in host byte order.
inline bool setup_peer_from_mapped_v6(const uint8_t addr[16], uint32_t *out)
{
    for (int i = 0; i < 10; ++i) {
        if (addr[i] != 0) {
            return false;
        }
    }
    if (addr[10] != 0xFF || addr[11] != 0xFF) {
        return false;
    }
    *out = (uint32_t{addr[12]} << 24) | (uint32_t{addr[13]} << 16) | (uint32_t{addr[14]} << 8) | addr[15];
    return true;
}

// **The passphrase is read off the panel and typed into a phone**, so the alphabet
// leaves out the characters that look alike in a sans-serif face: 0 and O, 1 and I.
// Thirty-two characters is a whole number of bits, so every character is equally likely
// and no byte is rejected. The rejection is kept, as the I/O board keeps it, so a
// different alphabet size stays correct.
constexpr char kSetupPassphraseAlphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
constexpr int kSetupPassphraseAlphabetSize = 32;
constexpr int kSetupPassphraseLength = 12;
static_assert(sizeof kSetupPassphraseAlphabet - 1 == kSetupPassphraseAlphabetSize,
              "the alphabet must hold exactly kSetupPassphraseAlphabetSize characters");

// One character from one random byte. False when the byte is rejected and another is
// needed; the caller loops until it has `kSetupPassphraseLength` characters.
inline bool setup_passphrase_char(uint8_t random, char *out)
{
    const int limit = 256 - 256 % kSetupPassphraseAlphabetSize;
    if (static_cast<int>(random) >= limit) {
        return false;
    }
    *out = kSetupPassphraseAlphabet[random % kSetupPassphraseAlphabetSize];
    return true;
}

// ---------------------------------------------------------------------------
// IPv4
// ---------------------------------------------------------------------------

inline uint32_t ipv4_be32(const uint8_t a[4])
{
    return (static_cast<uint32_t>(a[0]) << 24) | (static_cast<uint32_t>(a[1]) << 16) |
           (static_cast<uint32_t>(a[2]) << 8) | static_cast<uint32_t>(a[3]);
}

// Four decimal numbers from 0 to 255, separated by dots and nothing else. A leading
// zero is refused, because `010.0.0.1` is read as octal by some resolvers and as ten by
// others, and a setup form should not have two answers.
inline bool ipv4_parse(const char *text, uint8_t out[4])
{
    if (text == nullptr) {
        return false;
    }
    int value = 0;
    int digits = 0;
    int groups = 0;
    for (const char *p = text;; ++p) {
        if (*p >= '0' && *p <= '9') {
            if (digits == 1 && value == 0) {
                return false;
            }
            value = value * 10 + (*p - '0');
            if (value > 255) {
                return false;
            }
            ++digits;
        } else if (*p == '.' || *p == '\0') {
            if (digits == 0 || groups >= 4) {
                return false;
            }
            out[groups++] = static_cast<uint8_t>(value);
            value = 0;
            digits = 0;
            if (*p == '\0') {
                return groups == 4;
            }
        } else {
            return false;
        }
    }
}

// The prefix length of a subnet mask, or false when the mask is not a run of ones
// followed by zeros. `255.255.255.0` is 24; `255.0.255.0` is refused.
inline bool ipv4_mask_prefix(const uint8_t mask[4], int *prefix)
{
    const uint32_t m = ipv4_be32(mask);
    const uint32_t inverse = ~m;
    if ((inverse & (inverse + 1u)) != 0) {
        return false;
    }
    int bits = 0;
    for (uint32_t v = m; v != 0; v <<= 1) {
        bits += (v & 0x80000000u) != 0 ? 1 : 0;
    }
    *prefix = bits;
    return true;
}

// An address a station can hold as a host: a unicast address in the first three
// classes, not loopback, and not 0.0.0.0.
inline bool ipv4_unicast_host(const uint8_t a[4])
{
    if (a[0] == 0 || a[0] >= 224 || a[0] == 127) {
        return false;
    }
    return ipv4_be32(a) != 0;
}

enum class ip_problem {
    none,
    mask,                 // not a contiguous mask, or not between /8 and /30
    address,              // not a usable host address
    network_address,      // the host bits are all zero
    broadcast_address,    // the host bits are all one
    gateway,              // not a usable host address
    gateway_off_subnet,   // on another subnet from the address
    gateway_is_address,   // the same as the address
    dns_primary,          // not a usable address
    dns_secondary,        // given, and not a usable address
};

inline const char *ip_problem_text(ip_problem p)
{
    switch (p) {
    case ip_problem::none:
        return "";
    case ip_problem::mask:
        return "The subnet mask must be a run of ones then zeros, such as 255.255.255.0, with 8 to 30 ones.";
    case ip_problem::address:
        return "The IP address is not a usable host address, such as 192.168.1.50.";
    case ip_problem::network_address:
        return "The IP address is the network address for this subnet mask.";
    case ip_problem::broadcast_address:
        return "The IP address is the broadcast address for this subnet mask.";
    case ip_problem::gateway:
        return "The default gateway must be a host address, such as 192.168.1.1.";
    case ip_problem::gateway_off_subnet:
        return "The default gateway is not on the subnet of the IP address.";
    case ip_problem::gateway_is_address:
        return "The default gateway is the same as the IP address.";
    case ip_problem::dns_primary:
        return "The primary DNS server must be a host address, such as 192.168.1.1.";
    case ip_problem::dns_secondary:
        return "The secondary DNS server is not a usable address. Leave it empty if there is none.";
    }
    return "";
}

// Whether a static configuration can be applied. Each rule is one a station would
// otherwise fail on later and without a message: a gateway off the subnet is a station
// that associates and then never reaches the Relay server.
inline ip_problem static_ip_problem(const uint8_t addr[4], const uint8_t mask[4], const uint8_t gateway[4],
                                    const uint8_t dns1[4], const uint8_t dns2[4])
{
    int prefix = 0;
    if (!ipv4_mask_prefix(mask, &prefix) || prefix < 8 || prefix > 30) {
        return ip_problem::mask;
    }
    if (!ipv4_unicast_host(addr)) {
        return ip_problem::address;
    }
    const uint32_t a = ipv4_be32(addr);
    const uint32_t m = ipv4_be32(mask);
    const uint32_t network = a & m;
    const uint32_t broadcast = network | ~m;
    if (a == network) {
        return ip_problem::network_address;
    }
    if (a == broadcast) {
        return ip_problem::broadcast_address;
    }
    if (!ipv4_unicast_host(gateway)) {
        return ip_problem::gateway;
    }
    const uint32_t g = ipv4_be32(gateway);
    if ((g & m) != network) {
        return ip_problem::gateway_off_subnet;
    }
    if (g == network || g == broadcast) {
        return ip_problem::gateway;
    }
    if (g == a) {
        return ip_problem::gateway_is_address;
    }
    if (!ipv4_unicast_host(dns1)) {
        return ip_problem::dns_primary;
    }
    if (ipv4_be32(dns2) != 0 && !ipv4_unicast_host(dns2)) {
        return ip_problem::dns_secondary;
    }
    return ip_problem::none;
}

// Dotted-quad text for an address: `out` takes at most 15 characters and a terminator.
inline void ipv4_text(const uint8_t a[4], char out[16])
{
    snprintf(out, 16, "%u.%u.%u.%u", static_cast<unsigned>(a[0]), static_cast<unsigned>(a[1]),
             static_cast<unsigned>(a[2]), static_cast<unsigned>(a[3]));
}

// ---------------------------------------------------------------------------
// The Wi-Fi network
// ---------------------------------------------------------------------------

// A network name is 1 to 32 bytes. The byte count is what a station has, and a name in
// UTF-8 can be shorter in characters than it is in bytes.
inline const char *setup_ssid_problem(size_t len)
{
    if (len == 0) {
        return "Enter the name of the Wi-Fi network.";
    }
    if (len > 32) {
        return "The Wi-Fi network name is longer than 32 bytes.";
    }
    return nullptr;
}

// A WPA2 passphrase is 8 to 63 characters. An empty one is an open network, which is
// a choice the page makes on purpose, so it is allowed.
inline const char *setup_password_problem(size_t len)
{
    if (len == 0 || (len >= 8 && len <= 63)) {
        return nullptr;
    }
    return "The Wi-Fi password must be 8 to 63 characters, or empty for an open network.";
}

// ---------------------------------------------------------------------------
// The Relay address override
// ---------------------------------------------------------------------------

enum class url_problem {
    none,
    empty,        // kept for completeness; an empty override is valid and means "find Relay"
    scheme,       // https://, which Relay does not serve
    host,         // no host name or address, or one that does not parse
    port,         // not a number from 1 to 65535
    path,         // anything after the host and port but a single slash
    credentials,  // a user name or password before the host
    too_long,     // more than the stored field holds
};

inline const char *url_problem_text(url_problem p)
{
    switch (p) {
    case url_problem::none:
        return "";
    case url_problem::empty:
        return "Enter the Relay address, such as 192.168.1.20:8000, or leave it empty.";
    case url_problem::scheme:
        return "Relay is reached over plain http on the local network. Leave out https://.";
    case url_problem::host:
        return "The Relay address needs a host name or an IPv4 address.";
    case url_problem::port:
        return "The port in the Relay address must be a number from 1 to 65535.";
    case url_problem::path:
        return "The Relay address takes no path. Give the host, with a port if it has one.";
    case url_problem::credentials:
        return "The Relay address must not contain a user name or password.";
    case url_problem::too_long:
        return "The Relay address is too long.";
    }
    return "";
}

namespace setup_detail {

inline bool label_ok(const char *p, size_t len)
{
    if (len == 0 || len > 63 || p[0] == '-' || p[len - 1] == '-') {
        return false;
    }
    for (size_t i = 0; i < len; ++i) {
        const char c = p[i];
        const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == '-';
        if (!ok) {
            return false;
        }
    }
    return true;
}

inline bool host_ok(const char *p, size_t len)
{
    if (len == 0 || len > 253) {
        return false;
    }
    bool all_numeric = true;
    for (size_t i = 0; i < len; ++i) {
        const char c = p[i];
        if (!((c >= '0' && c <= '9') || c == '.')) {
            all_numeric = false;
        }
    }
    if (all_numeric) {
        // A dotted run of digits is an address, and an address that does not parse is a
        // mistake, not a host name that happens to look like one.
        char copy[16];
        if (len >= sizeof copy) {
            return false;
        }
        memcpy(copy, p, len);
        copy[len] = '\0';
        uint8_t ignored[4];
        return ipv4_parse(copy, ignored);
    }
    size_t start = 0;
    for (size_t i = 0; i <= len; ++i) {
        if (i == len || p[i] == '.') {
            if (!label_ok(p + start, i - start)) {
                return false;
            }
            start = i + 1;
        }
    }
    return true;
}

}  // namespace setup_detail

// Check the Relay address override and write its normal form into `out`: `http://`, the
// host as the user typed it, and the port if there is one. **An empty address is valid** and
// leaves `out` empty: the panel then finds Relay itself. The `http://` prefix is optional on
// input; `https://` is refused, because Relay's caption stream is plain HTTP on the LAN. One
// trailing slash is accepted and dropped, because a person copies an address with it;
// anything else after the host is a path and is refused. `cap` is the size of `out`,
// including the terminator.
inline url_problem relay_url_normalise(const char *in, char *out, size_t cap)
{
    if (in == nullptr || cap == 0) {
        return url_problem::empty;
    }
    out[0] = '\0';
    const char *s = in;
    while (*s == ' ') {
        ++s;
    }
    const char *e = s + strlen(s);
    while (e > s && e[-1] == ' ') {
        --e;
    }
    if (e == s) {
        return url_problem::none;
    }

    auto starts_with = [&](const char *prefix) {
        const size_t n = strlen(prefix);
        if (static_cast<size_t>(e - s) < n) {
            return false;
        }
        for (size_t i = 0; i < n; ++i) {
            char c = s[i];
            if (c >= 'A' && c <= 'Z') {
                c = static_cast<char>(c - 'A' + 'a');
            }
            if (c != prefix[i]) {
                return false;
            }
        }
        return true;
    };
    static const char kScheme[] = "http://";
    const size_t scheme_len = sizeof kScheme - 1;
    const char *auth = s;
    if (starts_with("https://")) {
        return url_problem::scheme;
    }
    if (starts_with(kScheme)) {
        auth = s + scheme_len;
    }
    const char *tail = auth;
    while (tail < e && *tail != '/' && *tail != '?' && *tail != '#') {
        ++tail;
    }
    for (const char *p = auth; p < tail; ++p) {
        if (*p == '@') {
            return url_problem::credentials;
        }
    }
    if (tail != e && !(e - tail == 1 && *tail == '/')) {
        return url_problem::path;
    }
    if (auth == tail) {
        return url_problem::host;
    }
    if (*auth == '[') {
        // An IPv6 literal. The board's network is IPv4, and a literal it cannot use would
        // be stored and then fail on every connect.
        return url_problem::host;
    }

    const char *host_end = tail;
    const char *colon = nullptr;
    for (const char *p = auth; p < tail; ++p) {
        if (*p == ':') {
            if (colon != nullptr) {
                return url_problem::host;
            }
            colon = p;
        }
    }
    if (colon != nullptr) {
        host_end = colon;
        const char *digits = colon + 1;
        const size_t n = static_cast<size_t>(tail - digits);
        if (n == 0 || n > 5) {
            return url_problem::port;
        }
        long value = 0;
        for (const char *p = digits; p < tail; ++p) {
            if (*p < '0' || *p > '9') {
                return url_problem::port;
            }
            value = value * 10 + (*p - '0');
        }
        if (value < 1 || value > 65535) {
            return url_problem::port;
        }
    }
    if (!setup_detail::host_ok(auth, static_cast<size_t>(host_end - auth))) {
        return url_problem::host;
    }

    const size_t total = scheme_len + static_cast<size_t>(tail - auth);
    if (total + 1 > cap) {
        return url_problem::too_long;
    }
    memcpy(out, kScheme, scheme_len);
    memcpy(out + scheme_len, auth, static_cast<size_t>(tail - auth));
    out[total] = '\0';
    return url_problem::none;
}

// ---------------------------------------------------------------------------
// The form
// ---------------------------------------------------------------------------

// What the setup page's form holds once it is parsed. The `*_text` fields are what the
// person typed, kept so a refused form can show it again. The password is kept only
// long enough to be stored; `setup_form_parse` leaves it out of every error message.
struct setup_form_t {
    char ssid[33];
    char password[65];
    bool dhcp;
    uint8_t addr[4];
    uint8_t mask[4];
    uint8_t gateway[4];
    uint8_t dns1[4];
    uint8_t dns2[4];
    char relay_url[129];  // normalised, once the address has passed; empty = find Relay
    char relay_text[160];  // as typed
    char ip_text[16];
    char mask_text[16];
    char gateway_text[16];
    char dns1_text[16];
    char dns2_text[16];
};

namespace setup_detail {

// Read one IPv4 field. A field that is missing is an empty string, which is refused the
// same way a wrong one is, with the field's own message.
inline const char *read_ip(const char *body, const char *name, char *text, size_t cap, uint8_t out[4],
                           const char *message)
{
    const int n = form_get(body, name, text, cap);
    if (n < 0) {
        text[0] = '\0';
    }
    if (n < 0 || !ipv4_parse(text, out)) {
        return message;
    }
    return nullptr;
}

}  // namespace setup_detail

// Parse and check the setup form. Returns nullptr when every value is usable, or the
// message for the first one that is not. The message names the field and never shows
// a value, so it is safe to show, and to log.
//
// `dhcp` defaults to true: a form that does not name an address mode gets DHCP, the
// default the board ships with. The static fields are read and checked only when the
// mode is static, so a form that has left them filled in from an earlier try does not
// refuse a DHCP configuration.
// What a field that did not decode should say. An absent field is empty, which the
// caller's own rules judge. A malformed one is refused as a whole form: a password with a
// bad escape in it must not read as an empty password, which would be an open network.
inline const char *setup_field_undecodable(int n, const char *too_long_message)
{
    if (n == FORM_TOO_LONG) {
        return too_long_message;
    }
    if (n == FORM_BAD) {
        return "The form could not be read. Try again.";
    }
    return nullptr;
}

// A network name or password with a control character in it is not one a person typed.
inline bool setup_has_control(const char *s)
{
    for (const char *p = s; *p != '\0'; ++p) {
        const unsigned char c = static_cast<unsigned char>(*p);
        if (c < 0x20 || c == 0x7F) {
            return true;
        }
    }
    return false;
}

inline const char *setup_form_parse(const char *body, setup_form_t *out)
{
    memset(out, 0, sizeof *out);
    out->dhcp = true;

    const int ssid = form_get(body, "ssid", out->ssid, sizeof out->ssid);
    if (const char *m = setup_field_undecodable(ssid, "The Wi-Fi network name is longer than 32 bytes.")) {
        out->ssid[0] = '\0';
        return m;
    }
    if (ssid < 0) {
        out->ssid[0] = '\0';
    }
    if (const char *m = setup_ssid_problem(strlen(out->ssid))) {
        return m;
    }
    if (setup_has_control(out->ssid)) {
        return "The Wi-Fi network name has a control character in it.";
    }

    const int pass = form_get(body, "pass", out->password, sizeof out->password);
    if (const char *m = setup_field_undecodable(pass, "The Wi-Fi password must be 8 to 63 characters, or empty for an open network.")) {
        out->password[0] = '\0';
        return m;
    }
    if (pass < 0) {
        out->password[0] = '\0';
    }
    if (const char *m = setup_password_problem(strlen(out->password))) {
        return m;
    }
    if (setup_has_control(out->password)) {
        return "The Wi-Fi password has a control character in it.";
    }

    char mode[8] = {0};
    const int mode_len = form_get(body, "mode", mode, sizeof mode);
    if (mode_len >= 0 && strcmp(mode, "static") == 0) {
        out->dhcp = false;
    } else if (mode_len >= 0 && strcmp(mode, "dhcp") != 0) {
        return "Choose DHCP or a static address.";
    }

    const int relay = form_get(body, "relay", out->relay_text, sizeof out->relay_text);
    if (const char *m = setup_field_undecodable(relay, url_problem_text(url_problem::too_long))) {
        out->relay_text[0] = '\0';
        return m;
    }
    if (relay < 0) {
        out->relay_text[0] = '\0';
    }
    const url_problem relay_check = relay_url_normalise(out->relay_text, out->relay_url, sizeof out->relay_url);
    if (relay_check != url_problem::none) {
        out->relay_url[0] = '\0';
        return url_problem_text(relay_check);
    }

    if (!out->dhcp) {
        if (const char *m = setup_detail::read_ip(body, "ip", out->ip_text, sizeof out->ip_text, out->addr,
                                                  "IP address: enter four numbers from 0 to 255, such as 192.168.1.50.")) {
            return m;
        }
        if (const char *m = setup_detail::read_ip(body, "mask", out->mask_text, sizeof out->mask_text, out->mask,
                                                  "Subnet mask: enter four numbers from 0 to 255, such as 255.255.255.0.")) {
            return m;
        }
        if (const char *m = setup_detail::read_ip(body, "gw", out->gateway_text, sizeof out->gateway_text,
                                                  out->gateway,
                                                  "Default gateway: enter four numbers from 0 to 255, such as 192.168.1.1.")) {
            return m;
        }
        if (const char *m = setup_detail::read_ip(body, "dns1", out->dns1_text, sizeof out->dns1_text, out->dns1,
                                                  "Primary DNS: enter four numbers from 0 to 255, such as 192.168.1.1.")) {
            return m;
        }
        // The secondary is optional. Empty is 0.0.0.0, which the station reads as none.
        const int dns2 = form_get(body, "dns2", out->dns2_text, sizeof out->dns2_text);
        if (const char *m = setup_field_undecodable(dns2, "Secondary DNS: leave it empty, or enter four numbers from 0 to 255.")) {
            out->dns2_text[0] = '\0';
            return m;
        }
        if (dns2 < 0) {
            out->dns2_text[0] = '\0';
        }
        if (out->dns2_text[0] != '\0' && !ipv4_parse(out->dns2_text, out->dns2)) {
            return "Secondary DNS: leave it empty, or enter four numbers from 0 to 255.";
        }
        const char *m = ip_problem_text(static_ip_problem(out->addr, out->mask, out->gateway, out->dns1, out->dns2));
        if (m[0] != '\0') {
            return m;
        }
    }
    return nullptr;
}

// Write a checked form into a settings blob: the network, the password, the address
// mode, the static values and the Relay address. The rotation, panel ID and feed are kept as
// they are, because this page does not change them. A DHCP form clears the static
// values rather than leaving the last static ones behind, so switching back to DHCP
// does not keep a dead address in NVS.
inline void setup_form_apply(relay_settings_t *s, const setup_form_t &f)
{
    settings_set_text(s->wifi_ssid, sizeof s->wifi_ssid, f.ssid);
    settings_set_text(s->wifi_password, sizeof s->wifi_password, f.password);
    settings_set_text(s->relay_addr, sizeof s->relay_addr, f.relay_url);
    s->ip_mode = f.dhcp ? kIpModeDhcp : kIpModeStatic;
    const uint8_t zero[4] = {0, 0, 0, 0};
    const uint8_t *src[5] = {f.addr, f.mask, f.gateway, f.dns1, f.dns2};
    uint8_t *dst[5] = {s->static_addr, s->static_mask, s->static_gateway, s->static_dns1, s->static_dns2};
    for (int i = 0; i < 5; ++i) {
        memcpy(dst[i], f.dhcp ? zero : src[i], 4);
    }
}

// The form a page shows before anything has been submitted: the stored network name,
// address mode and Relay address. **The stored password is never copied.** The form has
// a field for it, and it is left empty, so no page built from this form can show it.
inline void setup_form_from_settings(const relay_settings_t &s, setup_form_t *f)
{
    memset(f, 0, sizeof *f);
    f->dhcp = s.ip_mode != kIpModeStatic;
    settings_set_text(f->ssid, sizeof f->ssid, s.wifi_ssid);
    settings_set_text(f->relay_text, sizeof f->relay_text, s.relay_addr);
    if (!f->dhcp) {
        memcpy(f->addr, s.static_addr, 4);
        memcpy(f->mask, s.static_mask, 4);
        memcpy(f->gateway, s.static_gateway, 4);
        memcpy(f->dns1, s.static_dns1, 4);
        memcpy(f->dns2, s.static_dns2, 4);
        ipv4_text(f->addr, f->ip_text);
        ipv4_text(f->mask, f->mask_text);
        ipv4_text(f->gateway, f->gateway_text);
        ipv4_text(f->dns1, f->dns1_text);
        if (ipv4_be32(f->dns2) != 0) {
            ipv4_text(f->dns2, f->dns2_text);
        }
    }
}

// ---------------------------------------------------------------------------
// Why an attempt failed
// ---------------------------------------------------------------------------

enum class setup_failure {
    none,
    wrong_password,
    network_not_found,
    network_refused,
    link_lost,
    no_address,       // associated, and DHCP gave no address in time
    no_response,      // no association in time
    could_not_save,   // the network worked, and the settings could not be written
    could_not_start,  // the radio refused the connection before it was tried
};

inline const char *setup_failure_text(setup_failure f)
{
    switch (f) {
    case setup_failure::none:
        return "";
    case setup_failure::wrong_password:
        return "Sign-in failed. Check the password.";
    case setup_failure::network_not_found:
        return "Network not found.";
    case setup_failure::network_refused:
        return "The network refused the connection.";
    case setup_failure::link_lost:
        return "The connection to the network was lost.";
    case setup_failure::no_address:
        return "The network gave no address (DHCP timed out).";
    case setup_failure::no_response:
        return "The network did not respond.";
    case setup_failure::could_not_save:
        return "The settings could not be saved. Nothing has changed.";
    case setup_failure::could_not_start:
        return "Could not start the connection. Restart the display and try again.";
    }
    return "";
}

// The failure for a station disconnect, by the ESP-IDF reason code (`wifi_err_reason_t`
// in `esp_wifi_types_generic.h`). The codes are written out here so the mapping can be
// checked without the header. Reason 8, "left the network", is the station's own
// disconnect and is handled by the caller, not by this table.
inline setup_failure setup_failure_from_reason(uint8_t reason)
{
    switch (reason) {
    case 14:   // MIC failure
    case 15:   // 4-way handshake timeout
    case 23:   // 802.1X authentication failed
    case 202:  // authentication failed
    case 204:  // handshake timeout
        return setup_failure::wrong_password;
    case 201:  // no AP found
        return setup_failure::network_not_found;
    case 203:  // association failed
        return setup_failure::network_refused;
    default:
        return setup_failure::link_lost;
    }
}

// The reason code for "left the network" that a station reports when this firmware
// asked it to disconnect (`WIFI_REASON_ASSOC_LEAVE`).
constexpr uint8_t kSetupReasonLeft = 8;
