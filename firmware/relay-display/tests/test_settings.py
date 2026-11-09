"""The stored configuration, read back from NVS and applied or refused; the panel ID.

Each case is a seed from `seeds.py`, written into the `nvs` partition behind the merged image, so the
firmware reads it with the code the board runs. The expected warnings are the ones `main/settings.cpp`
prints; the test asserts on them and on what the boot then does with the defaults.

**The configured orientation is 180 in this build** (`sdkconfig.qemu`), so a fallback to the configured
value and a fallback to 0 are different pictures and different log lines.

Adapted from the VXP compact display's `tests/firmware_compact/test_settings.py`.
"""

from __future__ import annotations

import re

from conftest import BOOT_TIMEOUT, wait_ready
from qemu import Board


def test_the_stored_configuration_is_read_back(provisioned: Board) -> None:
    """The provisioned seed holds a network, a Relay address and a panel ID, and the boot reports all
    three as set. The values themselves are not logged, by design: the password never is, and the others
    are the installation's to know."""
    uart = provisioned.uart
    assert uart is not None
    uart.wait_for(
        r"settings: loaded \(ssid set, relay address set, panel id set, address DHCP\)",
        timeout=BOOT_TIMEOUT,
    )
    assert "panel id made" not in uart.text  # it had one


def test_the_stored_rotation_of_180_is_applied(provisioned: Board) -> None:
    uart = provisioned.uart
    assert uart is not None
    uart.wait_for(r"panel: QEMU virtual panel 800x480 RGB565, rotation 180", timeout=BOOT_TIMEOUT)


def test_an_unprovisioned_blob_loads_with_nothing_set_and_makes_a_panel_id(boot) -> None:
    """A blob with the right magic and schema and empty fields is accepted, not a defaults fallback. It
    has no panel ID, so the boot makes one and saves it, and the rotation is the one it stores, 0."""
    machine = boot("nvs-unprovisioned")
    wait_ready(machine)
    uart = machine.uart
    assert uart is not None
    uart.wait_for(
        r"settings: loaded \(ssid unset, relay address unset, panel id unset, address DHCP\)",
        timeout=BOOT_TIMEOUT,
    )
    uart.wait_for(r"settings: panel id made", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"panel: QEMU virtual panel 800x480 RGB565, rotation 0", timeout=BOOT_TIMEOUT)


def test_a_configured_panel_without_an_id_gets_one_and_keeps_its_network(boot) -> None:
    machine = boot("nvs-no-id")
    wait_ready(machine)
    uart = machine.uart
    assert uart is not None
    uart.wait_for(r"settings: panel id made", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"would start Wi-Fi for SSID 'Relay-QEMU-Net'", timeout=BOOT_TIMEOUT)


def _panel_id(machine: Board) -> str:
    """Ask the serial console for the panel ID, and read it from the answer."""
    uart = machine.uart
    assert uart is not None
    mark = uart.mark()
    uart.send_line("id")
    found = uart.wait_for_after(mark, r"console: panel id ([0-9a-f]{32})", timeout=BOOT_TIMEOUT)
    return found.group(1)


def test_the_panel_id_is_made_once_and_survives_a_reset(boot) -> None:
    """The first boot of a blank panel makes an ID; a reset finds the same one in NVS and makes nothing."""
    machine = boot("nvs-unprovisioned")
    wait_ready(machine)
    first = _panel_id(machine)
    assert re.fullmatch(r"[0-9a-f]{32}", first)
    assert machine.uart is not None and machine.uart.count(r"settings: panel id made") == 1

    machine.reset()
    uart = machine.uart
    assert uart is not None
    mark = uart.mark()
    uart.wait_for_after(mark, r"boot: ready", timeout=BOOT_TIMEOUT)
    assert uart.count(r"settings: panel id made", after=mark) == 0
    uart.wait_for_after(mark, r"panel id set", timeout=BOOT_TIMEOUT)
    assert _panel_id(machine) == first


def test_two_blank_panels_get_different_ids(boot) -> None:
    """The ID is random per panel, not derived from anything the two share."""
    a, b = boot("nvs-unprovisioned"), boot("nvs-unprovisioned")
    wait_ready(a)
    wait_ready(b)
    assert _panel_id(a) != _panel_id(b)


def test_a_stored_panel_id_is_the_one_the_console_reports(provisioned: Board) -> None:
    assert _panel_id(provisioned) == "0123456789abcdef0123456789abcdef"


def test_a_wrong_magic_falls_back_to_the_defaults(boot) -> None:
    """A blob that does not start with this firmware's magic is not read. The defaults stand: no network
    (so no Wi-Fi start is decided) and the configured orientation, 180. And with defaults there is no
    panel ID, so one is made."""
    machine = boot("nvs-bad-magic")
    wait_ready(machine)
    uart = machine.uart
    assert uart is not None
    uart.wait_for(
        r"settings: stored blob is schema \d+, this firmware reads 2; using defaults",
        timeout=BOOT_TIMEOUT,
    )
    uart.wait_for(r"panel: QEMU virtual panel 800x480 RGB565, rotation 180", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"net: would open the setup network if the radio were present", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"settings: panel id made", timeout=BOOT_TIMEOUT)


def test_a_wrong_schema_falls_back_to_the_defaults(boot) -> None:
    """A blob from a layout this firmware does not read is replaced with defaults, not read as the wrong
    fields. The seed is schema 3, one past this firmware's 2, and holds a valid-looking network, and the
    boot does not start it."""
    machine = boot("nvs-bad-schema")
    wait_ready(machine)
    uart = machine.uart
    assert uart is not None
    uart.wait_for(
        r"settings: stored blob is schema 3, this firmware reads 2; using defaults",
        timeout=BOOT_TIMEOUT,
    )
    uart.wait_for(r"panel: QEMU virtual panel 800x480 RGB565, rotation 180", timeout=BOOT_TIMEOUT)
    assert "would start Wi-Fi" not in uart.text


def test_a_stored_rotation_that_is_neither_0_nor_180_falls_back_to_the_configured_one(boot) -> None:
    """The stored rotation is 7. It falls back to the orientation this build was configured for, which
    `sdkconfig.qemu` makes 180. A fallback to a fixed 0 would print `rotation 0` here and fail. The rest
    of the blob is kept: the network it names is still decided on."""
    machine = boot("nvs-bad-rotation")
    wait_ready(machine)
    uart = machine.uart
    assert uart is not None
    uart.wait_for(
        r"settings: stored rotation is not 0 or 180; using the configured 180", timeout=BOOT_TIMEOUT
    )
    uart.wait_for(r"panel: QEMU virtual panel 800x480 RGB565, rotation 180", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"would start Wi-Fi for SSID 'Relay-QEMU-Net'", timeout=BOOT_TIMEOUT)


def test_an_erased_nvs_partition_boots_with_the_defaults(boot) -> None:
    """A board that has never been configured: the partition is erased (0xFF), no entry is found, and
    the boot uses the defaults, makes a panel ID, and comes up."""
    machine = boot(None)
    wait_ready(machine)
    uart = machine.uart
    assert uart is not None
    uart.wait_for(r"settings: none stored, using defaults", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"settings: panel id made", timeout=BOOT_TIMEOUT)
    uart.wait_for(r"panel: QEMU virtual panel 800x480 RGB565, rotation 180", timeout=BOOT_TIMEOUT)
