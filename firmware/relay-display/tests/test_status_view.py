"""The Status feed on the panel (#224), against a fake Relay: offered in the menu only while Relay offers it, set by the
operator like any feed, the band and rows up once Relay answers, **a still status costing no announcement** (Relay
repeats the state every heartbeat, and the panel says it changed only when something shown did), and an older Relay
that has no status feed saying so on the pane.

The fake copies app/streamstatus.py's wire format by hand (`tests/test_streamstatus.py` at the repository root checks
the real server's side). The parsing, the words and the counting are `test_native.cpp`'s (`check_status`). How the
band and rows read from across a room is a bench item (README).
"""

from __future__ import annotations

import time
from collections.abc import Callable

import pytest

from conftest import wait_ready
from fake_relay import FakeRelay
from qemu import Board
from test_feed_picker import relay, wait_report  # noqa: F401 - `relay` is a fixture
from test_propresenter_view import count, wait_request, wait_view

ON_AIR = {
    "running": True, "started_at": time.time() - 2712, "input": {"state": "ok", "dbfs": -18},
    "languages": ["Spanish", "French"], "viewers": 37, "on_air": True,
    "streams": [{"source": "obs", "label": "OBS Studio", "on_air": True, "health": "ok",
                 "live_since": time.time() - 1520, "viewers": None}],
}


@pytest.fixture
def panel(boot: Callable[[str | None], Board], relay: FakeRelay) -> Board:  # noqa: F811
    relay.status_offered = True
    machine = boot("nvs")
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"relay {relay.url}")
    machine.uart.wait_for_after(mark, r"console: relay set")
    relay.wait_checkins(1)
    return machine


def test_offered_only_while_relay_offers_it(panel: Board, relay: FakeRelay) -> None:
    r = wait_report(panel, lambda r: "status" in r.options, "status offered")
    assert r.options.index("status") < r.options.index("alert")
    relay.offer_status(False)
    wait_report(panel, lambda r: "status" not in r.options, "gone when an older Relay does not offer it")


def test_the_status_view(panel: Board, relay: FakeRelay) -> None:
    relay.set_status(ON_AIR)
    relay.set_feed([{"stream": "status"}])
    wait_report(panel, lambda r: r.shows[0] == "status", "pane 1 shows the status")
    wait_request(relay, lambda q: q.get("stream") == "status", "the status stream asked for")
    wait_view(panel, r'view: report: pane 1 status, air "ON AIR\s+\d+:\d\d", 1 streams', "on air with its time")

    # **A still status is not announced again**: the fake repeats it every second.
    time.sleep(4)
    assert count(panel, r"captions\[0\]: status changed") == 1

    # Off air is a change; so is the stream going away.
    relay.set_status({**ON_AIR, "on_air": False, "streams": [{**ON_AIR["streams"][0], "on_air": False}]})
    wait_view(panel, r'view: report: pane 1 status, air "OFF AIR", 1 streams', "off air")
    relay.set_status({**ON_AIR, "on_air": None, "streams": []})
    wait_view(panel, r'view: report: pane 1 status, air "NOT WATCHED", 0 streams', "no stream module")


def test_status_beside_captions(panel: Board, relay: FakeRelay) -> None:
    relay.set_status(ON_AIR)
    relay.set_feed([{"stream": "transcription"}, {"stream": "status"}])
    wait_report(panel, lambda r: r.split and r.shows[1] == "status", "pane 2 shows the status")
    wait_view(panel, r'view: report: pane 2 status, air "ON AIR', "pane 2 on air")


def test_an_older_relay_says_so(panel: Board, relay: FakeRelay) -> None:
    relay.status_offered = False
    relay.set_feed([{"stream": "status"}])
    wait_report(panel, lambda r: r.shows[0] == "status", "pane 1 stays on the status")
    assert panel.uart is not None
    panel.uart.wait_for(r"captions\[0\]: Relay answered 400", timeout=30)
    wait_view(panel, r"view: report: pane 1 status, idle: This Relay has no status view", "says to update Relay")
