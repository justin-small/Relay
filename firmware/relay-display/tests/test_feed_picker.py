"""The feed picker and the split view, against a fake Relay: what the menu offers, picking one feed and two, the
choice surviving a reboot, and a language that stops being live and comes back.

The emulator has no touch controller, so a finger is the console's `touch <x> <y>` (see `test_caption_view.py`, whose
helpers this uses). The board here is the 180 degree seed, and every position below is the *logical* one, as a person
at the panel sees it. `feeds` makes the firmware log what each pane has saved and shows and what the menu offers; that
and the `feeds:` and `view:` lines are what is read, and the framebuffer is read for the notice strip.

The seed `nvs` has a Relay address (the emulator's host, port 8000), a saved translation (SPANISH) and no split. The
fake Relay is on another port, and `relay <url>` points the feeds at it for the test, unsaved.

What this does not cover is how a real finger lands on a 56 px row at the bench: a README item.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from conftest import BOOT_TIMEOUT, wait_ready
from fake_relay import FakeRelay
from qemu import Board
from test_caption_view import ANSI, VIEW, pixel, tap

REPORT = re.compile(
    r"feeds: report: (single|split); pane 1 saved ([^;]*?) shows ([^;]*?); "
    r"pane 2 saved ([^;]*?) shows ([^;]*?); options (known|unknown) \[([^\]]*)\]"
)
SPLIT_VIEW = re.compile(r"view: split, pane 2: (following live|paused), (\d+) lines")

# Where things are, in logical coordinates. The menu button is 64 x 44 at the top right. The card is 620 x 440,
# centred, with 16 px of padding: a title row, a One feed / Two feeds row at y 94..146, (with two feeds) a Left /
# Right row at y 156..200, and then the feeds, 56 px each with 8 px between.
MENU_BUTTON = (756, 28)
DONE = (639, 60)
ONE_FEED = (206, 120)
TWO_FEEDS = (418, 120)
LEFT_TAB = (176, 178)
RIGHT_TAB = (328, 178)


def row(index: int, *, split: bool) -> tuple[int, int]:
    """The centre of the menu's `index`th feed."""
    return 400, (238 if split else 184) + 64 * index


@dataclass
class Report:
    split: bool
    saved: tuple[str, str]
    shows: tuple[str, str]
    known: bool
    options: list[str]


@pytest.fixture
def relay() -> Iterator[FakeRelay]:
    fake = FakeRelay()
    yield fake
    fake.close()


@pytest.fixture
def panel(boot: Callable[[str | None], Board], relay: FakeRelay) -> Board:
    """A panel from the `nvs` seed (a saved SPANISH translation), pointed at the fake Relay."""
    machine = boot("nvs")
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"relay {relay.url}")
    machine.uart.wait_for_after(mark, r"console: relay set")
    return machine


def report(board: Board) -> Report:
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line("feeds")
    m = board.uart.wait_for_after(mark, REPORT.pattern, timeout=10)
    return Report(
        split=m.group(1) == "split",
        saved=(m.group(2), m.group(4)),
        shows=(m.group(3), m.group(5)),
        known=m.group(6) == "known",
        options=[o for o in m.group(7).split(",") if o],
    )


def wait_report(board: Board, ok: Callable[[Report], bool], what: str, timeout: float = 45.0) -> Report:
    deadline = time.monotonic() + timeout
    last = report(board)
    while not ok(last):
        if time.monotonic() > deadline:
            assert board.uart is not None
            recent = [l for l in ANSI.sub("", board.uart.text).splitlines() if re.search(r"feeds:|view:", l)][-14:]
            raise AssertionError(f"{what}: the panel was {last}\n" + "\n".join(recent))
        time.sleep(0.5)
        last = report(board)
    return last


def open_menu(board: Board) -> int:
    """Tap the menu button; returns how many feeds the menu lists."""
    assert board.uart is not None
    mark = board.uart.mark()
    tap(board, *MENU_BUTTON)
    board.uart.wait_for_after(mark, r"view: menu opened", timeout=30)
    listed = board.uart.wait_for_after(mark, r"view: menu (?:one feed|two feeds), (\d+) on offer", timeout=30)
    return int(listed.group(1))


def tap_and_wait(board: Board, at: tuple[int, int], pattern: str) -> None:
    assert board.uart is not None
    mark = board.uart.mark()
    tap(board, *at)
    board.uart.wait_for_after(mark, pattern, timeout=45)


def both_views(board: Board) -> tuple[tuple[bool, int], tuple[bool, int]]:
    """(following, lines) of the left pane and of the right."""
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line("view")
    a = board.uart.wait_for_after(mark, VIEW.pattern, timeout=10)
    b = board.uart.wait_for_after(mark, SPLIT_VIEW.pattern, timeout=10)
    return (a.group(1) == "following live", int(a.group(2))), (b.group(1) == "following live", int(b.group(2)))


def test_the_menu_offers_what_is_live_and_what_the_panel_can_draw(panel: Board, relay: FakeRelay) -> None:
    """Transcription and the live translations, from Relay's `targets`. A live Japanese and Russian are never offered:
    the panel's font is Latin."""
    # Not announced on the stream: Relay says what is live only when it changes, so a panel that has just connected has to
    # ask `/api/targets` for it.
    relay.live = ["SPANISH", "FRENCH", "JAPANESE", "RUSSIAN"]
    r = wait_report(panel, lambda r: r.known and r.options, "the options from /api/targets")
    assert r.options == ["transcription", "FRENCH", "SPANISH"]
    assert relay.api_requests >= 1, "the panel never asked /api/targets: Relay says what is live only when it changes"
    assert open_menu(panel) == 3


def test_a_person_can_pick_one_feed(panel: Board, relay: FakeRelay) -> None:
    """Tap the menu, tap French: the pane shows French, the choice is saved, and the panel asked Relay for it."""
    wait_report(panel, lambda r: r.known, "Relay's live set")
    open_menu(panel)
    tap_and_wait(panel, row(1, split=False), r"feeds: pane 1 chosen: translation FRENCH")
    assert panel.uart is not None
    panel.uart.wait_for(r"feeds: saved", timeout=30)
    r = wait_report(panel, lambda r: r.shows[0] == "translation FRENCH", "pane 1 on French")
    assert r.saved[0] == "translation FRENCH" and not r.split
    deadline = time.monotonic() + 30
    while not any(q.get("lang") == "FRENCH" for q in relay.requests):
        assert time.monotonic() < deadline, f"Relay was never asked for French: {relay.requests}"
        time.sleep(0.2)
    # And back to the transcription.
    tap_and_wait(panel, row(0, split=False), r"feeds: pane 1 chosen: transcription")
    wait_report(panel, lambda r: r.shows[0] == "transcription", "pane 1 on the transcription")
    tap_and_wait(panel, DONE, r"view: menu closed")


def test_two_feeds_side_by_side_each_scroll_on_their_own(panel: Board, relay: FakeRelay) -> None:
    """Two feeds: transcription on the left and the first live translation on the right, each with its own lines."""
    wait_report(panel, lambda r: r.known, "Relay's live set")
    open_menu(panel)
    tap_and_wait(panel, row(0, split=False), r"feeds: pane 1 chosen: transcription")
    tap_and_wait(panel, TWO_FEEDS, r"view: caption view up for slot 1")
    r = wait_report(panel, lambda r: r.split and r.shows[1] != "nothing", "two panes")
    assert r.shows == ("transcription", "translation FRENCH"), r

    # A line on each feed lands in its own pane.
    relay.commit("Hello to the left", stream="transcription", lang="ENGLISH")
    relay.commit("Hola a la derecha", stream="translation", lang="FRENCH")
    relay.commit("Salut encore", stream="translation", lang="FRENCH")
    deadline = time.monotonic() + 45
    while (views := both_views(panel)) and (views[0][1], views[1][1]) != (1, 2):
        assert time.monotonic() < deadline, f"the panes held {views}, wanted 1 line left and 2 right"
        time.sleep(0.5)
    assert views[0][0] and views[1][0], "both panes follow live"

    # The two halves are drawn: the divider between them is lighter than the ground.
    ground = pixel(panel, "ground", 100, 300)
    divider = pixel(panel, "divider", 399, 300)
    assert divider[0] > ground[0] + 20, (divider, ground)

    # The right pane's pick is its own: change it and the left does not move.
    tap_and_wait(panel, RIGHT_TAB, r"view: menu two feeds, \d+ on offer, pane 2")
    tap_and_wait(panel, row(2, split=True), r"feeds: pane 2 chosen: translation SPANISH")
    r = wait_report(panel, lambda r: r.shows[1] == "translation SPANISH", "pane 2 on Spanish")
    assert r.shows[0] == "transcription" and r.saved == ("transcription", "translation SPANISH")

    # One feed again: the right pane goes, the left stays, and the choice for the right is kept for next time.
    tap_and_wait(panel, ONE_FEED, r"feeds: one feed")
    r = wait_report(panel, lambda r: not r.split and r.shows[1] == "nothing", "one pane again")
    assert r.shows[0] == "transcription" and r.saved[1] == "translation SPANISH"


def test_the_choice_survives_a_reboot(panel: Board, relay: FakeRelay) -> None:
    """Spanish on the left and the transcription on the right, saved, and found again after a reset."""
    wait_report(panel, lambda r: r.known, "Relay's live set")
    open_menu(panel)
    tap_and_wait(panel, row(2, split=False), r"feeds: pane 1 chosen: translation SPANISH")
    tap_and_wait(panel, TWO_FEEDS, r"view: caption view up for slot 1")
    tap_and_wait(panel, RIGHT_TAB, r"view: menu two feeds, \d+ on offer, pane 2")
    assert panel.uart is not None
    mark = panel.uart.mark()
    tap(panel, *row(0, split=True))
    panel.uart.wait_for_after(mark, r"feeds: pane 2 chosen: transcription", timeout=45)
    panel.uart.wait_for_after(mark, r"feeds: saved", timeout=45)
    before = wait_report(panel, lambda r: r.split and r.shows[1] == "transcription", "the choice shown")
    assert before.saved == ("translation SPANISH", "transcription")

    panel.reset()
    mark = panel.uart.mark()
    panel.uart.wait_for_after(mark, r"boot: ready", timeout=BOOT_TIMEOUT)
    panel.uart.wait_for_after(mark, r"feeds: started, two feeds", timeout=BOOT_TIMEOUT)
    after = report(panel)
    assert after.split
    assert after.saved == ("translation SPANISH", "transcription"), after


def test_a_language_that_goes_dark_falls_back_and_returns(panel: Board, relay: FakeRelay) -> None:
    """The seed's saved Spanish stops being live: the pane shows the transcription and says so; when Spanish is live
    again it is back, with nothing saved over in between."""
    relay.set_live(["FRENCH"])  # Spanish, the saved choice, is not live
    r = wait_report(panel, lambda r: r.known and r.shows[0].startswith("transcription"), "the fall-back")
    assert "(fallback)" in r.shows[0] and r.saved[0] == "translation SPANISH", r
    assert panel.uart is not None
    panel.uart.wait_for(r"feeds: pane 1 shows transcription \(its language is not live\)", timeout=10)
    # The notice strip under the header is amber.
    deadline = time.monotonic() + 30
    while True:
        red, green, blue = pixel(panel, "notice", 600, 72)
        if red > 55 and blue < 25 and red > green:
            break
        assert time.monotonic() < deadline, f"no notice strip on the glass: {(red, green, blue)}"
        time.sleep(0.5)

    relay.set_live(["FRENCH", "SPANISH"])
    r = wait_report(panel, lambda r: r.shows[0] == "translation SPANISH", "Spanish back")
    assert r.saved[0] == "translation SPANISH"
    deadline = time.monotonic() + 30
    while True:
        red, green, blue = pixel(panel, "no-notice", 600, 72)
        if not (red > 55 and blue < 25 and red > green):
            break
        assert time.monotonic() < deadline, "the notice strip stayed after Spanish came back"
        time.sleep(0.5)

    # And away again, as often as it likes.
    relay.set_live(["FRENCH"])
    wait_report(panel, lambda r: r.shows[0].startswith("transcription"), "away a second time")


def test_a_saved_language_that_is_not_live_is_still_in_the_menu(panel: Board, relay: FakeRelay) -> None:
    """What was chosen and is dark is listed, dim and ticked, so the menu says what is saved; it is not an offer."""
    relay.set_live(["FRENCH"])
    r = wait_report(panel, lambda r: r.known and "(fallback)" in r.shows[0], "the fall-back")
    assert r.options == ["transcription", "FRENCH"]
    assert panel.uart is not None
    mark = panel.uart.mark()
    tap(panel, *MENU_BUTTON)
    panel.uart.wait_for_after(mark, r"view: menu one feed, 3 on offer, pane 1", timeout=30)
