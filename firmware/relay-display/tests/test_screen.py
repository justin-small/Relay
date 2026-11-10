"""What the panel shows, read from the emulator's framebuffer.

The picture is `test_screen_pixel` in `main/panel_format.h`: a white border six pixels
wide, a red marker in the top-left corner, eight colour bars across the top 320 rows
(white, yellow, cyan, green, magenta, red, blue, black, left to right), and the name and
the version below. The virtual panel is turned in software for 180 degrees, so the same
picture reads in reverse on the panel: the marker bottom-right and the bars the other way
round. That is the check here.

The colours are compared after truncating each channel to RGB565, the rule
`rgb565_pack` uses, so an emulator that expands 5 bits to 8 by a different rule still
passes. The table below is `kBars` in `panel_format.h`, written out as 8-bit channels.
"""

from __future__ import annotations

import time

from conftest import HEIGHT, WIDTH, capture, pack565, wait_ready
from qemu import Board

# `kBars` in main/panel_format.h, left to right, as 8-bit channels.
BARS = [
    (255, 255, 255),  # white
    (255, 255, 0),  # yellow
    (0, 255, 255),  # cyan
    (0, 255, 0),  # green
    (255, 0, 255),  # magenta
    (255, 0, 0),  # red
    (0, 0, 255),  # blue
    (0, 0, 0),  # black
]
BAR_WIDTH = 100
BAR_ROW = 200  # below the marker, above the bottom of the bars
# The marker covers logical pixels FIRST to LAST inclusive on each axis: a 40-pixel square.
MARKER_FIRST = 16
MARKER_LAST = 55

# Panel centres of the eight bars, at BAR_ROW.
CENTRES = [BAR_WIDTH * i + BAR_WIDTH // 2 for i in range(8)]


def _shot(machine: Board, name: str):
    """The bench picture, asked for with the `bench` console command: the panel does not rest on it by itself."""
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line("bench")
    machine.uart.wait_for_after(mark, r"panel: test screen shown, backlight on", timeout=30)
    return capture(machine, name)


def test_the_border_is_white_at_all_four_corners_of_the_turned_panel(provisioned: Board) -> None:
    """The border is on all four sides, so the corners are white whichever way the picture
    is turned.
    """
    screen = _shot(provisioned, "turned-corners")
    for x, y in ((0, 0), (WIDTH - 1, 0), (0, HEIGHT - 1), (WIDTH - 1, HEIGHT - 1)):
        assert screen.rgb565(x, y) == pack565(255, 255, 255), (x, y, screen.rgb(x, y))


def test_the_border_is_white_at_all_four_corners_of_the_upright_panel(boot) -> None:
    screen = _shot(boot("nvs-upright"), "upright-corners")
    for x, y in ((0, 0), (WIDTH - 1, 0), (0, HEIGHT - 1), (WIDTH - 1, HEIGHT - 1)):
        assert screen.rgb565(x, y) == pack565(255, 255, 255), (x, y, screen.rgb(x, y))


def test_each_bar_is_at_its_centre_when_the_panel_is_turned(provisioned: Board) -> None:
    """Turned 180 degrees, panel bar j shows the logical bar 7 - j, so the first bar on the
    panel is black and the last is white. Each bar is sampled at its centre.
    """
    screen = _shot(provisioned, "turned-bars")
    for j, centre in enumerate(CENTRES):
        expected = BARS[7 - j]
        got = screen.rgb565(centre, BAR_ROW)
        assert got == pack565(*expected), (
            f"bar {j} at x={centre}: got {screen.rgb(centre, BAR_ROW)}"
        )


def test_each_bar_is_at_its_centre_when_the_panel_is_upright(boot) -> None:
    screen = _shot(boot("nvs-upright"), "upright-bars")
    for j, centre in enumerate(CENTRES):
        got = screen.rgb565(centre, BAR_ROW)
        assert got == pack565(*BARS[j]), f"bar {j} at x={centre}: got {screen.rgb(centre, BAR_ROW)}"


def test_the_red_marker_is_bottom_right_when_turned_and_top_left_when_upright(
    provisioned: Board, boot
) -> None:
    """The marker is logical pixels 16 to 55 on each axis. Turned, it covers 744 to 783
    across and 424 to 463 down; upright, 16 to 55 on both. The pixel just outside each
    corner is checked too, so an off-by-one in the turn fails here.
    """
    first, last = MARKER_FIRST, MARKER_LAST
    red = pack565(255, 0, 0)
    turned = _shot(provisioned, "turned-marker")
    far_x, far_y = WIDTH - 1 - first, HEIGHT - 1 - first  # the marker's logical origin
    assert turned.rgb565(far_x, far_y) == red
    assert turned.rgb565(WIDTH - 1 - last, HEIGHT - 1 - last) == red
    assert turned.rgb565(WIDTH - 1 - last - 1, HEIGHT - 1 - last - 1) != red  # outside
    assert turned.rgb565(far_x + 1, far_y) != red  # outside

    upright = _shot(boot("nvs-upright"), "upright-marker")
    assert upright.rgb565(first, first) == red
    assert upright.rgb565(last, last) == red
    assert upright.rgb565(last + 1, first) != red  # outside
    assert upright.rgb565(first - 1, first) != red  # outside


def test_the_turned_picture_is_the_upright_one_turned_over(provisioned: Board, boot) -> None:
    """Every pixel: the 180 degree frame is the upright frame reversed in both axes. This
    is the whole claim of the software turn, and the cases above say what it looks like.
    """
    turned = _shot(provisioned, "turned-frame")
    upright = _shot(boot("nvs-upright"), "upright-frame")

    row_bytes = WIDTH * 3
    rows = [upright.data[y * row_bytes : (y + 1) * row_bytes] for y in range(HEIGHT)]
    # Each row's pixels in reverse order, three bytes at a time, then the rows in reverse
    # order. Reversing the bytes instead would turn each pixel's channels around too.
    flipped = b"".join(
        b"".join(row[i : i + 3] for i in reversed(range(0, row_bytes, 3))) for row in rows[::-1]
    )
    assert len(flipped) == len(turned.data)
    assert flipped == turned.data


def test_the_title_reads_relay_not_the_borrowed_name(boot) -> None:
    """The bench picture's title is "RELAY" at scale 8: 232 pixels wide, from x=284, rows 340 to 395.

    The glyph table is shared with the VXP display, whose title was "VXP". R's top row is `0x1E`, so its
    fifth column is off, and E's top row is full. Upright panel, so the logical pixels are the panel's.
    """
    screen = _shot(boot("nvs-upright"), "upright-title")
    white, black = pack565(255, 255, 255), pack565(0, 0, 0)
    assert screen.rgb565(284, 340) == white  # R, top-left
    assert screen.rgb565(316, 340) == black  # R, top row, the fifth column is off
    assert screen.rgb565(332, 340) == white  # E, top-left
    assert screen.rgb565(371, 340) == white  # E, top-right
    assert screen.rgb565(283, 340) == black  # just left of the title


def test_an_unconfigured_panel_shows_the_setup_screen_not_the_bench_picture(boot) -> None:
    """With nothing stored the panel opens its setup network, and the screen is LVGL's, on the near-black
    ground: no colour bars, no white border. The text is drawn, so some pixels are white.

    Under the emulator the radio is skipped, so this is the screen the panel asks for as it starts: the
    drawing, the fonts and the flush are what is exercised, not the join.
    """
    machine = boot("nvs-unprovisioned")
    wait_ready(machine)
    # LVGL draws on its own task, after the request, in strips of a few rows. Wait until the last strip has
    # reached the corner and some text is lit, rather than for the first sign of either.
    ground, white = pack565(0x10, 0x10, 0x10), pack565(255, 255, 255)
    deadline = time.monotonic() + 30
    while True:
        screen = capture(machine, "setup")
        drawn = screen.rgb565(WIDTH - 1, HEIGHT - 1) == ground
        lit = any(screen.rgb565(x, y) == white for x in range(0, WIDTH, 4) for y in range(0, HEIGHT, 4))
        if drawn and lit:
            break
        assert time.monotonic() < deadline, "the setup screen never finished drawing"
        time.sleep(0.5)
    # The ground is LVGL's near-black (0x10), not the bench picture's white border.
    assert screen.rgb565(0, 0) == pack565(0x10, 0x10, 0x10), screen.rgb(0, 0)
    assert screen.rgb565(WIDTH - 1, HEIGHT - 1) == pack565(0x10, 0x10, 0x10)
    # No colour bars: the bench picture's red marker is not in the corner.
    assert screen.rgb565(30, 30) != pack565(255, 0, 0)


def test_a_panel_with_no_feed_shows_the_regular_display_not_the_bench_picture(boot) -> None:
    """The bench picture is a boot aid. A configured panel with nothing to show settles on LVGL's caption view,
    on its near-black ground, saying it has no feed: no white border and no colour bars.
    """
    machine = boot("nvs-upright")
    wait_ready(machine)
    ground, white = pack565(0x10, 0x10, 0x10), pack565(255, 255, 255)
    deadline = time.monotonic() + 30
    while True:
        screen = capture(machine, "no-feed")
        # The header bar is the top row's colour; the list below it is LVGL's near-black ground.
        if screen.rgb565(WIDTH - 1, HEIGHT - 1) == ground:
            break
        assert time.monotonic() < deadline, "the panel never left the bench picture"
        time.sleep(0.5)
    assert screen.rgb565(30, 30) != pack565(255, 0, 0)
    assert screen.rgb565(0, 0) != white
