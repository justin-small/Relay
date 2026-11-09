#pragma once
// HTTP Basic, parsed and compared by hand.
//
// `httpd_basic_auth` and a plain `strcmp` both return on the first differing
// byte: an attacker who can time the response learns the credential a byte at a
// time, and this credential unlocks a building's evacuation state. The cost of
// doing it by hand instead is this file. Nothing in it touches ESP-IDF, so
// `test_native.cpp` compiles it with g++ and checks it on a laptop.

#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

// Compare two byte strings without an early exit.
//
// The *length* of the stored credential still leaks through the loop count.
// That is accepted: length is not the secret, and hiding it would mean padding
// every comparison to a fixed maximum for no gain a real attacker can use.
inline bool ct_equal(const char *a, size_t alen, const char *b, size_t blen) {
  unsigned diff = (unsigned)(alen ^ blen);
  const size_t n = alen > blen ? alen : blen;
  for (size_t i = 0; i < n; ++i) {
    const unsigned char ca = i < alen ? (unsigned char)a[i] : 0u;
    const unsigned char cb = i < blen ? (unsigned char)b[i] : 0u;
    diff |= (unsigned)(ca ^ cb);
  }
  return diff == 0u;
}

inline int b64_value(char c) {
  if (c >= 'A' && c <= 'Z') return c - 'A';
  if (c >= 'a' && c <= 'z') return c - 'a' + 26;
  if (c >= '0' && c <= '9') return c - '0' + 52;
  if (c == '+') return 62;
  if (c == '/') return 63;
  return -1;
}

// Decode base64 into `out`. Returns the byte count, or -1 on bad input.
// Rejects rather than skips whitespace and stray characters: a header this
// board will not parse is a header it will not authenticate.
inline int b64_decode(const char *in, size_t inlen, char *out, size_t outcap) {
  if (inlen == 0u || inlen % 4u != 0u) return -1;
  size_t written = 0;
  for (size_t i = 0; i < inlen; i += 4) {
    int quad[4];
    size_t pad = 0;
    for (size_t j = 0; j < 4; ++j) {
      const char c = in[i + j];
      if (c == '=') {
        // Padding is legal only in the last group, and only in its last two
        // places.
        if (i + 4 != inlen || j < 2) return -1;
        quad[j] = 0;
        ++pad;
      } else {
        if (pad) return -1;  // A character after padding.
        quad[j] = b64_value(c);
        if (quad[j] < 0) return -1;
      }
    }
    const unsigned long triple = ((unsigned long)quad[0] << 18) | ((unsigned long)quad[1] << 12) |
                                 ((unsigned long)quad[2] << 6) | (unsigned long)quad[3];
    for (size_t k = 0; k < 3u - pad; ++k) {
      if (written >= outcap) return -1;
      out[written++] = (char)((triple >> (16 - 8 * k)) & 0xFFu);
    }
  }
  return (int)written;
}

// Does this `Authorization` header carry exactly `user` and `credential`?
//
// One answer for every way of being wrong — no header, the wrong scheme,
// undecodable base64, the wrong user, the wrong password. The caller sends the
// same 401 for all of them, because telling a caller *which* half was wrong
// turns one guess into two independent ones.
inline bool basic_authorized(const char *header, const char *user, const char *credential) {
  if (!header) return false;
  static const char kScheme[] = "Basic ";
  const size_t scheme_len = sizeof(kScheme) - 1;
  if (strncmp(header, kScheme, scheme_len) != 0) return false;

  const char *b64 = header + scheme_len;
  char decoded[129];  // 64 bytes of user, a colon, and 64 of credential.
  const int len = b64_decode(b64, strlen(b64), decoded, sizeof(decoded) - 1);
  if (len < 0) return false;
  decoded[len] = '\0';

  const char *colon = (const char *)memchr(decoded, ':', (size_t)len);
  if (!colon) return false;
  const size_t ulen = (size_t)(colon - decoded);
  const char *pass = colon + 1;
  const size_t plen = (size_t)len - ulen - 1u;

  // `&` and not `&&`. `&&` would skip the credential comparison whenever the
  // username was wrong, and the response time would say so.
  const bool ok_user = ct_equal(decoded, ulen, user, strlen(user));
  const bool ok_pass = ct_equal(pass, plen, credential, strlen(credential));
  return ok_user & ok_pass;
}

// Whether a state-changing request came from this board's own page (#866,
// F-14). `origin` is the `Origin` header, or `nullptr` when there was none;
// `host` is the `Host` header, or an empty string.
//
// `/save`, `/factory-reset` and `/update` are gated by HTTP Basic once the
// board is provisioned, and a browser re-sends cached Basic credentials on a
// cross-site form POST. So one visit to the board's page, then one visit to a
// hostile one, was enough for that page to erase the board or point its pushes
// somewhere else. During the unprovisioned window there was not even that.
//
// A browser always sends `Origin` on a cross-origin POST, and on a same-origin
// one it sends this board's own scheme and host. So: absent passes, because
// that is curl and every script, which is a caller holding the credential on
// purpose; present passes only as `https://` followed by exactly the `Host` the
// request was sent to. Only `https://` since #960: the page is served on TLS
// alone, and port 80 answers nothing but a redirect, so an `http://` origin is
// never this board's page. `null`, which is what a sandboxed frame or a redirect
// chain sends, does not pass.
//
// **What the alternatives cost.** A per-boot form token is the textbook
// answer, and it needs state in the page and in `/update`'s script and a story
// for a page left open across a restart; the `Origin` header is already that
// token, minted by the browser. `Referer` is stripped by referrer policies and
// would refuse real installers. `Sec-Fetch-Site` is newer than some browsers a
// site laptop runs.
//
// **What this does not stop.** DNS rebinding makes `Origin` and `Host` agree
// on the attacker's name. A provisioned board still wants the credential then,
// and a browser holds none for that name. What rebinding reaches is the
// unprovisioned window, from a page instead of from the LAN: fifteen minutes
// after power-on or a press of BOOT, on a board not yet in service. Refusing
// it would mean refusing every `Host` but an address or the `.local` name, and
// that refuses the installer whose DHCP server registered a DNS name for it.
inline bool origin_allowed_for(const char *scheme, const char *origin, const char *host) {
  if (origin == nullptr) return true;
  const size_t n = strlen(scheme);
  if (strncmp(origin, scheme, n) != 0) return false;
  if (host[0] == '\0') return false;
  // Case-insensitive: a host name is, and a browser may lower-case one header
  // and not the other.
  const char *a = origin + n;
  const char *b = host;
  for (; *a && *b; ++a, ++b) {
    char ca = *a, cb = *b;
    if (ca >= 'A' && ca <= 'Z') ca = (char)(ca - 'A' + 'a');
    if (cb >= 'A' && cb <= 'Z') cb = (char)(cb - 'A' + 'a');
    if (ca != cb) return false;
  }
  return *a == '\0' && *b == '\0';
}

// The board's own interface, on TLS: `https://`. See `origin_allowed_for` for the
// rule, and the note above for why it is this scheme and not a choice.
inline bool origin_allowed(const char *origin, const char *host) {
  return origin_allowed_for("https://", origin, host);
}

// ---------------------------------------------------------------------------
// Guessing the credential
// ---------------------------------------------------------------------------

// Wrong credentials an address may send before it has to wait, and how long the
// wait grows to. Three, so a person who mistyped twice is never made to wait;
// then a second, doubling to a minute.
//
// Until there was no limit at all, and the Basic credential is the one
// that repoints the board and flips an input's polarity: a false evacuation
// that comes back after every force-clear.
//
// **A refusal, not a delay.** `esp_http_server` runs every handler on one task,
// so sleeping in a handler would stall VXP's poll along with the guesser. An
// address that has to wait is answered 429 without its credential being looked
// at, so a right guess inside the wait is not confirmed either.
//
// Per address and eight addresses deep. A guesser spraying from more than
// eight addresses evicts the oldest, which is a limit a LAN attacker can work
// around by owning many addresses — the credential minimum is the other half.
static const uint8_t AUTH_FREE_FAILURES = 3;
static const uint32_t AUTH_BASE_DELAY_MS = 1000;
static const uint32_t AUTH_MAX_DELAY_MS = 60000;

struct AuthThrottle {
  struct Slot {
    uint32_t ip;
    uint8_t failures;
    uint64_t last_ms;
  };
  static const int kSlots = 8;
  Slot slots[kSlots];

  Slot *find(uint32_t ip) {
    for (int i = 0; i < kSlots; ++i) {
      if (slots[i].failures && slots[i].ip == ip) return &slots[i];
    }
    return nullptr;
  }

  // How long `ip` must still wait at `now`, in milliseconds. Zero means check
  // its credential.
  uint64_t wait_ms(uint32_t ip, uint64_t now) {
    const Slot *slot = find(ip);
    if (slot == nullptr || slot->failures < AUTH_FREE_FAILURES) return 0;
    const unsigned shift = (unsigned)(slot->failures - AUTH_FREE_FAILURES);
    const uint64_t delay =
        shift >= 6 ? AUTH_MAX_DELAY_MS
                   : (uint64_t)AUTH_BASE_DELAY_MS << shift;  // 1, 2, 4 ... 32 s, then 60.
    const uint64_t capped = delay > AUTH_MAX_DELAY_MS ? AUTH_MAX_DELAY_MS : delay;
    const uint64_t since = now - slot->last_ms;
    return since >= capped ? 0 : capped - since;
  }

  void failed(uint32_t ip, uint64_t now) {
    Slot *slot = find(ip);
    if (slot == nullptr) {
      // An empty slot, or else the one idle longest.
      slot = &slots[0];
      for (int i = 0; i < kSlots; ++i) {
        if (!slots[i].failures) {
          slot = &slots[i];
          break;
        }
        if (slots[i].last_ms < slot->last_ms) slot = &slots[i];
      }
      slot->ip = ip;
      slot->failures = 0;
    }
    if (slot->failures < 250) ++slot->failures;
    slot->last_ms = now;
  }

  void succeeded(uint32_t ip) {
    Slot *slot = find(ip);
    if (slot != nullptr) slot->failures = 0;
  }
};

// ---------------------------------------------------------------------------
// Port 80
// ---------------------------------------------------------------------------

// Where a plain-HTTP request is sent: `https://` plus the `Host` it arrived
// with, less any port, plus the path. Returns false when `host` is not a name
// or an address — a redirect built from anything else would be this board
// writing a stranger's text into a `Location` header.
//
// The port is dropped because the TLS server listens on 443, and a `Host` of
// `10.0.0.5:80` names the port that is being left. An IPv6 literal keeps its
// brackets. The query string goes too: the page has none, and `/status` is
// VXP's and VXP never asks over port 80.
inline bool https_location(const char *host, const char *path, char *out, size_t cap) {
  if (host == nullptr || host[0] == '\0' || path == nullptr || path[0] != '/') return false;
  size_t len = strlen(host);
  if (host[0] == '[') {
    const char *close = strchr(host, ']');
    if (close == nullptr) return false;
    len = (size_t)(close - host) + 1;
  } else {
    const char *colon = strchr(host, ':');
    if (colon != nullptr) len = (size_t)(colon - host);
  }
  if (len == 0) return false;
  for (size_t i = 0; i < len; ++i) {
    const char c = host[i];
    const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                    c == '.' || c == '-' || c == ':' || c == '[' || c == ']';
    if (!ok) return false;
  }
  size_t path_len = strcspn(path, "?#");
  for (size_t i = 0; i < path_len; ++i) {
    const unsigned char c = (unsigned char)path[i];
    if (c <= ' ' || c >= 0x7F) return false;
  }
  const int n = snprintf(out, cap, "https://%.*s%.*s", (int)len, host, (int)path_len, path);
  return n > 0 && (size_t)n < cap;
}
