"""The LIVE buttons on the Service feed (#83), against a fake Relay: Next, Previous, Take control (with its
confirmation) and Release; a press Relay refuses reads in words; a double tap is one press; and a panel whose mode does
not include control has no buttons.

The fake copies `POST /api/panels/<id>/service/<action>` from app/pccontrol.py by hand (`tests/test_pccontrol.py` at
the repository root checks the real one). Where the buttons are is read from the panel's `view` report.

What this does not cover is a thumb on the glass: the bench items in README.md.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable

import pytest

from conftest import wait_ready
from fake_relay import FakeRelay
from qemu import Board
from test_caption_view import tap
from test_feed_picker import relay, wait_report  # noqa: F401 - `relay` is a fixture
from test_service_view import choose_service, live, wait_service

KEY = "0123456789abcdef0123456789abcdef"
BUTTONS = re.compile(
    r'view: service pane 1 buttons: previous (\d+) (\d+), next (\d+) (\d+), (Take control|Take|Release) (\d+) (\d+), '
    r'message "([^"]*)"(, confirm up)?'
)
# The confirmation card: 600 x 220, centred; "Take control" at its bottom left.
CONFIRM_YES = (255, 290)
ORDER = ["1", "3", "4", "6"]  # the items Next steps through (headers are skipped)


def buttons(board: Board) -> re.Match:
    assert board.uart is not None
    mark = board.uart.mark()
    board.uart.send_line("view")
    return board.uart.wait_for_after(mark, BUTTONS.pattern, timeout=10)


def wait_buttons(board: Board, ok: Callable[[re.Match], bool], what: str, timeout: float = 30.0) -> re.Match:
    deadline = time.monotonic() + timeout
    last = buttons(board)
    while not ok(last):
        assert time.monotonic() < deadline, f"{what}: the buttons were {last.group(0)}"
        time.sleep(0.5)
        last = buttons(board)
    return last


class Planning:
    """The plan as Planning Center would run it, answering the fake Relay's presses as app/pccontrol.py does."""

    def __init__(self, relay: FakeRelay) -> None:
        self.relay = relay
        self.current = "3"
        self.controller = "Dana Ortiz"
        self.mine = False

    def publish(self) -> None:
        state = live(self.current, controller=self.controller)
        state["relay_controls"] = self.mine
        self.relay.set_service("7", state)

    def answer(self, action: str, body: dict) -> tuple[int, dict]:
        if action == "take_control":
            self.controller, self.mine = "Relay Owner", True
        elif action == "release":
            self.controller, self.mine = None, False
        elif not self.mine:
            return 409, {"ok": False, "error": "not_controlling"}
        else:
            i = ORDER.index(self.current)
            self.current = ORDER[min(i + 1, len(ORDER) - 1)] if action == "next" else ORDER[max(i - 1, 0)]
        self.publish()
        return 200, {"ok": True}


@pytest.fixture
def panel(boot: Callable[[str | None], Board], relay: FakeRelay) -> Board:  # noqa: F811
    relay.service_offers = [{"service_type": "7", "label": "Sunday AM"}]
    relay.set_service("7", live("3"))
    relay.set_service_mode("both", key=KEY)
    machine = boot("nvs")
    wait_ready(machine)
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"relay {relay.url}")
    machine.uart.wait_for_after(mark, r"console: relay set")
    relay.wait_checkins(1)
    wait_report(machine, lambda r: "service:7" in r.options, "offered")
    choose_service(machine)
    machine.uart.wait_for(r"view: service pane 1 up for slot 0, with controls", timeout=45)
    return machine


def press(board: Board, at: tuple[int, int]) -> None:
    tap(board, *at)


def test_next_previous_take_control_and_release(panel: Board, relay: FakeRelay) -> None:
    plan = Planning(relay)
    relay.on_action = plan.answer
    plan.publish()
    b = wait_buttons(panel, lambda b: b.group(5) == "Take control", "ready")

    # Next before Relay has control: refused, in words.
    press(panel, (int(b.group(3)), int(b.group(4))))
    wait_buttons(panel, lambda b: b.group(8) == "Take control first", "refused until control is taken")
    assert relay.actions[-1][0] == "next"
    assert relay.actions[-1][1] == {"key": KEY, "type": "7"}

    # Take control asks first, since someone else has it.
    press(panel, (int(b.group(6)), int(b.group(7))))
    wait_buttons(panel, lambda b: b.group(9) is not None, "the confirmation is up")
    assert panel.uart is not None
    panel.uart.wait_for(r'view: confirm "Take control from Dana Ortiz\?"')
    count = len(relay.actions)
    press(panel, CONFIRM_YES)
    b = wait_buttons(panel, lambda b: b.group(5) == "Release" and b.group(9) is None, "Relay has control")
    assert relay.actions[count][0] == "take_control"
    wait_service(panel, lambda m: m.group(7).endswith("Relay Owner is controlling"), "the footer says so")

    # Next and Previous move the service; the panel follows when Relay confirms.
    press(panel, (int(b.group(3)), int(b.group(4))))
    wait_service(panel, lambda m: m.group(4) == "4", "next")
    press(panel, (int(b.group(1)), int(b.group(2))))
    wait_service(panel, lambda m: m.group(4) == "3", "previous")

    # Release hands control back.
    press(panel, (int(b.group(6)), int(b.group(7))))
    wait_buttons(panel, lambda b: b.group(5) == "Take control", "released")
    assert relay.actions[-1][0] == "release"


def test_a_double_tap_is_one_press(panel: Board, relay: FakeRelay) -> None:
    plan = Planning(relay)
    plan.mine, plan.controller = True, "Relay Owner"
    relay.on_action = plan.answer
    plan.publish()
    b = wait_buttons(panel, lambda b: b.group(5) == "Release", "ready")
    relay.action_delay = 4.0
    count = len(relay.actions)
    at = (int(b.group(3)), int(b.group(4)))
    press(panel, at)
    press(panel, at)
    assert panel.uart is not None
    panel.uart.wait_for(r"control: next ignored, the last press is still waiting", timeout=30)
    wait_service(panel, lambda m: m.group(4) == "4", "one step")
    time.sleep(2)
    assert [a for a, _ in relay.actions[count:]] == ["next"]


def test_refused_without_the_key_and_no_buttons_without_control(panel: Board, relay: FakeRelay) -> None:
    b = wait_buttons(panel, lambda b: True, "ready")
    # Relay no longer knows this key (a key made again in admin that the panel has not heard of yet).
    relay.service_mode["key"] = "f" * 32
    press(panel, (int(b.group(3)), int(b.group(4))))
    wait_buttons(panel, lambda b: b.group(8) == "Not allowed", "refused")
    # The operator turns control off: the buttons go.
    assert panel.uart is not None
    mark = panel.uart.mark()
    relay.set_service_mode("show")
    panel.uart.wait_for_after(mark, r"view: service pane 1 up for slot 0$|view: service pane 1 up for slot 0\r?\n",
                              timeout=45)
    m = wait_service(panel, lambda m: m.group(2) == "known", "the view without buttons")
    assert m.group(3) == "6"
