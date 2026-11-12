"""ProPresenter's lyrics and timers on the panel (#138), against a fake Relay: offered in the menu only while Relay offers
them, set by the operator like any feed, the lyrics pane up with a slide and quiet without one, the timers pane up with
its rows and asking Relay for this panel's own by its id, and **a still screen costing no redraw**: Relay repeats each
feed's state every heartbeat, and the panel must announce a change only when what it shows has changed.

The fake copies app/modules/propresenter/propresenter.py's wire format by hand (`tests/test_propresenter.py` at the repository root checks
the real server's side). The panel's `feeds` and `view` console commands are what is read, as in
`test_service_view.py`.

What this does not cover is how the lyrics and the digits look from across a room, and the amber and red on the glass:
README items. The counting itself, the sizes and the colours are `test_native.cpp`'s.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import pytest

from conftest import wait_ready
from fake_relay import FakeRelay
from qemu import Board
from test_caption_view import ANSI
from test_feed_picker import relay, wait_report  # noqa: F401 - `relay` is a fixture

SLIDE = {"live": True, "song": True, "title": "Great Is Thy Faithfulness", "part": "Verse 1", "index": 0,
         "text": "Great is Thy faithfulness\nO God my Father", "next": "Morning by morning", "rev": 1}
TIMERS = {"live": True, "amber_s": 120, "red_s": 30, "rev": 1, "timers": [
    {"id": "T-A", "name": "Segment Countdown", "state": "running", "dir": "down", "value": 298.0, "level": "ok"},
    {"id": "T-B", "name": "Game Timer", "state": "stopped", "dir": "up", "value": 60.0, "level": "ok"}]}


def view_line(board: Board, pattern: str, timeout: float = 20.0) -> re.Match:
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line("view")
    return board.uart.wait_for_after(mark, pattern, timeout=timeout)


def wait_view(board: Board, pattern: str, what: str, timeout: float = 30.0) -> re.Match:
    deadline = time.monotonic() + timeout
    while True:
        try:
            return view_line(board, pattern, timeout=4)
        except Exception:
            if time.monotonic() > deadline:
                assert board.uart is not None
                recent = [l for l in ANSI.sub("", board.uart.text).splitlines() if re.search(r"view:|feeds:|captions", l)]
                raise AssertionError(f"{what}: no {pattern}\n" + "\n".join(recent[-14:]))


def wait_request(relay: FakeRelay, ok: Callable[[dict[str, str]], bool], what: str, timeout: float = 30.0) -> None:
    """The panel has opened the stream (showing a feed and having connected to it are two moments)."""
    deadline = time.monotonic() + timeout
    while not any(ok(q) for q in list(relay.requests)):
        assert time.monotonic() < deadline, f"{what}: {relay.requests}"
        time.sleep(0.2)


def count(board: Board, pattern: str) -> int:
    assert board.uart is not None
    return len(re.findall(pattern, ANSI.sub("", board.uart.text)))


@pytest.fixture
def panel(boot: Callable[[str | None], Board], relay: FakeRelay) -> Board:  # noqa: F811
    relay.offer_propresenter(lyrics=True, timers=True)
    machine = boot("nvs")
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"relay {relay.url}")
    machine.uart.wait_for_after(mark, r"console: relay set")
    relay.wait_checkins(1)
    return machine


def test_offered_only_while_relay_offers_them(panel: Board, relay: FakeRelay) -> None:
    r = wait_report(panel, lambda r: "lyrics" in r.options and "timers" in r.options, "both offered")
    assert r.options.index("lyrics") < r.options.index("timers") < r.options.index("alert")
    relay.offer_propresenter(lyrics=False, timers=True)
    r = wait_report(panel, lambda r: "lyrics" not in r.options, "the lyrics gone when they are off for panels")
    assert "timers" in r.options
    relay.offer_propresenter(lyrics=False, timers=False)
    wait_report(panel, lambda r: "timers" not in r.options, "and the timers when ProPresenter is off")


def test_the_lyrics_view(panel: Board, relay: FakeRelay) -> None:
    relay.set_propresenter("lyrics", SLIDE)
    relay.set_feed([{"stream": "lyrics"}])
    wait_report(panel, lambda r: r.shows[0] == "lyrics", "pane 1 shows the lyrics")
    wait_request(relay, lambda q: q.get("stream") == "lyrics" and q.get("for") == "panel", "the lyrics stream asked for")
    wait_view(panel, r"view: report: pane 1 lyrics, a slide up", "the slide up")

    # **A still screen is not redrawn**: Relay repeats the state every second here, and the panel announces it once.
    time.sleep(4)
    assert count(panel, r"captions\[0\]: lyrics changed") == 1

    # The next slide is a change; the same one again, a new `rev` and all, is not.
    relay.set_propresenter("lyrics", {**SLIDE, "index": 1, "part": "Chorus", "text": "Morning by morning", "next": "", "rev": 2})
    wait_view(panel, r"view: report: pane 1 lyrics, a slide up", "still up")
    deadline = time.monotonic() + 20
    while count(panel, r"captions\[0\]: lyrics changed") < 2:
        assert time.monotonic() < deadline, "the new slide was not read"
        time.sleep(0.3)
    relay.set_propresenter("lyrics", {**SLIDE, "index": 1, "part": "Chorus", "text": "Morning by morning", "next": "", "rev": 99})
    time.sleep(3)
    assert count(panel, r"captions\[0\]: lyrics changed") == 2

    # Blank, then not live: quiet, never the last slide.
    relay.set_propresenter("lyrics", {**SLIDE, "text": "", "next": "", "rev": 3})
    wait_view(panel, r"view: report: pane 1 lyrics, idle", "a blank slide is idle")
    relay.set_propresenter("lyrics", {"live": False, "rev": 4})
    wait_view(panel, r"view: report: pane 1 lyrics, idle", "no song is idle")
    relay.set_propresenter("lyrics", SLIDE)
    wait_view(panel, r"view: report: pane 1 lyrics, a slide up", "and a slide again")


def test_a_lyrics_feed_that_is_off_says_so(panel: Board, relay: FakeRelay) -> None:
    relay.pp_status = 403
    relay.set_feed([{"stream": "lyrics"}])
    wait_report(panel, lambda r: r.shows[0] == "lyrics", "pane 1 stays on the lyrics")
    assert panel.uart is not None
    panel.uart.wait_for(r"captions\[0\]: Relay answered 403", timeout=30)
    wait_view(panel, r"view: report: pane 1 lyrics, idle", "idle while Relay refuses it")
    relay.pp_status = 200
    relay.set_propresenter("lyrics", SLIDE)
    wait_view(panel, r"view: report: pane 1 lyrics, a slide up", "and up when Relay allows it", timeout=60)


def test_the_timers_view(panel: Board, relay: FakeRelay) -> None:
    relay.set_propresenter("pptimers", TIMERS)
    relay.set_feed([{"stream": "timers"}])
    wait_report(panel, lambda r: r.shows[0] == "timers", "pane 1 shows the timers")
    panel_id = relay.checkins[0]["id"]
    wait_request(relay, lambda q: q.get("stream") == "pptimers" and q.get("for") == "panel" and q.get("panel") == panel_id,
                 "the timers stream asked for, naming this panel")
    wait_view(panel, r"view: report: pane 1 timers, 2 shown", "two rows")

    # A running countdown is counted by the panel, and announced once: Relay's repeats change nothing.
    time.sleep(4)
    assert count(panel, r"captions\[0\]: timers changed") == 1

    # One timer fewer is a change to the rows.
    relay.set_propresenter("pptimers", {**TIMERS, "timers": TIMERS["timers"][:1], "rev": 2})
    wait_view(panel, r"view: report: pane 1 timers, 1 shown", "one row")
    relay.set_propresenter("pptimers", {"live": False, "timers": [], "rev": 3})
    wait_view(panel, r"view: report: pane 1 timers, 0 shown", "no rows when ProPresenter is away")
