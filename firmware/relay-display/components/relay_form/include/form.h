#pragma once
// One `application/x-www-form-urlencoded` body, taken apart.
//
// `esp_http_server` hands over the body as bytes, so parsing it is this
// firmware's job. The safest place for a parse this firmware owns is a header
// with no ESP-IDF in it: `test_native.cpp` compiles it with g++ and checks the
// cases a browser will not produce on demand.
//
// `httpd_query_key_value()` exists and is not used. It does not percent-decode,
// which means a pasted PEM — every newline arriving as `%0A` and every `+` as
// `%2B` — would be stored with the escapes still in it, and the TLS error that
// produces reads as a wrong certificate.

#include <stddef.h>
#include <string.h>

// The field was not in the body at all. Distinct from present-and-empty:
// emptying a visible box is how it is cleared, and a box that was never
// submitted must leave the stored value alone.
static const int FORM_ABSENT = -1;

// Present, but longer than the caller can hold. Refused rather than truncated —
// a silently shortened API key is a 401 nobody can explain.
static const int FORM_TOO_LONG = -2;

// Malformed percent-escape. Refused for the same reason `b64_decode` refuses
// stray characters: a body this board will not parse is a body it will not act
// on.
static const int FORM_BAD = -3;

inline int form_hex_value(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  return -1;
}

// Decode `len` bytes of one field value into `out`, NUL-terminated. Returns the
// decoded length, or one of the negatives above.
inline int form_decode(const char *in, size_t len, char *out, size_t cap) {
  size_t written = 0;
  for (size_t i = 0; i < len; ++i) {
    char c = in[i];
    if (c == '+') {
      c = ' ';
    } else if (c == '%') {
      if (i + 2 >= len) return FORM_BAD;
      const int hi = form_hex_value(in[i + 1]);
      const int lo = form_hex_value(in[i + 2]);
      if (hi < 0 || lo < 0) return FORM_BAD;
      c = (char)((hi << 4) | lo);
      i += 2;
    }
    // A NUL in the middle of a field would truncate whatever is stored from it
    // without the caller ever seeing a short value.
    if (c == '\0') return FORM_BAD;
    if (written + 1 >= cap) return FORM_TOO_LONG;
    out[written++] = c;
  }
  out[written] = '\0';
  return (int)written;
}

// Find `name` in `body` and decode its value into `out`.
//
// Matching is on whole pairs, so `cred` does not match `clear_cred` and
// `di1` does not match `di10`. The first occurrence wins: a body carrying a
// field twice is a body somebody built by hand, and taking the last one would
// let a trailing copy overwrite what the form sent.
inline int form_get(const char *body, const char *name, char *out, size_t cap) {
  if (!body || !name) return FORM_ABSENT;
  const size_t name_len = strlen(name);
  const char *p = body;
  while (*p) {
    const char *amp = strchr(p, '&');
    const char *end = amp ? amp : p + strlen(p);
    const char *eq = (const char *)memchr(p, '=', (size_t)(end - p));
    const size_t key_len = (size_t)((eq ? eq : end) - p);
    if (key_len == name_len && memcmp(p, name, name_len) == 0) {
      // A pair with no `=` is a field submitted with an empty value.
      const char *value = eq ? eq + 1 : end;
      return form_decode(value, (size_t)(end - value), out, cap);
    }
    if (!amp) break;
    p = amp + 1;
  }
  return FORM_ABSENT;
}

// Whether the field is there at all, whatever its value. This is what an
// unticked checkbox is missing from and what `clear_<name>` is asked with.
inline bool form_has(const char *body, const char *name) {
  char scratch[2];
  const int n = form_get(body, name, scratch, sizeof(scratch));
  return n != FORM_ABSENT;
}
