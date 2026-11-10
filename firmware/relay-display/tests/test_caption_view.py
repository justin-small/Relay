"""The caption screen against a fake Relay: lines arrive and follow, a drag back pauses, Jump to live resumes.

The emulator has no touch controller, so the firmware takes a finger from the console (`touch <x> <y>`,
`touch up`; see `test_touch.py`), in the controller's own coordinates. The board here is the 180 degree seed, so a
logical position (x, y) is put in as (799 - x, 479 - y), and the framebuffer is read the same way. `view` makes the
firmware log whether the list follows, how many lines it holds, how far it is from the bottom and whether the
button shows, which is what is checked here; the framebuffer is checked for the button and for the header.

What this does not cover is how a real finger and the GT911 feel, and how smooth the scroll is at 200 lines while
lines keep arriving: those are the bench items in README.md.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from conftest import HEIGHT, WIDTH, capture, wait_ready
from fake_relay import FakeRelay
from qemu import Board

ANSI = re.compile(r"\x1b\[[0-9;]*m")
VIEW = re.compile(r"view: (following live|paused), (\d+) lines, (-?\d+) px below, jump button (shown|hidden)")

# The button's place in logical coordinates: centred, 20 px above the bottom edge, 280 x 64.
JUMP_X, JUMP_Y = 400, 428


@dataclass
class View:
    following: bool
    lines: int
    below: int
    jump_shown: bool


@pytest.fixture
def panel(provisioned: Board) -> Iterator[Board]:
    wait_ready(provisioned)
    yield provisioned
    assert provisioned.uart is not None
    mark = provisioned.uart.mark()
    provisioned.uart.send_line("touch up")
    provisioned.uart.send_line("feed 0 off")
    provisioned.uart.wait_for_after(mark, r"console: feed set")


@pytest.fixture
def relay() -> Iterator[FakeRelay]:
    fake = FakeRelay()
    yield fake
    fake.close()


def view(board: Board) -> View:
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line("view")
    m = board.uart.wait_for_after(mark, VIEW.pattern, timeout=10)
    return View(m.group(1) == "following live", int(m.group(2)), int(m.group(3)), m.group(4) == "shown")


def wait_view(board: Board, ok: Callable[[View], bool], what: str, timeout: float = 20.0) -> View:
    deadline = time.monotonic() + timeout
    last = view(board)
    while not ok(last):
        if time.monotonic() > deadline:
            assert board.uart is not None
            recent = [l for l in ANSI.sub("", board.uart.text).splitlines() if re.search(r"view:|pointer|touch", l)][-12:]
            raise AssertionError(f"{what}: the view was {last}\n" + "\n".join(recent))
        time.sleep(0.25)
        last = view(board)
    return last


def settled(board: Board, timeout: float = 60.0) -> View:
    """Wait for the list to stop moving. The emulator reads the finger once per frame, which takes it a second or
    more, so a quick drag reads as a hard flick and LVGL throws the list on after it for several frames.
    """
    deadline = time.monotonic() + timeout
    readings = [view(board)]
    while len(readings) < 4 or len({r.below for r in readings[-4:]}) > 1:
        assert time.monotonic() < deadline, f"the list kept moving: {[r.below for r in readings]}"
        readings.append(view(board))
    return readings[-1]


def show_feed(board: Board, relay: FakeRelay) -> None:
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line(f"feed 0 {relay.url} translation SPANISH")
    board.uart.wait_for_after(mark, r"console: feed set")
    board.uart.wait_for_after(mark, r"view: caption view up for slot 0")


def finger(board: Board, x: int, y: int) -> None:
    """Put the finger down, or move it, at logical (x, y) of the turned panel."""
    assert board.uart is not None
    board.uart.send_line(f"touch {WIDTH - 1 - x} {HEIGHT - 1 - y}")
    time.sleep(0.15)


def tap(board: Board, x: int, y: int) -> None:
    """Press and lift at logical (x, y), holding until LVGL has seen the press (it reads once a frame)."""
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line(f"touch {WIDTH - 1 - x} {HEIGHT - 1 - y}")
    board.uart.wait_for_after(mark, r"pointer down at", timeout=30)
    board.uart.send_line("touch up")
    board.uart.wait_for_after(mark, r"pointer up at", timeout=30)


def drag(board: Board, x: int, y0: int, y1: int, steps: int = 6) -> None:
    """Put the finger down at (x, y0), move it to y1 and lift it. LVGL reads the finger once per frame, so the press
    is waited for before the finger moves, and the finger is held still before it lifts.
    """
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line(f"touch {WIDTH - 1 - x} {HEIGHT - 1 - y0}")
    board.uart.wait_for_after(mark, r"pointer down at", timeout=30)
    for i in range(1, steps + 1):
        finger(board, x, y0 + (y1 - y0) * i // steps)
    time.sleep(1.5)
    board.uart.send_line("touch up")
    board.uart.wait_for_after(mark, r"pointer up at", timeout=30)
    time.sleep(0.5)


def feed_lines(relay: FakeRelay, n: int, tag: str = "Línea") -> None:
    for i in range(n):
        relay.commit(f"{tag} {i}: una frase que es lo bastante larga para ocupar bastante espacio en pantalla.")


def pixel(board: Board, name: str, x: int, y: int) -> tuple[int, int, int]:
    """The framebuffer at logical (x, y) of the turned panel."""
    return capture(board, name).rgb(WIDTH - 1 - x, HEIGHT - 1 - y)


def test_lines_arrive_and_the_view_follows(panel: Board, relay: FakeRelay) -> None:
    feed_lines(relay, 30)
    show_feed(panel, relay)
    first = wait_view(panel, lambda v: v.lines == 30, "the snapshot's lines")
    assert first.following and first.below <= 6 and not first.jump_shown
    assert first.below >= 0, "thirty long lines fill more than a screen, so the list is at its end and not short of it"
    # Lines that arrive after the first picture scroll into view.
    feed_lines(relay, 5, tag="Nueva")
    wait_view(panel, lambda v: v.lines == 35 and v.following and v.below <= 6, "five more lines following")


def test_the_header_shows_the_feed_and_the_connection(panel: Board, relay: FakeRelay) -> None:
    feed_lines(relay, 3)
    show_feed(panel, relay)
    wait_view(panel, lambda v: v.lines == 3, "three lines")
    # The header bar is a step lighter than the ground, and the status dot in it is green while connected.
    header = pixel(panel, "header", 8, 8)
    ground = pixel(panel, "ground", 8, 300)
    assert header[0] > ground[0], (header, ground)
    green = [pixel(panel, "dot", x, 28) for x in range(WIDTH - 220, WIDTH - 28, 4)]
    assert any(g > r + 60 and g > b for r, g, b in green), "no green status dot in the header"


def test_a_drag_back_pauses_and_jump_to_live_resumes(panel: Board, relay: FakeRelay) -> None:
    feed_lines(relay, 40)
    show_feed(panel, relay)
    wait_view(panel, lambda v: v.lines == 40 and v.following, "forty lines following")
    # The finger moves down the glass, which takes the list back in time.
    drag(panel, 400, 120, 400)
    wait_view(panel, lambda v: not v.following, "a drag back should pause")
    paused = settled(panel)
    assert not paused.following and paused.below > 100 and paused.jump_shown
    r, g, b = pixel(panel, "jump", JUMP_X, JUMP_Y - 26)  # inside the button, above its label
    assert b > 180 and r < 100, ("Jump to live is not on the glass", (r, g, b))

    # New lines do not pull a paused view along, and the way back to live grows.
    feed_lines(relay, 3, tag="Mientras")
    still = wait_view(panel, lambda v: v.lines == 43, "three more lines while paused")
    assert not still.following and still.below > paused.below and still.jump_shown

    # Tapping the button resumes following and takes the button away.
    tap(panel, JUMP_X, JUMP_Y)
    live = wait_view(panel, lambda v: v.following, "Jump to live should resume")
    assert live.below <= 6 and not live.jump_shown


def test_scrolling_back_to_the_bottom_resumes(panel: Board, relay: FakeRelay) -> None:
    feed_lines(relay, 40)
    show_feed(panel, relay)
    wait_view(panel, lambda v: v.lines == 40 and v.following, "forty lines following")
    drag(panel, 400, 120, 400)
    wait_view(panel, lambda v: not v.following, "a drag back should pause")
    # The finger moves up the glass, from the middle to the top, over and over, until the list is at its end.
    deadline = time.monotonic() + 90
    seen = []
    while (v := view(panel)).below > 6:
        seen.append(v.below)
        assert time.monotonic() < deadline, f"the list never reached its end; px below after each drag: {seen}"
        drag(panel, 400, 380, 80)
    wait_view(panel, lambda v: v.following and not v.jump_shown, "reaching the bottom should resume")


def test_vietnamese_and_accented_text_draws(panel: Board, relay: FakeRelay) -> None:
    """The lines are stored as sent (the client folds only what the font lacks), and the view takes them as they are."""
    relay.commit("Tiếng Việt: Ặ ẫ ơ ư đ Ỳ ỹ — Español: ñ Ñ ¿ ¡ — Français: œ Œ ç — Čeština: ř Ž — € Ł ł", lang="SPANISH")
    show_feed(panel, relay)
    wait_view(panel, lambda v: v.lines == 1, "the line")
    time.sleep(0.5)
    screen = capture(panel, "vietnamese")
    # Some of the list area is lit: a line drawn in white on the dark ground.
    lit = sum(
        1
        for y in range(HEIGHT - 480, HEIGHT - 60, 3)
        for x in range(0, WIDTH, 3)
        if sum(screen.rgb(x, y)) > 500
    )
    assert lit > 200, f"only {lit} lit samples: the line did not draw"


def test_a_full_buffer_keeps_following_and_keeps_its_place_when_paused(panel: Board, relay: FakeRelay) -> None:
    """Two hundred lines is the buffer. Each new line pushes the oldest out: a following list stays at the end, and
    a paused one stays paused, with the way back to live growing by what arrived.
    """
    feed_lines(relay, 230)
    show_feed(panel, relay)
    wait_view(panel, lambda v: v.lines == 200 and v.following, "the newest 200 of 230")
    feed_lines(relay, 15, tag="Más")
    wait_view(panel, lambda v: v.lines == 200 and v.following and v.below <= 6, "following while the oldest go")

    drag(panel, 400, 120, 400)
    wait_view(panel, lambda v: not v.following, "a drag back should pause")
    before = settled(panel)
    feed_lines(relay, 10, tag="Pausa")
    after = wait_view(panel, lambda v: v.lines == 200 and not v.following and v.below > before.below, "ten more while paused")
    assert after.jump_shown
    assert capture(panel, "heap").width == WIDTH  # the board is still drawing
