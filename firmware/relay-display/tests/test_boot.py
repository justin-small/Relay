"""The image boots, reads its configuration and decides about Wi-Fi.

Asserted on the strings the firmware prints, never on line numbers: the boot log is what an installer
reads, and it is what the emulator can see.

`provisioned` is one board for the whole session, seeded with `nvs`. Tests here that need a different
seed take their own board from `boot`.

Adapted from the VXP compact display's `tests/firmware_compact/test_boot.py`.
"""

from __future__ import annotations

import time

from conftest import BOOT_LINE, BOOT_TIMEOUT, wait_ready
from qemu import Board


def test_boots_to_ready_with_the_version_and_the_board(provisioned: Board) -> None:
    """The version and board name are the ones `main/config.h` gives, and `ready` is the last thing the
    boot says.

    **The PSRAM line reports 0, and that is the emulator, not the firmware.** `qemu-system-xtensa` models
    no octal PSRAM chip, so ESP-IDF finds none and carries on (`CONFIG_SPIRAM_IGNORE_NOTFOUND` in
    `sdkconfig.qemu`). The shipped board reports 8388608; that is the first bench check in README.md.
    """
    uart = provisioned.uart
    assert uart is not None
    uart.wait_for(BOOT_LINE, timeout=BOOT_TIMEOUT)
    uart.wait_for(r"boot: psram 0 bytes", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"boot: ready", timeout=BOOT_TIMEOUT)


def test_the_backlight_is_on_before_the_boot_is_ready(provisioned: Board) -> None:
    """The panel is lit by the time the boot says so. The emulator has no backlight current, so this is
    the order the firmware does things in, not how bright the panel is."""
    uart = provisioned.uart
    assert uart is not None
    wait_ready(provisioned)
    lit = uart.text.index("panel: test screen shown, backlight on")
    ready = uart.text.index("boot: ready")
    assert lit < ready


def test_no_fault_is_printed(provisioned: Board) -> None:
    """No panic, abort, assertion or guru meditation anywhere in the transcript."""
    wait_ready(provisioned)
    text = provisioned.uart.text if provisioned.uart else ""
    for fault in (
        r"Guru Meditation",
        r"abort\(\) was called",
        r"assert failed",
        r"Backtrace:",
        r"panic",
    ):
        assert fault not in text, f"the firmware printed {fault!r}:\n{text[-2000:]}"


def test_lvgl_starts_before_the_network(provisioned: Board) -> None:
    """The screen is up before the network decides anything, so it can show the setup network as it opens."""
    uart = provisioned.uart
    assert uart is not None
    wait_ready(provisioned)
    up = uart.text.index("screen: lvgl: up")
    decided = uart.text.index("Wi-Fi skipped under emulation")
    assert up < decided


def test_no_reboot_loop_over_ten_seconds(provisioned: Board) -> None:
    """Once ready, the board stays up: the boot line is printed exactly once.

    A reboot runs the boot again and prints the boot line again, so the count is the detector. A fixed
    wait of ten seconds, not a poll: a boot loop reboots within a second or two, so ten seconds is a
    window a loop cannot hide in.
    """
    wait_ready(provisioned)
    uart = provisioned.uart
    assert uart is not None
    time.sleep(10)
    assert uart.count(BOOT_LINE) == 1


def test_wifi_is_skipped_under_emulation_and_the_decision_is_logged(provisioned: Board) -> None:
    """The radio is not started, the fixed line says so, and the decision the board would make is logged.
    With a network configured that decision is to start. The emulator has no radio model, so an unskipped
    start would hang the boot rather than fail."""
    uart = provisioned.uart
    assert uart is not None
    wait_ready(provisioned)
    uart.wait_for(r"Wi-Fi skipped under emulation \(QEMU has no radio\)", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"would start Wi-Fi for SSID 'Relay-QEMU-Net'", timeout=BOOT_TIMEOUT)
    assert "would open the setup network" not in uart.text
    assert "net: station for SSID" not in uart.text
    assert "got address" not in uart.text


def test_an_unprovisioned_board_opens_the_setup_network_and_skips_the_radio(boot) -> None:
    """With no network configured the station does not start, and the setup network is the decision
    instead. The skip is printed, so the two cases are distinguishable in the log."""
    machine = boot("nvs-unprovisioned")
    wait_ready(machine)
    uart = machine.uart
    assert uart is not None
    uart.wait_for(r"net: would open the setup network if the radio were present", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"Wi-Fi skipped under emulation", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"provision: setup page skipped under emulation", timeout=BOOT_TIMEOUT)
    assert "would start Wi-Fi" not in uart.text


def test_nothing_from_vxp_is_left_in_the_boot(provisioned: Board) -> None:
    """The VXP display's pairing, content stream, update and clock are not in this firmware, so none of
    their log lines can appear."""
    wait_ready(provisioned)
    text = provisioned.uart.text if provisioned.uart else ""
    for gone in ("pairing", "rtc:", "content", "firmware_update", "VXP", "vxp"):
        assert gone not in text, f"{gone!r} is in the boot log:\n{text[-1500:]}"
