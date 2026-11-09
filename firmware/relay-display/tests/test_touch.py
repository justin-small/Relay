"""Touches reach LVGL's pointer at the right place for both orientations.

The emulator has no GT911, so the firmware takes a finger from the console (`touch <x> <y>`,
`touch up`) in the controller's own coordinates and runs it through the same rotation as a real
report. LVGL's pointer logs each press and release with the position it was given, which is what
is read here. The 180 degree seed is `nvs`, the 0 degree seed is `nvs-upright` (`test_screen.py`).
"""

from __future__ import annotations

from conftest import wait_ready
from qemu import Board

WIDTH = 800
HEIGHT = 480


def _down_at(machine: Board, x: int, y: int) -> tuple[int, int]:
    assert machine.uart is not None
    mark = machine.uart.mark()
    machine.uart.send_line(f"touch {x} {y}")
    match = machine.uart.wait_for_after(mark, r"pointer down at \((\d+), (\d+)\)")
    machine.uart.send_line("touch up")
    machine.uart.wait_for_after(mark, r"pointer up at")
    return int(match.group(1)), int(match.group(2))


def test_the_pointer_starts_with_the_screen(provisioned: Board) -> None:
    wait_ready(provisioned)
    assert provisioned.uart is not None
    provisioned.uart.wait_for(r"touch: emulator input, rotation 180")


def test_taps_land_where_they_were_made_when_upright(boot) -> None:
    machine = boot("nvs-upright")
    wait_ready(machine)
    for x, y in ((0, 0), (799, 479), (123, 321), (400, 240)):
        assert _down_at(machine, x, y) == (x, y)


def test_taps_are_mirrored_when_the_panel_is_turned(provisioned: Board) -> None:
    wait_ready(provisioned)
    for x, y in ((0, 0), (799, 479), (123, 321), (400, 240)):
        assert _down_at(provisioned, x, y) == (WIDTH - 1 - x, HEIGHT - 1 - y)


def test_a_touch_past_the_edge_is_held_on_the_screen(provisioned: Board) -> None:
    wait_ready(provisioned)
    assert _down_at(provisioned, 5000, 5000) == (0, 0)
