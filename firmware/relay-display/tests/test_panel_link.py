"""The panel's link to Relay's registry, against a fake Relay: it checks in with what Relay lists, keeps its event
stream open, applies a `set_feed` that arrives on the stream or in a check-in reply, tells Relay which revision it
applied so it is not sent again, and checks in again when Relay forgets it.

`FakeRelay` has Relay's wire format and the rule for when a `set_feed` is sent (`app/panels.py`), copied by hand.
`tests/test_panels.py` at the repository root checks the real server's side, so between them the two ends are covered
and neither is the other's evidence.

The seed `nvs` has a Relay address (the emulator's host, port 8000) that nothing answers, and `relay <url>` points the
panel at the fake. Check-ins are every 30 s, so a test that needs a second one waits on the stream's 404 (which asks for
one at once) rather than the clock.

What this does not cover is Wi-Fi: the signal strength is absent here (the emulator has no radio), and a panel on a
real network is the bench item in README.md.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable, Iterator

import pytest

from conftest import wait_ready
from fake_relay import FakeRelay
from qemu import Board
from seeds import PANEL_ID
from test_feed_picker import wait_report


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


def log_has(board: Board, pattern: str) -> bool:
    assert board.uart is not None
    return re.search(pattern, board.uart.text) is not None


def wait_log(board: Board, pattern: str, timeout: float = 60.0) -> None:
    assert board.uart is not None
    board.uart.wait_for(pattern, timeout=timeout)


def test_checks_in_with_what_relay_lists(panel: Board, relay: FakeRelay) -> None:
    first = relay.wait_checkins(1)[0]
    assert first["id"] == PANEL_ID
    assert first["name"] == f"Panel {PANEL_ID[:4]}"
    assert first["version"] == "0.1.0"
    assert first["scripts"] == ["latin"]
    assert first["feeds"] == [{"stream": "translation", "lang": "SPANISH"}]
    assert isinstance(first["uptime_s"], int) and first["uptime_s"] >= 0
    assert first["feed_rev"] == 0
    # The emulator's wired link has an address, and no radio, so no signal strength.
    assert re.fullmatch(r"\d+\.\d+\.\d+\.\d+", first.get("ip", "")), first
    assert "rssi" not in first
    wait_log(panel, r"panel_link: checked in with Relay")
    assert len(first) <= 12  # nothing the registry has no field for


def test_the_event_stream_opens_after_the_check_in(panel: Board, relay: FakeRelay) -> None:
    relay.wait_panel_streams(1)
    wait_log(panel, r"panel_link: event stream open")
    assert relay.panel_streams[0][0] == PANEL_ID


def test_a_feed_pushed_on_the_stream_is_applied(panel: Board, relay: FakeRelay) -> None:
    relay.wait_panel_streams(1)
    rev = relay.set_feed([{"stream": "translation", "lang": "FRENCH"}])
    wait_log(panel, rf"panel_link: set_feed rev {rev} applied \(1 feed, stream\)")
    got = wait_report(panel, lambda r: r.saved[0] == "translation FRENCH" and not r.split, "pane 1 follows Relay")
    assert got.shows[0] == "translation FRENCH"


def test_two_feeds_make_a_split(panel: Board, relay: FakeRelay) -> None:
    relay.wait_panel_streams(1)
    rev = relay.set_feed([{"stream": "transcription"}, {"stream": "translation", "lang": "FRENCH"}])
    wait_log(panel, rf"panel_link: set_feed rev {rev} applied \(2 feeds, stream\)")
    got = wait_report(panel, lambda r: r.split, "the panel splits")
    assert got.saved == ("transcription", "translation FRENCH")


def test_a_one_feed_setting_turns_the_split_off(panel: Board, relay: FakeRelay) -> None:
    relay.wait_panel_streams(1)
    relay.set_feed([{"stream": "transcription"}, {"stream": "translation", "lang": "FRENCH"}])
    wait_report(panel, lambda r: r.split, "the panel splits")
    relay.set_feed([{"stream": "translation", "lang": "SPANISH"}])
    got = wait_report(panel, lambda r: not r.split, "the panel is one feed again")
    assert got.saved[0] == "translation SPANISH"


def test_a_feed_set_while_the_panel_was_away_arrives_with_the_stream(
    boot: Callable[[str | None], Board], relay: FakeRelay
) -> None:
    # Relay has an operator feed before the panel has ever checked in: the check-in reply carries it.
    rev = relay.set_feed([{"stream": "transcription"}], push=False)
    machine = boot("nvs")
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"relay {relay.url}")
    machine.uart.wait_for_after(mark, r"console: relay set")
    wait_log(machine, rf"panel_link: set_feed rev {rev} applied \(1 feed, check-in\)")
    wait_report(machine, lambda r: r.saved[0] == "transcription", "the check-in's feed is applied")


def test_the_next_check_in_says_which_revision_it_applied(panel: Board, relay: FakeRelay) -> None:
    relay.wait_panel_streams(1)
    rev = relay.set_feed([{"stream": "translation", "lang": "FRENCH"}])
    wait_log(panel, rf"panel_link: set_feed rev {rev} applied")
    # A Relay restart makes the panel check in at once (the stream's 404), and that body carries the revision.
    before = len(relay.checkins)
    relay.forget_panels()
    relay.wait_checkins(before + 1)
    assert relay.checkins[-1]["feed_rev"] == rev
    assert relay.checkins[-1]["feeds"] == [{"stream": "translation", "lang": "FRENCH"}]
    # …so Relay does not send the same setting again, and the screen's own choice would stand.
    relay.wait_panel_streams(1)
    time.sleep(3)
    assert len(re.findall(rf"panel_link: set_feed rev {rev} applied", panel.uart.text)) == 1  # type: ignore[union-attr]


def test_relay_forgetting_the_panel_is_cured_by_a_check_in(panel: Board, relay: FakeRelay) -> None:
    relay.wait_panel_streams(1)
    opens = relay.panel_stream_opens
    before = len(relay.checkins)
    relay.forget_panels()
    # The stream is cut, comes back, is told 404, asks for a check-in, and opens again: well inside one 30 s interval.
    relay.wait_checkins(before + 1, timeout=25)
    relay.wait_panel_streams(1)
    assert relay.panel_stream_opens > opens


def test_an_update_notice_without_what_to_fetch_is_not_acted_on(panel: Board, relay: FakeRelay) -> None:
    """A notice with no size, checksum or path is not an offer: it is logged and nothing is fetched. The update
    itself is `test_update.py`."""
    relay.wait_panel_streams(1)
    relay.push({"type": "update_available", "version": "0.2.0"})
    wait_log(panel, r"panel_link: update available \(not one this panel reads\)")
    assert relay.image_requests == 0


def test_a_refused_check_in_is_retried_and_the_stream_waits(
    boot: Callable[[str | None], Board], relay: FakeRelay
) -> None:
    relay.checkin_status = 429  # Relay's registry is full
    machine = boot("nvs")
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"relay {relay.url}")
    machine.uart.wait_for_after(mark, r"console: relay set")
    wait_log(machine, r"panel_link: Relay answered the check-in with 429")
    assert relay.panel_stream_opens == 0  # the stream only opens for a panel Relay has accepted
    relay.checkin_status = 200
    relay.wait_panel_streams(1, timeout=60)


def test_panel_report_line(panel: Board, relay: FakeRelay) -> None:
    relay.wait_panel_streams(1)
    assert panel.uart is not None
    mark = panel.uart.mark()
    panel.uart.send_line("panel")
    m = panel.uart.wait_for_after(mark, r"panel_link: report: running, (\d+) check-ins, stream (open|closed), rev (\d+)")
    assert int(m.group(1)) >= 1 and m.group(2) == "open"
