"""The firmware update from Relay, under the emulator.

Three boots, each a panel from the `nvs-update` seed pointed at a fake Relay on the address the seed holds (the emulator's
host, port 8100), so the address survives the restarts an update makes. What is asserted is what a person at the
panel and at Relay's admin page could each see: the serial log the firmware writes, and the reports and requests the
fake Relay answered.

* **a good image installs.** Relay asks for 0.2.0 with `now`; the panel downloads it, checks the checksum and the
  signature, restarts into it, the new image checks in with Relay and is confirmed, and Relay hears `installed`.
* **a bad signature is refused.** The same image signed with a key the panel does not hold is whole, correctly
  checksummed and validly signed, and refused by `esp_ota_end` on the panel. The panel keeps running 0.1.0, tells
  Relay `failed` with `signature`, and never restarts. (Relay checks only that the image is a panel's and is
  signed at all; this is the check that keeps a stranger's image off a panel.)
* **an image that never checks in rolls back.** The new image boots and cannot reach Relay (the fake answers its
  check-ins 503), the panel restarts at its bound, the bootloader takes the image back, the old version runs again,
  tells Relay `rolled_back`, and does not fetch the refused version a second time.

The images are the ones `build_update.py` makes. The run needs them, the emulator image and QEMU, and is skipped
without them as the rest of the suite is. Every boot is minutes; the fixtures are class-scoped, so each board and its fake Relay (which holds the seed's port) are gone before the next.
"""

from __future__ import annotations

import re
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

import qemu
from conftest import (
    DISPLAY_BACKEND,
    FLASH_BYTES,
    PROJECT,
    flash_with_seed,
    merged_image,
    wait_ready,
)
from fake_relay import FakeRelay
from qemu import Board
from seeds import UPDATE_PANEL_ID as PANEL_ID

UPDATE_VERSION = "0.2.0"
UPDATE_DIR = PROJECT / "build-qemu-update"
GOOD = UPDATE_DIR / "relay-display-app.bin"
WRONG_KEY = UPDATE_DIR / "relay-display-app-wrongkey.bin"
# The `nvs-update` seed's Relay address: the emulator's host, port 8100, so no other board in the suite reaches this fake.
RELAY_PORT = 8100
# `sdkconfig.qemu`: the proof window of a new image, and the quiet moment a verified image waits for.
CONFIRM_SECONDS = 60
QUIET_SECONDS = 6
DOWNLOAD_TIMEOUT = 240.0
BOOT_TIMEOUT = 150.0


def _unavailable() -> str | None:
    for path in (GOOD, WRONG_KEY):
        if not path.exists():
            return f"{path} is missing. Build it with `python3 firmware/relay-display/tests/build_update.py`."
    return None


@pytest.fixture(scope="module", autouse=True)
def _update_present(request: pytest.FixtureRequest) -> None:
    reason = _unavailable()
    if reason is None:
        return
    if request.config.getoption("--firmware"):
        pytest.fail(reason, pytrace=False)
    pytest.skip(reason, allow_module_level=True)


def started(workdir: Path) -> tuple[Board, FakeRelay]:
    """A panel from the `nvs` seed and a fake Relay on the address it holds.

    **One TCG thread.** `esp_ota_end` maps the slot it has just written through the flash MMU, and in a
    multi-threaded QEMU a memory region changing under one vCPU while the other executes aborts the emulator in
    `physmem.c` (`qemu.py` says where this was found). The panel restarts from the update task, which survives it.
    """
    import os

    os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
    relay = FakeRelay(port=RELAY_PORT)
    flash = workdir / "flash.bin"
    flash_with_seed(flash, "nvs-update")
    board = Board(merged_image(), workdir=workdir, flash=flash, display=DISPLAY_BACKEND, single_thread=True).start()
    wait_ready(board)
    return board, relay


def wait(board: Board, pattern: str, timeout: float = DOWNLOAD_TIMEOUT) -> None:
    assert board.uart is not None
    board.uart.wait_for(pattern, timeout=timeout)


def boots_of(board: Board, version: str) -> int:
    assert board.uart is not None
    return len(re.findall(rf"boot: relay-display {re.escape(version)}\b", board.uart.text))


class Run:
    def __init__(self, board: Board, relay: FakeRelay) -> None:
        self.board = board
        self.relay = relay
        self.transcript = ""


@pytest.fixture(scope="class")
def installed(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Run]:
    workdir = tmp_path_factory.mktemp("update-good")
    board, relay = started(workdir)
    run = Run(board, relay)
    try:
        relay.wait_checkins(1)
        relay.wait_panel_streams(1)
        relay.serve_image(GOOD.read_bytes(), UPDATE_VERSION)
        relay.offer_update(now=True)
        wait(board, r"update: restarting into " + re.escape(UPDATE_VERSION))
        wait(board, r"boot: relay-display " + re.escape(UPDATE_VERSION), BOOT_TIMEOUT)
        wait(board, r"update: " + re.escape(UPDATE_VERSION) + r" is on approval", BOOT_TIMEOUT)
        wait(board, r"update: confirmed " + re.escape(UPDATE_VERSION), BOOT_TIMEOUT)
        relay.wait_update_report("installed", timeout=BOOT_TIMEOUT)
        assert board.uart is not None
        run.transcript = board.uart.text
        yield run
    finally:
        board.stop()
        relay.close()


@pytest.fixture(scope="class")
def refused(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Run]:
    workdir = tmp_path_factory.mktemp("update-wrongkey")
    board, relay = started(workdir)
    run = Run(board, relay)
    try:
        relay.wait_checkins(1)
        relay.wait_panel_streams(1)
        image = WRONG_KEY.read_bytes()
        relay.serve_image(image, UPDATE_VERSION)
        relay.offer_update(now=True)
        wait(board, r"update: refused \(signature\)")
        relay.wait_update_report("failed", timeout=60)
        # Time for the panel to restart if it were going to, and to be offered it again.
        time.sleep(QUIET_SECONDS + 10)
        assert board.uart is not None
        run.transcript = board.uart.text
        yield run
    finally:
        board.stop()
        relay.close()


@pytest.fixture(scope="class")
def rolled_back(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Run]:
    workdir = tmp_path_factory.mktemp("update-rollback")
    board, relay = started(workdir)
    run = Run(board, relay)
    try:
        relay.wait_checkins(1)
        relay.wait_panel_streams(1)
        relay.serve_image(GOOD.read_bytes(), UPDATE_VERSION)
        # The new version's check-ins are answered 503: it cannot prove itself. And Relay does not take the reports
        # either, so its ask stands in every check-in reply and the panel has to hold off on its own.
        relay.refuse_versions = {UPDATE_VERSION}
        relay.update_report_status = 503
        relay.offer_update(now=True)
        wait(board, r"update: restarting into " + re.escape(UPDATE_VERSION))
        wait(board, r"update: " + re.escape(UPDATE_VERSION) + r" is on approval", BOOT_TIMEOUT)
        wait(board, rf"update: not confirmed within {CONFIRM_SECONDS} s", CONFIRM_SECONDS + BOOT_TIMEOUT)
        deadline = time.monotonic() + BOOT_TIMEOUT
        while boots_of(board, "0.1.0") < 2:
            assert time.monotonic() < deadline, "the old image never booted again"
            time.sleep(0.25)
        wait(board, r"update: the bootloader took " + re.escape(UPDATE_VERSION) + r" back", BOOT_TIMEOUT)
        relay.wait_update_report("rolled_back", timeout=BOOT_TIMEOUT)
        # With the order still standing a while, the panel must not fetch the refused version again.
        asked = relay.image_requests
        time.sleep(40)
        run.transcript = board.uart.text if board.uart else ""
        run.again = relay.image_requests - asked  # type: ignore[attr-defined]
        yield run
    finally:
        board.stop()
        relay.close()


class TestAGoodImageInstalls:
    def test_it_is_downloaded_verified_and_the_panel_restarts_into_it(self, installed: Run) -> None:
        assert f"update: fetching {UPDATE_VERSION}" in installed.transcript
        assert f"update: restarting into {UPDATE_VERSION}" in installed.transcript
        assert f"boot: relay-display {UPDATE_VERSION}" in installed.transcript

    def test_the_new_image_checks_in_and_is_confirmed(self, installed: Run) -> None:
        assert f"update: confirmed {UPDATE_VERSION}" in installed.transcript
        after = [c for c in installed.relay.checkins if c["version"] == UPDATE_VERSION]
        assert after and all(c["id"] == PANEL_ID for c in after)

    def test_relay_is_told_each_step_in_order(self, installed: Run) -> None:
        states = [r["state"] for r in installed.relay.update_reports]
        assert states[:1] == ["downloading"]
        assert states.index("restarting") > states.index("downloading")
        assert states[-1] == "installed"
        assert {r["version"] for r in installed.relay.update_reports} == {UPDATE_VERSION}
        progress = [r["progress"] for r in installed.relay.update_reports if r["state"] == "downloading"]
        assert progress == sorted(progress) and progress[-1] <= 100

    def test_the_panel_keeps_its_identity_across_the_restart(self, installed: Run) -> None:
        assert {c["id"] for c in installed.relay.checkins} == {PANEL_ID}


class TestABadSignatureIsRefusedOnThePanel:
    def test_the_image_is_refused_at_the_signature(self, refused: Run) -> None:
        assert "update: refused (signature)" in refused.transcript

    def test_relay_is_told_why(self, refused: Run) -> None:
        failed = [r for r in refused.relay.update_reports if r["state"] == "failed"]
        assert failed and failed[0]["error"] == "signature"

    def test_the_panel_never_restarts_into_it(self, refused: Run) -> None:
        assert "update: restarting into" not in refused.transcript
        assert boots_of(refused.board, UPDATE_VERSION) == 0
        assert boots_of(refused.board, "0.1.0") == 1
        assert not any(c["version"] == UPDATE_VERSION for c in refused.relay.checkins)


class TestAnImageThatNeverChecksInRollsBack:
    def test_the_bootloader_takes_the_unconfirmed_image_back(self, rolled_back: Run) -> None:
        assert f"update: not confirmed within {CONFIRM_SECONDS} s" in rolled_back.transcript
        assert boots_of(rolled_back.board, "0.1.0") >= 2
        assert f"update: the bootloader took {UPDATE_VERSION} back" in rolled_back.transcript

    def test_relay_hears_the_rollback_from_the_old_version(self, rolled_back: Run) -> None:
        reports = [r for r in rolled_back.relay.update_reports if r["state"] == "rolled_back"]
        assert reports and reports[0]["version"] == UPDATE_VERSION
        # The old version checks in again, so it is the one running.
        assert rolled_back.relay.checkins[-1]["version"] == "0.1.0"

    def test_the_refused_version_is_not_fetched_again(self, rolled_back: Run) -> None:
        assert rolled_back.again == 0  # type: ignore[attr-defined]


def test_the_emulator_binary_is_the_one_the_suite_expects() -> None:
    qemu.find_binary()
    assert FLASH_BYTES == 16 * 1024 * 1024
