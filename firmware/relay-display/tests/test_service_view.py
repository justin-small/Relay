"""The Planning Center Service feed on the panel (#82), against a fake Relay: offered in the menu only while the operator's
service mode allows it, chosen like any feed, and drawn with the current and next item, the order of service, the
timer counting down and running over, notes on a tap, and the footer saying when Planning Center goes away and comes
back.

The fake copies app/servicefeed.py's wire format by hand (`tests/test_servicefeed.py` at the repository root checks the
real server's side). The panel's `feeds` and `view` console commands are what is read; a finger is the console's
`touch`, as in `test_caption_view.py`.

What this does not cover is reading it from a few metres at the bench, and a real finger on a row: README items.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import pytest

from conftest import HEIGHT, WIDTH, wait_ready
from fake_relay import FakeRelay
from qemu import Board
from test_caption_view import ANSI, tap
from test_feed_picker import DONE, open_menu, relay, row, tap_and_wait, wait_report  # noqa: F401 - `relay` is a fixture

SERVICE = re.compile(
    r'view: service pane (\d), (known|waiting), (\d+) items, current (\d+), next (\d+), timer "([^"]*)", '
    r'footer "([^"]*)", (following|paused), row at (-?\d+)(, notes open)?'
)

PLAN = {"id": "700", "title": "Sunday Morning", "series_title": "Autumn", "dates": "November 8, 2026"}
ITEMS = [
    {"id": "1", "title": "Countdown", "type": "media", "length": 300, "position": "pre"},
    {"id": "2", "title": "Worship", "type": "header", "length": 0, "position": "during"},
    {"id": "3", "title": "Great Song", "type": "song", "length": 240, "position": "during", "key": "G",
     "arrangement": "Radio", "sequence": ["Verse 1", "Chorus 1"],
     "notes": [{"category": "Vocals", "content": "Ask Sam to lead"}]},
    {"id": "4", "title": "Café Prayer", "type": "item", "length": 120, "position": "during"},
    {"id": "5", "title": "Word", "type": "header", "length": 0, "position": "during"},
    {"id": "6", "title": "Sermon", "type": "item", "length": 1500, "position": "during"},
]


def live(current: str | None, *, left: float = 200.0, over: float = -30.0, reachable: bool = True,
         controller: str | None = "Dana Ortiz") -> dict:
    ids = [i["id"] for i in ITEMS if i["type"] != "header"]
    nxt = ids[ids.index(current) + 1] if current in ids and ids.index(current) + 1 < len(ids) else None
    return {"name": "Sunday AM", "plan": PLAN, "items": ITEMS, "notes": True,
            "current_item": current, "next_item": nxt, "item_left_s": left if current else None,
            "item_length_s": 240, "service_over_s": over if current else None,
            "live": controller is not None, "controller": controller, "reachable": reachable,
            "updated_at": time.time()}


def service(board: Board) -> re.Match:
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line("view")
    return board.uart.wait_for_after(mark, SERVICE.pattern, timeout=10)


def wait_service(board: Board, ok: Callable[[re.Match], bool], what: str, timeout: float = 30.0) -> re.Match:
    deadline = time.monotonic() + timeout
    last = service(board)
    while not ok(last):
        if time.monotonic() > deadline:
            assert board.uart is not None
            recent = [l for l in ANSI.sub("", board.uart.text).splitlines() if re.search(r"view:|feeds:|captions", l)]
            raise AssertionError(f"{what}: the view was {last.group(0)}\n" + "\n".join(recent[-14:]))
        time.sleep(0.5)
        last = service(board)
    return last


@pytest.fixture
def panel(boot: Callable[[str | None], Board], relay: FakeRelay) -> Board:  # noqa: F811
    """A panel from the `nvs` seed, pointed at a fake Relay that follows one service type."""
    relay.service_offers = [{"service_type": "7", "label": "Sunday AM"}]
    relay.set_service("7", live("3"))
    machine = boot("nvs")
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"relay {relay.url}")
    machine.uart.wait_for_after(mark, r"console: relay set")
    relay.wait_checkins(1)
    return machine


def choose_service(board: Board) -> None:
    """Open the menu, pick the Service feed (the last row) and close the menu with Done: the view is built again for
    the new feed and the menu goes up again over it, so it would take the next tap."""
    listed = open_menu(board)
    tap_and_wait(board, row(listed - 1, split=False), r"feeds: pane 1 shows service 7")
    board.uart.wait_for(r"view: service pane 1 up for slot 0", timeout=45)
    tap_and_wait(board, DONE, r"view: menu closed")


def test_offered_only_while_the_service_mode_allows_it(panel: Board, relay: FakeRelay) -> None:
    wait_report(panel, lambda r: r.known, "Relay's offer read")
    assert not any(o.startswith("service:") for o in wait_report(panel, lambda r: r.known, "known").options)
    relay.set_service_mode("show")
    r = wait_report(panel, lambda r: "service:7" in r.options, "the Service feed offered once the mode is show")
    assert r.options[-1] == "service:7"
    relay.set_service_mode("off")
    wait_report(panel, lambda r: "service:7" not in r.options, "and gone again when it is off")


def test_the_service_view(panel: Board, relay: FakeRelay) -> None:
    relay.set_service_mode("show")
    wait_report(panel, lambda r: "service:7" in r.options, "offered")
    choose_service(panel)

    # Now, next, the order of service and the footer.
    m = wait_service(panel, lambda m: m.group(2) == "known", "the state read")
    assert (m.group(3), m.group(4), m.group(5)) == ("6", "3", "4")
    assert re.fullmatch(r"3:[12]\d", m.group(6)), m.group(6)
    assert m.group(7) == "Service 0:30 ahead - Dana Ortiz is controlling"
    assert m.group(8) == "following"
    assert panel.uart is not None
    panel.uart.wait_for(r"view: service pane 1, current item 3 of 6")

    # Next on the controller's laptop: the panel follows.
    relay.set_service("7", live("4", left=3.0, over=0.0))
    m = wait_service(panel, lambda m: m.group(4) == "4", "the current item moved")
    assert m.group(5) == "6"

    # The timer runs over: it reads "+" and the footer says the service is falling behind.
    m = wait_service(panel, lambda m: m.group(6).startswith("+"), "the timer ran over", timeout=40)
    m = wait_service(panel, lambda m: "behind" in m.group(7), "the service falls behind", timeout=60)

    # Planning Center goes away, and comes back.
    relay.set_service("7", live("4", reachable=False))
    wait_service(panel, lambda m: m.group(7) == "Planning Center unreachable", "unreachable")
    relay.set_service("7", live("4"))
    wait_service(panel, lambda m: m.group(7).endswith("Dana Ortiz is controlling"), "back")

    # Nobody controlling is "Not live".
    relay.set_service("7", live(None, controller=None))
    m = wait_service(panel, lambda m: m.group(7) == "Not live", "not live")
    assert m.group(4) == "0"


def held_tap(board: Board, x: int, y: int, hold: float = 1.0) -> None:
    """A finger down at (x, y), held `hold` seconds, then lifted. Held past LVGL's 400 ms long-press time, as a finger
    on the glass often is: a row must open its notes on that too."""
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line(f"touch {WIDTH - 1 - x} {HEIGHT - 1 - y}")
    board.uart.wait_for_after(mark, r"pointer down at", timeout=30)
    time.sleep(hold)
    board.uart.send_line("touch up")
    board.uart.wait_for_after(mark, r"pointer up at", timeout=30)


def test_notes_open_on_a_tap(panel: Board, relay: FakeRelay) -> None:
    relay.set_service_mode("show")
    wait_report(panel, lambda r: "service:7" in r.options, "offered")
    choose_service(panel)
    m = wait_service(panel, lambda m: m.group(2) == "known" and int(m.group(9)) > 0, "the current row on the glass")
    y = int(m.group(9))
    assert 172 < y < 440, y
    assert panel.uart is not None
    mark = panel.uart.mark()
    held_tap(panel, 300, y)
    panel.uart.wait_for_after(mark, r"view: service notes open for item 3", timeout=30)
    mark = panel.uart.mark()
    tap(panel, 400, 20)
    panel.uart.wait_for_after(mark, r"view: service notes closed", timeout=30)


def test_a_service_relay_stops_following_falls_back(panel: Board, relay: FakeRelay) -> None:
    relay.set_service_mode("show")
    wait_report(panel, lambda r: "service:7" in r.options, "offered")
    choose_service(panel)
    relay.offer_service([])
    r = wait_report(panel, lambda r: r.shows[0] == "transcription (fallback)", "the pane falls back to the transcription")
    assert r.saved[0] == "service 7"
    relay.offer_service([{"service_type": "7", "label": "Sunday AM"}])
    wait_report(panel, lambda r: r.shows[0] == "service 7", "and returns to the service")
