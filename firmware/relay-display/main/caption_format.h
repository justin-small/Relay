#pragma once

// The caption client's rules, with no ESP-IDF in it: the HTTP response and its chunked body, the
// server-sent event stream, the few JSON fields Relay's caption messages carry, the text a panel can
// draw, the 200-line buffer and what each message does to it. `captions.cpp` owns the socket, the task
// and the lock and calls these; `test_native.cpp` compiles this header with g++ and checks them.
//
// What Relay sends on `GET /stream?...&finals=1` (app/hub.py, app/main.py):
//
//   id: 123                    a finished line, `id:` is its seq
//   data: {"stream":..,"lang":..,"seq":123,"delta":"","text":"..","final":true,"ts":..}
//
//   data: {"type":"snapshot","lines":[{"seq":..,"text":..,"ts":..},..],"open":"",..}
//   data: {"type":"targets","source":{..},"live":[..],"running":..,"font_px":..}
//   : ping                     every 15 s of quiet
//
// A snapshot is the whole history: it comes on a fresh connection, on a resume the server's history
// no longer covers, and on a reset (Start), where its `lines` are empty. Anything else is ignored.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>

// ---------------------------------------------------------------------------
// Text the panel can draw
// ---------------------------------------------------------------------------

// The longest line kept, in bytes of UTF-8 after folding. A longer line is cut at a character and ends
// in "...". Relay's lines are a sentence or two; a VAD miss can run to hundreds of words, and this keeps
// one such line from costing the buffer more than a screenful.
constexpr size_t kCaptionLineMax = 1024;

// The longest line read from the wire before folding: room for kCaptionLineMax of text written with
// escapes and multi-byte characters.
constexpr size_t kCaptionRawMax = 4096;

// **What an unsupported character becomes: "?".** The panel's font is Latin only, and a line can still
// carry an emoji, a name in another script or a symbol. Dropping those silently would change what was
// said without a sign, so each run of them becomes one "?". A "?" and not U+FFFD or a box, because it
// is in every font the panel could be given, so the fallback can never itself be the missing glyph.
constexpr char kCaptionFallback = '?';

// The characters a panel font is expected to carry, kept as they are: ASCII, Latin-1 and Latin
// Extended-A and -B (Spanish, French, German, Portuguese, Italian, Romanian, Hmong, Indonesian...),
// Latin Extended Additional up to U+1EFB (Vietnamese), and the euro sign. The caption view's font
// (`fonts/`, checked by `tests/test_caption_font.py`) has to cover exactly this set. U+1EFC-1EFF
// (Middle Welsh Ỽ, Ỿ) are left out because neither source font has them.
inline bool caption_glyph_kept(uint32_t cp)
{
    return (cp >= 0x20 && cp <= 0x7E) || (cp >= 0xA1 && cp <= 0x24F && cp != 0xAD) ||
           (cp >= 0x1E00 && cp <= 0x1EFB) || cp == 0x20AC;
}

// Characters outside that set with a plain equivalent inside it: typographic quotes, dashes, the
// ellipsis, the unusual spaces. Returns the UTF-8 to write, or nullptr.
inline const char *caption_glyph_plain(uint32_t cp)
{
    if (cp == '\t' || cp == '\n' || cp == '\r' || cp == 0xA0 || (cp >= 0x2000 && cp <= 0x200A) ||
        cp == 0x2028 || cp == 0x2029 || cp == 0x202F || cp == 0x205F || cp == 0x3000) {
        return " ";
    }
    switch (cp) {
    case 0x2018: case 0x2019: case 0x201A: case 0x201B: case 0x2032: case 0x2035:
        return "'";
    case 0x201C: case 0x201D: case 0x201E: case 0x201F: case 0x2033: case 0x2036:
        return "\"";
    case 0x2010: case 0x2011: case 0x2012: case 0x2013: case 0x2014: case 0x2015: case 0x2212:
        return "-";
    case 0x2026:
        return "...";
    case 0x2022: case 0x2023: case 0x2043: case 0x2219:
        return "\xC2\xB7";  // U+00B7, the middle dot
    case 0x2122:
        return "TM";
    default:
        return nullptr;
    }
}

// Characters that draw nothing on their own: controls, the soft hyphen, combining marks (the base
// letter stays), zero-width joiners and spaces, variation selectors, emoji skin tones and tags. They are
// dropped, and they do not end a run of unsupported characters, so a joined emoji is still one "?".
inline bool caption_glyph_invisible(uint32_t cp)
{
    return cp < 0x20 || (cp >= 0x7F && cp <= 0x9F) || cp == 0xAD || (cp >= 0x300 && cp <= 0x36F) ||
           (cp >= 0x200B && cp <= 0x200F) || (cp >= 0x2060 && cp <= 0x2064) || cp == 0xFEFF ||
           (cp >= 0xFE00 && cp <= 0xFE0F) || (cp >= 0x1F3FB && cp <= 0x1F3FF) ||
           (cp >= 0xE0000 && cp <= 0xE01EF);
}

// One code point from UTF-8 at `p`, or -1 for a byte that does not start a valid sequence (a stray
// continuation, an overlong form, a surrogate, past U+10FFFF, or cut off by `end`). `*used` is the bytes
// it took: 1 for an invalid byte, so the next call resynchronises on the following byte.
inline int32_t caption_utf8_next(const unsigned char *p, const unsigned char *end, size_t *used)
{
    *used = 1;
    const unsigned c = p[0];
    if (c < 0x80) {
        return static_cast<int32_t>(c);
    }
    size_t n = 0;
    uint32_t cp = 0;
    uint32_t min = 0;
    if ((c & 0xE0) == 0xC0) {
        n = 2, cp = c & 0x1F, min = 0x80;
    } else if ((c & 0xF0) == 0xE0) {
        n = 3, cp = c & 0x0F, min = 0x800;
    } else if ((c & 0xF8) == 0xF0) {
        n = 4, cp = c & 0x07, min = 0x10000;
    } else {
        return -1;
    }
    if (static_cast<size_t>(end - p) < n) {
        return -1;
    }
    for (size_t i = 1; i < n; ++i) {
        if ((p[i] & 0xC0) != 0x80) {
            return -1;
        }
        cp = (cp << 6) | (p[i] & 0x3F);
    }
    if (cp < min || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
        return -1;
    }
    *used = n;
    return static_cast<int32_t>(cp);
}

// Append a code point as UTF-8. Returns the bytes written (0 when it does not fit in `room`).
inline size_t caption_utf8_put(uint32_t cp, char *out, size_t room)
{
    unsigned char b[4];
    size_t n;
    if (cp < 0x80) {
        b[0] = static_cast<unsigned char>(cp), n = 1;
    } else if (cp < 0x800) {
        b[0] = static_cast<unsigned char>(0xC0 | (cp >> 6));
        b[1] = static_cast<unsigned char>(0x80 | (cp & 0x3F)), n = 2;
    } else if (cp < 0x10000) {
        b[0] = static_cast<unsigned char>(0xE0 | (cp >> 12));
        b[1] = static_cast<unsigned char>(0x80 | ((cp >> 6) & 0x3F));
        b[2] = static_cast<unsigned char>(0x80 | (cp & 0x3F)), n = 3;
    } else {
        b[0] = static_cast<unsigned char>(0xF0 | (cp >> 18));
        b[1] = static_cast<unsigned char>(0x80 | ((cp >> 12) & 0x3F));
        b[2] = static_cast<unsigned char>(0x80 | ((cp >> 6) & 0x3F));
        b[3] = static_cast<unsigned char>(0x80 | (cp & 0x3F)), n = 4;
    }
    if (n > room) {
        return 0;
    }
    memcpy(out, b, n);
    return n;
}

// Fold a line of UTF-8 into what the panel can draw, into `out` (`cap` bytes, terminator included).
// Kept characters stay, plain equivalents replace typography, invisible characters go, and every
// other run becomes one "?". Spaces are collapsed and the ends trimmed. A line longer than `cap` is
// cut at a character and ends in "...". Returns the length written.
inline size_t caption_fold(const char *in, size_t len, char *out, size_t cap)
{
    if (cap == 0) {
        return 0;
    }
    const size_t limit = cap - 1;
    size_t o = 0;
    bool in_fallback = false;  // the last visible thing written was a fallback
    bool pending_space = false;
    bool cut = false;
    const auto *p = reinterpret_cast<const unsigned char *>(in);
    const auto *end = p + len;

    // Write `piece` (n bytes), after any space owed. False when it does not fit.
    auto put = [&](const char *piece, size_t n) {
        const size_t need = n + (pending_space && o > 0 ? 1 : 0);
        if (o + need > limit) {
            return false;
        }
        if (pending_space && o > 0) {
            out[o++] = ' ';
        }
        pending_space = false;
        memcpy(out + o, piece, n);
        o += n;
        return true;
    };

    while (p < end && !cut) {
        size_t used = 0;
        const int32_t got = caption_utf8_next(p, end, &used);
        p += used;
        if (got < 0) {
            if (!in_fallback) {
                const char f = kCaptionFallback;
                cut = !put(&f, 1);
                in_fallback = true;
            }
            continue;
        }
        const uint32_t cp = static_cast<uint32_t>(got);
        const char *plain = caption_glyph_plain(cp);
        if (plain != nullptr && plain[0] == ' ' && plain[1] == '\0') {
            pending_space = true;
            in_fallback = false;
            continue;
        }
        if (cp == ' ') {
            pending_space = true;
            in_fallback = false;
            continue;
        }
        if (caption_glyph_kept(cp)) {
            char buf[4];
            const size_t n = caption_utf8_put(cp, buf, sizeof buf);
            cut = !put(buf, n);
            in_fallback = false;
        } else if (plain != nullptr) {
            cut = !put(plain, strlen(plain));
            in_fallback = false;
        } else if (caption_glyph_invisible(cp)) {
            // Nothing drawn, and a run of fallbacks continues across it.
        } else if (!in_fallback) {
            const char f = kCaptionFallback;
            cut = !put(&f, 1);
            in_fallback = true;
        }
    }

    if (cut) {
        // Back off to a character boundary with room for "...".
        while (o > 0 && o + 3 > limit) {
            --o;
            while (o > 0 && (static_cast<unsigned char>(out[o]) & 0xC0) == 0x80) {
                --o;
            }
        }
        while (o > 0 && out[o - 1] == ' ') {
            --o;
        }
        if (o + 3 <= limit) {
            memcpy(out + o, "...", 3);
            o += 3;
        }
    }
    out[o] = '\0';
    return o;
}

// ---------------------------------------------------------------------------
// JSON: the few fields a caption message carries
// ---------------------------------------------------------------------------
//
// Not a general parser. It finds a key in an object, walks an array, and reads a string, an unsigned
// integer or a boolean. It never recurses (a deeply nested message cannot run the stack out) and never
// allocates. Malformed input is "not found", which makes the message ignored.

namespace caption_json {

inline const char *ws(const char *p, const char *e)
{
    while (p < e && (*p == ' ' || *p == '\t' || *p == '\n' || *p == '\r')) {
        ++p;
    }
    return p;
}

// Past the string that starts at `p` (on its opening quote), or nullptr.
inline const char *skip_string(const char *p, const char *e)
{
    ++p;
    while (p < e) {
        if (*p == '\\') {
            p += 2;
            continue;
        }
        if (*p == '"') {
            return p + 1;
        }
        if (static_cast<unsigned char>(*p) < 0x20) {
            return nullptr;
        }
        ++p;
    }
    return nullptr;
}

// Past the value that starts at `p`, or nullptr.
inline const char *skip_value(const char *p, const char *e)
{
    p = ws(p, e);
    if (p >= e) {
        return nullptr;
    }
    if (*p == '"') {
        return skip_string(p, e);
    }
    if (*p == '{' || *p == '[') {
        int depth = 0;
        while (p < e) {
            const char c = *p;
            if (c == '"') {
                p = skip_string(p, e);
                if (p == nullptr) {
                    return nullptr;
                }
                continue;
            }
            if (c == '{' || c == '[') {
                ++depth;
            } else if (c == '}' || c == ']') {
                if (--depth == 0) {
                    return p + 1;
                }
            }
            ++p;
        }
        return nullptr;
    }
    const char *s = p;
    while (p < e && *p != ',' && *p != '}' && *p != ']' && *p != ' ' && *p != '\t' && *p != '\n' &&
           *p != '\r') {
        ++p;
    }
    return p == s ? nullptr : p;
}

// The value of `key` in the object at `p`, or nullptr. Keys are compared as written, so a key spelt
// with escapes does not match; Relay's never are.
inline const char *get(const char *p, const char *e, const char *key)
{
    p = ws(p, e);
    if (p >= e || *p != '{') {
        return nullptr;
    }
    ++p;
    const size_t klen = strlen(key);
    for (;;) {
        p = ws(p, e);
        if (p >= e || *p != '"') {
            return nullptr;
        }
        const char *ks = p + 1;
        const char *ke = skip_string(p, e);
        if (ke == nullptr) {
            return nullptr;
        }
        const bool match = static_cast<size_t>(ke - 1 - ks) == klen && memcmp(ks, key, klen) == 0;
        p = ws(ke, e);
        if (p >= e || *p != ':') {
            return nullptr;
        }
        p = ws(p + 1, e);
        if (match) {
            return p;
        }
        p = skip_value(p, e);
        if (p == nullptr) {
            return nullptr;
        }
        p = ws(p, e);
        if (p < e && *p == ',') {
            ++p;
            continue;
        }
        return nullptr;
    }
}

struct array_iter {
    const char *p;
    const char *e;
    bool first;
};

inline bool array_begin(const char *p, const char *e, array_iter *it)
{
    p = ws(p, e);
    if (p == nullptr || p >= e || *p != '[') {
        return false;
    }
    *it = {p + 1, e, true};
    return true;
}

// The next element, or nullptr at the end (or at malformed input).
inline const char *array_next(array_iter *it)
{
    const char *p = ws(it->p, it->e);
    if (p >= it->e || *p == ']') {
        return nullptr;
    }
    if (!it->first) {
        if (*p != ',') {
            return nullptr;
        }
        p = ws(p + 1, it->e);
    }
    it->first = false;
    const char *end = skip_value(p, it->e);
    if (end == nullptr) {
        return nullptr;
    }
    it->p = end;
    return p;
}

inline bool read_uint(const char *p, const char *e, uint64_t *out)
{
    if (p == nullptr || p >= e || *p < '0' || *p > '9') {
        return false;
    }
    uint64_t v = 0;
    while (p < e && *p >= '0' && *p <= '9') {
        const uint64_t next = v * 10 + static_cast<uint64_t>(*p - '0');
        if (next / 10 != v) {
            return false;
        }
        v = next;
        ++p;
    }
    if (p < e && (*p == '.' || *p == 'e' || *p == 'E')) {
        return false;
    }
    *out = v;
    return true;
}

inline bool read_bool(const char *p, const char *e, bool *out)
{
    if (p != nullptr && e - p >= 4 && memcmp(p, "true", 4) == 0) {
        *out = true;
        return true;
    }
    if (p != nullptr && e - p >= 5 && memcmp(p, "false", 5) == 0) {
        *out = false;
        return true;
    }
    return false;
}

inline int hex4(const char *p, const char *e)
{
    if (e - p < 4) {
        return -1;
    }
    int v = 0;
    for (int i = 0; i < 4; ++i) {
        const char c = p[i];
        v <<= 4;
        if (c >= '0' && c <= '9') {
            v |= c - '0';
        } else if (c >= 'a' && c <= 'f') {
            v |= c - 'a' + 10;
        } else if (c >= 'A' && c <= 'F') {
            v |= c - 'A' + 10;
        } else {
            return -1;
        }
    }
    return v;
}

// The string at `p`, unescaped into UTF-8 in `out` (`cap` bytes, terminator included). A string longer
// than that is cut at a character boundary. A lone surrogate becomes U+FFFD. False when `p` is not a
// well-formed string.
inline bool read_string(const char *p, const char *e, char *out, size_t cap, size_t *len)
{
    if (p == nullptr || p >= e || *p != '"' || cap == 0) {
        return false;
    }
    ++p;
    size_t o = 0;
    bool full = false;
    auto put_cp = [&](uint32_t cp) {
        if (full) {
            return;
        }
        const size_t n = caption_utf8_put(cp, out + o, cap - 1 - o);
        if (n == 0) {
            full = true;
        }
        o += n;
    };
    while (p < e) {
        const char c = *p;
        if (c == '"') {
            out[o] = '\0';
            if (len != nullptr) {
                *len = o;
            }
            return true;
        }
        if (static_cast<unsigned char>(c) < 0x20) {
            return false;
        }
        if (c != '\\') {
            // Raw bytes are copied whole; a multi-byte character that would not fit is not started.
            size_t n = 1;
            const auto u = static_cast<unsigned char>(c);
            if (u >= 0xF0) {
                n = 4;
            } else if (u >= 0xE0) {
                n = 3;
            } else if (u >= 0xC0) {
                n = 2;
            }
            if (static_cast<size_t>(e - p) < n) {
                n = static_cast<size_t>(e - p);
            }
            if (!full && o + n <= cap - 1) {
                memcpy(out + o, p, n);
                o += n;
            } else {
                full = true;
            }
            p += n;
            continue;
        }
        if (e - p < 2) {
            return false;
        }
        const char x = p[1];
        p += 2;
        switch (x) {
        case '"': put_cp('"'); break;
        case '\\': put_cp('\\'); break;
        case '/': put_cp('/'); break;
        case 'b': put_cp('\b'); break;
        case 'f': put_cp('\f'); break;
        case 'n': put_cp('\n'); break;
        case 'r': put_cp('\r'); break;
        case 't': put_cp('\t'); break;
        case 'u': {
            int hi = hex4(p, e);
            if (hi < 0) {
                return false;
            }
            p += 4;
            uint32_t cp = static_cast<uint32_t>(hi);
            if (cp >= 0xD800 && cp <= 0xDBFF) {
                int lo = (e - p >= 6 && p[0] == '\\' && p[1] == 'u') ? hex4(p + 2, e) : -1;
                if (lo >= 0xDC00 && lo <= 0xDFFF) {
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (static_cast<uint32_t>(lo) - 0xDC00);
                    p += 6;
                } else {
                    cp = 0xFFFD;
                }
            } else if (cp >= 0xDC00 && cp <= 0xDFFF) {
                cp = 0xFFFD;
            }
            put_cp(cp);
            break;
        }
        default:
            return false;
        }
    }
    return false;
}

}  // namespace caption_json

// ---------------------------------------------------------------------------
// HTTP: the response's status, headers and body
// ---------------------------------------------------------------------------

// Reads an HTTP/1.x response as it arrives and hands its body on, decoded from chunked transfer
// encoding when the server uses it. uvicorn streams with chunked encoding; Caddy in front of it may
// pass that through or not, so both are read.
class caption_http {
public:
    enum class phase { status, headers, body, done, bad };

    phase state() const { return phase_; }
    int status() const { return status_; }
    bool chunked() const { return chunked_; }
    bool event_stream() const { return event_stream_; }

    // Feed received bytes. `on_body(ctx, bytes, n)` gets the decoded body. Returns false once the
    // response is malformed.
    bool feed(const char *p, size_t n, void (*on_body)(void *, const char *, size_t), void *ctx)
    {
        size_t i = 0;
        while (i < n) {
            switch (phase_) {
            case phase::status:
            case phase::headers:
            case phase::done:
            case phase::bad:
                if (phase_ == phase::done || phase_ == phase::bad) {
                    return phase_ != phase::bad;
                }
                if (!header_byte(p[i++])) {
                    phase_ = phase::bad;
                    return false;
                }
                break;
            case phase::body:
                if (!chunked_) {
                    on_body(ctx, p + i, n - i);
                    return true;
                }
                if (!chunk_byte(p, n, &i, on_body, ctx)) {
                    phase_ = phase::bad;
                    return false;
                }
                break;
            }
        }
        return phase_ != phase::bad;
    }

private:
    // A header line longer than this is read and its excess dropped: only the status line,
    // Transfer-Encoding and Content-Type matter, and they are short.
    static constexpr size_t kLineMax = 160;

    phase phase_ = phase::status;
    int status_ = 0;
    bool chunked_ = false;
    bool event_stream_ = false;
    char line_[kLineMax + 1] = {};
    size_t line_len_ = 0;

    enum class chunk { size, ext, size_lf, data, data_cr, data_lf, trailer } chunk_ = chunk::size;
    uint64_t chunk_left_ = 0;
    int chunk_digits_ = 0;
    size_t trailer_len_ = 0;

    static char lower(char c) { return (c >= 'A' && c <= 'Z') ? static_cast<char>(c - 'A' + 'a') : c; }

    static bool starts_ci(const char *s, const char *prefix)
    {
        for (; *prefix != '\0'; ++s, ++prefix) {
            if (lower(*s) != *prefix) {
                return false;
            }
        }
        return true;
    }

    static bool contains_ci(const char *s, const char *needle)
    {
        const size_t n = strlen(needle);
        for (; *s != '\0'; ++s) {
            size_t k = 0;
            while (k < n && s[k] != '\0' && lower(s[k]) == needle[k]) {
                ++k;
            }
            if (k == n) {
                return true;
            }
        }
        return false;
    }

    bool header_byte(char c)
    {
        if (c == '\r') {
            return true;
        }
        if (c != '\n') {
            if (line_len_ < kLineMax) {
                line_[line_len_++] = c;
            }
            return true;
        }
        line_[line_len_] = '\0';
        const size_t len = line_len_;
        line_len_ = 0;
        if (phase_ == phase::status) {
            // "HTTP/1.1 200 OK"
            if (!starts_ci(line_, "http/1.") || len < 12 || line_[8] != ' ') {
                return false;
            }
            int code = 0;
            for (int k = 9; k < 12; ++k) {
                if (line_[k] < '0' || line_[k] > '9') {
                    return false;
                }
                code = code * 10 + (line_[k] - '0');
            }
            status_ = code;
            phase_ = phase::headers;
            return true;
        }
        if (len == 0) {
            phase_ = phase::body;
            return true;
        }
        if (starts_ci(line_, "transfer-encoding:") && contains_ci(line_ + 18, "chunked")) {
            chunked_ = true;
        } else if (starts_ci(line_, "content-type:") && contains_ci(line_ + 13, "text/event-stream")) {
            event_stream_ = true;
        }
        return true;
    }

    bool chunk_byte(const char *p, size_t n, size_t *i, void (*on_body)(void *, const char *, size_t), void *ctx)
    {
        const char c = p[*i];
        switch (chunk_) {
        case chunk::size: {
            int v = -1;
            if (c >= '0' && c <= '9') {
                v = c - '0';
            } else if (lower(c) >= 'a' && lower(c) <= 'f') {
                v = lower(c) - 'a' + 10;
            }
            ++*i;
            if (v >= 0) {
                if (++chunk_digits_ > 15) {
                    return false;
                }
                chunk_left_ = chunk_left_ * 16 + static_cast<uint64_t>(v);
                return true;
            }
            if (chunk_digits_ == 0) {
                return false;
            }
            if (c == ';' || c == ' ' || c == '\t') {
                chunk_ = chunk::ext;
                return true;
            }
            if (c == '\r') {
                chunk_ = chunk::size_lf;
                return true;
            }
            if (c == '\n') {
                return chunk_size_done();
            }
            return false;
        }
        case chunk::ext:
            ++*i;
            if (c == '\n') {
                return chunk_size_done();
            }
            if (c == '\r') {
                chunk_ = chunk::size_lf;
            }
            return true;
        case chunk::size_lf:
            ++*i;
            return c == '\n' && chunk_size_done();
        case chunk::data: {
            size_t take = n - *i;
            if (take > chunk_left_) {
                take = static_cast<size_t>(chunk_left_);
            }
            on_body(ctx, p + *i, take);
            *i += take;
            chunk_left_ -= take;
            if (chunk_left_ == 0) {
                chunk_ = chunk::data_cr;
            }
            return true;
        }
        case chunk::data_cr:
            ++*i;
            if (c == '\r') {
                chunk_ = chunk::data_lf;
                return true;
            }
            if (c == '\n') {
                chunk_ = chunk::size;
                return true;
            }
            return false;
        case chunk::data_lf:
            ++*i;
            if (c != '\n') {
                return false;
            }
            chunk_ = chunk::size;
            return true;
        case chunk::trailer:
            ++*i;
            if (c == '\n') {
                if (trailer_len_ == 0) {
                    phase_ = phase::done;
                }
                trailer_len_ = 0;
            } else if (c != '\r') {
                ++trailer_len_;
            }
            return true;
        }
        return false;
    }

    bool chunk_size_done()
    {
        chunk_digits_ = 0;
        if (chunk_left_ == 0) {
            chunk_ = chunk::trailer;
            trailer_len_ = 0;
        } else {
            chunk_ = chunk::data;
        }
        return true;
    }
};

// ---------------------------------------------------------------------------
// Server-sent events
// ---------------------------------------------------------------------------

struct caption_sse_event {
    const char *data;  // the data lines joined with "\n", terminated
    size_t len;
    const char *id;    // the `id:` field of this event, "" when it had none
    const char *type;  // the `event:` field, "" when it had none
    bool oversize;     // the data did not fit and was dropped; `data` is empty
};

// Reads an event stream as it arrives, per the WHATWG rules the panel needs: fields `data`, `id` and
// `event`, comments (a line starting with ":"), and any of CRLF, LF or CR as the line end. **The data
// is written straight into a buffer the caller owns**, never into a line buffer first: a snapshot is
// one `data:` line of tens of kilobytes, and on the board that buffer is in PSRAM.
class caption_sse {
public:
    caption_sse(char *buf, size_t cap) : buf_(buf), cap_(cap) {}

    using event_fn = void (*)(void *ctx, const caption_sse_event &event);
    using comment_fn = void (*)(void *ctx, const char *text);

    void feed(const char *p, size_t n, event_fn on_event, comment_fn on_comment, void *ctx)
    {
        for (size_t i = 0; i < n; ++i) {
            const char c = p[i];
            if (after_cr_ && c == '\n') {
                after_cr_ = false;
                continue;
            }
            after_cr_ = c == '\r';
            if (c == '\r' || c == '\n') {
                end_line(on_event, on_comment, ctx);
            } else {
                line_byte(c);
            }
        }
    }

private:
    static constexpr size_t kSmall = 40;

    enum class part { start, name, value_start, value, comment, skip };
    enum class field { none, data, id, event };

    char *buf_;
    size_t cap_;
    size_t len_ = 0;
    bool has_data_ = false;
    bool oversize_ = false;
    bool after_cr_ = false;
    part part_ = part::start;
    field field_ = field::none;
    char name_[kSmall + 1] = {};
    size_t name_len_ = 0;
    char id_[kSmall + 1] = {};
    size_t id_len_ = 0;
    char type_[kSmall + 1] = {};
    size_t type_len_ = 0;
    char small_[kSmall + 1] = {};
    size_t small_len_ = 0;

    void put_data(char c)
    {
        if (oversize_) {
            return;
        }
        if (len_ + 1 >= cap_) {
            oversize_ = true;
            return;
        }
        buf_[len_++] = c;
    }

    void begin_value()
    {
        name_[name_len_] = '\0';
        field_ = field::none;
        small_len_ = 0;
        if (strcmp(name_, "data") == 0) {
            field_ = field::data;
            if (has_data_) {
                put_data('\n');
            }
            has_data_ = true;
        } else if (strcmp(name_, "id") == 0) {
            field_ = field::id;
        } else if (strcmp(name_, "event") == 0) {
            field_ = field::event;
        }
    }

    void line_byte(char c)
    {
        switch (part_) {
        case part::start:
            if (c == ':') {
                part_ = part::comment;
                small_len_ = 0;
                return;
            }
            part_ = part::name;
            name_len_ = 0;
            [[fallthrough]];
        case part::name:
            if (c == ':') {
                begin_value();
                part_ = part::value_start;
                return;
            }
            if (name_len_ < kSmall) {
                name_[name_len_++] = c;
            } else {
                part_ = part::skip;
            }
            return;
        case part::value_start:
            part_ = part::value;
            if (c == ' ') {
                return;
            }
            [[fallthrough]];
        case part::value:
            if (field_ == field::data) {
                put_data(c);
            } else if (field_ != field::none && small_len_ < kSmall) {
                small_[small_len_++] = c;
            }
            return;
        case part::comment:
            if (small_len_ < kSmall) {
                small_[small_len_++] = c;
            }
            return;
        case part::skip:
            return;
        }
    }

    void end_line(event_fn on_event, comment_fn on_comment, void *ctx)
    {
        switch (part_) {
        case part::start:
            dispatch(on_event, ctx);
            break;
        case part::name:
            // A field name with no colon: the whole line is the name and the value is empty.
            begin_value();
            break;
        case part::value_start:
        case part::value:
            if (field_ == field::id) {
                memcpy(id_, small_, small_len_);
                id_len_ = small_len_;
            } else if (field_ == field::event) {
                memcpy(type_, small_, small_len_);
                type_len_ = small_len_;
            }
            break;
        case part::comment:
            small_[small_len_] = '\0';
            on_comment(ctx, small_[0] == ' ' ? small_ + 1 : small_);
            break;
        case part::skip:
            break;
        }
        part_ = part::start;
    }

    void dispatch(event_fn on_event, void *ctx)
    {
        if (has_data_) {
            id_[id_len_] = '\0';
            type_[type_len_] = '\0';
            buf_[oversize_ ? 0 : len_] = '\0';
            const caption_sse_event event = {buf_, oversize_ ? 0 : len_, id_, type_, oversize_};
            on_event(ctx, event);
        }
        len_ = 0;
        has_data_ = false;
        oversize_ = false;
        id_len_ = 0;
        type_len_ = 0;
    }
};

// ---------------------------------------------------------------------------
// The 200-line buffer
// ---------------------------------------------------------------------------

constexpr int kCaptionLines = 200;

struct caption_line {
    uint64_t seq;
    char *text;
    uint16_t len;
};

using caption_alloc_fn = void *(*)(size_t);
using caption_free_fn = void (*)(void *);

// The last kCaptionLines finished lines of one feed, oldest first. Each line's text is its own
// allocation, from the allocator the owner gives (PSRAM on the board), so the buffer costs what its
// lines cost and not 200 times the longest. Adding the 201st line frees the oldest.
class caption_buffer {
public:
    caption_buffer(caption_alloc_fn alloc, caption_free_fn release) : alloc_(alloc), free_(release) {}
    ~caption_buffer() { clear(); }
    caption_buffer(const caption_buffer &) = delete;
    caption_buffer &operator=(const caption_buffer &) = delete;

    int size() const { return count_; }
    // Line `i`, 0 the oldest.
    const caption_line &at(int i) const { return lines_[(head_ + i) % kCaptionLines]; }

    bool contains(uint64_t seq) const
    {
        for (int i = 0; i < count_; ++i) {
            if (at(i).seq == seq) {
                return true;
            }
        }
        return false;
    }

    // Copy `text` in as the newest line. False when the allocation fails; the buffer is unchanged.
    bool push(uint64_t seq, const char *text, size_t len)
    {
        if (len > 0xFFFF) {
            len = 0xFFFF;
        }
        char *copy = static_cast<char *>(alloc_(len + 1));
        if (copy == nullptr) {
            return false;
        }
        memcpy(copy, text, len);
        copy[len] = '\0';
        if (count_ == kCaptionLines) {
            free_(lines_[head_].text);
            lines_[head_] = {seq, copy, static_cast<uint16_t>(len)};
            head_ = (head_ + 1) % kCaptionLines;
        } else {
            lines_[(head_ + count_) % kCaptionLines] = {seq, copy, static_cast<uint16_t>(len)};
            ++count_;
        }
        return true;
    }

    void clear()
    {
        for (int i = 0; i < count_; ++i) {
            free_(lines_[(head_ + i) % kCaptionLines].text);
        }
        head_ = 0;
        count_ = 0;
    }

private:
    caption_alloc_fn alloc_;
    caption_free_fn free_;
    caption_line lines_[kCaptionLines] = {};
    int head_ = 0;
    int count_ = 0;
};

// ---------------------------------------------------------------------------
// One feed: what each message does to it
// ---------------------------------------------------------------------------

enum class caption_change {
    ignored,    // not a finished line, a snapshot or targets; or a duplicate
    malformed,  // not JSON the client reads
    line,       // a finished line was added
    rebuilt,    // a snapshot replaced the buffer with its lines
    cleared,    // a snapshot with no lines: a reset
    targets,    // the live language set changed
    no_memory,  // a line could not be stored
};

// The longest `targets` message kept. Relay's is a few hundred bytes per language.
constexpr size_t kCaptionTargetsMax = 4096;

class caption_feed {
public:
    caption_feed(caption_alloc_fn alloc, caption_free_fn release) : lines_(alloc, release) {}

    const caption_buffer &lines() const { return lines_; }
    // The highest seq applied: what a reconnect asks to resume after. 0 until there is one, and then
    // the connection asks for a snapshot.
    uint64_t resume_id() const { return last_seq_; }
    const char *targets() const { return targets_; }
    uint32_t targets_revision() const { return targets_rev_; }
    // Lines whose text could not be stored, since this feed started.
    uint32_t lost() const { return lost_; }

    caption_change apply(const char *json, size_t len)
    {
        const char *e = json + len;
        const char *p = caption_json::ws(json, e);
        if (p >= e || *p != '{' || caption_json::skip_value(p, e) == nullptr) {
            return caption_change::malformed;
        }
        char type[16];
        const char *type_value = caption_json::get(p, e, "type");
        if (type_value != nullptr) {
            if (!caption_json::read_string(type_value, e, type, sizeof type, nullptr)) {
                return caption_change::malformed;
            }
            if (strcmp(type, "snapshot") == 0) {
                return apply_snapshot(p, e);
            }
            if (strcmp(type, "targets") == 0) {
                if (len >= sizeof targets_) {
                    return caption_change::ignored;
                }
                memcpy(targets_, json, len);
                targets_[len] = '\0';
                ++targets_rev_;
                return caption_change::targets;
            }
            return caption_change::ignored;
        }

        bool final = false;
        if (!caption_json::read_bool(caption_json::get(p, e, "final"), e, &final) || !final) {
            return caption_change::ignored;
        }
        uint64_t seq = 0;
        if (!caption_json::read_uint(caption_json::get(p, e, "seq"), e, &seq) || seq == 0) {
            return caption_change::malformed;
        }
        // **A seq already held is a duplicate and is dropped.** One lower than the last that is not
        // held is a Relay that restarted and counts from 1 again: it is taken, and the resume point
        // follows it down.
        if (seq <= last_seq_ && lines_.contains(seq)) {
            return caption_change::ignored;
        }
        const caption_change added = add(seq, caption_json::get(p, e, "text"), e);
        if (added == caption_change::line) {
            last_seq_ = seq;
        }
        return added;
    }

private:
    caption_buffer lines_;
    uint64_t last_seq_ = 0;
    char targets_[kCaptionTargetsMax] = {};
    uint32_t targets_rev_ = 0;
    uint32_t lost_ = 0;
    char raw_[kCaptionRawMax];
    char folded_[kCaptionLineMax + 1];

    caption_change add(uint64_t seq, const char *text_value, const char *e)
    {
        size_t raw_len = 0;
        if (!caption_json::read_string(text_value, e, raw_, sizeof raw_, &raw_len)) {
            return caption_change::malformed;
        }
        const size_t n = caption_fold(raw_, raw_len, folded_, sizeof folded_);
        if (n == 0) {
            return caption_change::ignored;
        }
        if (!lines_.push(seq, folded_, n)) {
            ++lost_;
            return caption_change::no_memory;
        }
        return caption_change::line;
    }

    // **A snapshot replaces the buffer, as the browser viewer does.** It arrives on a fresh
    // connection, on a resume older than Relay's history, and on a reset. The first two can leave a
    // gap that only the server's history can fill, and the last must empty the screen, so the panel
    // shows exactly what Relay holds. Set Relay's history to 200 lines for a full refill.
    caption_change apply_snapshot(const char *p, const char *e)
    {
        caption_json::array_iter it;
        if (!caption_json::array_begin(caption_json::get(p, e, "lines"), e, &it)) {
            return caption_change::malformed;
        }
        lines_.clear();
        uint64_t top = 0;
        while (const char *line = caption_json::array_next(&it)) {
            uint64_t seq = 0;
            if (!caption_json::read_uint(caption_json::get(line, e, "seq"), e, &seq)) {
                continue;
            }
            if (add(seq, caption_json::get(line, e, "text"), e) == caption_change::line && seq > top) {
                top = seq;
            }
        }
        if (top != 0) {
            last_seq_ = top;
        }
        return lines_.size() == 0 ? caption_change::cleared : caption_change::rebuilt;
    }
};

// ---------------------------------------------------------------------------
// The connection: where, what to ask for, and how long to wait between tries
// ---------------------------------------------------------------------------

// Split a stored Relay address (`http://host[:port]`, as the setup page normalises it; the scheme and
// a trailing "/" are optional) into its host and port. False for anything else.
inline bool caption_url_split(const char *url, char *host, size_t host_cap, uint16_t *port)
{
    if (url == nullptr || host_cap == 0) {
        return false;
    }
    const char *s = url;
    if (strncmp(s, "http://", 7) == 0 || strncmp(s, "HTTP://", 7) == 0) {
        s += 7;
    }
    const char *end = s + strlen(s);
    if (end > s && end[-1] == '/') {
        --end;
    }
    const char *colon = nullptr;
    for (const char *q = s; q < end; ++q) {
        if (*q == '/' || *q == '@' || *q == '?' || *q == '#' || *q == ' ' || *q == '[') {
            return false;
        }
        if (*q == ':') {
            colon = q;
        }
    }
    const char *host_end = colon != nullptr ? colon : end;
    const size_t n = static_cast<size_t>(host_end - s);
    if (n == 0 || n >= host_cap) {
        return false;
    }
    uint32_t p = 80;
    if (colon != nullptr) {
        p = 0;
        if (colon + 1 == end || end - colon > 6) {
            return false;
        }
        for (const char *q = colon + 1; q < end; ++q) {
            if (*q < '0' || *q > '9') {
                return false;
            }
            p = p * 10 + static_cast<uint32_t>(*q - '0');
        }
        if (p == 0 || p > 65535) {
            return false;
        }
    }
    memcpy(host, s, n);
    host[n] = '\0';
    *port = static_cast<uint16_t>(p);
    return true;
}

// The request for a feed. `stream` is kFeedTranscription (1), kFeedTranslation (2), kFeedService (3) or kFeedAlert (4), as stored in
// the settings; `lang` is the translation's language as Relay names it, or the service type id. `resume_id` 0 asks for a snapshot.
// Returns the length, or 0 when it does not fit in `cap` or the feed is not one.
inline size_t caption_request(char *out, size_t cap, const char *host, uint16_t port, uint8_t stream,
                              const char *lang, uint64_t resume_id, const char *version)
{
    char query[96];
    size_t q = 0;
    auto add = [&](const char *s) {
        const size_t n = strlen(s);
        if (q + n >= sizeof query) {
            return false;
        }
        memcpy(query + q, s, n);
        q += n;
        query[q] = '\0';
        return true;
    };
    if (stream == 1) {
        add("stream=transcription");
    } else if (stream == 2) {
        if (lang == nullptr || lang[0] == '\0') {
            return 0;
        }
        add("stream=translation&lang=");
        for (const char *c = lang; *c != '\0'; ++c) {
            const bool plain = (*c >= 'A' && *c <= 'Z') || (*c >= 'a' && *c <= 'z') ||
                               (*c >= '0' && *c <= '9') || *c == '-' || *c == '_' || *c == '.' || *c == '~';
            char piece[4];
            if (plain) {
                piece[0] = *c, piece[1] = '\0';
            } else {
                snprintf(piece, sizeof piece, "%%%02X", static_cast<unsigned char>(*c));
            }
            if (!add(piece)) {
                return 0;
            }
        }
    } else if (stream == 3) {
        // The Planning Center service (servicefeed.py): whole state per message, nothing to resume.
        if (lang == nullptr || lang[0] == '\0' || strlen(lang) > 20) {
            return 0;
        }
        for (const char *c = lang; *c != '\0'; ++c) {
            if (*c < '0' || *c > '9') {
                return 0;
            }
        }
        add("stream=service&type=");
        if (!add(lang)) {
            return 0;
        }
        resume_id = 0;
    } else if (stream == 4) {
        // The alert buttons (#121): whole state per message, nothing to resume.
        add("stream=alert");
        resume_id = 0;
    } else {
        return 0;
    }
    if (stream != 3 && stream != 4 && !add("&finals=1")) {
        return 0;
    }
    if (resume_id != 0) {
        char tail[32];
        snprintf(tail, sizeof tail, "&last_id=%llu", static_cast<unsigned long long>(resume_id));
        if (!add(tail)) {
            return 0;
        }
    }
    char host_header[80];
    if (port == 80) {
        snprintf(host_header, sizeof host_header, "%s", host);
    } else {
        snprintf(host_header, sizeof host_header, "%s:%u", host, static_cast<unsigned>(port));
    }
    const int n = snprintf(out, cap,
                           "GET /stream?%s HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Accept: text/event-stream\r\n"
                           "Cache-Control: no-cache\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n",
                           query, host_header, version);
    if (n <= 0 || static_cast<size_t>(n) >= cap) {
        return 0;
    }
    return static_cast<size_t>(n);
}

// The request for what is live: `GET /api/targets`, which answers with the `targets` message's fields (no `type`).
// **Relay's `/stream` says what is live only when that changes**, not when a client connects, so a panel asks for it
// once itself, and then keeps up from the stream's `targets` messages. Returns the length, or 0 when it does not fit.
inline size_t caption_targets_request(char *out, size_t cap, const char *host, uint16_t port, const char *version)
{
    char host_header[160];
    if (port == 80) {
        snprintf(host_header, sizeof host_header, "%s", host);
    } else {
        snprintf(host_header, sizeof host_header, "%s:%u", host, static_cast<unsigned>(port));
    }
    const int n = snprintf(out, cap,
                           "GET /api/targets HTTP/1.1\r\n"
                           "Host: %s\r\n"
                           "Accept: application/json\r\n"
                           "User-Agent: relay-display/%s\r\n"
                           "Connection: close\r\n"
                           "\r\n",
                           host_header, version);
    if (n <= 0 || static_cast<size_t>(n) >= cap) {
        return 0;
    }
    return static_cast<size_t>(n);
}

// What the UI is told about a feed's connection.
enum class caption_state : uint8_t {
    off,                  // no feed chosen, or no Relay address
    waiting_for_network,  // the panel has no address yet
    connecting,           // the first try for this feed
    connected,            // the stream is open
    reconnecting,         // the stream dropped or a try failed; trying again
    relay_not_running,    // the host answered and nothing is serving captions there
    feed_refused,         // Relay refused the feed (a language it does not offer)
};

inline const char *caption_state_name(caption_state s)
{
    switch (s) {
    case caption_state::off: return "off";
    case caption_state::waiting_for_network: return "waiting for network";
    case caption_state::connecting: return "connecting";
    case caption_state::connected: return "connected";
    case caption_state::reconnecting: return "reconnecting";
    case caption_state::relay_not_running: return "relay not running";
    case caption_state::feed_refused: return "feed refused";
    }
    return "?";
}

// How a connection ended.
enum class caption_end {
    closed,       // the server closed the stream
    silent,       // nothing, not even a ping, for the silence limit
    refused,      // the TCP connection was refused: the host is up and nothing listens
    unreachable,  // no route, no answer, or the name did not resolve
    http_status,  // an answer other than 200
    bad_response, // not an event stream
    stopped,      // the feed was changed or turned off
};

// What the UI shows after an ended connection. A refused connection, or a gateway with nothing behind
// it (Caddy's 502, 503 and 504 while Relay's app is down), is "Relay not running"; a 4xx is a feed
// Relay will not serve; everything else is a drop that is being retried.
inline caption_state caption_state_after(caption_end end, int http_status)
{
    switch (end) {
    case caption_end::refused:
        return caption_state::relay_not_running;
    case caption_end::http_status:
        if (http_status >= 500) {
            return caption_state::relay_not_running;
        }
        if (http_status >= 400) {
            return caption_state::feed_refused;
        }
        return caption_state::reconnecting;
    default:
        return caption_state::reconnecting;
    }
}

// The wait before try number `failures` + 1, in ms: 1 s doubling to 30 s. The caller adds up to a
// quarter more at random, so panels that lost Relay together do not all come back in one second.
inline uint32_t caption_backoff_ms(uint32_t failures)
{
    constexpr uint32_t kFirst = 1000;
    constexpr uint32_t kMost = 30000;
    if (failures > 5) {
        failures = 5;
    }
    const uint32_t ms = kFirst << failures;
    return ms > kMost ? kMost : ms;
}

// The failure count after a connection. A connection that was open for `kCaptionHealthyMs` resets it,
// so a long-lived stream that drops is retried at once; one that dies straight after opening keeps
// backing off, so a server that accepts and closes is not hammered.
constexpr uint32_t kCaptionHealthyMs = 10000;

inline uint32_t caption_failures_after(uint32_t failures, bool opened, uint32_t open_ms)
{
    if (opened && open_ms >= kCaptionHealthyMs) {
        return 0;
    }
    return failures + 1;
}
