#pragma once

// What the panel is drawn with, and how a frame is laid out, with no ESP-IDF in it
//. `display.cpp` owns the driver and calls these; `test_native.cpp`
// compiles this header with g++ and checks it on a laptop, so a wrong colour bar
// or a wrong mirror flag fails the build before a panel is plugged in.
//
// Everything here is a pure function of its arguments. The frame buffer, the
// driver calls and the backlight stay in `display.cpp`.

#include <cstdint>
#include <cstring>

#include "config.h"
#include "ui_scale.h"

// ---------------------------------------------------------------------------
// Colour
// ---------------------------------------------------------------------------

// RGB565: five bits of red, six of green and five of blue, packed high to low.
// The low bits of each 8-bit channel are dropped.
constexpr uint16_t rgb565_pack(uint8_t r, uint8_t g, uint8_t b)
{
    return static_cast<uint16_t>(((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3));
}

constexpr uint16_t kBlack = rgb565_pack(0, 0, 0);
constexpr uint16_t kWhite = rgb565_pack(255, 255, 255);
constexpr uint16_t kRed = rgb565_pack(255, 0, 0);
constexpr uint16_t kYellow = rgb565_pack(255, 255, 0);
constexpr uint16_t kCyan = rgb565_pack(0, 255, 255);
constexpr uint16_t kGreen = rgb565_pack(0, 255, 0);
constexpr uint16_t kMagenta = rgb565_pack(255, 0, 255);
constexpr uint16_t kBlue = rgb565_pack(0, 0, 255);

// ---------------------------------------------------------------------------
// Rotation
// ---------------------------------------------------------------------------

// The orientation is 0, 90, 180 or 270 degrees, clockwise: how far the picture is turned on the glass. 0 and 180
// are landscape, the panel's own 800 x 480; 90 and 270 are portrait, a 480 x 800 picture (#122). Every turn is
// made in software as the picture is written to the frame buffer (`display.cpp`), and touch is turned back the
// same way (`touch_format.h`).
constexpr bool panel_rotation_valid(int deg)
{
    return deg == 0 || deg == 90 || deg == 180 || deg == 270;
}

constexpr bool panel_rotation_mirrors(int deg)
{
    return deg == 180;
}

constexpr bool panel_rotation_portrait(int deg)
{
    return deg == 90 || deg == 270;
}

// The picture's size at `deg`: what LVGL lays out.
constexpr int panel_logical_width(int deg)
{
    return panel_rotation_portrait(deg) ? kLcdHeight : kLcdWidth;
}

constexpr int panel_logical_height(int deg)
{
    return panel_rotation_portrait(deg) ? kLcdWidth : kLcdHeight;
}

// The glass pixel that picture pixel (lx, ly) lands on at `deg`. At 90 the picture's top edge runs down the
// glass's right-hand side; at 270, down its left.
constexpr int panel_physical_x(int lx, int ly, int deg)
{
    return deg == 180 ? kLcdWidth - 1 - lx : deg == 90 ? kLcdWidth - 1 - ly : deg == 270 ? ly : lx;
}

constexpr int panel_physical_y(int lx, int ly, int deg)
{
    return deg == 180 ? kLcdHeight - 1 - ly : deg == 90 ? lx : deg == 270 ? kLcdHeight - 1 - lx : ly;
}

// The picture pixel on glass pixel (px, py) at `deg`: the inverse of the two above.
constexpr int panel_logical_x(int px, int py, int deg)
{
    return deg == 180 ? kLcdWidth - 1 - px : deg == 90 ? py : deg == 270 ? kLcdHeight - 1 - py : px;
}

constexpr int panel_logical_y(int px, int py, int deg)
{
    return deg == 180 ? kLcdHeight - 1 - py : deg == 90 ? kLcdWidth - 1 - px : deg == 270 ? px : py;
}

// ---------------------------------------------------------------------------
// Strips
//
// The frame is painted a few rows at a time. These say which rows each strip
// covers. The panel is 480 rows and eight rows a strip divides it exactly, so the
// last strip is full, but the schedule does not assume that: a height that is not
// a multiple gets a short last strip rather than a write past the frame.
// ---------------------------------------------------------------------------

// Rows a strip holds. Eight rows is 12,800 bytes of internal RAM for the strip
// buffer and 60 driver calls for the panel. A whole-frame buffer would be 768 KB of
// CPU writes, and nothing in this target needs the frame built in one place.
constexpr int kStripRows = 8;

struct strip_span {
    int y0;    // the first row of the strip
    int rows;  // how many rows it covers, at most the strip height
};

constexpr int strip_count(int height, int strip_rows)
{
    return (height + strip_rows - 1) / strip_rows;
}

constexpr strip_span strip_at(int index, int height, int strip_rows)
{
    const int y0 = index * strip_rows;
    const int left = height - y0;
    return strip_span{y0, left < strip_rows ? left : strip_rows};
}

// Fill `out` with rows [y0, y0 + rows) of the frame, `width` pixels a row, from
// `pixel(x, y)`. `out` holds `rows * width` pixels.
template <typename Pixel>
void render_strip(Pixel pixel, int y0, int rows, int width, uint16_t *out)
{
    for (int r = 0; r < rows; ++r) {
        for (int x = 0; x < width; ++x) {
            out[r * width + x] = pixel(x, y0 + r);
        }
    }
}

// ---------------------------------------------------------------------------
// The panel's text. Glyphs are 5 columns by 7 rows, written for this file from the
// shapes the characters need. Each row is five bits, leftmost column in bit 4.
//
// The first fifteen entries are the bench test screen's, and keep their indices: the
// shape checks in `test_native.cpp` read them by number. The rest were added for the
// setup screen, which has to print a network name, a passphrase and an
// address. A character the font lacks prints as a space, and the native test says
// which ones those are.
// ---------------------------------------------------------------------------

constexpr int kGlyphColumns = 5;
constexpr int kGlyphRows = 7;
constexpr int kGlyphGap = 1;  // blank columns between glyphs
constexpr int kSpaceIndex = 14;

// The glyph for each character, in the order of `kGlyphChars` below.
constexpr uint8_t kGlyphs[71][kGlyphRows] = {
    {0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E},  // 0
    {0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E},  // 1
    {0x0E, 0x11, 0x01, 0x02, 0x04, 0x08, 0x1F},  // 2
    {0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E},  // 3
    {0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02},  // 4
    {0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E},  // 5
    {0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E},  // 6
    {0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08},  // 7
    {0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E},  // 8
    {0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C},  // 9
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x0C, 0x0C},  // .
    {0x11, 0x11, 0x11, 0x11, 0x11, 0x0A, 0x04},  // V
    {0x11, 0x11, 0x0A, 0x04, 0x0A, 0x11, 0x11},  // X
    {0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10},  // P
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00},  // space
    {0x00, 0x0C, 0x0C, 0x00, 0x0C, 0x0C, 0x00},  // :
    {0x01, 0x01, 0x02, 0x04, 0x08, 0x10, 0x10},  // /
    {0x00, 0x00, 0x00, 0x1F, 0x00, 0x00, 0x00},  // -
    {0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x1F},  // _
    {0x00, 0x00, 0x00, 0x00, 0x0C, 0x04, 0x08},  // ,
    {0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04},  // !
    {0x0E, 0x11, 0x01, 0x02, 0x04, 0x00, 0x04},  // ?
    {0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},  // A
    {0x1E, 0x11, 0x11, 0x1E, 0x11, 0x11, 0x1E},  // B
    {0x0E, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0E},  // C
    {0x1C, 0x12, 0x11, 0x11, 0x11, 0x12, 0x1C},  // D
    {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x1F},  // E
    {0x1F, 0x10, 0x10, 0x1E, 0x10, 0x10, 0x10},  // F
    {0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F},  // G
    {0x11, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11},  // H
    {0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E},  // I
    {0x07, 0x02, 0x02, 0x02, 0x12, 0x12, 0x0C},  // J
    {0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11},  // K
    {0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F},  // L
    {0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11},  // M
    {0x11, 0x13, 0x15, 0x19, 0x11, 0x11, 0x11},  // N
    {0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},  // O
    {0x0E, 0x11, 0x11, 0x11, 0x15, 0x12, 0x0D},  // Q
    {0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11},  // R
    {0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E},  // S
    {0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04},  // T
    {0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E},  // U
    {0x11, 0x11, 0x11, 0x15, 0x15, 0x15, 0x0A},  // W
    {0x11, 0x11, 0x11, 0x0A, 0x04, 0x04, 0x04},  // Y
    {0x1F, 0x01, 0x02, 0x04, 0x08, 0x10, 0x1F},  // Z
    {0x00, 0x00, 0x0E, 0x01, 0x0F, 0x11, 0x0F},  // a
    {0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x1E},  // b
    {0x00, 0x00, 0x0E, 0x10, 0x10, 0x11, 0x0E},  // c
    {0x01, 0x01, 0x0D, 0x13, 0x11, 0x11, 0x0F},  // d
    {0x00, 0x00, 0x0E, 0x11, 0x1F, 0x10, 0x0E},  // e
    {0x06, 0x09, 0x08, 0x1C, 0x08, 0x08, 0x08},  // f
    {0x00, 0x0F, 0x11, 0x11, 0x0F, 0x01, 0x0E},  // g
    {0x10, 0x10, 0x16, 0x19, 0x11, 0x11, 0x11},  // h
    {0x04, 0x00, 0x0C, 0x04, 0x04, 0x04, 0x0E},  // i
    {0x02, 0x00, 0x06, 0x02, 0x02, 0x12, 0x0C},  // j
    {0x10, 0x10, 0x12, 0x14, 0x18, 0x14, 0x12},  // k
    {0x0C, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E},  // l
    {0x00, 0x00, 0x1A, 0x15, 0x15, 0x15, 0x15},  // m
    {0x00, 0x00, 0x16, 0x19, 0x11, 0x11, 0x11},  // n
    {0x00, 0x00, 0x0E, 0x11, 0x11, 0x11, 0x0E},  // o
    {0x00, 0x00, 0x1E, 0x11, 0x1E, 0x10, 0x10},  // p
    {0x00, 0x00, 0x0D, 0x13, 0x0F, 0x01, 0x01},  // q
    {0x00, 0x00, 0x16, 0x19, 0x10, 0x10, 0x10},  // r
    {0x00, 0x00, 0x0F, 0x10, 0x0E, 0x01, 0x1E},  // s
    {0x08, 0x08, 0x1C, 0x08, 0x08, 0x09, 0x06},  // t
    {0x00, 0x00, 0x11, 0x11, 0x11, 0x13, 0x0D},  // u
    {0x00, 0x00, 0x11, 0x11, 0x11, 0x0A, 0x04},  // v
    {0x00, 0x00, 0x11, 0x11, 0x15, 0x15, 0x0A},  // w
    {0x00, 0x00, 0x11, 0x0A, 0x04, 0x0A, 0x11},  // x
    {0x00, 0x00, 0x11, 0x11, 0x0F, 0x01, 0x0E},  // y
    {0x00, 0x00, 0x1F, 0x02, 0x04, 0x08, 0x1F},  // z
};

// The characters the glyph table holds, in table order. Position n is glyph n. The
// space is at `kSpaceIndex`, which is also what any character not in the string
// prints as.
constexpr char kGlyphChars[] =
    "0123456789.VXP "    // 0..14: the bench screen's characters, space last
    ":/-_,!?"            // 15..21
    "ABCDEFGHIJKLMNOQRSTUWYZ"  // 22..44: the capitals, without V, X and P above
    "abcdefghijklmnopqrstuvwxyz";  // 45..70

constexpr int kGlyphCount = static_cast<int>(sizeof(kGlyphChars) - 1);
static_assert(kGlyphCount == 71, "kGlyphChars and kGlyphs must name the same glyphs");

// Which row of `kGlyphs` a character draws with. Anything not in `kGlyphChars` is the
// space, so an unknown character is blank rather than a stray glyph or a read past
// the table.
inline int glyph_index(char c)
{
    for (int i = 0; i < kGlyphCount; ++i) {
        if (kGlyphChars[i] == c) {
            return i;
        }
    }
    return kSpaceIndex;
}

// Pixel width of `chars` glyphs at `scale`, without the gap after the last one.
inline int text_width(int chars, int scale)
{
    return chars * (kGlyphColumns + kGlyphGap) * scale - kGlyphGap * scale;
}

// Whether the pixel (x, y) is lit by the first `chars` characters of `text`,
// drawn with its top-left at (x0, y0) and each glyph pixel `scale` screen pixels
// square. Pixels outside the text, above or below it, and in the gaps between
// glyphs are not lit. Nothing past the `chars`th character is read, so a string
// longer than the screen clips at the edge without touching memory beyond it.
inline bool text_lit(const char *text, int chars, int scale, int x0, int y0, int x, int y)
{
    const int advance = (kGlyphColumns + kGlyphGap) * scale;
    const int dx = x - x0;
    const int dy = y - y0;
    if (dx < 0 || dy < 0 || dy >= kGlyphRows * scale) {
        return false;
    }
    // The bound is checked before the index is used. A pixel to the right of the
    // text would otherwise read past the terminator.
    const int index = dx / advance;
    if (index >= chars) {
        return false;
    }
    const int in_glyph = dx % advance;
    if (in_glyph >= kGlyphColumns * scale) {
        return false;
    }
    const int col = in_glyph / scale;
    const int row = dy / scale;
    return ((kGlyphs[glyph_index(text[index])][row] >> (kGlyphColumns - 1 - col)) & 1) != 0;
}

// ---------------------------------------------------------------------------
// The bench test screen
// ---------------------------------------------------------------------------

// Colour of the panel pixel (x, y) on the test screen, in the logical frame. The
// driver mirrors the frame for 180 degrees, so the red marker moves to the opposite
// corner and the bars read in reverse order. That is the bench check.
//
// Layout, in panel pixels: a white border six pixels wide; a red square in the
// top-left corner; eight colour bars across the top 320 rows, each 100 pixels wide;
// the name "RELAY" at scale 8 and `version` at scale 4, both centred, below the bars.
// `version_chars` is `strlen(version)`, passed in so the caller measures once.
inline uint16_t test_screen_pixel(int x, int y, const char *version, int version_chars)
{
    constexpr int kBorder = 6;
    constexpr int kBarsBottom = 320;
    constexpr uint16_t kBars[8] = {kWhite, kYellow, kCyan, kGreen, kMagenta, kRed, kBlue, kBlack};

    if (x < kBorder || y < kBorder || x >= kLcdWidth - kBorder || y >= kLcdHeight - kBorder) {
        return kWhite;
    }
    if (x >= 16 && x < 56 && y >= 16 && y < 56) {
        return kRed;
    }
    if (y < kBarsBottom) {
        return kBars[x * 8 / kLcdWidth];
    }

    constexpr int kTitleScale = 8;
    constexpr int kTitleY = 340;
    const int title_x = (kLcdWidth - text_width(5, kTitleScale)) / 2;
    if (text_lit("RELAY", 5, kTitleScale, title_x, kTitleY, x, y)) {
        return kWhite;
    }

    constexpr int kVersionScale = 4;
    constexpr int kVersionY = 420;
    const int version_x = (kLcdWidth - text_width(version_chars, kVersionScale)) / 2;
    if (text_lit(version, version_chars, kVersionScale, version_x, kVersionY, x, y)) {
        return kWhite;
    }
    return kBlack;
}

// ---------------------------------------------------------------------------
// Text screens
//
// The setup screen and the network status screen were stacks of lines of text, each at its own scale,
// centred and set down from the middle of the panel, drawn with these glyphs. The room screen draws every
// such stack now, with LVGL, at the four sizes `room_layout.h` maps the scales
// onto. This function is no longer called by the firmware. It stays as the reference for the stack's
// layout, which the native test checks, until the layout is covered by the room screen's own checks.
// ---------------------------------------------------------------------------

struct panel_text_line {
    const char *text;  // NUL-terminated; a character the font lacks prints as a space
    int scale;         // each glyph pixel is this many screen pixels square
    // Set in the monospaced face instead of the proportional one. The fingerprint's rows set it, so
    // a digit in one row sits over the same column in the other device's row. The 5x7 fallback draws every
    // line the same way, so the flag is only read by the room screen.
    bool mono = false;
};

constexpr int kPanelTextGap = ui_px(18);  // screen pixels between two lines
constexpr int kPanelTextMaxLines = 9;  // the pairing screen's step line made it nine

constexpr int panel_text_line_height(int scale)
{
    return kGlyphRows * scale;
}

// Colour of the pixel (x, y) of a `width` by `height` panel showing `lines`: white for
// text on black. The stack is centred vertically and each line horizontally. A line
// wider than the panel is clipped at the edge, and `text_lit` never reads past its
// terminator, so a long network name cannot run off the buffer.
inline uint16_t text_screen_pixel(int x, int y, const panel_text_line *lines, int count, int width, int height)
{
    if (count <= 0 || count > kPanelTextMaxLines) {
        return kBlack;
    }
    int total = 0;
    for (int i = 0; i < count; ++i) {
        total += panel_text_line_height(lines[i].scale);
    }
    total += kPanelTextGap * (count - 1);

    int line_y = (height - total) / 2;
    for (int i = 0; i < count; ++i) {
        const int h = panel_text_line_height(lines[i].scale);
        if (y >= line_y && y < line_y + h) {
            const char *text = lines[i].text;
            const int chars = static_cast<int>(strlen(text));
            const int x0 = (width - text_width(chars, lines[i].scale)) / 2;
            return text_lit(text, chars, lines[i].scale, x0, line_y, x, y) ? kWhite : kBlack;
        }
        line_y += h + kPanelTextGap;
    }
    return kBlack;
}
