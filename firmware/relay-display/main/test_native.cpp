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
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <initializer_list>

#include <string>
#include <utility>
#include <vector>

#include "auth.h"
#include "board_format.h"
#include "caption_format.h"
#include "caption_view_format.h"
#include "discovery_format.h"
#include "feed_format.h"
#include "button_policy.h"
#include "config.h"
#include "panel_format.h"
#include "panel_link_format.h"
#include "service_format.h"
#include "settings_format.h"
#include "setup_format.h"
#include "setup_page.h"
#include "ota_format.h"
#include "touch_format.h"
#include "update_format.h"
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

// ---------------------------------------------------------------------------
// The caption client (caption_format.h)
// ---------------------------------------------------------------------------

std::string fold(const char *s, size_t cap = kCaptionLineMax + 1)
{
    std::vector<char> out(cap);
    const size_t n = caption_fold(s, strlen(s), out.data(), cap);
    CHECK(n == strlen(out.data()));
    return std::string(out.data(), n);
}

void check_caption_fold()
{
    CHECK(fold("Hello, world.") == "Hello, world.");
    // Latin letters stay, in Spanish, French, German, Romanian and Vietnamese.
    CHECK(fold("¿Qué tal? Ça va. Größe. Ștefan. Tiếng Việt.") == "¿Qué tal? Ça va. Größe. Ștefan. Tiếng Việt.");
    CHECK(fold("5 €") == "5 €");
    // Typography becomes its plain form.
    CHECK(fold("\xE2\x80\x9CHola\xE2\x80\x9D \xE2\x80\x94 it\xE2\x80\x99s\xE2\x80\xA6") == "\"Hola\" - it's...");
    // Anything else is one visible "?" per run, never silently gone.
    CHECK(fold("Thanks \xF0\x9F\x91\x8D") == "Thanks ?");
    CHECK(fold("\xF0\x9F\x91\x8D\xF0\x9F\x8F\xBD great") == "? great");                      // emoji + skin tone
    CHECK(fold("\xF0\x9F\x91\xA8\xE2\x80\x8D\xF0\x9F\x91\xA9 family") == "? family");           // ZWJ sequence
    CHECK(fold("Name: \xE6\x9D\x8E\xE5\xB0\x8F\xE9\xBE\x99.") == "Name: ?.");
    CHECK(fold("a \xE6\x9D\x8E b \xE6\x9D\x8E c") == "a ? b ? c");
    // Invalid UTF-8 is a fallback too, and resynchronises.
    CHECK(fold("bad \xC3 byte") == "bad ? byte");
    CHECK(fold("over\xC0\xAFlong") == "over?long");
    CHECK(fold("\xED\xA0\x80surrogate") == "?surrogate");
    // Combining marks and invisible characters go; the base letter stays.
    CHECK(fold("cafe\xCC\x81 soft\xC2\xADhyphen zero\xE2\x80\x8Bwidth") == "cafe softhyphen zerowidth");
    // Whitespace is collapsed and trimmed; controls never reach the screen.
    CHECK(fold("  two\n\tlines\xC2\xA0here  ") == "two lines here");
    CHECK(fold("bell\x07") == "bell");
    CHECK(fold("") == "");
    CHECK(fold("\xF0\x9F\x91\x8D").size() == 1);
    // A long line is cut at a character and ends in "...".
    std::string longer(2000, 'a');
    std::string cut = fold(longer.c_str());
    CHECK(cut.size() == kCaptionLineMax && cut.substr(cut.size() - 3) == "...");
    CHECK(fold("ééééé", 8) == "éé...");  // 7 bytes of room: two two-byte letters and the dots
    CHECK(fold("abcdef", 7) == "abcdef");
    CHECK(fold("abcdefg", 7) == "abc...");
    CHECK(fold("abcdef", 4) == "...");
    CHECK(fold("abc", 4) == "abc");
}

void check_caption_json()
{
    using namespace caption_json;
    const char *j = R"({"a":1,"s":"x\"y\\z\u00e9\ud83d\udc4d\u0041","n":{"deep":[1,{"k":"v"}]},"b":true,"c":false,"seq":18446744073709551615})";
    const char *e = j + strlen(j);
    uint64_t v = 0;
    CHECK(read_uint(get(j, e, "a"), e, &v) && v == 1);
    CHECK(read_uint(get(j, e, "seq"), e, &v) && v == 18446744073709551615ULL);
    char s[32];
    size_t n = 0;
    CHECK(read_string(get(j, e, "s"), e, s, sizeof s, &n));
    CHECK(strcmp(s, "x\"y\\z\xC3\xA9\xF0\x9F\x91\x8D" "A") == 0 && n == strlen(s));
    bool b = false;
    CHECK(read_bool(get(j, e, "b"), e, &b) && b);
    CHECK(read_bool(get(j, e, "c"), e, &b) && !b);
    CHECK(get(j, e, "missing") == nullptr);
    CHECK(get(j, e, "k") == nullptr);  // only the top level is searched
    // Lone surrogates are U+FFFD, not garbage.
    const char *lone = R"({"t":"\ud800x\udc00"})";
    CHECK(read_string(get(lone, lone + strlen(lone), "t"), lone + strlen(lone), s, sizeof s, &n));
    CHECK(strcmp(s, "\xEF\xBF\xBDx\xEF\xBF\xBD") == 0);
    // A string longer than its room is cut whole characters at a time.
    const char *wide = R"({"t":"ééééé"})";
    CHECK(read_string(get(wide, wide + strlen(wide), "t"), wide + strlen(wide), s, 6, &n));
    CHECK(n == 4 && strcmp(s, "éé") == 0);
    // Malformed input is "not found", never a crash.
    for (const char *bad : {"", "{", "{\"a\"", "{\"a\":", "[1,2]", "{\"a\":\"\x01\"}", "{\"s\":\"\\q\"}"}) {
        const char *be = bad + strlen(bad);
        CHECK(!read_uint(get(bad, be, "a"), be, &v));
        CHECK(!read_string(get(bad, be, "s"), be, s, sizeof s, &n));
    }
    // Deep nesting is walked without recursion.
    std::string deep = "{\"x\":" + std::string(5000, '[') + std::string(5000, ']') + ",\"a\":7}";
    CHECK(read_uint(get(deep.c_str(), deep.c_str() + deep.size(), "a"), deep.c_str() + deep.size(), &v) && v == 7);
    // Arrays.
    const char *arr = R"({"lines":[ {"seq":1}, {"seq":2} ,{"seq":3}]})";
    const char *ae = arr + strlen(arr);
    array_iter it;
    CHECK(array_begin(get(arr, ae, "lines"), ae, &it));
    int count = 0;
    while (const char *el = array_next(&it)) {
        CHECK(read_uint(get(el, ae, "seq"), ae, &v) && v == static_cast<uint64_t>(++count));
    }
    CHECK(count == 3);
    for (const char *num : {"1.5", "-1", "1e3", "99999999999999999999"}) {
        CHECK(!read_uint(num, num + strlen(num), &v));
    }
}

struct http_capture {
    std::string body;
};

void http_sink(void *ctx, const char *p, size_t n)
{
    static_cast<http_capture *>(ctx)->body.append(p, n);
}

// Feed `wire` to a fresh reader `step` bytes at a time.
caption_http http_read(const std::string &wire, size_t step, http_capture *got)
{
    caption_http h;
    for (size_t i = 0; i < wire.size(); i += step) {
        h.feed(wire.data() + i, std::min(step, wire.size() - i), http_sink, got);
    }
    return h;
}

void check_caption_http()
{
    const std::string chunked =
        "HTTP/1.1 200 OK\r\ncontent-type: text/event-stream; charset=utf-8\r\n"
        "Transfer-Encoding: chunked\r\nX-Long: " + std::string(500, 'x') + "\r\n\r\n"
        "5\r\nhello\r\n19;ext=1\r\n, chunked world, in parts\r\n0\r\nTrailer: t\r\n\r\n";
    for (size_t step : {1u, 2u, 3u, 7u, 64u, 10000u}) {
        http_capture got;
        caption_http h = http_read(chunked, step, &got);
        CHECK(h.status() == 200 && h.chunked() && h.event_stream());
        CHECK(got.body == "hello, chunked world, in parts");
        CHECK(h.state() == caption_http::phase::done);
    }
    // Identity body (HTTP/1.0 or a proxy that dechunks): everything after the headers.
    http_capture plain;
    caption_http h = http_read("HTTP/1.0 200 OK\nContent-Type: text/event-stream\n\ndata: x\n\n", 5, &plain);
    CHECK(h.status() == 200 && !h.chunked() && h.event_stream() && plain.body == "data: x\n\n");
    // Status codes come through for the caller to judge.
    http_capture none;
    CHECK(http_read("HTTP/1.1 502 Bad Gateway\r\n\r\n", 4, &none).status() == 502);
    CHECK(http_read("HTTP/1.1 400 Bad Request\r\ncontent-type: application/json\r\n\r\n{}", 4, &none).status() == 400);
    // Not HTTP, or a broken chunk size, is a bad response.
    CHECK(http_read("SSH-2.0-OpenSSH\r\n", 3, &none).state() == caption_http::phase::bad);
    CHECK(http_read("HTTP/1.1 2x0 OK\r\n", 3, &none).state() == caption_http::phase::bad);
    CHECK(http_read("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nzz\r\n", 3, &none).state() ==
          caption_http::phase::bad);
    CHECK(http_read("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n3\r\nabcX", 3, &none).state() ==
          caption_http::phase::bad);
}

struct sse_capture {
    std::vector<std::string> data;
    std::vector<std::string> ids;
    std::vector<std::string> types;
    std::vector<std::string> comments;
    int oversize = 0;
};

void sse_on_event(void *ctx, const caption_sse_event &ev)
{
    auto *c = static_cast<sse_capture *>(ctx);
    CHECK(ev.data[ev.len] == '\0');
    if (ev.oversize) {
        ++c->oversize;
        return;
    }
    c->data.emplace_back(ev.data, ev.len);
    c->ids.emplace_back(ev.id);
    c->types.emplace_back(ev.type);
}

void sse_on_comment(void *ctx, const char *text)
{
    static_cast<sse_capture *>(ctx)->comments.emplace_back(text);
}

sse_capture sse_read(const std::string &wire, size_t step, size_t cap = 4096)
{
    std::vector<char> buf(cap);
    caption_sse sse(buf.data(), cap);
    sse_capture got;
    for (size_t i = 0; i < wire.size(); i += step) {
        sse.feed(wire.data() + i, std::min(step, wire.size() - i), sse_on_event, sse_on_comment, &got);
    }
    return got;
}

void check_caption_sse()
{
    // Relay's frames, a ping, and the line endings the spec allows, split at every boundary.
    const std::string wire =
        "id: 12\ndata: {\"seq\":12}\n\n"
        ": ping\n\n"
        "data: {\"type\":\"targets\"}\r\n\r\n"
        "id:13\rdata:one\rdata: two\r\r"
        "event: custom\r\nretry: 5\r\nunknown: x\r\ndata\r\n\r\n"
        "id: 14\n\n";  // an id with no data dispatches nothing
    for (size_t step : {1u, 2u, 5u, 4096u}) {
        sse_capture got = sse_read(wire, step);
        CHECK(got.data.size() == 4);
        if (got.data.size() != 4) {
            continue;
        }
        CHECK(got.data[0] == "{\"seq\":12}" && got.ids[0] == "12");
        CHECK(got.data[1] == "{\"type\":\"targets\"}" && got.ids[1].empty());
        CHECK(got.data[2] == "one\ntwo" && got.ids[2] == "13");
        CHECK(got.data[3].empty() && got.types[3] == "custom");
        CHECK(got.comments.size() == 1 && got.comments[0] == "ping");
    }
    // An event larger than the buffer is reported as oversize and does not spill into the next.
    sse_capture big = sse_read("data: " + std::string(100, 'x') + "\n\ndata: ok\n\n", 7, 64);
    CHECK(big.oversize == 1 && big.data.size() == 1 && big.data[0] == "ok");
    // A field name too long to be one of ours is skipped whole.
    sse_capture odd = sse_read(std::string(100, 'n') + ": v\ndata: kept\n\n", 3);
    CHECK(odd.data.size() == 1 && odd.data[0] == "kept");
}

void check_caption_buffer()
{
    caption_buffer b(malloc, free);
    CHECK(b.size() == 0 && !b.contains(1));
    char text[16];
    for (int i = 1; i <= 250; ++i) {
        const int n = snprintf(text, sizeof text, "line %d", i);
        CHECK(b.push(static_cast<uint64_t>(i), text, static_cast<size_t>(n)));
    }
    // The newest 200, oldest first.
    CHECK(b.size() == kCaptionLines);
    CHECK(b.at(0).seq == 51 && strcmp(b.at(0).text, "line 51") == 0);
    CHECK(b.at(199).seq == 250 && strcmp(b.at(199).text, "line 250") == 0 && b.at(199).len == 8);
    for (int i = 1; i < b.size(); ++i) {
        CHECK(b.at(i).seq == b.at(i - 1).seq + 1);
    }
    CHECK(!b.contains(50) && b.contains(51) && b.contains(250));
    b.clear();
    CHECK(b.size() == 0);
    CHECK(b.push(7, "again", 5) && b.size() == 1 && b.at(0).seq == 7);
    // A failed allocation leaves the buffer as it was.
    caption_buffer none([](size_t) -> void * { return nullptr; }, free);
    CHECK(!none.push(1, "x", 1) && none.size() == 0);
}

std::string final_line(uint64_t seq, const char *text)
{
    return "{\"stream\":\"translation\",\"lang\":\"SPANISH\",\"seq\":" + std::to_string(seq) +
           ",\"delta\":\"\",\"text\":\"" + text + "\",\"final\":true,\"ts\":1730000000.5}";
}

caption_change feed_apply(caption_feed &f, const std::string &json)
{
    return f.apply(json.data(), json.size());
}

std::vector<uint64_t> seqs(const caption_feed &f)
{
    std::vector<uint64_t> out;
    for (int i = 0; i < f.lines().size(); ++i) {
        out.push_back(f.lines().at(i).seq);
    }
    return out;
}

void check_caption_feed()
{
    auto *f = new caption_feed(malloc, free);
    CHECK(f->resume_id() == 0);

    // A fresh connection: the snapshot fills the buffer and sets the resume point.
    CHECK(feed_apply(*f, R"({"type":"snapshot","stream":"translation","lang":"SPANISH","lines":[)"
                    R"({"seq":3,"text":"uno","ts":1},{"seq":9,"text":"dos","ts":2}],"open":"","ts":3})") ==
          caption_change::rebuilt);
    CHECK((seqs(*f) == std::vector<uint64_t>{3, 9}) && f->resume_id() == 9);

    // Finished lines append; seqs have gaps because Relay's counter is shared by every feed.
    CHECK(feed_apply(*f, final_line(15, "tres")) == caption_change::line);
    CHECK(feed_apply(*f, final_line(22, "\\u201ccuatro\\u201d \xF0\x9F\x99\x82")) == caption_change::line);
    CHECK(f->resume_id() == 22 && strcmp(f->lines().at(3).text, "\"cuatro\" ?") == 0);

    // A replayed line is not added twice.
    CHECK(feed_apply(*f, final_line(15, "tres")) == caption_change::ignored);
    CHECK(f->lines().size() == 4 && f->resume_id() == 22);

    // Partial lines, unknown types and pings never touch the buffer.
    CHECK(feed_apply(*f, R"({"stream":"translation","lang":"SPANISH","seq":23,"delta":"ho","final":false,"ts":1})") ==
          caption_change::ignored);
    CHECK(feed_apply(*f, R"({"type":"status","x":1})") == caption_change::ignored);
    CHECK(feed_apply(*f, "not json") == caption_change::malformed);
    CHECK(feed_apply(*f, "{\"final\":true,\"seq\":31,\"text\":\"cut\",") == caption_change::malformed);
    CHECK(feed_apply(*f, R"({"final":true,"text":"no seq"})") == caption_change::malformed);
    CHECK(feed_apply(*f, final_line(30, "   ")) == caption_change::ignored);  // nothing left to show
    CHECK(f->lines().size() == 4 && f->resume_id() == 22);

    // Targets are kept whole for the feed picker.
    const std::string targets = R"({"type":"targets","source":{"lang":"ENGLISH"},"live":["SPANISH"],"running":true})";
    CHECK(feed_apply(*f, targets) == caption_change::targets);
    CHECK(f->targets() == targets && f->targets_revision() == 1);

    // A reset (Start) is a snapshot with no lines: the buffer empties, and the resume point stays, so a
    // reconnect asks only for what came after it.
    CHECK(feed_apply(*f, R"({"type":"snapshot","lines":[],"open":""})") == caption_change::cleared);
    CHECK(f->lines().size() == 0 && f->resume_id() == 22);
    CHECK(feed_apply(*f, final_line(40, "nuevo")) == caption_change::line);

    // A Relay that restarted counts from 1 again: its lines are taken and the resume point follows.
    CHECK(feed_apply(*f, final_line(2, "otra vez")) == caption_change::line);
    CHECK(f->resume_id() == 2 && (seqs(*f) == std::vector<uint64_t>{40, 2}));

    // 250 lines keep the newest 200.
    for (uint64_t s = 100; s < 350; ++s) {
        CHECK(feed_apply(*f, final_line(s, "x")) == caption_change::line);
    }
    CHECK(f->lines().size() == kCaptionLines && f->lines().at(0).seq == 150 && f->resume_id() == 349);

    // A snapshot of 500 lines (Relay's most) keeps its newest 200.
    std::string snap = R"({"type":"snapshot","lines":[)";
    for (int s = 1; s <= 500; ++s) {
        snap += (s > 1 ? "," : "") + std::string("{\"seq\":") + std::to_string(s) + ",\"text\":\"l\",\"ts\":0}";
    }
    snap += "]}";
    CHECK(feed_apply(*f, snap) == caption_change::rebuilt);
    CHECK(f->lines().size() == kCaptionLines && f->lines().at(0).seq == 301 && f->resume_id() == 500);
    delete f;

    // Lines that cannot be stored are counted, not silently lost.
    auto *starved = new caption_feed([](size_t) -> void * { return nullptr; }, free);
    CHECK(feed_apply(*starved, final_line(1, "x")) == caption_change::no_memory && starved->lost() == 1);
    CHECK(starved->resume_id() == 0);
    delete starved;
}

void check_caption_connection()
{
    char host[64];
    uint16_t port = 0;
    CHECK(caption_url_split("http://192.168.1.20:8000", host, sizeof host, &port));
    CHECK(strcmp(host, "192.168.1.20") == 0 && port == 8000);
    CHECK(caption_url_split("relay.local/", host, sizeof host, &port) && strcmp(host, "relay.local") == 0 && port == 80);
    CHECK(!caption_url_split("http://", host, sizeof host, &port));
    CHECK(!caption_url_split("http://h:0", host, sizeof host, &port));
    CHECK(!caption_url_split("http://h:65536", host, sizeof host, &port));
    CHECK(!caption_url_split("http://h/stream", host, sizeof host, &port));
    CHECK(!caption_url_split("http://u@h", host, sizeof host, &port));
    CHECK(!caption_url_split("http://h:80x", host, sizeof host, &port));
    CHECK(!caption_url_split("http://h", host, 1, &port));

    char req[512];
    CHECK(caption_request(req, sizeof req, "10.0.2.2", 8000, kFeedTranslation, "SPANISH", 0, "0.1.0") > 0);
    CHECK(strncmp(req, "GET /stream?stream=translation&lang=SPANISH&finals=1 HTTP/1.1\r\n", 63) == 0);
    CHECK(strstr(req, "\r\nHost: 10.0.2.2:8000\r\n") != nullptr);
    CHECK(strstr(req, "\r\nAccept: text/event-stream\r\n") != nullptr);
    CHECK(strstr(req, "last_id") == nullptr);
    CHECK(strcmp(req + strlen(req) - 4, "\r\n\r\n") == 0);
    CHECK(caption_request(req, sizeof req, "relay.local", 80, kFeedTranscription, "", 123456789012ULL, "0.1.0") > 0);
    CHECK(strncmp(req, "GET /stream?stream=transcription&finals=1&last_id=123456789012 HTTP/1.1\r\n", 73) == 0);
    CHECK(strstr(req, "\r\nHost: relay.local\r\n") != nullptr);
    CHECK(caption_request(req, sizeof req, "h", 80, kFeedTranslation, "A B&", 0, "v") > 0);
    CHECK(strstr(req, "lang=A%20B%26&finals=1") != nullptr);
    CHECK(caption_request(req, sizeof req, "h", 80, kFeedTranslation, "", 0, "v") == 0);
    CHECK(caption_request(req, sizeof req, "h", 80, kFeedNone, "X", 0, "v") == 0);
    CHECK(caption_request(req, 40, "h", 80, kFeedTranscription, "", 0, "v") == 0);

    // Backoff: 1 s doubling to 30 s.
    CHECK(caption_backoff_ms(0) == 1000 && caption_backoff_ms(1) == 2000 && caption_backoff_ms(4) == 16000);
    CHECK(caption_backoff_ms(5) == 30000 && caption_backoff_ms(1000) == 30000);
    CHECK(caption_failures_after(4, true, kCaptionHealthyMs) == 0);
    CHECK(caption_failures_after(4, true, 500) == 5);
    CHECK(caption_failures_after(4, false, 60000) == 5);

    // What the UI is told.
    CHECK(caption_state_after(caption_end::refused, 0) == caption_state::relay_not_running);
    CHECK(caption_state_after(caption_end::http_status, 502) == caption_state::relay_not_running);
    CHECK(caption_state_after(caption_end::http_status, 400) == caption_state::feed_refused);
    CHECK(caption_state_after(caption_end::closed, 0) == caption_state::reconnecting);
    CHECK(caption_state_after(caption_end::silent, 0) == caption_state::reconnecting);
    CHECK(caption_state_after(caption_end::unreachable, 0) == caption_state::reconnecting);
    CHECK(strcmp(caption_state_name(caption_state::relay_not_running), "relay not running") == 0);
}

// What the caption view decides: following, what to add and remove on the glass, and the header's words.
void check_caption_view()
{
    // Following: on by default, a drag away pauses, back to the bottom resumes, and a pause times out.
    caption_follow f;
    f.reset(1000);
    CHECK(f.following());
    CHECK(!f.user_scrolled(true, 1100) && f.following());   // a drag that stayed at the bottom changes nothing
    CHECK(f.user_scrolled(false, 1200) && !f.following());  // away from the bottom: paused
    CHECK(!f.user_scrolled(false, 1300) && !f.following());
    CHECK(f.user_scrolled(true, 1400) && f.following());    // back at the bottom: live again
    CHECK(f.user_scrolled(false, 2000) && !f.following());
    CHECK(f.jump(2500) && f.following());                    // the button
    CHECK(!f.jump(2600));
    // The pause times out after a minute of no touch, and a touch restarts the minute.
    CHECK(f.user_scrolled(false, 10000));
    CHECK(!f.tick(10000 + kCaptionAutoLiveMs - 1) && !f.following());
    f.touched(10000 + kCaptionAutoLiveMs - 1);
    CHECK(!f.tick(10000 + kCaptionAutoLiveMs) && !f.following());
    CHECK(f.tick(10000 + 2 * kCaptionAutoLiveMs - 1) && f.following());
    CHECK(!f.tick(10000 + 5 * kCaptionAutoLiveMs));  // following already: nothing to end
    // The tick counter wraps.
    caption_follow w;
    w.reset(0);
    CHECK(w.user_scrolled(false, 0xFFFFFF00u) && !w.following());
    CHECK(!w.tick(0xFFFFFF00u + 1000));
    CHECK(w.tick(0xFFFFFF00u + kCaptionAutoLiveMs));
    CHECK(caption_at_bottom(0) && caption_at_bottom(kCaptionBottomSlackPx) && !caption_at_bottom(kCaptionBottomSlackPx + 1));
    CHECK(caption_at_bottom(-12));  // a list shorter than the view has "negative" content below

    // What to do to the glass when the buffer changes.
    auto buf = new caption_buffer(malloc, free);
    std::vector<caption_shown_line> shown;
    auto sync = [&](const caption_plan &plan) {
        if (plan.rebuild) {
            shown.clear();
        }
        shown.erase(shown.begin(), shown.begin() + plan.drop_front);
        if (plan.append_from >= 0) {
            for (int i = plan.append_from; i < buf->size(); ++i) {
                shown.push_back({buf->at(i).seq, caption_text_hash(buf->at(i).text)});
            }
        }
    };
    auto consistent = [&]() {
        if (static_cast<int>(shown.size()) != buf->size()) {
            return false;
        }
        for (int i = 0; i < buf->size(); ++i) {
            if (shown[i].seq != buf->at(i).seq || shown[i].hash != caption_text_hash(buf->at(i).text)) {
                return false;
            }
        }
        return true;
    };
    auto plan = [&]() { return caption_plan_update(shown.data(), static_cast<int>(shown.size()), *buf); };

    CHECK(plan().rebuild);  // nothing on the glass yet: build it (an empty buffer is a rebuild to nothing)
    sync(plan());
    CHECK(consistent());
    buf->push(5, "one", 3);
    buf->push(9, "two", 3);
    caption_plan p = plan();
    CHECK(p.rebuild);  // an empty glass is always a rebuild
    sync(p);
    CHECK(consistent() && plan().nothing());  // the same buffer again: nothing to do
    buf->push(12, "three", 5);
    p = plan();
    CHECK(!p.rebuild && p.drop_front == 0 && p.append_from == 2);
    sync(p);
    CHECK(consistent());
    // A full buffer: each new line evicts the oldest, and the glass drops one from the top and adds one.
    for (int i = 0; i < kCaptionLines + 20; ++i) {
        char t[16];
        const int n = snprintf(t, sizeof t, "line %d", i);
        buf->push(100 + static_cast<uint64_t>(i), t, static_cast<size_t>(n));
        p = plan();
        CHECK(!p.rebuild);
        sync(p);
        CHECK(consistent());
    }
    CHECK(buf->size() == kCaptionLines && shown.size() == static_cast<size_t>(kCaptionLines));
    // Several lines at once, with eviction.
    buf->push(400, "a", 1);
    buf->push(401, "b", 1);
    buf->push(402, "c", 1);
    p = plan();
    CHECK(!p.rebuild && p.drop_front == 3 && p.append_from == kCaptionLines - 3);
    sync(p);
    CHECK(consistent());
    // Lines evicted from the buffer faster than the glass was synced still plan as an append.
    for (int i = 0; i < 50; ++i) {
        buf->push(500 + static_cast<uint64_t>(i), "x", 1);
    }
    p = plan();
    CHECK(!p.rebuild && p.drop_front == 50 && p.append_from == kCaptionLines - 50);
    sync(p);
    CHECK(consistent());
    // More than a buffer's worth between syncs: nothing on the glass is in the buffer, so rebuild.
    for (int i = 0; i < kCaptionLines + 5; ++i) {
        buf->push(1000 + static_cast<uint64_t>(i), "y", 1);
    }
    CHECK(plan().rebuild);
    sync(plan());
    CHECK(consistent());
    // A snapshot that replaces the buffer with other lines, or with the same seqs and other text, is a rebuild.
    buf->clear();
    buf->push(1000 + kCaptionLines + 4, "y", 1);
    p = plan();  // a snapshot that holds only the newest line is the same picture as 199 evictions
    CHECK(!p.rebuild && p.drop_front == kCaptionLines - 1 && p.append_from == -1);
    sync(p);
    CHECK(consistent());
    buf->clear();
    buf->push(3, "restarted", 9);
    CHECK(plan().rebuild);  // a Relay restart: lower seqs
    sync(plan());
    buf->clear();
    buf->push(3, "different text", 14);
    CHECK(plan().rebuild);  // the same seq after a reset, other words
    sync(plan());
    CHECK(consistent());
    buf->clear();
    CHECK(plan().rebuild);  // reset: the glass empties
    sync(plan());
    CHECK(shown.empty());
    delete buf;

    // The header.
    char title[40];
    CHECK(caption_feed_title(kFeedTranscription, "", title, sizeof title) == 13 && strcmp(title, "Transcription") == 0);
    caption_feed_title(kFeedTranslation, "SPANISH", title, sizeof title);
    CHECK(strcmp(title, "Spanish") == 0);
    caption_feed_title(kFeedTranslation, "CHINESE_SIMPLIFIED", title, sizeof title);
    CHECK(strcmp(title, "Chinese Simplified") == 0);
    caption_feed_title(kFeedTranslation, "", title, sizeof title);
    CHECK(strcmp(title, "Translation") == 0);
    CHECK(caption_feed_title(kFeedNone, "", title, sizeof title) == 0 && title[0] == '\0');
    char tiny[6];
    caption_feed_title(kFeedTranscription, "", tiny, sizeof tiny);
    CHECK(strcmp(tiny, "Trans") == 0);  // cut at the buffer, still terminated
    CHECK(caption_state_tone(caption_state::connected) == caption_tone::good);
    CHECK(caption_state_tone(caption_state::reconnecting) == caption_tone::busy);
    CHECK(caption_state_tone(caption_state::relay_not_running) == caption_tone::bad);
    CHECK(caption_state_tone(caption_state::off) == caption_tone::idle);
    for (int s = 0; s <= static_cast<int>(caption_state::feed_refused); ++s) {
        const auto st = static_cast<caption_state>(s);
        CHECK(caption_state_label(st)[0] != '\0' && caption_empty_text(st)[0] != '\0');
    }
}

// ---------------------------------------------------------------------------
// The feed picker: what is offered, what a pane shows, and the saved choice
// ---------------------------------------------------------------------------

// A `targets` message as Relay sends it (`Engine.targets_payload`).
const char *kTargets =
    "{\"type\":\"targets\",\"source\":{\"lang\":\"ENGLISH\",\"label\":\"English\",\"iso\":\"en\",\"script\":\"latin\"},"
    "\"live\":[{\"target\":\"FRENCH\",\"label\":\"French\",\"iso\":\"fr\",\"script\":\"latin\",\"state\":\"open\"},"
    "{\"target\":\"JAPANESE\",\"label\":\"Japanese\",\"iso\":\"ja\",\"script\":\"kana\",\"state\":\"open\"},"
    "{\"target\":\"SPANISH\",\"label\":\"Spanish\",\"iso\":\"es\",\"script\":\"latin\",\"state\":\"open\"},"
    "{\"target\":\"RUSSIAN\",\"label\":\"Russian\",\"iso\":\"ru\",\"script\":\"cyrillic\",\"state\":\"open\"}],"
    "\"running\":true,\"font_px\":48}";

feed_options parse_options(const char *json)
{
    feed_options o;
    CHECK(feed_options_parse(json, strlen(json), &o));
    return o;
}

void check_feed_options()
{
    // Transcription first, then the live translations the panel can draw, in Relay's order. Japanese (kana) and
    // Russian (cyrillic) are not drawable and are never offered.
    const feed_options o = parse_options(kTargets);
    CHECK(o.known && o.count == 3);
    CHECK(o.items[0].pick.stream == kFeedTranscription && strcmp(o.items[0].label, "Transcription") == 0);
    CHECK(o.items[1].pick.stream == kFeedTranslation && strcmp(o.items[1].pick.lang, "FRENCH") == 0);
    CHECK(strcmp(o.items[1].label, "French") == 0);
    CHECK(strcmp(o.items[2].pick.lang, "SPANISH") == 0);
    CHECK(!o.has(feed_pick_make(kFeedTranslation, "JAPANESE")));
    CHECK(!o.has(feed_pick_make(kFeedTranslation, "RUSSIAN")));
    CHECK(o.has(feed_pick_make(kFeedTranscription, "")));

    // A source language the panel cannot draw has no transcription on offer; the translations still are.
    const feed_options ru = parse_options(
        "{\"type\":\"targets\",\"source\":{\"lang\":\"RUSSIAN\",\"script\":\"cyrillic\"},"
        "\"live\":[{\"target\":\"ENGLISH\",\"label\":\"English\",\"script\":\"latin\"}]}");
    CHECK(ru.known && ru.count == 1 && ru.items[0].pick.stream == kFeedTranslation);
    CHECK(!ru.has(feed_pick_make(kFeedTranscription, "")));

    // A language with no script is not offered: what the panel cannot be sure of it does not draw.
    const feed_options none = parse_options(
        "{\"type\":\"targets\",\"source\":{\"script\":\"latin\"},\"live\":[{\"target\":\"GERMAN\",\"label\":\"German\"}]}");
    CHECK(none.count == 1);  // the transcription only

    // Nothing live is a known, short list. A label that is missing falls back to the language's name.
    const feed_options empty = parse_options("{\"type\":\"targets\",\"source\":{\"script\":\"latin\"},\"live\":[]}");
    CHECK(empty.known && empty.count == 1);
    const feed_options nolabel = parse_options(
        "{\"type\":\"targets\",\"source\":{\"script\":\"latin\"},\"live\":[{\"target\":\"ITALIAN\",\"script\":\"latin\"}]}");
    CHECK(nolabel.count == 2 && strcmp(nolabel.items[1].label, "ITALIAN") == 0);

    // A poll that says nothing new is not a change; a language coming or going is.
    CHECK(feed_options_same(o, parse_options(kTargets)));
    CHECK(!feed_options_same(o, empty));
    CHECK(!feed_options_same(o, feed_options()));

    // Not JSON the client reads leaves the options as they were.
    feed_options keep = parse_options(kTargets);
    CHECK(!feed_options_parse("nope", 4, &keep));
    CHECK(keep.count == 3);

    // More languages than the menu holds are cut, never overrun.
    std::string many = "{\"type\":\"targets\",\"source\":{\"script\":\"latin\"},\"live\":[";
    for (int i = 0; i < 40; ++i) {
        many += std::string(i ? "," : "") + "{\"target\":\"L" + std::to_string(i) + "\",\"label\":\"L\",\"script\":\"latin\"}";
    }
    many += "]}";
    const feed_options big = parse_options(many.c_str());
    CHECK(big.count == kFeedOptionsMax);

    // A language name longer than the saved field is dropped rather than cut into another language.
    const feed_options longname = parse_options(
        "{\"type\":\"targets\",\"source\":{\"script\":\"latin\"},\"live\":[{\"target\":\"ABCDEFGHIJKLMNOPQRSTUVWXYZ\",\"script\":\"latin\"}]}");
    CHECK(longname.count == 1);
}

void check_feed_resolve()
{
    const feed_options o = parse_options(kTargets);
    const feed_options unknown;  // no `targets` yet
    const feed_pick es = feed_pick_make(kFeedTranslation, "SPANISH");
    const feed_pick de = feed_pick_make(kFeedTranslation, "GERMAN");
    const feed_pick tr = feed_pick_make(kFeedTranscription, "");

    // A live language is shown as chosen.
    feed_resolved r = feed_resolve(es, o, false);
    CHECK(feed_pick_same(r.pick, es) && !r.fallback);
    // A language that is not live falls back to the transcription, and says so.
    r = feed_resolve(de, o, false);
    CHECK(feed_pick_same(r.pick, tr) && r.fallback);
    // ...and the same pick is shown again the moment the language is live: nothing was forgotten.
    feed_options back = o;
    feed_option g;
    g.pick = de;
    back.items[back.count++] = g;
    r = feed_resolve(de, back, false);
    CHECK(feed_pick_same(r.pick, de) && !r.fallback);
    // A language the panel cannot draw is never shown, even when chosen (a saved choice from another build).
    r = feed_resolve(feed_pick_make(kFeedTranslation, "JAPANESE"), o, false);
    CHECK(feed_pick_same(r.pick, tr) && r.fallback);

    // No choice is the transcription.
    r = feed_resolve(feed_pick(), o, false);
    CHECK(feed_pick_same(r.pick, tr) && !r.fallback);
    r = feed_resolve(feed_pick(), unknown, false);
    CHECK(feed_pick_same(r.pick, tr) && !r.fallback);

    // Before Relay has said what is live, a pick is tried as it is; a 4xx on it says it is not live.
    r = feed_resolve(es, unknown, false);
    CHECK(feed_pick_same(r.pick, es) && !r.fallback);
    r = feed_resolve(es, unknown, true);
    CHECK(feed_pick_same(r.pick, tr) && r.fallback);

    // A source the panel cannot draw has no transcription: a pane that wants it, or falls back to it, shows nothing.
    const feed_options ru = parse_options(
        "{\"type\":\"targets\",\"source\":{\"script\":\"cyrillic\"},\"live\":[{\"target\":\"ENGLISH\",\"script\":\"latin\"}]}");
    r = feed_resolve(tr, ru, false);
    CHECK(r.pick.stream == kFeedNone && !r.fallback);
    r = feed_resolve(de, ru, false);
    CHECK(r.pick.stream == kFeedNone && r.fallback);

    // The second pane's default is the first live translation that is not the first pane's.
    CHECK(feed_pick_same(feed_default_second(o, tr), feed_pick_make(kFeedTranslation, "FRENCH")));
    CHECK(feed_pick_same(feed_default_second(o, feed_pick_make(kFeedTranslation, "FRENCH")),
                         feed_pick_make(kFeedTranslation, "SPANISH")));
    CHECK(feed_pick_same(feed_default_second(unknown, tr), tr));  // nothing to pair with yet

    // The notice.
    char note[64];
    CHECK(feed_fallback_note(feed_pick_make(kFeedTranslation, "CHINESE_SIMPLIFIED"), note, sizeof note) > 0);
    CHECK(strcmp(note, "Chinese Simplified is not live") == 0);
    CHECK(feed_fallback_note(es, note, sizeof note) > 0 && strcmp(note, "Spanish is not live") == 0);
    CHECK(feed_fallback_note(tr, note, sizeof note) == 0 && note[0] == '\0');
    char tiny[8];
    feed_fallback_note(es, tiny, sizeof tiny);
    CHECK(strlen(tiny) == sizeof tiny - 1);
}

void check_targets_request()
{
    char req[256];
    const size_t n = caption_targets_request(req, sizeof req, "10.0.2.2", 8000, "0.1.0");
    CHECK(n > 0 && n == strlen(req));
    CHECK(strncmp(req, "GET /api/targets HTTP/1.1\r\nHost: 10.0.2.2:8000\r\n", 47) == 0);
    CHECK(strstr(req, "Connection: close\r\n\r\n") != nullptr);
    CHECK(caption_targets_request(req, sizeof req, "relay.local", 80, "0.1.0") > 0 && strstr(req, "Host: relay.local\r\n") != nullptr);
    CHECK(caption_targets_request(req, 20, "10.0.2.2", 8000, "0.1.0") == 0);  // does not fit

    // The answer's body is a `targets` message without its `type`, and the options read it the same.
    const feed_options o = parse_options(
        "{\"source\":{\"lang\":\"ENGLISH\",\"script\":\"latin\"},\"live\":[{\"target\":\"SPANISH\",\"label\":\"Spanish\","
        "\"script\":\"latin\"}],\"running\":true,\"font_px\":48}");
    CHECK(o.known && o.count == 2);
}

void check_feed_panes()
{
    // One feed takes the whole width; two split it with a divider, and the halves are equal.
    feed_pane_rect a = feed_pane_rect_for(false, 0, 800);
    CHECK(a.x == 0 && a.w == 800);
    a = feed_pane_rect_for(true, 0, 800);
    const feed_pane_rect b = feed_pane_rect_for(true, 1, 800);
    CHECK(a.x == 0 && a.w == 398 && b.x == 402 && b.w == 398);
    CHECK(a.x + a.w + kFeedPaneDividerPx == b.x && b.x + b.w == 800);
    // A saved language is a whole language name: the field is as long as the settings'.
    CHECK(kFeedLangBytes == sizeof(relay_settings_t::feed2_lang));
}

void check_settings_split_and_v1()
{
    // The split choice is saved and read back whole.
    relay_settings_t s = example_settings();
    s.feed_split = 1;
    s.feed2_stream = kFeedTranslation;
    settings_set_text(s.feed2_lang, sizeof s.feed2_lang, "FRENCH");
    relay_settings_t out;
    settings_defaults_fill(&out, 0);
    CHECK(settings_decode(&s, sizeof s, &out, 0) == settings_blob::accepted);
    CHECK(out.feed_split == 1 && out.feed2_stream == kFeedTranslation && strcmp(out.feed2_lang, "FRENCH") == 0);
    CHECK(out.schema == kSettingsSchema);

    // Damaged values are not read as a choice.
    relay_settings_t bad = s;
    bad.feed_split = 7;
    bad.feed2_stream = 9;
    memset(bad.feed2_lang, 'z', sizeof bad.feed2_lang);
    settings_defaults_fill(&out, 0);
    CHECK(settings_decode(&bad, sizeof bad, &out, 0) == settings_blob::accepted);
    CHECK(out.feed_split == 0 && out.feed2_stream == kFeedNone);
    CHECK(strlen(out.feed2_lang) == sizeof out.feed2_lang - 1);

    // **A schema 1 blob is kept**: its network, panel ID and feed read as stored, and the split reads as off. A panel
    // updated over one that wrote schema 1 does not lose its setup.
    relay_settings_t v1src = example_settings();
    v1src.schema = 1;
    uint8_t v1[kSettingsSizeV1];
    memcpy(v1, &v1src, sizeof v1);
    settings_defaults_fill(&out, 0);
    CHECK(settings_decode(v1, sizeof v1, &out, 0) == settings_blob::accepted);
    CHECK(strcmp(out.wifi_ssid, "Office-5G") == 0);
    CHECK(strcmp(out.panel_id, "0123456789abcdef0123456789abcdef") == 0);
    CHECK(out.feed_stream == kFeedTranslation && strcmp(out.feed_lang, "SPANISH") == 0);
    CHECK(out.feed_split == 0 && out.feed2_stream == kFeedNone && out.feed2_lang[0] == '\0');
    CHECK(out.schema == kSettingsSchema);
    // A short blob that names schema 2, and a long one that names schema 1, are not read.
    relay_settings_t lie = example_settings();
    memcpy(v1, &lie, sizeof v1);
    CHECK(settings_decode(v1, sizeof v1, &out, 0) == settings_blob::wrong_schema);
    lie.schema = 1;
    CHECK(settings_decode(&lie, sizeof lie, &out, 0) == settings_blob::wrong_schema);
}

}  // namespace

void check_discovery()
{
    char name[kRelayNameCap];
    relay_name_clean("  Main Hall  ", name, sizeof name);
    CHECK(strcmp(name, "Main Hall") == 0);
    relay_name_clean("Caf\xC3\xA9 \xE2\x80\x93 hall", name, sizeof name);  // each non-ASCII character is one ?
    CHECK(strcmp(name, "Caf? ? hall") == 0);
    relay_name_clean(std::string(80, 'x').c_str(), name, sizeof name);
    CHECK(strlen(name) == kRelayNameCap - 1);
    relay_name_clean("tab\there", name, sizeof name);
    CHECK(strcmp(name, "tab?here") == 0);
    relay_name_clean(nullptr, name, sizeof name);
    CHECK(name[0] == '\0');

    relay_found a = {{192, 168, 1, 20}, 80, "Main Hall"};
    relay_found b = {{192, 168, 1, 31}, 8080, "Chapel"};
    char url[64];
    CHECK(relay_url_make(a, url, sizeof url) && strcmp(url, "http://192.168.1.20") == 0);
    CHECK(relay_url_make(b, url, sizeof url) && strcmp(url, "http://192.168.1.31:8080") == 0);
    CHECK(!relay_url_make(a, url, 10));  // too small a buffer
    CHECK(!relay_url_make(relay_found{{0, 0, 0, 0}, 80, "x"}, url, sizeof url));
    CHECK(!relay_url_make(relay_found{{127, 0, 0, 1}, 80, "x"}, url, sizeof url));
    CHECK(!relay_url_make(relay_found{{10, 0, 0, 1}, 0, "x"}, url, sizeof url));
    // What the panel hands the caption client is something it reads back.
    char host[64];
    uint16_t port = 0;
    CHECK(relay_url_make(b, url, sizeof url) && caption_url_split(url, host, sizeof host, &port));
    CHECK(strcmp(host, "192.168.1.31") == 0 && port == 8080);
    CHECK(relay_url_make(a, url, sizeof url) && caption_url_split(url, host, sizeof host, &port) && port == 80);

    // Nothing remembered: the first usable answer. Unusable answers are skipped.
    relay_found bad = {{0, 0, 0, 0}, 80, "Broken"};
    const relay_found both[] = {bad, a, b};
    CHECK(relay_pick(both, 3, "") == 1);
    CHECK(relay_pick(both, 3, nullptr) == 1);
    CHECK(relay_pick(both, 0, "") == -1);
    CHECK(relay_pick(&bad, 1, "") == -1);
    // Remembered: stays with that one, whatever order the answers come in, and never takes another.
    const relay_found swapped[] = {b, a};
    CHECK(relay_pick(swapped, 2, "Main Hall") == 1);
    CHECK(relay_pick(swapped, 2, "Chapel") == 0);
    CHECK(relay_pick(&b, 1, "Main Hall") == -1);
    CHECK(relay_pick(swapped, 2, "main hall") == -1);  // names are compared exactly
}

// The panel's check-in with Relay, and what Relay tells it back (`panel_link_format.h`).
void check_panel_link()
{
    panel_status st = {};
    snprintf(st.id, sizeof st.id, "%s", "0123456789abcdef0123456789abcdef");
    snprintf(st.version, sizeof st.version, "%s", "0.1.0");
    snprintf(st.ip, sizeof st.ip, "%s", "192.168.1.50");
    st.uptime_s = 42;
    st.has_rssi = true;
    st.rssi = -58;
    st.feed_count = 1;
    st.feeds[0] = feed_pick_make(kFeedTranslation, "SPANISH");

    char body[kPanelBodyMax];
    size_t n = panel_checkin_body(body, sizeof body, st);
    CHECK(n > 0 && n == strlen(body));
    CHECK(strcmp(body,
                 "{\"id\":\"0123456789abcdef0123456789abcdef\",\"name\":\"Panel 0123\",\"version\":\"0.1.0\","
                 "\"scripts\":[\"latin\"],\"feeds\":[{\"stream\":\"translation\",\"lang\":\"SPANISH\"}],"
                 "\"ip\":\"192.168.1.50\",\"uptime_s\":42,\"rssi\":-58,\"feed_rev\":0}") == 0);
    // The body is JSON the readers here understand.
    uint64_t up = 0;
    CHECK(caption_json::read_uint(caption_json::get(body, body + n, "uptime_s"), body + n, &up) && up == 42);

    // Two feeds, a transcription, no radio and no address: the fields that are unknown are left out.
    st.feed_count = 2;
    st.feeds[0] = feed_pick_make(kFeedTranscription, nullptr);
    st.feeds[1] = feed_pick_make(kFeedTranslation, "FRENCH");
    st.has_rssi = false;
    st.ip[0] = '\0';
    st.feed_rev = 7;
    n = panel_checkin_body(body, sizeof body, st);
    CHECK(strstr(body, "\"feeds\":[{\"stream\":\"transcription\"},{\"stream\":\"translation\",\"lang\":\"FRENCH\"}]") != nullptr);
    CHECK(strstr(body, "\"ip\"") == nullptr && strstr(body, "\"rssi\"") == nullptr);
    CHECK(strstr(body, "\"feed_rev\":7}") != nullptr);
    st.feed_count = 0;  // a panel with nothing chosen says so
    n = panel_checkin_body(body, sizeof body, st);
    CHECK(strstr(body, "\"feeds\":[]") != nullptr);
    st.feed_count = 2;
    st.feeds[0] = feed_pick();  // a pane with no feed is not reported
    n = panel_checkin_body(body, sizeof body, st);
    CHECK(strstr(body, "\"feeds\":[{\"stream\":\"translation\",\"lang\":\"FRENCH\"}]") != nullptr);

    // Text is escaped, and a body that does not fit is refused, not cut.
    snprintf(st.version, sizeof st.version, "%s", "a\"b\\c\n");
    n = panel_checkin_body(body, sizeof body, st);
    CHECK(strstr(body, "\"version\":\"a\\\"b\\\\c\\u000a\"") != nullptr);
    CHECK(panel_checkin_body(body, 60, st) == 0);
    CHECK(panel_checkin_body(body, 0, st) == 0);
    CHECK(kPanelBodyMax < 2048);  // Relay's cap (app/panels.py CHECKIN_MAX_BYTES)

    char name[kPanelNameMax];
    CHECK(panel_default_name("abcd1234", name, sizeof name) > 0 && strcmp(name, "Panel abcd") == 0);

    // The requests.
    char req[640];
    n = panel_checkin_request(req, sizeof req, "10.0.2.2", 8000, "{\"a\":1}", 7, "0.1.0");
    CHECK(n > 0 && n == strlen(req));
    CHECK(strncmp(req, "POST /api/panels/checkin HTTP/1.1\r\nHost: 10.0.2.2:8000\r\n", 56) == 0);
    CHECK(strstr(req, "Content-Length: 7\r\n") != nullptr && strstr(req, "Connection: close\r\n\r\n{\"a\":1}") != nullptr);
    CHECK(panel_checkin_request(req, sizeof req, "relay.local", 80, "{}", 2, "0.1.0") > 0 &&
          strstr(req, "Host: relay.local\r\n") != nullptr);
    CHECK(panel_checkin_request(req, 30, "10.0.2.2", 8000, "{}", 2, "0.1.0") == 0);
    n = panel_events_request(req, sizeof req, "10.0.2.2", 8000, st.id, "0.1.0");
    CHECK(n > 0 && strncmp(req, "GET /api/panels/0123456789abcdef0123456789abcdef/events HTTP/1.1\r\n", 65) == 0);
    CHECK(strstr(req, "Accept: text/event-stream\r\n") != nullptr);
    CHECK(panel_events_request(req, sizeof req, "10.0.2.2", 8000, "../admin", "0.1.0") == 0);  // not a panel ID
    CHECK(panel_events_request(req, 40, "10.0.2.2", 8000, st.id, "0.1.0") == 0);

    // A `set_feed`, as Relay writes it.
    auto event = [](const char *json) { return panel_parse_event(json, strlen(json)); };
    panel_event ev = event("{\"type\":\"set_feed\",\"rev\":3,\"feeds\":[{\"stream\":\"transcription\"}]}");
    CHECK(ev.kind == panel_message::set_feed && ev.feed.rev == 3 && ev.feed.count == 1 &&
          ev.feed.feeds[0].stream == kFeedTranscription);
    ev = event("{\"type\":\"set_feed\",\"rev\":4,\"feeds\":[{\"stream\":\"translation\",\"lang\":\"FRENCH\"},"
               "{\"stream\":\"translation\",\"lang\":\"SPANISH\"}]}");
    CHECK(ev.kind == panel_message::set_feed && ev.feed.count == 2 && strcmp(ev.feed.feeds[0].lang, "FRENCH") == 0 &&
          strcmp(ev.feed.feeds[1].lang, "SPANISH") == 0);
    // What Relay would never send is ignored, not half applied.
    for (const char *bad : {
             "{\"type\":\"set_feed\",\"rev\":1,\"feeds\":[]}",
             "{\"type\":\"set_feed\",\"rev\":1}",
             "{\"type\":\"set_feed\",\"feeds\":[{\"stream\":\"transcription\"}]}",
             "{\"type\":\"set_feed\",\"rev\":-1,\"feeds\":[{\"stream\":\"transcription\"}]}",
             "{\"type\":\"set_feed\",\"rev\":99999999999,\"feeds\":[{\"stream\":\"transcription\"}]}",
             "{\"type\":\"set_feed\",\"rev\":1,\"feeds\":[{\"stream\":\"nope\"}]}",
             "{\"type\":\"set_feed\",\"rev\":1,\"feeds\":[{\"stream\":\"translation\"}]}",
             "{\"type\":\"set_feed\",\"rev\":1,\"feeds\":[{\"stream\":\"translation\",\"lang\":\"\"}]}",
             "{\"type\":\"set_feed\",\"rev\":1,\"feeds\":[{\"stream\":\"transcription\"},{\"stream\":\"transcription\"},"
             "{\"stream\":\"transcription\"}]}",
             "{\"type\":\"set_feed\",\"rev\":1,\"feeds\":\"transcription\"}",
             "{\"type\":\"snapshot\"}", "{\"type\":5}", "{}", "[]", "nope", ""}) {
        CHECK(event(bad).kind == panel_message::none);
    }
    ev = event("{\"type\":\"update_available\",\"version\":\"0.2.0\",\"size\":1234}");
    CHECK(ev.kind == panel_message::update_available && !ev.update.present);  // not enough to act on
    CHECK(event("{\"type\":\"update_available\"}").kind == panel_message::update_available);

    // A check-in reply.
    auto reply = [](const char *json) { return panel_parse_reply(json, strlen(json)); };
    panel_reply r = reply("{\"ok\":true,\"name\":\"Lobby\",\"interval_s\":30}");
    CHECK(r.ok && !r.has_feed && strcmp(r.name, "Lobby") == 0);
    r = reply("{\"ok\":true,\"name\":\"Lobby\",\"interval_s\":30,\"set_feed\":{\"type\":\"set_feed\",\"rev\":2,"
              "\"feeds\":[{\"stream\":\"translation\",\"lang\":\"FRENCH\"}]}}");
    CHECK(r.ok && r.has_feed && r.feed.rev == 2 && r.feed.count == 1 && strcmp(r.feed.feeds[0].lang, "FRENCH") == 0);
    r = reply("{\"ok\":true,\"set_feed\":{\"rev\":2,\"feeds\":[]}}");
    CHECK(r.ok && !r.has_feed);  // a feed that does not read is dropped, the check-in still counts
    CHECK(!reply("{\"ok\":false}").ok && !reply("{\"detail\":\"too many panels\"}").ok && !reply("").ok);
    // A name longer than the field is cut, not overrun.
    r = reply("{\"ok\":true,\"name\":\"0123456789012345678901234567890123456789\"}");
    CHECK(r.ok && strlen(r.name) < kPanelNameMax);

    // Applying: a revision already applied is not applied again; any other is.
    CHECK(!panel_should_apply(3, 3));
    CHECK(panel_should_apply(4, 3));
    CHECK(panel_should_apply(1, 3));  // Relay's counter restarted
    CHECK(panel_should_apply(1, 0));

    // The stream's retry: a panel Relay does not know is retried soon, anything else backs off.
    CHECK(panel_events_wait_ms(5, 404) == 2000);
    CHECK(panel_events_wait_ms(0, 0) == 1000 && panel_events_wait_ms(9, 500) == 30000);
}

// An application head as a built image has it: header, segment header, descriptor with version and project.
static void make_head(uint8_t *h, const char *version, const char *project)
{
    memset(h, 0, kOtaHeadBytes);
    h[0] = 0xE9;
    h[kOtaAppDescMagicOffset] = 0x32;
    h[kOtaAppDescMagicOffset + 1] = 0x54;
    h[kOtaAppDescMagicOffset + 2] = 0xCD;
    h[kOtaAppDescMagicOffset + 3] = 0xAB;
    memcpy(h + kOtaVersionOffset, version, strlen(version));
    memcpy(h + kOtaProjectOffset, project, strlen(project));
}

void check_update()
{
    using namespace update_format;
    uint8_t head[kOtaHeadBytes];
    make_head(head, "0.2.0", "relay-display-app");
    CHECK(ota_inspect_head(head, sizeof head) == ota_verdict::accept);
    CHECK(ota_inspect_head(head, sizeof head - 1) == ota_verdict::not_an_image);
    uint8_t bad[kOtaHeadBytes];
    memcpy(bad, head, sizeof bad);
    bad[0] = 0x00;
    CHECK(ota_inspect_head(bad, sizeof bad) == ota_verdict::not_an_image);
    memcpy(bad, head, sizeof bad);
    bad[kOtaAppDescMagicOffset] = 0;  // a bootloader's descriptor: the merged serial image
    CHECK(ota_inspect_head(bad, sizeof bad) == ota_verdict::merged_image);
    CHECK(error_for(ota_verdict::merged_image) == update_error::compatible && error_for(ota_verdict::older) == update_error::version &&
          error_for(ota_verdict::not_an_image) == update_error::download);

    CHECK(ota_project_matches(head, sizeof head, "relay-display-app"));
    CHECK(!ota_project_matches(head, sizeof head, "relay-display"));      // a prefix is not the name
    CHECK(!ota_project_matches(head, sizeof head, "relay-display-app2"));
    CHECK(!ota_project_matches(head, sizeof head, "vxp-io-8di8ro"));
    uint8_t other[kOtaHeadBytes];
    make_head(other, "0.2.0", "vxp-io-8di8ro");
    CHECK(!ota_project_matches(other, sizeof other, "relay-display-app"));
    CHECK(ota_version_matches(head, sizeof head, "0.2.0") && !ota_version_matches(head, sizeof head, "0.2.1") &&
          !ota_version_matches(head, sizeof head, "0.2"));

    // An update only goes forward; the same version again is allowed.
    CHECK(ota_inspect_version(head, sizeof head, "0.1.0") == ota_verdict::accept);
    CHECK(ota_inspect_version(head, sizeof head, "0.2.0") == ota_verdict::accept);
    CHECK(ota_inspect_version(head, sizeof head, "0.2.1") == ota_verdict::older);
    CHECK(ota_inspect_version(head, sizeof head, "0.10.0") == ota_verdict::older);    // numbers, not text
    uint8_t ten[kOtaHeadBytes];
    make_head(ten, "0.10.0", "relay-display-app");
    CHECK(ota_inspect_version(ten, sizeof ten, "0.9.0") == ota_verdict::accept);
    CHECK(ota_inspect_version(head, sizeof head, "junk") == ota_verdict::accept);     // a running version nobody can read
    uint8_t unreadable[kOtaHeadBytes];
    make_head(unreadable, "next", "relay-display-app");
    CHECK(ota_inspect_version(unreadable, sizeof unreadable, "0.1.0") == ota_verdict::older);

    // What proves a new image works.
    CHECK(ota_confirmed_by(true, true, true));
    CHECK(!ota_confirmed_by(false, true, true) && !ota_confirmed_by(true, false, true) &&
          !ota_confirmed_by(true, true, false));

    // What Relay may offer.
    CHECK(version_ok("0.2.0") && version_ok("10.20.30"));
    CHECK(!version_ok("") && !version_ok("0.2") && !version_ok("v0.2.0") && !version_ok("0.2.0-rc1") && !version_ok(nullptr));
    CHECK(path_ok("/api/panels/firmware/0.2.0.bin"));
    CHECK(!path_ok("") && !path_ok("api/x") && !path_ok("http://evil/x") && !path_ok("//evil/x") && !path_ok("/a/../b") &&
          !path_ok("/a b") && !path_ok("/a?x=1") && !path_ok("/a%2e%2e/b") && !path_ok("/a\\b"));
    const char *sha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
    CHECK(sha256_ok(sha) && !sha256_ok("0123") && !sha256_ok(nullptr));
    char upper[65];
    snprintf(upper, sizeof upper, "%s", sha);
    upper[3] = 'D';
    CHECK(!sha256_ok(upper));

    // Reading the offer, as Relay writes it.
    const char *offer = "{\"version\":\"0.2.0\",\"size\":1048576,\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef"
                        "0123456789abcdef\",\"path\":\"/api/panels/firmware/0.2.0.bin\",\"now\":true}";
    Order o = panel_read_update(offer, offer + strlen(offer), 0x1E0000);
    CHECK(o.present && o.now && o.size == 1048576 && strcmp(o.version, "0.2.0") == 0 &&
          strcmp(o.path, "/api/panels/firmware/0.2.0.bin") == 0);
    CHECK(!panel_read_update(offer, offer + strlen(offer), 1000).present);  // bigger than the slot
    const char *no_now = "{\"version\":\"0.2.0\",\"size\":2000,\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef"
                         "0123456789abcdef\",\"path\":\"/x\"}";
    o = panel_read_update(no_now, no_now + strlen(no_now), 0x1E0000);
    CHECK(o.present && !o.now);
    for (const char *badoffer : {
             "{\"version\":\"0.2.0\",\"size\":2000,\"sha256\":\"00\",\"path\":\"/x\"}",
             "{\"version\":\"x\",\"size\":2000,\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\",\"path\":\"/x\"}",
             "{\"version\":\"0.2.0\",\"size\":2000,\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\",\"path\":\"http://h/x\"}",
             "{\"version\":\"0.2.0\",\"size\":10,\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\",\"path\":\"/x\"}",
             "{\"version\":\"0.2.0\",\"sha256\":\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\",\"path\":\"/x\"}"}) {
        CHECK(!panel_read_update(badoffer, badoffer + strlen(badoffer), 0x1E0000).present);
    }
    // In a check-in reply, and absent from one.
    char reply_json[512];
    snprintf(reply_json, sizeof reply_json, "{\"ok\":true,\"name\":\"Lobby\",\"interval_s\":30,\"update\":%s}", offer);
    panel_reply r = panel_parse_reply(reply_json, strlen(reply_json));
    CHECK(r.ok && r.update.present && strcmp(r.update.version, "0.2.0") == 0);
    const char *plain = "{\"ok\":true,\"name\":\"Lobby\",\"interval_s\":30}";
    r = panel_parse_reply(plain, strlen(plain));
    CHECK(r.ok && !r.update.present);  // the ask withdrawn
    // On the stream, with its type.
    char event_json[512];
    snprintf(event_json, sizeof event_json, "{\"type\":\"update_available\",%s", offer + 1);
    panel_event ev = panel_parse_event(event_json, strlen(event_json));
    CHECK(ev.kind == panel_message::update_available && ev.update.present && ev.update.now);

    // The decisions.
    CHECK(may_restart(true, true, false) && may_restart(true, false, true) && !may_restart(true, false, false) &&
          !may_restart(false, true, true));
    CHECK(!quiet_moment(kQuietSeconds - 1) && quiet_moment(kQuietSeconds));
    CHECK(attempt_was_taken_back("0.2.0", "0.1.0", false));   // the attempt is on record and the old image runs
    CHECK(!attempt_was_taken_back("0.2.0", "0.2.0", false));  // confirmed: the record was not cleared
    CHECK(!attempt_was_taken_back("0.2.0", "0.2.0", true));   // on approval
    CHECK(!attempt_was_taken_back("", "0.1.0", false) && !attempt_was_taken_back(nullptr, "0.1.0", false));
    CHECK(retry_seconds(0) == 0 && retry_seconds(1) == 60 && retry_seconds(2) == 120 && retry_seconds(30) == 1800);

    // The words Relay keeps (`app/panels.py`, UPDATE_STATES and UPDATE_ERRORS).
    CHECK(strcmp(state_word(update_state::downloading), "downloading") == 0 && strcmp(state_word(update_state::ready), "ready") == 0 &&
          strcmp(state_word(update_state::restarting), "restarting") == 0 && strcmp(state_word(update_state::failed), "failed") == 0);
    CHECK(strcmp(error_word(update_error::signature), "signature") == 0 && strcmp(error_word(update_error::rollback), "rollback") == 0 &&
          strcmp(error_word(update_error::none), "") == 0);
    char body[160];
    CHECK(report_body(body, sizeof body, "downloading", "0.2.0", 40, "") > 0 &&
          strcmp(body, "{\"state\":\"downloading\",\"version\":\"0.2.0\",\"progress\":40}") == 0);
    CHECK(report_body(body, sizeof body, "failed", "0.2.0", 0, "signature") > 0 &&
          strcmp(body, "{\"state\":\"failed\",\"version\":\"0.2.0\",\"error\":\"signature\"}") == 0);
    CHECK(report_body(body, 10, "failed", "0.2.0", 0, "signature") == 0);

    // The two requests.
    char req[512];
    const char *id = "0123456789abcdef0123456789abcdef";
    size_t n = panel_update_report_request(req, sizeof req, "10.0.2.2", 8000, id, "{\"a\":1}", 7, "0.1.0");
    CHECK(n > 0 && strncmp(req, "POST /api/panels/0123456789abcdef0123456789abcdef/update HTTP/1.1\r\nHost: 10.0.2.2:8000\r\n", 83) == 0);
    CHECK(panel_update_report_request(req, sizeof req, "10.0.2.2", 8000, "../x", "{}", 2, "0.1.0") == 0);
    n = panel_image_request(req, sizeof req, "10.0.2.2", 8000, "/api/panels/firmware/0.2.0.bin", "0.1.0");
    CHECK(n > 0 && strncmp(req, "GET /api/panels/firmware/0.2.0.bin HTTP/1.1\r\nHost: 10.0.2.2:8000\r\n", 66) == 0);
    CHECK(panel_image_request(req, sizeof req, "10.0.2.2", 8000, "http://evil/x", "0.1.0") == 0);
}

// ---------------------------------------------------------------------------
// The Planning Center service (#82)
// ---------------------------------------------------------------------------

// A `service` message as app/servicefeed.py sends it.
const char *kService =
    "{\"type\":\"service\",\"service_type\":\"7\",\"followed\":true,\"name\":\"Sunday AM\","
    "\"plan\":{\"id\":\"700\",\"title\":\"Sunday Morning\",\"series_title\":\"Autumn\",\"dates\":\"November 8, 2026\"},"
    "\"items\":[{\"id\":\"1\",\"title\":\"Countdown\",\"type\":\"media\",\"length\":300,\"position\":\"pre\"},"
    "{\"id\":\"2\",\"title\":\"Worship\",\"type\":\"header\",\"length\":0,\"position\":\"during\"},"
    "{\"id\":\"3\",\"title\":\"Caf\\u00e9 Song \\u201cLive\\u201d\",\"type\":\"song\",\"length\":240,\"position\":\"during\","
    "\"key\":\"G\",\"arrangement\":\"Radio\",\"sequence\":[\"Verse 1\",\"Chorus 1\",\"Bridge\"],"
    "\"notes\":[{\"category\":\"Vocals\",\"content\":\"Ask Jos\\u00e9 to lead\"},{\"category\":\"\",\"content\":\"Slow end\"}]},"
    "{\"id\":\"4\",\"title\":\"Sermon\",\"type\":\"item\",\"length\":1500,\"position\":\"during\"},"
    "{\"id\":\"5\",\"title\":\"Odd\",\"type\":\"something\",\"length\":-3,\"position\":\"post\"}],"
    "\"notes\":true,\"current_item\":\"3\",\"next_item\":\"4\",\"item_started_at\":1799999940.0,\"item_length_s\":240,"
    "\"item_left_s\":20.0,\"service_started_at\":1799999400,\"service_over_s\":-45.5,"
    "\"live\":true,\"controller\":\"Dana Ort\\u00edz\",\"reachable\":true,\"updated_at\":1800000000.0,"
    "\"server_time\":1800000000.5}";

void check_service()
{
    // The panel's service mode, from a check-in reply or a `service_mode` event.
    {
        panel_service m;
        const char *both = "{\"mode\":\"both\",\"service_type\":\"7\",\"key\":\"0123456789abcdef0123456789abcdef\"}";
        CHECK(panel_service_parse(both, both + strlen(both), &m));
        CHECK(m.mode == service_mode::both && strcmp(m.type, "7") == 0 && strlen(m.key) == 32);
        CHECK(service_mode_shows(m.mode) && service_mode_controls(m.mode));
        const char *show = "{\"mode\":\"show\",\"service_type\":null,\"key\":\"0123456789abcdef0123456789abcdef\"}";
        CHECK(panel_service_parse(show, show + strlen(show), &m));
        CHECK(m.mode == service_mode::show && m.type[0] == '\0' && m.key[0] == '\0');  // no key without control
        const char *bad_type = "{\"mode\":\"control\",\"service_type\":\"x1\",\"key\":\"short\"}";
        CHECK(panel_service_parse(bad_type, bad_type + strlen(bad_type), &m));
        CHECK(m.type[0] == '\0' && m.key[0] == '\0');
        panel_service keep = m;
        const char *unknown = "{\"mode\":\"sideways\"}";
        CHECK(!panel_service_parse(unknown, unknown + strlen(unknown), &keep));
        CHECK(keep.mode == service_mode::control);

        const char *ev = "{\"type\":\"service_mode\",\"mode\":\"show\",\"service_type\":\"42\"}";
        const panel_event e = panel_parse_event(ev, strlen(ev));
        CHECK(e.kind == panel_message::service_mode && e.service.mode == service_mode::show &&
              strcmp(e.service.type, "42") == 0);
        const char *reply = "{\"ok\":true,\"name\":\"Lobby\",\"interval_s\":30,\"service\":{\"mode\":\"off\",\"service_type\":null}}";
        const panel_reply r = panel_parse_reply(reply, strlen(reply));
        CHECK(r.ok && r.has_service && r.service.mode == service_mode::off);
        const char *old_relay = "{\"ok\":true,\"name\":\"Lobby\",\"interval_s\":30}";
        CHECK(!panel_parse_reply(old_relay, strlen(old_relay)).has_service);
    }

    // The offers in `targets`, and the menu the mode allows.
    {
        std::string targets = kTargets;
        targets.insert(targets.size() - 1,
                       ",\"service\":[{\"service_type\":\"7\",\"label\":\"Sunday AM\"},"
                       "{\"service_type\":\"42\",\"label\":\"Mi\\u00e9rcoles\"},{\"service_type\":\"x\",\"label\":\"Bad\"}]");
        const service_offers offers = service_offers_parse(targets.c_str(), targets.size());
        CHECK(offers.count == 2 && strcmp(offers.items[1].type, "42") == 0);
        const feed_options base = parse_options(targets.c_str());
        CHECK(base.count == 3);  // the captions alone: Transcription, French, Spanish

        panel_service off;
        CHECK(feed_options_same(feed_options_with_service(base, offers, off), base));
        panel_service show;
        show.mode = service_mode::show;
        feed_options all = feed_options_with_service(base, offers, show);
        CHECK(all.count == 5 && all.items[3].pick.stream == kFeedService && strcmp(all.items[3].pick.lang, "7") == 0);
        CHECK(strcmp(all.items[3].label, "Service: Sunday AM") == 0);
        CHECK(strcmp(all.items[4].label, "Service: Miercoles") == 0);  // the menu's font is ASCII
        snprintf(show.type, sizeof show.type, "42");
        feed_options one = feed_options_with_service(base, offers, show);
        CHECK(one.count == 4 && strcmp(one.items[3].label, "Service") == 0 && strcmp(one.items[3].pick.lang, "42") == 0);
        CHECK(service_offers_parse(kTargets, strlen(kTargets)).count == 0);  // an older Relay offers none

        // A service pick is shown while it is offered; otherwise the pane falls back to the transcription.
        const feed_pick svc = feed_pick_make(kFeedService, "42");
        CHECK(svc.stream == kFeedService && strcmp(svc.lang, "42") == 0);
        feed_resolved r = feed_resolve(svc, one, false);
        CHECK(feed_pick_same(r.pick, svc) && !r.fallback);
        r = feed_resolve(svc, base, false);
        CHECK(r.pick.stream == kFeedTranscription && r.fallback);
        char note[64];
        CHECK(feed_fallback_note(svc, note, sizeof note) > 0);
        feed_options unknown;
        r = feed_resolve(svc, unknown, false);
        CHECK(feed_pick_same(r.pick, svc));  // tried before Relay has said what it offers
        CHECK(feed_pick_make(4, "1").stream == kFeedNone);
    }

    // The request.
    {
        char req[512];
        CHECK(caption_request(req, sizeof req, "relay.local", 8000, kFeedService, "678893", 99, "0.3.0") > 0);
        CHECK(strstr(req, "GET /stream?stream=service&type=678893 HTTP/1.1") != nullptr);  // no finals, no resume
        CHECK(caption_request(req, sizeof req, "relay.local", 8000, kFeedService, "7&x=1", 0, "0.3.0") == 0);
        CHECK(caption_request(req, sizeof req, "relay.local", 8000, kFeedService, "", 0, "0.3.0") == 0);
    }

    // The settings keep a saved service pick; an unknown feed is still none.
    {
        relay_settings_t st;
        settings_defaults_fill(&st, 0);
        st.feed_stream = kFeedService;
        st.feed2_stream = 9;
        relay_settings_t out;
        CHECK(settings_decode(&st, sizeof st, &out, 0) == settings_blob::accepted);
        CHECK(out.feed_stream == kFeedService && out.feed2_stream == kFeedNone);
    }

    // The message.
    {
        static service_state s;
        CHECK(service_apply(&s, kService, strlen(kService), 1000) == service_change::state);
        CHECK(s.known && s.followed && strcmp(s.name, "Sunday AM") == 0);
        CHECK(s.has_plan && strcmp(s.plan_title, "Sunday Morning") == 0);
        CHECK(s.count == 5 && s.total == 5);
        CHECK(s.items[0].type == service_item_type::media && s.items[0].position == service_position::pre);
        CHECK(s.items[1].type == service_item_type::header);
        CHECK(s.items[4].type == service_item_type::item && s.items[4].length_s == 0 &&
              s.items[4].position == service_position::post);
        const service_item &song = s.items[2];
        CHECK(strcmp(song.title, "Caf\xC3\xA9 Song \"Live\"") == 0);  // accents kept for the caption font
        CHECK(strcmp(song.key, "G") == 0 && strcmp(song.sequence, "Verse 1, Chorus 1, Bridge") == 0);
        CHECK(strcmp(song.notes, "Vocals: Ask Jos\xC3\xA9 to lead\nSlow end") == 0);
        CHECK(s.current == 2 && s.next == 3 && s.timing && s.item_left_s == 20.0 && s.has_over);
        CHECK(s.live && s.reachable && s.notes_allowed && strcmp(s.controller, "Dana Ort\xC3\xADz") == 0);
        char words[96];
        service_song_words(song, words, sizeof words);
        CHECK(strcmp(words, "Key G - Verse 1, Chorus 1, Bridge") == 0);
        service_length_words(s.items[3], words, sizeof words);
        CHECK(strcmp(words, "25:00") == 0);
        service_length_words(s.items[1], words, sizeof words);
        CHECK(words[0] == '\0');  // a header has no length

        // The countdown, from when the message arrived.
        CHECK(service_item_left(s, 6000) == 15.0);
        service_item_left_words(s, 6000, words, sizeof words);
        CHECK(strcmp(words, "0:15 left") == 0);
        service_item_left_words(s, 95000, words, sizeof words);
        CHECK(strcmp(words, "1:14 over") == 0);
        // Over/under only grows once the item has run past its length.
        CHECK(service_over(s, 11000) == -45.5);
        CHECK(service_over(s, 21000) == -45.5);
        CHECK(service_over(s, 101000) == 34.5);
        service_over_words(s, 1000, words, sizeof words);
        CHECK(strcmp(words, "Service 0:45 ahead") == 0);
        service_over_words(s, 41000, words, sizeof words);
        CHECK(strcmp(words, "Service on time") == 0);
        service_over_words(s, 201000, words, sizeof words);
        CHECK(strcmp(words, "Service 2:14 behind") == 0);
        service_status st = service_footer(s, 201000);
        CHECK(st.tone == service_tone::good);
        char ascii[96];
        service_ascii(st.text, ascii, sizeof ascii);
        CHECK(strcmp(ascii, "Service 2:14 behind - Dana Ortiz is controlling") == 0);
        CHECK(service_focus(s) == 2);

        // A tick moves `updated_at` and leaves the countdown alone.
        const char *tick = "{\"type\":\"service_tick\",\"service_type\":\"7\",\"server_time\":1800000015.5,\"updated_at\":1800000014}";
        CHECK(service_apply(&s, tick, strlen(tick), 16000) == service_change::tick);
        CHECK(s.updated_at == 1800000014.0 && service_item_left(s, 16000) == 5.0);
        CHECK(s.changes == 1);

        // The footer, worst news first.
        std::string m = kService;
        auto with = [&](const char *from, const char *to) {
            std::string out = m;
            out.replace(out.find(from), strlen(from), to);
            return out;
        };
        std::string j = with("\"reachable\":true", "\"reachable\":false");
        service_apply(&s, j.c_str(), j.size(), 0);
        CHECK(strcmp(service_footer(s, 0).text, "Planning Center unreachable") == 0 &&
              service_footer(s, 0).tone == service_tone::bad);
        j = with("\"live\":true,\"controller\":\"Dana Ort\\u00edz\"", "\"live\":false,\"controller\":null");
        service_apply(&s, j.c_str(), j.size(), 0);
        CHECK(strcmp(service_footer(s, 0).text, "Not live") == 0 && s.controller[0] == '\0');
        j = with("\"followed\":true", "\"followed\":false");
        service_apply(&s, j.c_str(), j.size(), 0);
        CHECK(strcmp(service_footer(s, 0).text, "Relay does not follow this service") == 0);
        const char *unfollowed = "{\"type\":\"service\",\"service_type\":\"8\",\"followed\":false,\"server_time\":1}";
        CHECK(service_apply(&s, unfollowed, strlen(unfollowed), 0) == service_change::state);
        CHECK(!s.followed && s.count == 0 && s.current == -1 && service_focus(s) == -1);
        const char *noplan = "{\"type\":\"service\",\"followed\":true,\"plan\":null,\"items\":[],\"live\":false}";
        service_apply(&s, noplan, strlen(noplan), 0);
        CHECK(!s.has_plan && strcmp(service_footer(s, 0).text, "No service planned today") == 0);
        CHECK(service_apply(&s, "{\"type\":\"targets\"}", 18, 0) == service_change::ignored);
        CHECK(service_apply(&s, "nope", 4, 0) == service_change::malformed);
        static service_state fresh;
        CHECK(service_apply(&fresh, tick, strlen(tick), 0) == service_change::ignored);  // a tick before any state
        CHECK(strcmp(service_footer(fresh, 0).text, "Waiting for Relay") == 0);

        // A long plan keeps the first kServiceItemsMax and says how many there were.
        std::string big = "{\"type\":\"service\",\"followed\":true,\"items\":[";
        for (int i = 0; i < kServiceItemsMax + 5; ++i) {
            big += (i ? "," : "") + std::string("{\"id\":\"") + std::to_string(i) + "\",\"title\":\"Item\"}";
        }
        big += "],\"current_item\":\"" + std::to_string(kServiceItemsMax + 2) + "\"}";
        service_apply(&s, big.c_str(), big.size(), 0);
        CHECK(s.count == kServiceItemsMax && s.total == kServiceItemsMax + 5 && s.current == -1);
    }

    // Clocks and the ASCII the Montserrat text needs.
    {
        char c[16];
        service_clock(3725.9, c, sizeof c);
        CHECK(strcmp(c, "1:02:05") == 0);
        service_clock(-61, c, sizeof c);
        CHECK(strcmp(c, "1:01") == 0);
        char a[64];
        service_ascii("\xC3\x89" "glise \xC5\x81\xC3\xB3" "d\xC5\xBA \xE2\x80\x94 \xE6\x97\xA5", a, sizeof a);
        CHECK(strcmp(a, "Eglise Lodz - ?") == 0);
    }
}

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
    check_discovery();
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
    check_caption_fold();
    check_caption_json();
    check_caption_http();
    check_caption_sse();
    check_caption_buffer();
    check_caption_feed();
    check_caption_connection();
    check_caption_view();
    check_feed_options();
    check_feed_resolve();
    check_feed_panes();
    check_targets_request();
    check_settings_split_and_v1();
    check_panel_link();
    check_service();
    check_update();

    if (g_failures != 0) {
        fprintf(stderr, "%d check(s) failed\n", g_failures);
        return 1;
    }
    printf("ok\n");
    return 0;
}
