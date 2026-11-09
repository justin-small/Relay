// The parts of the Relay panel's firmware that can be wrong without a panel plugged in: the
// stored configuration's defaults, decode and string bounds; the setup page's form checks and
// page; the Wi-Fi start decisions; the rotation's mirror flags; RGB565 packing; and the bench
// test screen's colours, border, marker, text and strip schedule. The headers they come from
// are free of ESP-IDF, so this compiles and runs on a laptop.
//
// What is *not* here: the panel driver, NVS, the radio and the backlight. Those need the board.
// `display.cpp`, `settings.cpp` and `net.cpp` call the functions this file checks, and hold the
// calls that need the hardware.
//
// Adapted from the VXP compact display's test_native.cpp (vxp-compact-display 0.2.2): the checks
// for the panel, the Wi-Fi, the setup page and the button are carried over, and the checks for
// pairing, the room, the clock and the update are not, because this target has none of them.
//
// `build.sh` runs this before anything is built for the board:
//
//   c++ -std=c++17 -Wall -Werror -I components/... -o /tmp/relay-test test_native.cpp && /tmp/relay-test
//
// It is not in the component's SRCS (`main/CMakeLists.txt`): it has a `main()`, and linking it into
// the application would be a second one.
//
// No framework. A CHECK macro that reports the line, and a main that prints "ok" or exits 1.

#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <initializer_list>

#include <string>
#include <utility>
#include <vector>

#include "auth.h"
#include "board_format.h"
#include "button_policy.h"
#include "config.h"
#include "panel_format.h"
#include "settings_format.h"
#include "setup_format.h"
#include "setup_page.h"
#include "touch_format.h"
#include "wifi_policy.h"

namespace {

int g_failures = 0;

#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) {                                                           \
            fprintf(stderr, "%s:%d: check failed: %s\n", __FILE__, __LINE__, #cond); \
            ++g_failures;                                                        \
        }                                                                        \
    } while (0)

// A settings blob with every field filled in, as a board that was provisioned would hold it.
relay_settings_t example_settings()
{
    relay_settings_t s = {};
    settings_defaults_fill(&s, 180);
    settings_set_text(s.wifi_ssid, sizeof s.wifi_ssid, "Office-5G");
    settings_set_text(s.wifi_password, sizeof s.wifi_password, "correct horse");
    settings_set_text(s.relay_addr, sizeof s.relay_addr, "http://192.168.1.20:8000");
    settings_set_text(s.panel_id, sizeof s.panel_id, "0123456789abcdef0123456789abcdef");
    s.feed_stream = kFeedTranslation;
    settings_set_text(s.feed_lang, sizeof s.feed_lang, "SPANISH");
    return s;
}

// ---------------------------------------------------------------------------

// What the panel shows at (x, y) on the test screen, for the firmware's own version.
uint16_t screen(int x, int y)
{
    return test_screen_pixel(x, y, RELAY_FW_VERSION, static_cast<int>(strlen(RELAY_FW_VERSION)));
}

// Whether a glyph's pixel is lit: row `row`, column `col`, leftmost column first.

// Whether a glyph's pixel is lit: row `row`, column `col`, leftmost column first.
bool glyph_lit(int glyph, int row, int col)
{
    return ((kGlyphs[glyph][row] >> (kGlyphColumns - 1 - col)) & 1) != 0;
}


// ---------------------------------------------------------------------------

void check_panel_constants()
{
    // The test screen's layout is drawn for this panel.
    CHECK(kLcdWidth == 800);
    CHECK(kLcdHeight == 480);
    CHECK(kStripRows == 8);
}

// The board's pins, as Elecrow names them for the CrowPanel Advance 7.0 at V1.2 and later. The data
// list is in the driver's order, B0 first. A mistyped pin shows on the bench as a wrong colour, so
// this catches the table before a board is plugged in.

// The board's pins, as Elecrow names them for the CrowPanel Advance 7.0 at V1.2 and later. The data
// list is in the driver's order, B0 first. A mistyped pin shows on the bench as a wrong colour, so
// this catches the table before a board is plugged in.
void check_board_pins()
{
    constexpr int kData[16] = {21, 47, 48, 45, 38, 9, 10, 11, 12, 13, 14, 7, 17, 18, 3, 46};
    for (int i = 0; i < 16; ++i) {
        CHECK(kPinData[i] == kData[i]);
    }
    CHECK(kPinPclk == 39);
    CHECK(kPinHsync == 40);
    CHECK(kPinVsync == 41);
    CHECK(kPinDe == 42);
    CHECK(kPinI2cSda == 15);
    CHECK(kPinI2cScl == 16);

    // No two signals share a pin, and none of them is GPIO0, which is the BOOT button.
    int used[16 + 6];
    int n = 0;
    for (int i = 0; i < 16; ++i) {
        used[n++] = kPinData[i];
    }
    used[n++] = kPinPclk;
    used[n++] = kPinHsync;
    used[n++] = kPinVsync;
    used[n++] = kPinDe;
    used[n++] = kPinI2cSda;
    used[n++] = kPinI2cScl;
    for (int i = 0; i < n; ++i) {
        CHECK(used[i] != 0);
        for (int j = i + 1; j < n; ++j) {
            CHECK(used[i] != used[j]);
        }
    }
}

// The porches and sync widths give the frame: 820 by 500 clocks. The rate `Kconfig.projbuild`
// quotes follows from that and the pixel clock, so a change to one of these is a change to the help.

// The porches and sync widths give the frame: 820 by 500 clocks. The rate `Kconfig.projbuild`
// quotes follows from that and the pixel clock, so a change to one of these is a change to the help.
void check_panel_timing()
{
    CHECK(kHsyncPulse == 4 && kHsyncBack == 8 && kHsyncFront == 8);
    CHECK(kVsyncPulse == 4 && kVsyncBack == 8 && kVsyncFront == 8);
    CHECK(kHsyncPulse + kHsyncBack + kLcdWidth + kHsyncFront == 820);
    CHECK(kVsyncPulse + kVsyncBack + kLcdHeight + kVsyncFront == 500);
}

// The STC8 is at 0x30 and the RTC at 0x51, on a 400 kHz bus. The backlight pair is one byte each,
// and each revision has its own. The two values for a revision are never the same byte.

// The STC8 is at 0x30 and the RTC at 0x51, on a 400 kHz bus. The backlight pair is one byte each,
// and each revision has its own. The two values for a revision are never the same byte.
void check_stc8_commands()
{
    CHECK(kStc8Addr == 0x30);
    CHECK(kI2cSpeedHz == 400000);

    const stc8_backlight_commands v12 = stc8_backlight_for(true);
    CHECK(v12.on == 0x10);
    CHECK(v12.off == 0x05);

    const stc8_backlight_commands v13 = stc8_backlight_for(false);
    CHECK(v13.on == 0);
    CHECK(v13.off == 245);

    CHECK(v12.on != v12.off);
    CHECK(v13.on != v13.off);
}

void check_rgb565()
{
    CHECK(rgb565_pack(255, 255, 255) == 0xFFFF);
    CHECK(rgb565_pack(0, 0, 0) == 0x0000);
    CHECK(rgb565_pack(255, 0, 0) == 0xF800);
    CHECK(rgb565_pack(0, 255, 0) == 0x07E0);
    CHECK(rgb565_pack(0, 0, 255) == 0x001F);
    // The low three bits of red and blue and the low two of green are dropped, so a
    // value just under a step reads as the step below it.
    CHECK(rgb565_pack(7, 3, 7) == 0x0000);
    CHECK(rgb565_pack(8, 4, 8) == 0x0821);
    // The bar colours are the packed values, so a hand-typed constant cannot drift.
    CHECK(kBlack == 0x0000);
    CHECK(kWhite == 0xFFFF);
    CHECK(kRed == 0xF800);
    CHECK(kYellow == 0xFFE0);
    CHECK(kCyan == 0x07FF);
    CHECK(kGreen == 0x07E0);
    CHECK(kMagenta == 0xF81F);
    CHECK(kBlue == 0x001F);
}

void check_rotation()
{
    CHECK(panel_rotation_valid(0));
    CHECK(panel_rotation_valid(180));
    CHECK(!panel_rotation_valid(1));
    CHECK(!panel_rotation_valid(90));
    CHECK(!panel_rotation_valid(181));
    CHECK(!panel_rotation_valid(-180));
    CHECK(!panel_rotation_valid(360));

    // Only 180 mirrors. Upright draws the frame as it is.
    CHECK(!panel_rotation_mirrors(0));
    CHECK(panel_rotation_mirrors(180));
    CHECK(!panel_rotation_mirrors(90));
}

// The GT911's report, and where it lands on the picture.
void check_touch()
{
    CHECK(kGt911AddrPrimary == 0x5D);
    CHECK(kGt911AddrAlternate == 0x14);

    touch_raw t;
    // Ready, one point at (0x0123, 0x00A5) = (291, 165).
    const uint8_t one[kGt911ReadBytes] = {0x81, 0x00, 0x23, 0x01, 0xA5, 0x00, 0x10, 0x00, 0x00};
    CHECK(gt911_decode(one, &t) == touch_report::pressed);
    CHECK(t.pressed && t.x == 291 && t.y == 165);

    // Ready with no points: the finger is up.
    const uint8_t up[kGt911ReadBytes] = {0x80};
    t = {true, 5, 6};
    CHECK(gt911_decode(up, &t) == touch_report::released);
    CHECK(!t.pressed);

    // Not ready: the bytes are stale and the state stands, whatever they say.
    const uint8_t stale[kGt911ReadBytes] = {0x01, 0x00, 0x23, 0x01, 0xA5, 0x00};
    t = {true, 7, 8};
    CHECK(gt911_decode(stale, &t) == touch_report::none);
    CHECK(t.pressed && t.x == 7 && t.y == 8);

    // More points than the chip tracks is bus noise, not a release.
    const uint8_t noisy[kGt911ReadBytes] = {0x8F};
    CHECK(gt911_decode(noisy, &t) == touch_report::none);
    CHECK(t.pressed);

    // Upright: positions pass through, and a report past the edge is pulled onto it.
    CHECK(touch_logical_x(0, 0) == 0 && touch_logical_y(0, 0) == 0);
    CHECK(touch_logical_x(799, 0) == 799 && touch_logical_y(479, 0) == 479);
    CHECK(touch_logical_x(400, 0) == 400 && touch_logical_y(100, 0) == 100);
    CHECK(touch_logical_x(900, 0) == 799 && touch_logical_y(-3, 0) == 0);
    // Turned: both axes mirror, so the corners swap and the centre stays near the centre.
    CHECK(touch_logical_x(0, 180) == 799 && touch_logical_y(0, 180) == 479);
    CHECK(touch_logical_x(799, 180) == 0 && touch_logical_y(479, 180) == 0);
    CHECK(touch_logical_x(100, 180) == 699 && touch_logical_y(100, 180) == 379);
    CHECK(touch_logical_x(900, 180) == 0 && touch_logical_y(-3, 180) == 479);
}

void check_settings_text()
{
    char field[33];

    settings_set_text(field, sizeof field, "Office-5G");
    CHECK(strcmp(field, "Office-5G") == 0);

    settings_set_text(field, sizeof field, "");
    CHECK(field[0] == '\0');

    // Forty characters into a 33-byte field: the first 32 are kept and the field
    // ends in a terminator at 32. Nothing is written past it.
    const char *long_ssid = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789ABCD";
    CHECK(strlen(long_ssid) == 40);
    settings_set_text(field, sizeof field, long_ssid);
    CHECK(strlen(field) == 32);
    CHECK(field[32] == '\0');
    CHECK(memcmp(field, long_ssid, 32) == 0);

    // Exactly 32 characters fit, and the terminator is still at 32.
    settings_set_text(field, sizeof field, long_ssid + 8);
    CHECK(strlen(field) == 32);
    CHECK(field[32] == '\0');

    // A shorter value replaces a longer one: the new terminator ends it.
    settings_set_text(field, sizeof field, "x");
    CHECK(strlen(field) == 1);
    CHECK(field[1] == '\0');

    // A one-byte field holds only the terminator, and a zero-byte one is not written.
    char one[1] = {'q'};
    settings_set_text(one, sizeof one, "abc");
    CHECK(one[0] == '\0');
    char none[2] = {'q', 'z'};
    settings_set_text(none, 0, "abc");
    CHECK(none[0] == 'q' && none[1] == 'z');
}

void check_wifi_policy()
{
    // No SSID means no radio. Empty and missing both count.
    CHECK(!wifi_should_start(""));
    CHECK(!wifi_should_start(nullptr));
    CHECK(wifi_should_start("Office-5G"));
    char full[33];
    memset(full, 'S', 32);
    full[32] = '\0';
    CHECK(wifi_should_start(full));

    // An empty password is an open network. Anything else is WPA2, and a single
    // space is a password.
    CHECK(wifi_is_open(""));
    CHECK(wifi_is_open(nullptr));
    CHECK(!wifi_is_open("correct horse"));
    CHECK(!wifi_is_open(" "));
}

void check_glyphs()
{
    // Every character the screen prints has its own glyph, and none is blank.
    const char *drawn = "0123456789.VXP";
    const int count = static_cast<int>(strlen(drawn));
    for (int i = 0; i < count; ++i) {
        const int g = glyph_index(drawn[i]);
        CHECK(g != kSpaceIndex);
        bool any = false;
        for (int r = 0; r < kGlyphRows; ++r) {
            CHECK((kGlyphs[g][r] & ~0x1F) == 0);
            any = any || kGlyphs[g][r] != 0;
        }
        CHECK(any);
        for (int j = i + 1; j < count; ++j) {
            CHECK(g != glyph_index(drawn[j]));
        }
    }

    // The digits map to their own values.
    for (char d = '0'; d <= '9'; ++d) {
        CHECK(glyph_index(d) == d - '0');
    }

    // The version string is drawn in full, so every character in it has a glyph.
    // A version with a letter the font lacks would print a gap, and this fails
    // first.
    for (const char *p = RELAY_FW_VERSION; *p != '\0'; ++p) {
        CHECK(glyph_index(*p) != kSpaceIndex);
    }

    // Space, and anything the screen never prints, is blank. Unknown characters are
    // handled, not read past the table.
    CHECK(glyph_index(' ') == kSpaceIndex);
    CHECK(glyph_index('#') == kSpaceIndex);
    CHECK(glyph_index('@') == kSpaceIndex);
    CHECK(glyph_index('\0') == kSpaceIndex);
    for (int r = 0; r < kGlyphRows; ++r) {
        CHECK(kGlyphs[kSpaceIndex][r] == 0);
    }
    for (int r = 0; r < kGlyphRows; ++r) {
        for (int c = 0; c < kGlyphColumns; ++c) {
            CHECK(!text_lit(" ", 1, 1, 0, 0, c, r));
        }
    }
    // The characters added for the setup screen have glyphs of their own (#1267).
    CHECK(glyph_index('Q') != kSpaceIndex);
    CHECK(glyph_index('a') != kSpaceIndex);
    CHECK(glyph_index('-') != kSpaceIndex);
    CHECK(glyph_index('/') != kSpaceIndex);
    CHECK(glyph_index(':') != kSpaceIndex);

    // Shape checks, so a glyph filed under the wrong letter fails here and not on the
    // panel. V, X and 8 are symmetric left to right. P has a full stem on the left.
    for (int r = 0; r < kGlyphRows; ++r) {
        for (int c = 0; c < kGlyphColumns; ++c) {
            CHECK(glyph_lit(11, r, c) == glyph_lit(11, r, kGlyphColumns - 1 - c));
            CHECK(glyph_lit(12, r, c) == glyph_lit(12, r, kGlyphColumns - 1 - c));
            CHECK(glyph_lit(8, r, c) == glyph_lit(8, r, kGlyphColumns - 1 - c));
        }
        CHECK(glyph_lit(13, r, 0));
    }
    // A full stop sits on the baseline: nothing in the top five rows.
    for (int r = 0; r < 5; ++r) {
        for (int c = 0; c < kGlyphColumns; ++c) {
            CHECK(!glyph_lit(10, r, c));
        }
    }
    CHECK(glyph_lit(10, 6, 1) && glyph_lit(10, 6, 2) && !glyph_lit(10, 6, 0) && !glyph_lit(10, 6, 3));
}

// Every character the setup screen prints has a glyph: the network name's, the
// passphrase's and the page's address.

void check_test_screen_bars_and_border()
{
    // Eight bars, each 100 pixels wide, read along y = 200: below the marker and above
    // the bottom of the bars. The first bar starts inside the border, which is white,
    // and the last ends where the border starts.
    const uint16_t bars[8] = {kWhite, kYellow, kCyan, kGreen, kMagenta, kRed, kBlue, kBlack};
    for (int i = 0; i < 8; ++i) {
        const int left = i == 0 ? 6 : 100 * i;
        const int right = i == 7 ? 793 : 100 * i + 99;
        CHECK(screen(left, 200) == bars[i]);
        CHECK(screen(right, 200) == bars[i]);
    }

    // Every edge between two bars: the last pixel of one and the first of the next.
    for (int e = 100; e < 800; e += 100) {
        CHECK(screen(e - 1, 200) == bars[e / 100 - 1]);
        CHECK(screen(e, 200) == bars[e / 100]);
    }

    // The bars end at row 319, and the background starts at row 320.
    CHECK(screen(100, 319) == kYellow);
    CHECK(screen(100, 320) == kBlack);
    CHECK(screen(650, 319) == kBlue);

    // The border is white on all four sides. The left side is white like bar 0, so
    // the right, top and bottom are the ones that show it. The pixels at x=0 and
    // x=799 are on the border, and the bars are inside it.
    CHECK(screen(0, 0) == kWhite);
    CHECK(screen(799, 479) == kWhite);
    CHECK(screen(799, 200) == kWhite);
    CHECK(screen(0, 200) == kWhite);
    CHECK(screen(400, 5) == kWhite);
    CHECK(screen(400, 6) == kMagenta);
    CHECK(screen(793, 200) == kBlack);
    CHECK(screen(794, 200) == kWhite);
    CHECK(screen(400, 473) == kBlack);
    CHECK(screen(400, 474) == kWhite);
}

void check_test_screen_marker_title_version()
{
    // The red square: 40 pixels, from (16, 16) to (55, 55).
    CHECK(screen(16, 16) == kRed);
    CHECK(screen(55, 55) == kRed);
    CHECK(screen(15, 16) != kRed);
    CHECK(screen(56, 16) != kRed);
    CHECK(screen(16, 15) != kRed);
    CHECK(screen(16, 56) != kRed);

    // "RELAY" at scale 8: 232 pixels wide, starting at x=284, rows 340 to 395. R's top row is
    // 0x1E, so its first four columns are on and its fifth is off; E's top row is full and its
    // last row is full too. Each glyph pixel is eight screen pixels square.
    CHECK(screen(283, 340) == kBlack);
    CHECK(screen(284, 340) == kWhite);   // R, top-left
    CHECK(screen(315, 340) == kWhite);   // R, top row, fourth column
    CHECK(screen(316, 340) == kBlack);   // R, top row, the fifth column is off
    CHECK(screen(332, 340) == kWhite);   // E, top-left
    CHECK(screen(371, 340) == kWhite);   // E, top-right
    CHECK(screen(284, 395) == kWhite);   // R, last row of the stem
    CHECK(screen(292, 395) == kBlack);   // R, last row, the second column is off
    CHECK(screen(284, 396) == kBlack);   // one row below the title

    // The version at scale 4 with the firmware's string: "0.1.0" is 116 pixels wide,
    // starting at x=342, rows 420 to 447.
    CHECK(screen(342, 420) == kBlack);   // '0', top-left corner, unlit in its shape
    CHECK(screen(346, 420) == kWhite);   // '0', top row, second column
    CHECK(screen(366, 440) == kBlack);   // '.', the first column of its row
    CHECK(screen(370, 440) == kWhite);   // '.', lit in row 5
    CHECK(screen(458, 424) == kBlack);   // one pixel past the last glyph
    CHECK(screen(342 - 1, 420) == kBlack);
}

void check_text_clipping()
{
    // A single "1": lit where its glyph is, and nothing before the origin, above,
    // below, to the right of the text, or past its last glyph.
    CHECK(text_lit("1", 1, 4, 100, 420, 108, 420));
    CHECK(!text_lit("1", 1, 4, 100, 420, 99, 420));
    CHECK(!text_lit("1", 1, 4, 100, 420, 108, 419));
    CHECK(!text_lit("1", 1, 4, 100, 420, 108, 448));
    CHECK(!text_lit("1", 1, 4, 100, 420, 124, 420));

    // The gap between two glyphs is never lit.
    CHECK(!text_lit("11", 2, 4, 100, 420, 120, 420));
    CHECK(!text_lit("11", 2, 4, 100, 420, 123, 424));

    // A string wider than the panel starts off the left edge. The 80-character
    // version is 1916 pixels wide, so its origin is -558. Pixel x=0 is the seventh
    // column of the 24th character, a full stop, whose row 5 is lit in column 1.
    char longv[81];
    for (int i = 0; i < 80; ++i) {
        longv[i] = (i % 4 == 1 || i % 4 == 3) ? '.' : static_cast<char>('0' + (i % 4 == 0 ? 0 : 1));
    }
    longv[80] = '\0';
    CHECK(text_width(80, 4) == 1916);
    const int origin = (kLcdWidth - text_width(80, 4)) / 2;
    CHECK(origin == -558);
    CHECK(text_lit(longv, 80, 4, origin, 420, 0, 440));
    CHECK(!text_lit(longv, 80, 4, origin, 420, 0, 420));

    // The whole frame with that string never reads past its terminator, and it
    // draws. Run under -fsanitize=address, this is the check that the clip holds.
    int lit = 0;
    for (int y = 0; y < kLcdHeight; ++y) {
        for (int x = 0; x < kLcdWidth; ++x) {
            lit += test_screen_pixel(x, y, longv, 80) == kWhite ? 1 : 0;
        }
    }
    CHECK(lit > 0);
}

void check_strip_schedule()
{
    // The panel is 480 rows, eight to a strip: sixty strips, all full.
    CHECK(strip_count(kLcdHeight, kStripRows) == 60);
    const strip_span first = strip_at(0, kLcdHeight, kStripRows);
    CHECK(first.y0 == 0 && first.rows == 8);
    const strip_span last = strip_at(59, kLcdHeight, kStripRows);
    CHECK(last.y0 == 472 && last.rows == 8);

    // Every row is covered exactly once, in order, for the real panel and for heights
    // that do not divide evenly. A short last strip covers only the rows left.
    const int heights[] = {480, 470, 9, 8, 7, 1};
    for (int height : heights) {
        int covered[480] = {};
        int next_row = 0;
        const int strips = strip_count(height, kStripRows);
        for (int i = 0; i < strips; ++i) {
            const strip_span s = strip_at(i, height, kStripRows);
            CHECK(s.y0 == next_row);
            CHECK(s.rows > 0 && s.rows <= kStripRows);
            for (int r = 0; r < s.rows; ++r) {
                covered[s.y0 + r] += 1;
            }
            next_row = s.y0 + s.rows;
        }
        CHECK(next_row == height);
        for (int y = 0; y < height; ++y) {
            CHECK(covered[y] == 1);
        }
    }

    // A 470-row panel ends in a six-row strip, and that strip is the one that writes
    // only its six rows and leaves the rest of the buffer alone.
    const strip_span short_last = strip_at(58, 470, kStripRows);
    CHECK(short_last.y0 == 464 && short_last.rows == 6);

    // render_strip: the last full strip of the real panel, rows 472 to 479. Each row
    // is filled from its own y, and the buffer holds exactly rows times width pixels.
    static uint16_t buf[kStripRows * kLcdWidth];
    auto row_pixel = [](int, int y) { return static_cast<uint16_t>(y); };
    render_strip(row_pixel, last.y0, last.rows, kLcdWidth, buf);
    for (int r = 0; r < last.rows; ++r) {
        CHECK(buf[r * kLcdWidth + 0] == last.y0 + r);
        CHECK(buf[r * kLcdWidth + 400] == last.y0 + r);
        CHECK(buf[r * kLcdWidth + kLcdWidth - 1] == last.y0 + r);
    }

    // The short strip writes six rows and not one pixel more.
    for (int i = 0; i < kStripRows * kLcdWidth; ++i) {
        buf[i] = 0xAAAA;
    }
    render_strip(row_pixel, short_last.y0, short_last.rows, kLcdWidth, buf);
    CHECK(buf[0] == 464);
    CHECK(buf[6 * kLcdWidth - 1] == 469);
    CHECK(buf[6 * kLcdWidth] == 0xAAAA);

    // The strip path paints what the pixel calls say, including the last strip.
    render_strip([](int x, int y) { return screen(x, y); }, last.y0, last.rows, kLcdWidth, buf);
    for (int r = 0; r < last.rows; ++r) {
        for (int x = 0; x < kLcdWidth; x += 37) {
            CHECK(buf[r * kLcdWidth + x] == screen(x, last.y0 + r));
        }
    }
}

void check_mirror_180()
{
    // The panel shows the frame the firmware drew, turned over in both axes for 180
    // degrees (`esp_lcd_panel_mirror`). What a pixel on the panel shows is the
    // logical pixel at its mirror image when mirrored, and the same pixel otherwise.
    auto shown = [](int deg, int px, int py) {
        const bool mirror = panel_rotation_mirrors(deg);
        return screen(mirror ? kLcdWidth - 1 - px : px, mirror ? kLcdHeight - 1 - py : py);
    };

    // The 180 output is the 0 output turned over in both axes, everywhere on the
    // panel. This is the whole check; the cases below say what it looks like.
    for (int py = 0; py < kLcdHeight; ++py) {
        for (int px = 0; px < kLcdWidth; ++px) {
            CHECK(shown(180, px, py) == shown(0, kLcdWidth - 1 - px, kLcdHeight - 1 - py));
        }
    }

    // Concretely: the marker moves to the bottom-right corner, and the bars read in
    // reverse, so the black bar is on the left.
    CHECK(shown(0, 16, 16) == kRed);
    CHECK(shown(180, 783, 463) == kRed);
    CHECK(shown(180, 16, 16) == kBlack);
    CHECK(shown(0, 50, 200) == kWhite);
    CHECK(shown(180, 50, 200) == kBlack);
    CHECK(shown(0, 750, 200) == kBlack);
    CHECK(shown(180, 750, 200) == kWhite);
}


// ---------------------------------------------------------------------------
// The setup network (#1267)
// ---------------------------------------------------------------------------

// A schema 1 blob: the first 272 bytes of the struct, with the schema it was written with.

void check_ipv4_parse()
{
    uint8_t a[4] = {9, 9, 9, 9};
    CHECK(ipv4_parse("192.168.1.50", a) && a[0] == 192 && a[1] == 168 && a[2] == 1 && a[3] == 50);
    CHECK(ipv4_parse("0.0.0.0", a) && a[0] == 0 && a[3] == 0);
    CHECK(ipv4_parse("255.255.255.255", a) && a[0] == 255 && a[3] == 255);
    CHECK(!ipv4_parse("256.1.1.1", a));
    CHECK(!ipv4_parse("1.2.3", a));
    CHECK(!ipv4_parse("1.2.3.4.5", a));
    CHECK(!ipv4_parse("1.2.3.", a));
    CHECK(!ipv4_parse(".1.2.3", a));
    CHECK(!ipv4_parse("1..2.3", a));
    CHECK(!ipv4_parse("01.2.3.4", a));   // a leading zero is refused
    CHECK(!ipv4_parse("1.2.3.04", a));
    CHECK(!ipv4_parse("1.2.3.a", a));
    CHECK(!ipv4_parse(" 1.2.3.4", a));
    CHECK(!ipv4_parse("1.2.3.4 ", a));
    CHECK(!ipv4_parse("", a));
    CHECK(!ipv4_parse(nullptr, a));
    CHECK(!ipv4_parse("1234.1.1.1", a));
}

void check_ipv4_mask_and_hosts()
{
    int prefix = 0;
    const uint8_t m24[4] = {255, 255, 255, 0};
    CHECK(ipv4_mask_prefix(m24, &prefix) && prefix == 24);
    const uint8_t m30[4] = {255, 255, 255, 252};
    CHECK(ipv4_mask_prefix(m30, &prefix) && prefix == 30);
    const uint8_t m8[4] = {255, 0, 0, 0};
    CHECK(ipv4_mask_prefix(m8, &prefix) && prefix == 8);
    const uint8_t bad[4] = {255, 0, 255, 0};  // not a run of ones
    CHECK(!ipv4_mask_prefix(bad, &prefix));
    const uint8_t gap[4] = {255, 255, 254, 255};
    CHECK(!ipv4_mask_prefix(gap, &prefix));

    const uint8_t lan[4] = {192, 168, 1, 1};
    const uint8_t zero[4] = {0, 0, 0, 0};
    const uint8_t lo[4] = {127, 0, 0, 1};
    const uint8_t multicast[4] = {224, 0, 0, 1};
    const uint8_t all_ones[4] = {255, 255, 255, 255};
    CHECK(ipv4_unicast_host(lan));
    CHECK(!ipv4_unicast_host(zero));
    CHECK(!ipv4_unicast_host(lo));
    CHECK(!ipv4_unicast_host(multicast));
    CHECK(!ipv4_unicast_host(all_ones));
}

void check_static_ip_rules()
{
    const uint8_t mask24[4] = {255, 255, 255, 0};
    const uint8_t gw[4] = {192, 168, 1, 1};
    const uint8_t dns[4] = {192, 168, 1, 1};
    const uint8_t none[4] = {0, 0, 0, 0};

    // A sound static configuration.
    const uint8_t good[4] = {192, 168, 1, 50};
    CHECK(static_ip_problem(good, mask24, gw, dns, none) == ip_problem::none);
    // A secondary DNS is optional, and a usable one is accepted.
    const uint8_t dns2[4] = {8, 8, 8, 8};
    CHECK(static_ip_problem(good, mask24, gw, dns, dns2) == ip_problem::none);

    // Each refusal is for the rule it breaks.
    const uint8_t network[4] = {192, 168, 1, 0};
    const uint8_t broadcast[4] = {192, 168, 1, 255};
    CHECK(static_ip_problem(network, mask24, gw, dns, none) == ip_problem::network_address);
    CHECK(static_ip_problem(broadcast, mask24, gw, dns, none) == ip_problem::broadcast_address);
    const uint8_t zero_addr[4] = {0, 0, 0, 0};
    CHECK(static_ip_problem(zero_addr, mask24, gw, dns, none) == ip_problem::address);
    const uint8_t loopback[4] = {127, 0, 0, 2};
    CHECK(static_ip_problem(loopback, mask24, gw, dns, none) == ip_problem::address);

    const uint8_t bad_mask[4] = {255, 0, 255, 0};
    CHECK(static_ip_problem(good, bad_mask, gw, dns, none) == ip_problem::mask);
    const uint8_t mask31[4] = {255, 255, 255, 254};
    CHECK(static_ip_problem(good, mask31, gw, dns, none) == ip_problem::mask);
    const uint8_t mask4[4] = {240, 0, 0, 0};
    CHECK(static_ip_problem(good, mask4, gw, dns, none) == ip_problem::mask);

    // The gateway: off the subnet, the network or broadcast address, the address itself, or
    // unusable.
    const uint8_t off[4] = {192, 168, 2, 1};
    CHECK(static_ip_problem(good, mask24, off, dns, none) == ip_problem::gateway_off_subnet);
    CHECK(static_ip_problem(good, mask24, network, dns, none) == ip_problem::gateway);
    CHECK(static_ip_problem(good, mask24, broadcast, dns, none) == ip_problem::gateway);
    CHECK(static_ip_problem(good, mask24, good, dns, none) == ip_problem::gateway_is_address);
    CHECK(static_ip_problem(good, mask24, zero_addr, dns, none) == ip_problem::gateway);

    // The primary DNS is required; the secondary is optional but must be usable if given.
    CHECK(static_ip_problem(good, mask24, gw, zero_addr, none) == ip_problem::dns_primary);
    const uint8_t bad_dns2[4] = {224, 0, 0, 1};
    CHECK(static_ip_problem(good, mask24, gw, dns, bad_dns2) == ip_problem::dns_secondary);

    // A /30 with two usable addresses works: address and gateway.
    const uint8_t m30[4] = {255, 255, 255, 252};
    const uint8_t a30[4] = {10, 0, 0, 1};
    const uint8_t g30[4] = {10, 0, 0, 2};
    CHECK(static_ip_problem(a30, m30, g30, dns, none) == ip_problem::none);
    // Every problem has a message, and the none case has none.
    CHECK(ip_problem_text(ip_problem::none)[0] == '\0');
    CHECK(ip_problem_text(ip_problem::gateway_off_subnet)[0] != '\0');
}

void check_ssid_and_password()
{
    CHECK(setup_ssid_problem(0) != nullptr);
    CHECK(setup_ssid_problem(1) == nullptr);
    CHECK(setup_ssid_problem(32) == nullptr);
    CHECK(setup_ssid_problem(33) != nullptr);

    // Empty is an open network, 8 to 63 is WPA2, and 1 to 7 and 64 and up are refused.
    CHECK(setup_password_problem(0) == nullptr);
    CHECK(setup_password_problem(7) != nullptr);
    CHECK(setup_password_problem(8) == nullptr);
    CHECK(setup_password_problem(63) == nullptr);
    CHECK(setup_password_problem(64) != nullptr);
}

void check_form_codecs()
{
    // An encoded body with a line break in a value (a pasted field) is decoded. A bad escape
    // is refused.
    setup_form_t f = {};
    CHECK(setup_form_parse("ssid=A%0ABC&vxp=https%3A%2F%2F10.0.0.5", &f) != nullptr);
    CHECK(setup_form_parse("ssid=Office&pass=%ZZ&vxp=https%3A%2F%2F10.0.0.5", &f) != nullptr);
    // An absent password is still an open network; only a malformed one is refused.
    CHECK(setup_form_parse("ssid=Office&vxp=https%3A%2F%2F10.0.0.5", &f) == nullptr);
    CHECK(f.password[0] == '\0');
}

void check_passphrase()
{
    // The alphabet has no look-alikes, and its length is the one the check assumes.
    CHECK(sizeof kSetupPassphraseAlphabet - 1 == 32);
    for (const char *p = "01OI"; *p != '\0'; ++p) {
        CHECK(strchr(kSetupPassphraseAlphabet, *p) == nullptr);
    }
    CHECK(kSetupPassphraseLength >= 12);

    // Every byte is accepted, and each of the 32 characters is drawn by eight of the 256
    // bytes: the mapping is even, so no character is more likely than another.
    int counts[256] = {0};
    for (int r = 0; r < 256; ++r) {
        char c = 0;
        CHECK(setup_passphrase_char(static_cast<uint8_t>(r), &c));
        CHECK(c != '\0');
        CHECK(strchr(kSetupPassphraseAlphabet, c) != nullptr);
        counts[static_cast<unsigned char>(c)]++;
    }
    for (const char *p = kSetupPassphraseAlphabet; *p != '\0'; ++p) {
        CHECK(counts[static_cast<unsigned char>(*p)] == 8);
    }
}

void check_ssid_for_mac()
{
    const uint8_t mac[6] = {0x24, 0x0A, 0xC4, 0x12, 0x3F, 0x9A};
    char name[17];
    setup_ssid_for_mac(mac, name);
    CHECK(strcmp(name, "Relay-Panel-3F9A") == 0);
    CHECK(strlen(name) == 16);
    const uint8_t low[6] = {0, 0, 0, 0, 0x00, 0x0B};
    setup_ssid_for_mac(low, name);
    CHECK(strcmp(name, "Relay-Panel-000B") == 0);
}

void check_setup_peers()
{
    // Clients of the setup network: .2 to .254 of 192.168.4.0/24.
    CHECK(setup_peer_allowed(0xC0A80402));  // 192.168.4.2
    CHECK(setup_peer_allowed(0xC0A804FE));  // 192.168.4.254
    CHECK(!setup_peer_allowed(0xC0A80401)); // the board's own address
    CHECK(!setup_peer_allowed(0xC0A80400)); // the network address
    CHECK(!setup_peer_allowed(0xC0A804FF)); // broadcast
    CHECK(!setup_peer_allowed(0xC0A80502)); // 192.168.5.2
    CHECK(!setup_peer_allowed(0x0A000005)); // 10.0.0.5, the building's network
    CHECK(!setup_peer_allowed(0x7F000001)); // loopback

    // The page's socket is dual-stack: an IPv4 client arrives as ::ffff:a.b.c.d.
    uint32_t mapped = 0;
    const uint8_t v4_in_v6[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0xFF, 0xFF, 192, 168, 4, 2};
    CHECK(setup_peer_from_mapped_v6(v4_in_v6, &mapped));
    CHECK(mapped == 0xC0A80402);
    CHECK(setup_peer_allowed(mapped));
    const uint8_t link_local[16] = {0xFE, 0x80, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 1};
    CHECK(!setup_peer_from_mapped_v6(link_local, &mapped));
    const uint8_t v4_compatible[16] = {0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 192, 168, 4, 2};
    CHECK(!setup_peer_from_mapped_v6(v4_compatible, &mapped));
}

void check_setup_wifi_qr()
{
    char qr[96];
    CHECK(setup_wifi_qr("VXP-Display-9330", "KAC9PKYRZ55V", qr, sizeof qr));
    CHECK(std::strcmp(qr, "WIFI:T:WPA;S:VXP-Display-9330;P:KAC9PKYRZ55V;;") == 0);
    // The format's special characters are escaped in the name and the password, and only there.
    CHECK(setup_wifi_qr("a;b", "c:d\\e", qr, sizeof qr));
    CHECK(std::strcmp(qr, "WIFI:T:WPA;S:a\\;b;P:c\\:d\\\\e;;") == 0);
    // A buffer one byte short is refused and left empty, never cut into a code that joins the wrong network.
    char exact[47];
    CHECK(setup_wifi_qr("VXP-Display-9330", "KAC9PKYRZ55V", exact, sizeof exact));
    char short_by_one[46];
    CHECK(!setup_wifi_qr("VXP-Display-9330", "KAC9PKYRZ55V", short_by_one, sizeof short_by_one));
    CHECK(short_by_one[0] == '\0');
}

void check_setup_dns()
{
    // A query for captive.apple.com, type A, as an iPhone sends it, with an EDNS record after the question.
    const uint8_t query[] = {0x12, 0x34, 0x01, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
                             7, 'c', 'a', 'p', 't', 'i', 'v', 'e', 5, 'a', 'p', 'p', 'l', 'e', 3, 'c', 'o', 'm', 0,
                             0x00, 0x01, 0x00, 0x01,
                             0x00, 0x00, 0x29, 0x10, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00};
    const size_t question_end = 12 + 19 + 4;
    uint8_t out[512];
    const size_t n = setup_dns_answer(query, sizeof query, kSetupHostAddress, out, sizeof out);
    CHECK(n == question_end + 16);
    CHECK(out[0] == 0x12 && out[1] == 0x34);    // the query's id
    CHECK(out[2] == 0x85 && out[3] == 0x80);    // a response, authoritative, RD kept, no error
    CHECK(out[5] == 1 && out[7] == 1 && out[11] == 0);  // one question, one answer, the EDNS record dropped
    CHECK(std::memcmp(out + 12, query + 12, question_end - 12) == 0);
    const uint8_t *a = out + question_end;
    CHECK(a[0] == 0xC0 && a[1] == 0x0C && a[3] == 1 && a[5] == 1 && a[9] == 10 && a[11] == 4);
    CHECK(a[12] == 192 && a[13] == 168 && a[14] == 4 && a[15] == 1);

    // AAAA: the name exists and has no such record, so the phone asks for A without waiting.
    uint8_t aaaa[sizeof query];
    std::memcpy(aaaa, query, sizeof query);
    aaaa[12 + 19 + 1] = 28;
    CHECK(setup_dns_answer(aaaa, sizeof aaaa, kSetupHostAddress, out, sizeof out) == question_end);
    CHECK(out[3] == 0x80 && out[7] == 0);

    // No reply: a response, another opcode, two questions, a name that runs off the end, a short buffer.
    uint8_t bad[sizeof query];
    std::memcpy(bad, query, sizeof query);
    bad[2] = 0x81;
    CHECK(setup_dns_answer(bad, sizeof bad, kSetupHostAddress, out, sizeof out) == 0);
    std::memcpy(bad, query, sizeof query);
    bad[2] = 0x28;  // opcode 5, an update
    CHECK(setup_dns_answer(bad, sizeof bad, kSetupHostAddress, out, sizeof out) == 0);
    std::memcpy(bad, query, sizeof query);
    bad[5] = 2;
    CHECK(setup_dns_answer(bad, sizeof bad, kSetupHostAddress, out, sizeof out) == 0);
    CHECK(setup_dns_answer(query, 20, kSetupHostAddress, out, sizeof out) == 0);
    std::memcpy(bad, query, sizeof query);
    bad[12] = 0xC0;  // a compressed name in a question
    CHECK(setup_dns_answer(bad, sizeof bad, kSetupHostAddress, out, sizeof out) == 0);
    CHECK(setup_dns_answer(query, sizeof query, kSetupHostAddress, out, question_end + 15) == 0);
}

void check_failure_reasons()
{
    // The reasons the station reports, by ESP-IDF's codes.
    CHECK(setup_failure_from_reason(201) == setup_failure::network_not_found);
    CHECK(setup_failure_from_reason(202) == setup_failure::wrong_password);
    CHECK(setup_failure_from_reason(15) == setup_failure::wrong_password);
    CHECK(setup_failure_from_reason(204) == setup_failure::wrong_password);
    CHECK(setup_failure_from_reason(203) == setup_failure::network_refused);
    CHECK(setup_failure_from_reason(200) == setup_failure::link_lost);
    CHECK(setup_failure_from_reason(2) == setup_failure::link_lost);
    // Every failure has a sentence for the page.
    for (int f = 1; f <= static_cast<int>(setup_failure::could_not_start); ++f) {
        CHECK(setup_failure_text(static_cast<setup_failure>(f))[0] != '\0');
    }
    CHECK(setup_failure_text(setup_failure::none)[0] == '\0');
    CHECK(kSetupReasonLeft == 8);
}

void check_origin_for_setup()
{
    // The setup page is plain HTTP, so its origin is http://, and the TLS rule refuses it.
    CHECK(origin_allowed_for("http://", "http://192.168.4.1", "192.168.4.1"));
    // The scheme is compared as the browser sends it, lower-case. An upper-case one is
    // refused, which fails closed; the shared rule is not loosened for this page.
    CHECK(!origin_allowed_for("http://", "HTTP://192.168.4.1", "192.168.4.1"));
    CHECK(origin_allowed_for("http://", nullptr, "192.168.4.1"));  // a script with no Origin
    CHECK(!origin_allowed_for("http://", "https://192.168.4.1", "192.168.4.1"));
    CHECK(!origin_allowed_for("http://", "http://evil.example", "192.168.4.1"));
    CHECK(!origin_allowed_for("http://", "null", "192.168.4.1"));
    // The board's own interface still needs https, and nothing here changed that.
    CHECK(origin_allowed("https://vxp.lan", "vxp.lan"));
    CHECK(!origin_allowed("http://vxp.lan", "vxp.lan"));
}

// The setup page: what it says, and what it never says.

// Samples the button every 20 ms over [from, to) with the level held at `pressed`, and counts the events of kind
// `want`.
int button_events(button::Hold &h, int64_t from, int64_t to, bool pressed,
                  button::Event want = button::Event::recover)
{
    int events = 0;
    for (int64_t t = from; t < to; t += 20) {
        events += h.sample(t, pressed) == want ? 1 : 0;
    }
    return events;
}

// The sample time at which `h` reports `want` while held at `pressed` over [from, to), or -1 for none.
int64_t button_fire_time(button::Hold &h, int64_t from, int64_t to, bool pressed,
                         button::Event want = button::Event::recover)
{
    for (int64_t t = from; t < to; t += 20) {
        if (h.sample(t, pressed) == want) {
            return t;
        }
    }
    return -1;
}

// The BOOT hold (#1347), on a fake clock. Times are milliseconds, and the button is sampled every 20 ms.
void check_button_hold()
{
    using button::Hold;
    using button::kRecoverHoldMs;
    CHECK(kRecoverHoldMs == 5000);

    // A five-second hold fires once, on the sample that completes it, and the hold that goes on does not fire again.
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        CHECK(button_fire_time(h, 100, 7000, true) == 5100);
        CHECK(button_events(h, 5120, 20000, true) == 0);
    }

    // Held for 30 seconds, the button fires once. A second event needs a release first.
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        CHECK(button_events(h, 100, 30100, true) == 1);
    }

    // A hold of 4.9 seconds does not fire, and the release after it arms nothing.
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        CHECK(button_events(h, 100, 5000, true) == 0);
        CHECK(button_events(h, 5000, 5200, false) == 0);
    }

    // A release shorter than the debounce window does not reset the hold. The 30 ms release at 2000 ms is a
    // bounce: the hold still fires at five seconds from the press, not five seconds from the bounce.
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        CHECK(button_events(h, 100, 2000, true) == 0);
        CHECK(h.sample(2000, false) == button::Event::none);
        CHECK(button_fire_time(h, 2020, 7000, true) == 5100);
    }

    // A release, then a new five-second hold: the second press fires again, five seconds after it began.
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        CHECK(button_fire_time(h, 100, 6000, true) == 5100);
        CHECK(button_events(h, 5120, 5400, false) == 0);
        CHECK(button_fire_time(h, 5400, 11000, true) == 10400);
    }

    // A press already down at the first sample, as at boot, does not fire while it is held. It fires only
    // after a release and a new press, five seconds after that press.
    {
        Hold h;
        CHECK(button_events(h, 0, 30000, true) == 0);
        CHECK(button_events(h, 30000, 30100, false) == 0);
        CHECK(button_fire_time(h, 30100, 40000, true) == 35100);
    }

    // The factory reset (#1481): the same press held on to fifteen seconds, once, after the recovery at five.
    using button::Event;
    CHECK(button::kFactoryResetHoldMs == 15000);
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        CHECK(button_fire_time(h, 100, 6000, true) == 5100);
        CHECK(button_fire_time(h, 5120, 20000, true, Event::factory_reset) == 15100);
        CHECK(button_events(h, 15120, 60000, true, Event::factory_reset) == 0);
    }

    // Held for 30 seconds: one recovery and one reset, nothing else.
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        int recover = 0;
        int reset = 0;
        for (int64_t t = 100; t < 30100; t += 20) {
            const Event e = h.sample(t, true);
            recover += e == Event::recover ? 1 : 0;
            reset += e == Event::factory_reset ? 1 : 0;
        }
        CHECK(recover == 1);
        CHECK(reset == 1);
    }

    // Released at 14.9 seconds: the recovery happened, the reset did not, and the release arms nothing.
    {
        Hold h;
        CHECK(button_events(h, 0, 100, false) == 0);
        CHECK(button_events(h, 100, 15000, true, Event::factory_reset) == 0);
        CHECK(button_events(h, 15000, 15200, false, Event::factory_reset) == 0);
    }

    // A press held from boot never resets, however long: the bootloader owns it until a release.
    {
        Hold h;
        CHECK(button_events(h, 0, 60000, true, Event::factory_reset) == 0);
    }
}

// ---- the room screen's layout (#1490) ---------------------------------------------------------

// ---------------------------------------------------------------------------
// The stored configuration
// ---------------------------------------------------------------------------

void check_settings_defaults()
{
    relay_settings_t s;
    memset(&s, 0xAA, sizeof s);
    settings_defaults_fill(&s, 180);
    CHECK(s.magic == kSettingsMagic);
    CHECK(s.schema == kSettingsSchema);
    CHECK(s.rotation_deg == 180);
    CHECK(s.wifi_ssid[0] == '\0' && s.wifi_password[0] == '\0');
    CHECK(s.relay_addr[0] == '\0');
    CHECK(s.panel_id[0] == '\0');
    CHECK(s.ip_mode == kIpModeDhcp);
    CHECK(s.feed_stream == kFeedNone && s.feed_lang[0] == '\0');
    uint8_t zero[4] = {0, 0, 0, 0};
    CHECK(memcmp(s.static_addr, zero, 4) == 0 && memcmp(s.static_dns2, zero, 4) == 0);
}

void check_settings_decode()
{
    const relay_settings_t good = example_settings();
    relay_settings_t out;
    settings_defaults_fill(&out, 0);
    CHECK(settings_decode(&good, sizeof good, &out, 0) == settings_blob::accepted);
    CHECK(strcmp(out.wifi_ssid, "Office-5G") == 0);
    CHECK(strcmp(out.relay_addr, "http://192.168.1.20:8000") == 0);
    CHECK(strcmp(out.panel_id, "0123456789abcdef0123456789abcdef") == 0);
    CHECK(out.feed_stream == kFeedTranslation && strcmp(out.feed_lang, "SPANISH") == 0);
    CHECK(out.rotation_deg == 180);

    // A refusal leaves the caller's defaults alone.
    settings_defaults_fill(&out, 0);
    CHECK(settings_decode(&good, sizeof good - 1, &out, 0) == settings_blob::wrong_size);
    CHECK(settings_decode(nullptr, sizeof good, &out, 0) == settings_blob::wrong_size);
    CHECK(out.wifi_ssid[0] == '\0');
    relay_settings_t bad = good;
    bad.magic = 0x44435856;  // the VXP display's magic: not this firmware's blob
    CHECK(settings_decode(&bad, sizeof bad, &out, 0) == settings_blob::wrong_magic);
    bad = good;
    bad.schema = kSettingsSchema + 1;
    CHECK(settings_decode(&bad, sizeof bad, &out, 0) == settings_blob::wrong_schema);
    CHECK(out.wifi_ssid[0] == '\0');

    // A rotation that is neither 0 nor 180 falls back to the build's.
    bad = good;
    bad.rotation_deg = 90;
    CHECK(settings_decode(&bad, sizeof bad, &out, 0) == settings_blob::rotation_reset);
    CHECK(out.rotation_deg == 0);

    // Text fields are terminated whatever the blob holds.
    bad = good;
    memset(bad.wifi_ssid, 'x', sizeof bad.wifi_ssid);
    memset(bad.relay_addr, 'y', sizeof bad.relay_addr);
    memset(bad.feed_lang, 'z', sizeof bad.feed_lang);
    CHECK(settings_decode(&bad, sizeof bad, &out, 0) == settings_blob::accepted);
    CHECK(strlen(out.wifi_ssid) == sizeof out.wifi_ssid - 1);
    CHECK(strlen(out.relay_addr) == sizeof out.relay_addr - 1);
    CHECK(strlen(out.feed_lang) == sizeof out.feed_lang - 1);

    // A damaged address mode is DHCP, and a damaged feed is no feed.
    bad = good;
    bad.ip_mode = 7;
    bad.feed_stream = 9;
    CHECK(settings_decode(&bad, sizeof bad, &out, 0) == settings_blob::accepted);
    CHECK(out.ip_mode == kIpModeDhcp);
    CHECK(out.feed_stream == kFeedNone);

    // A panel ID this firmware did not make is dropped, so boot makes a new one.
    bad = good;
    settings_set_text(bad.panel_id, sizeof bad.panel_id, "NOT-A-PANEL-ID");
    CHECK(settings_decode(&bad, sizeof bad, &out, 0) == settings_blob::accepted);
    CHECK(out.panel_id[0] == '\0');
}

void check_panel_id()
{
    uint8_t bytes[16];
    for (int i = 0; i < 16; ++i) {
        bytes[i] = static_cast<uint8_t>(i * 17);
    }
    char id[33];
    settings_panel_id_from_bytes(bytes, id);
    CHECK(strcmp(id, "00112233445566778899aabbccddeeff") == 0);
    CHECK(settings_panel_id_valid(id));
    CHECK(!settings_panel_id_valid(""));
    CHECK(!settings_panel_id_valid("00112233445566778899AABBCCDDEEFF"));  // upper case is not what we make
    CHECK(!settings_panel_id_valid("00112233445566778899aabbccddeef"));   // one short
    CHECK(!settings_panel_id_valid("00112233445566778899aabbccddeeffa")); // one long
    CHECK(!settings_panel_id_valid("0011223344556677 899aabbccddeeff"));
}

// ---------------------------------------------------------------------------
// The setup page
// ---------------------------------------------------------------------------

void check_relay_address()
{
    char out[129];
    // Empty is valid: the panel finds Relay itself.
    CHECK(relay_url_normalise("", out, sizeof out) == url_problem::none && out[0] == '\0');
    CHECK(relay_url_normalise("   ", out, sizeof out) == url_problem::none && out[0] == '\0');
    // A bare host or address, with or without a port, gains http://.
    CHECK(relay_url_normalise("192.168.1.20", out, sizeof out) == url_problem::none);
    CHECK(strcmp(out, "http://192.168.1.20") == 0);
    CHECK(relay_url_normalise("192.168.1.20:8000", out, sizeof out) == url_problem::none);
    CHECK(strcmp(out, "http://192.168.1.20:8000") == 0);
    CHECK(relay_url_normalise("relay.local", out, sizeof out) == url_problem::none);
    CHECK(strcmp(out, "http://relay.local") == 0);
    // An http:// prefix is kept once, in any case, and one trailing slash is dropped.
    CHECK(relay_url_normalise("  HTTP://relay.local:8000/ ", out, sizeof out) == url_problem::none);
    CHECK(strcmp(out, "http://relay.local:8000") == 0);
    // What Relay does not serve or the board cannot use is refused.
    CHECK(relay_url_normalise("https://relay.local", out, sizeof out) == url_problem::scheme);
    CHECK(relay_url_normalise("relay.local/stream", out, sizeof out) == url_problem::path);
    CHECK(relay_url_normalise("user:pw@relay.local", out, sizeof out) == url_problem::credentials);
    CHECK(relay_url_normalise("relay.local:0", out, sizeof out) == url_problem::port);
    CHECK(relay_url_normalise("relay.local:70000", out, sizeof out) == url_problem::port);
    CHECK(relay_url_normalise("relay.local:", out, sizeof out) == url_problem::port);
    CHECK(relay_url_normalise("relay.local:80a", out, sizeof out) == url_problem::port);
    CHECK(relay_url_normalise("[::1]:8000", out, sizeof out) == url_problem::host);
    CHECK(relay_url_normalise("-bad-.local", out, sizeof out) == url_problem::host);
    CHECK(relay_url_normalise("http://", out, sizeof out) == url_problem::host);
    // It never writes past `cap`.
    char tiny[12];
    CHECK(relay_url_normalise("relay.example.org", tiny, sizeof tiny) == url_problem::too_long);
    // Every refusal has a sentence, and none of them names VXP.
    for (url_problem p : {url_problem::scheme, url_problem::host, url_problem::port, url_problem::path,
                          url_problem::credentials, url_problem::too_long}) {
        CHECK(url_problem_text(p)[0] != '\0');
        CHECK(strstr(url_problem_text(p), "VXP") == nullptr);
    }
}

void check_setup_form()
{
    setup_form_t form = {};
    // A complete DHCP form with a Relay address.
    CHECK(setup_form_parse("ssid=Office-5G&pass=correct+horse&mode=dhcp&relay=192.168.1.20%3A8000", &form) == nullptr);
    CHECK(strcmp(form.ssid, "Office-5G") == 0);
    CHECK(strcmp(form.password, "correct horse") == 0);
    CHECK(form.dhcp);
    CHECK(strcmp(form.relay_url, "http://192.168.1.20:8000") == 0);

    // The Relay address is optional: leaving it out, or empty, is a form that passes.
    CHECK(setup_form_parse("ssid=Office-5G&pass=&mode=dhcp", &form) == nullptr);
    CHECK(form.relay_url[0] == '\0');
    CHECK(setup_form_parse("ssid=Office-5G&pass=&mode=dhcp&relay=", &form) == nullptr);
    CHECK(form.relay_url[0] == '\0');

    // A bad address is refused with its sentence, and nothing is kept.
    const char *m = setup_form_parse("ssid=Office-5G&pass=&mode=dhcp&relay=https%3A%2F%2Fx.local", &form);
    CHECK(m != nullptr && strcmp(m, url_problem_text(url_problem::scheme)) == 0);
    CHECK(form.relay_url[0] == '\0');

    // A static form needs its addresses.
    CHECK(setup_form_parse("ssid=A&pass=&mode=static", &form) != nullptr);
    CHECK(setup_form_parse("ssid=A&pass=&mode=static&ip=192.168.1.50&mask=255.255.255.0&gw=192.168.1.1&dns1=192.168.1.1",
                           &form) == nullptr);
    CHECK(!form.dhcp && form.addr[3] == 50 && form.gateway[3] == 1);
    CHECK(setup_form_parse("ssid=A&pass=&mode=bogus", &form) != nullptr);
    CHECK(setup_form_parse("pass=&mode=dhcp", &form) != nullptr);  // a network name is required

    // Applying a form keeps the rotation, the panel ID and the saved feed: this page does not change them.
    relay_settings_t s = example_settings();
    CHECK(setup_form_parse("ssid=New-Net&pass=password1&mode=dhcp&relay=", &form) == nullptr);
    setup_form_apply(&s, form);
    CHECK(strcmp(s.wifi_ssid, "New-Net") == 0 && strcmp(s.wifi_password, "password1") == 0);
    CHECK(s.relay_addr[0] == '\0');  // an emptied override clears it
    CHECK(s.rotation_deg == 180);
    CHECK(strcmp(s.panel_id, "0123456789abcdef0123456789abcdef") == 0);
    CHECK(s.feed_stream == kFeedTranslation && strcmp(s.feed_lang, "SPANISH") == 0);
    // Switching back to DHCP clears the old static values rather than leaving them behind.
    relay_settings_t t = example_settings();
    t.ip_mode = kIpModeStatic;
    t.static_addr[0] = 10;
    CHECK(setup_form_parse("ssid=A&pass=&mode=dhcp", &form) == nullptr);
    setup_form_apply(&t, form);
    CHECK(t.ip_mode == kIpModeDhcp && t.static_addr[0] == 0);

    // The form a page shows first never carries the stored password.
    setup_form_t shown = {};
    setup_form_from_settings(example_settings(), &shown);
    CHECK(shown.password[0] == '\0');
    CHECK(strcmp(shown.ssid, "Office-5G") == 0 && strcmp(shown.relay_text, "http://192.168.1.20:8000") == 0);
}

void check_setup_page()
{
    static char page[6144];
    page_view_t view;
    setup_form_t form = {};
    setup_form_from_settings(example_settings(), &form);
    view.form = &form;
    CHECK(setup_page_render(page, sizeof page, view) > 0);
    CHECK(strstr(page, "Relay panel setup") != nullptr);
    CHECK(strstr(page, "VXP") == nullptr);
    // The stored password is never in a page, however the form was built.
    CHECK(strstr(page, "correct horse") == nullptr);
    CHECK(strstr(page, "<input type=\"password\" name=\"pass\" maxlength=\"63\" autocomplete=\"new-password\">") != nullptr);
    // The stored network and Relay address are shown, so a second visit needs no retyping; the field is optional.
    CHECK(strstr(page, "value=\"Office-5G\"") != nullptr);
    CHECK(strstr(page, "name=\"relay\" maxlength=\"159\" placeholder=\"192.168.1.20:8000\" value=\"http://192.168.1.20:8000\"") != nullptr);
    CHECK(strstr(page, "name=\"relay\" maxlength=\"159\" required") == nullptr);
    CHECK(strstr(page, "method=\"post\" action=\"/configure\"") != nullptr);

    // Escaping: a name with markup in it is shown as text.
    setup_form_t hostile = {};
    settings_set_text(hostile.ssid, sizeof hostile.ssid, "<script>x</script>");
    settings_set_text(hostile.relay_text, sizeof hostile.relay_text, "\"q'&");
    hostile.dhcp = true;
    view.form = &hostile;
    CHECK(setup_page_render(page, sizeof page, view) > 0);
    CHECK(strstr(page, "<script>x") == nullptr);
    CHECK(strstr(page, "&lt;script&gt;x&lt;/script&gt;") != nullptr);
    CHECK(strstr(page, "&quot;q&#39;&amp;") != nullptr);

    // A refused form shows its message as an error, escaped.
    view.form = &form;
    view.notice = "The Relay address needs a host name or an IPv4 address.";
    view.notice_is_error = true;
    CHECK(setup_page_render(page, sizeof page, view) > 0);
    CHECK(strstr(page, "<p class=\"notice error\">The Relay address") != nullptr);

    // Connecting: the page refreshes to itself and has no form.
    page_view_t connecting;
    connecting.state = page_state::connecting;
    connecting.network = "Office-5G";
    CHECK(setup_page_render(page, sizeof page, connecting) > 0);
    CHECK(strstr(page, "http-equiv=\"refresh\" content=\"2;url=/\"") != nullptr);
    CHECK(strstr(page, "<form") == nullptr);

    // Connected: the address is shown and the page says the network closes.
    page_view_t connected;
    connected.state = page_state::connected;
    connected.network = "Office-5G";
    connected.address = "192.168.1.50";
    connected.close_in_seconds = 60;
    CHECK(setup_page_render(page, sizeof page, connected) > 0);
    CHECK(strstr(page, "192.168.1.50") != nullptr);
    CHECK(strstr(page, "closes in about 60 seconds") != nullptr);

    // A page that is too big for its buffer is refused, not truncated.
    char small[200];
    CHECK(setup_page_render(small, sizeof small, view) == 0);
}

void check_setup_ssid_name()
{
    const uint8_t mac[6] = {0x24, 0x6F, 0x28, 0x01, 0xAB, 0xCD};
    char ssid[17];
    setup_ssid_for_mac(mac, ssid);
    CHECK(strcmp(ssid, "Relay-Panel-ABCD") == 0);
}

}  // namespace

int main()
{
    check_panel_constants();
    check_board_pins();
    check_panel_timing();
    check_stc8_commands();
    check_rgb565();
    check_rotation();
    check_settings_defaults();
    check_settings_decode();
    check_settings_text();
    check_panel_id();
    check_wifi_policy();
    check_glyphs();
    check_test_screen_bars_and_border();
    check_test_screen_marker_title_version();
    check_text_clipping();
    check_strip_schedule();
    check_mirror_180();
    check_ipv4_parse();
    check_ipv4_mask_and_hosts();
    check_static_ip_rules();
    check_ssid_and_password();
    check_relay_address();
    check_setup_form();
    check_form_codecs();
    check_passphrase();
    check_ssid_for_mac();
    check_setup_ssid_name();
    check_setup_peers();
    check_setup_wifi_qr();
    check_setup_dns();
    check_failure_reasons();
    check_origin_for_setup();
    check_setup_page();
    check_button_hold();
    check_touch();

    if (g_failures != 0) {
        fprintf(stderr, "%d check(s) failed\n", g_failures);
        return 1;
    }
    printf("ok\n");
    return 0;
}
