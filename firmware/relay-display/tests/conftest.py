"""Fixtures, lifecycle and the screen reader for the Relay panel's emulator suite.

Run it with one command, from the repository root, after `firmware/relay-display/build.sh --qemu`:

    RELAY_QEMU=<espressif qemu-system-xtensa> pytest firmware/relay-display/tests

**It is skipped, not failed, without a QEMU and a built image**, and `--firmware` turns the skip
into a failure. CI passes it.

## What a green run here proves, and what it does not

The emulator runs the panel's real image: the boot, the stored configuration read from NVS, the
settings decode, the panel ID made on first boot, the panel driver calling the virtual RGB panel,
the strips, the software turn of the picture, and the Wi-Fi decision.

It does not reach what `qemu-system-xtensa` has no model of, and each is a bench item in README.md:

* **the real RGB interface's timing.** The virtual panel takes a copy of each strip. It has no pixel
  clock, so the 16 MHz choice, the porches and the sync polarity are not exercised. The picture a test
  reads back is the framebuffer's, not the glass's.
* **the backlight.** The STC8 on the I2C bus is not modelled, so the backlight calls do nothing here
  and the test can see only the log line.
* **Wi-Fi.** `esp_wifi_start` never returns under this emulator, so the radio is not started. The
  decision is logged, and the log says the radio was skipped. No association, DHCP, access point or
  setup page is tested.
* **PSRAM timing**, if the module's PSRAM is modelled at all; `test_boot.py` says what the boot line
  reports.
* **the touch panel.** There is no GT911 in the emulator, so touches are injected from the console (`touch <x> <y>`, `touch up`) in the controller's own coordinates. The rotation and LVGL's pointer are tested (`test_touch.py`); the chip, its address and its I2C traffic are bench items.

## The framebuffer

`screendump` over QMP writes the console the virtual panel draws to, as a PPM. It is read headlessly.
A colour is compared after truncating its 8-bit channels back to RGB565, so it does not depend on how
the emulator expands a 5-bit channel to 8 bits.

Adapted from the VXP compact display's `tests/firmware_compact/conftest.py`.
"""

from __future__ import annotations

import contextlib
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

import qemu
from qemu import Board
from seeds import SEEDS
from staleness import stale_reason

PROJECT = Path(__file__).resolve().parents[1]
IMAGE_NAME = "relay-display.bin"

# The module's flash: 16 MB (`sdkconfig.defaults`, `partitions.csv`). The emulated flash has to be the
# same size or the partition table is not the one that ships.
FLASH_BYTES = 16 * 1024 * 1024
# `nvs` in `partitions.csv`: above the two application slots, so a merged image cannot erase it, and
# the harness writes the seed there behind the merged file.
NVS_OFFSET = 0x3D0000
NVS_BYTES = 0x8000

# The panel's size, as `main/config.h` gives it.
WIDTH = 800
HEIGHT = 480

# What a wait costs when the caller does not say. The boot is a few seconds on a laptop and the timeout
# is generous for the same reason `qemu.py` gives its own.
BOOT_TIMEOUT = 90.0

# Everything whose change produces a different test image. `build.sh --qemu` writes the image and the
# seeds, so a source edited after the build makes both stale.
IMAGE_SOURCES = (
    "main",
    "components",
    "CMakeLists.txt",
    "partitions.csv",
    "sdkconfig.defaults",
    "sdkconfig.qemu",
    "build.sh",
    "tests/seeds.py",
)


def pytest_addoption(parser: pytest.Parser) -> None:
    with contextlib.suppress(ValueError):
        parser.addoption(
            "--firmware",
            action="store_true",
            default=False,
            help=(
                "fail rather than skip when QEMU or a built firmware image is missing. "
                "The switch CI passes when it means to actually run this suite."
            ),
        )


def build_dir() -> Path:
    """Where `build.sh --qemu` left its output. `RELAY_FW_BUILD` overrides it."""
    override = os.environ.get("RELAY_FW_BUILD")
    if override:
        return Path(override)
    return PROJECT / "build-qemu"


def _stale(out: Path) -> str | None:
    """The image is older than the firmware it is supposed to be, or None (see `staleness.py`)."""
    return stale_reason(
        out / IMAGE_NAME,
        PROJECT,
        IMAGE_SOURCES,
        rebuild="firmware/relay-display/build.sh --qemu",
        override="RELAY_FW_BUILD",
    )


def _unavailable() -> str | None:
    """Why this suite cannot run here, in one sentence, or None."""
    try:
        qemu.find_binary()
    except FileNotFoundError as error:
        return str(error)
    out = build_dir()
    names = [IMAGE_NAME, "sdkconfig", *(f"{seed}.bin" for seed in SEEDS)]
    for name in names:
        if not (out / name).exists():
            return (
                f"{out / name} is missing. Build it with `firmware/relay-display/build.sh --qemu`, "
                "or point RELAY_FW_BUILD at a build made elsewhere."
            )
    return _stale(out)


@pytest.fixture(scope="module", autouse=True)
def _requires_qemu(request: pytest.FixtureRequest) -> None:
    # The seed layout check reads a header and needs no emulator, so it always runs.
    if request.module.__name__ == "test_seed_layout":
        return
    reason = _unavailable()
    if reason is None:
        return
    if request.config.getoption("--firmware"):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason, allow_module_level=True)


# **The panel's refresh comes from the display backend, so the emulator runs with one.** The virtual
# RGB panel finishes an update only when the console refreshes: its `draw_bitmap` busy-waits on the
# update's enable bit, and the device clears that bit from the refresh. With `-display none` nothing
# refreshes, and the first strip hangs the boot. `-display sdl` with SDL's `dummy` video driver is a
# real refresh and no window.
DISPLAY_BACKEND = "sdl"


def new_board(workdir: Path, flash: Path) -> Board:
    """A board for this suite: the image, its flash, and the display backend above."""
    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    return Board(merged_image(), workdir=workdir, flash=flash, display=DISPLAY_BACKEND)


def merged_image() -> Path:
    return build_dir() / IMAGE_NAME


def flash_with_seed(flash: Path, seed: str | None) -> None:
    """The merged image at 0x0, and the seed's `nvs` behind it.

    `seed=None` leaves the partition erased, which is a board that has never been configured at all:
    the case `settings.cpp` answers with defaults.
    """
    qemu.write_flash(flash, merged_image(), size=FLASH_BYTES)
    if seed is None:
        return
    nvs = (build_dir() / f"{seed}.bin").read_bytes()
    assert len(nvs) == NVS_BYTES, f"{seed}.bin is {len(nvs)} bytes, expected {NVS_BYTES}"
    blob = bytearray(flash.read_bytes())
    blob[NVS_OFFSET : NVS_OFFSET + NVS_BYTES] = nvs
    flash.write_bytes(bytes(blob))


BOOT_LINE = r"boot: relay-display 0\.1\.0, CrowPanel Advance 7\.0 DIS02170A"


def wait_ready(machine: Board, *, timeout: float = BOOT_TIMEOUT) -> None:
    """Block until the firmware says `boot: ready`, with a deadline."""
    assert machine.uart is not None
    machine.uart.wait_for(r"boot: ready", timeout=timeout)


@pytest.fixture(scope="session")
def provisioned(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Board]:
    """One board, seeded with `nvs` and booted, for the tests that only read it."""
    workdir = tmp_path_factory.mktemp("relay-board")
    flash = workdir / "flash.bin"
    flash_with_seed(flash, "nvs")
    machine = new_board(workdir, flash).start()
    try:
        wait_ready(machine)
        yield machine
    finally:
        machine.stop()


@pytest.fixture
def boot(tmp_path: Path) -> Iterator[Callable[[str | None], Board]]:
    """Start a board of this test's own, seeded with `seed`, and stop it afterwards.

    Returns the started board without waiting for it, so a test can ask about the boot as it happens.
    `wait_ready` is there for the ones that want the whole boot first.
    """
    machines: list[Board] = []

    def start(seed: str | None = "nvs") -> Board:
        workdir = tmp_path / f"board-{len(machines)}"
        workdir.mkdir()
        flash = workdir / "flash.bin"
        flash_with_seed(flash, seed)
        machine = new_board(workdir, flash)
        machines.append(machine)
        return machine.start()

    yield start
    for machine in machines:
        machine.stop()


# ---------------------------------------------------------------------------
# The framebuffer
# ---------------------------------------------------------------------------


def pack565(r: int, g: int, b: int) -> int:
    """RGB888 to RGB565 by truncation, the rule `panel_format.h`'s `rgb565_pack` uses."""
    return ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)


@dataclass(frozen=True)
class Screen:
    """One frame the emulator's console holds, as the panel's pixels."""

    width: int
    height: int
    data: bytes  # RGB888, row by row, as a PPM carries it

    @classmethod
    def read(cls, path: Path) -> Screen:
        """A binary PPM (P6) as QEMU's `screendump` writes it."""
        raw = path.read_bytes()
        # Four whitespace-separated header fields, then one whitespace byte, then pixels.
        fields = []
        i = 0
        while len(fields) < 4:
            while raw[i : i + 1].isspace():
                i += 1
            start = i
            while not raw[i : i + 1].isspace():
                i += 1
            fields.append(raw[start:i])
        i += 1
        magic, width, height, maxval = fields
        assert magic == b"P6" and maxval == b"255", f"unexpected PPM header {fields!r}"
        screen = cls(int(width), int(height), raw[i:])
        assert len(screen.data) == screen.width * screen.height * 3, "PPM is short"
        return screen

    def rgb(self, x: int, y: int) -> tuple[int, int, int]:
        offset = (y * self.width + x) * 3
        return (self.data[offset], self.data[offset + 1], self.data[offset + 2])

    def rgb565(self, x: int, y: int) -> int:
        return pack565(*self.rgb(x, y))


def capture(machine: Board, name: str) -> Screen:
    """The emulator's console as a PPM, read back (`screendump` is synchronous in QEMU).

    The panel is the top 800 x 480 of the console. **The console is 800 x 600, not 800 x 480:** the
    virtual panel's own frame buffer is sized for 800 x 600, and the panel's window is the top 480
    rows of it. So the panel is read from the top, and the rows below it are not part of the picture.
    """
    assert machine.qmp is not None
    out = machine.workdir / f"{name}.ppm"
    machine.qmp.command("screendump", filename=str(out))
    if not out.exists():
        raise AssertionError(f"QEMU did not write {out}; the console has no surface")
    screen = Screen.read(out)
    assert screen.width == WIDTH and screen.height >= HEIGHT, (
        f"the console is {screen.width}x{screen.height}; the panel is {WIDTH}x{HEIGHT} "
        "in its top-left corner"
    )
    return screen
