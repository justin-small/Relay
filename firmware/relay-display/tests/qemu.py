"""Start, drive, reset and stop `qemu-system-xtensa`.

One class. It owns the emulator process, the flash file under it, the console socket and the QMP
socket, and it leaves none of the four behind: a suite that leaked an emulator per test would make
the next run's failures somebody else's.

Copied from the VXP firmware's `tests/firmware/qemu.py` (see README.md, "Copied from VXP"), with
the I/O board's parts taken out.

**What was found by running it.** Two peripherals in this firmware's path are *unimplemented*
devices in the esp32s3 machine rather than absent ones, which is the worse of the two failures
because the writes succeed:

* `esp32s3.rmt`, which the WS2812 hangs off.
* `misc.esp32c3.usb_serial_jtag`, which the machine instantiates at address -1: present in the
  device tree and mapped nowhere. Pointing the console at it produces a board that resets inside
  its second-stage bootloader on every attempt and never prints a word. The UART is modelled and
  works; `-serial` below is `serial0` and carries everything from the ROM banner onward.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path

from uart import BoardDied, Uart

# The board's flash, and the emulator's. 16 MB because `sdkconfig.qemu`
# deliberately keeps the production geometry: resetting the machine and finding the
# configuration still there only tests the partition table if it is that table.
FLASH_BYTES = 16 * 1024 * 1024

# Where the guest sees the host. QEMU's user-mode network puts the host at the
# gateway address, so an `api_server` bound to loopback here is reachable from
# the firmware at this address and no route has to be configured either side.
HOST_FROM_GUEST = "10.0.2.2"


def find_binary() -> str:
    """`qemu-system-xtensa`, or a skip with a sentence saying how to get one.

    The Espressif fork, not the distribution's: only that one has the esp32s3
    machine. `RELAY_QEMU` overrides the search for anybody who keeps it somewhere
    else.

    **And the version matters, in a way that is invisible on a laptop**.
    The build IDF v5.4.1 pins, `esp_develop_9.0.0_20240606`, gets the S3's SHA
    accelerator's DMA transfer wrong and corrupts its own heap on the first
    push signature. macOS's allocator does not notice and this suite passes;
    glibc's does, and every board aborts on `[SHA] Error reading from GDMA
    buffer` followed by a double free. `firmware-emulator.yml` pins
    `esp-develop-9.2.2-20260417` through `RELAY_QEMU` for that reason, and
    `firmware/vxp-io-8di8ro/README.md` points a laptop at the same release.
    Nothing here enforces a floor: the version is not in the binary's path in
    any form worth parsing, and a suite that refused to run on the emulator
    somebody already has is one they run less often.
    """
    override = os.environ.get("RELAY_QEMU")
    if override:
        return override
    found = shutil.which("qemu-system-xtensa")
    if found:
        return found
    # The path Espressif's own installer uses. Globbed rather than pinned: the
    # directory carries the build date, and a suite that broke on a toolchain
    # update would be a suite nobody re-ran.
    pattern = ".espressif/tools/qemu-xtensa/*/qemu/bin/qemu-system-xtensa"
    for candidate in sorted(Path.home().glob(pattern)):
        return str(candidate)
    raise FileNotFoundError(
        "qemu-system-xtensa not found. Install Espressif's QEMU fork — the "
        "distribution's build has no esp32s3 machine — or set RELAY_QEMU to it."
    )


def free_port() -> int:
    """A port the kernel says is free. Racy in principle, and never in practice
    for the seconds between here and QEMU binding it."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def write_flash(image: Path, merged: Path, *, size: int = FLASH_BYTES) -> None:
    """The merged image at 0x0 and erased flash behind it.

    `size` is the module's flash: 16 MB for both boards, which is the default. The
    emulated flash is the production geometry in either case.

    `0xFF` and not zeroes: that is what an erased sector reads as, and `nvs`
    decides a page is free by exactly that. A zero-filled flash would come up
    with an NVS partition that is full of invalid entries rather than empty.
    """
    blob = bytearray(b"\xff" * size)
    body = merged.read_bytes()
    if len(body) > size:  # pragma: no cover - would be a build change
        raise ValueError(f"{merged} is {len(body)} bytes, larger than the emulated flash")
    blob[: len(body)] = body
    image.write_bytes(bytes(blob))


class Qmp:
    """Enough of QMP for what the suite asks: reset, query, screendump, quit."""

    def __init__(self, path: Path, *, timeout: float = 10.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self._sock.connect(str(path))
                break
            except OSError:
                self._sock.close()
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)
        self._sock.settimeout(10.0)
        self._file = self._sock.makefile("rwb")
        self._read()  # the greeting
        self.command("qmp_capabilities")

    def _read(self) -> dict:
        while True:
            line = self._file.readline()
            if not line:
                raise ConnectionError("QMP closed")
            message = json.loads(line)
            # Events arrive unsolicited and interleave with replies. Nothing
            # here subscribes to them, so they are read past rather than
            # queued — a queue nobody drains is a leak with a nicer name.
            if "event" in message:
                continue
            return message

    def command(self, name: str, **arguments: object) -> dict:
        payload = {"execute": name}
        if arguments:
            payload["arguments"] = arguments
        self._file.write(json.dumps(payload).encode() + b"\n")
        self._file.flush()
        reply = self._read()
        if "error" in reply:
            raise RuntimeError(f"QMP {name} failed: {reply['error']}")
        return reply

    def close(self) -> None:
        with contextlib.suppress(OSError):
            self._sock.close()


class Board:
    """One emulated board: the process, its flash, its console and its QMP.

    Used as a context manager so that a test which fails part way through still
    stops its emulator, with nothing left behind.
    """

    def __init__(
        self,
        merged_image: Path,
        *,
        workdir: Path,
        flash: Path | None = None,
        single_thread: bool = False,
        display: str = "none",
    ) -> None:
        self.single_thread = single_thread
        # `-display`. The I/O board runs with none. The compact display needs a backend
        # that refreshes its console: see `tests/firmware_compact/conftest.py`.
        self.display = display
        self.workdir = workdir
        self.workdir.mkdir(parents=True, exist_ok=True)
        # A test that wants the flash to survive a restart passes its own path
        # and reuses it; everything else gets a fresh one and an empty NVS.
        self.flash = flash or (self.workdir / "flash.bin")
        if not self.flash.exists():
            write_flash(self.flash, merged_image)
        self._serial_port = free_port()
        # The board's own web server, forwarded so a later test can drive the setup page the way an
        # installer does. `http_port` is the guest's port 80, where the page is served over plain HTTP
        # (setup is on the panel's own network); `https_port` is kept for the guest's 443, which this
        # firmware does not serve.
        self.https_port = free_port()
        self.http_port = free_port()
        # A unix socket in a short directory. macOS caps the path at 104 bytes
        # and pytest's tmp_path is long enough to go over it on its own.
        self._qmp_dir = tempfile.mkdtemp(prefix="relayqmp")
        self._qmp_path = Path(self._qmp_dir) / "qmp.sock"
        self.process: subprocess.Popen[bytes] | None = None
        self._stderr: object | None = None
        self.uart: Uart | None = None
        self.qmp: Qmp | None = None

    def argv(self) -> list[str]:
        argv = [
            find_binary(),
            "-M",
            "esp32s3",
            "-nographic",
            "-display",
            self.display,
            "-monitor",
            "none",
            "-drive",
            f"file={self.flash},if=mtd,format=raw",
            "-serial",
            f"tcp:127.0.0.1:{self._serial_port},server=on,wait=off",
            "-qmp",
            f"unix:{self._qmp_path},server=on,wait=off",
        ]
        # **Always a network device.** `open_eth` is part of the esp32s3 machine
        # rather than an option on it: with no `-nic` at all QEMU instantiates
        # it anyway, and `-nic none` — the only way to leave it out — produces a
        # machine that resets inside the second-stage bootloader every time.
        # "No ethernet device" is therefore not a state this emulator can be
        # put in, and `test_boot.py` says where that case went instead.
        argv += [
            "-nic",
            f"user,model=open_eth,hostfwd=tcp:127.0.0.1:{self.https_port}-:443,"
            f"hostfwd=tcp:127.0.0.1:{self.http_port}-:80",
        ]
        # **One TCG thread, for the tests that ask for it — and only those.**
        #
        # The esp32s3 machine has two cores, and QEMU's default is a host
        # thread each. Two of this emulator's bugs sit on opposite sides of
        # that switch, so neither setting can be the one the whole suite uses:
        #
        # * **Multi-threaded**, QEMU aborts in `physmem.c`'s
        #   `iotlb_to_section` — or segfaults outright — while the firmware
        #   verifies an uploaded image. `esp_ota_end` maps the slot it has just
        #   written through the flash MMU, and a memory region changing under
        #   one vCPU while the other executes is the race that assertion exists
        #   to catch. Roughly one run in three of the I/O board's update-refusal tests, with no
        #   panic on the console: the *emulator* exits, not the board.
        # * **Single-threaded**, an `esp_restart()` called straight from the
        #   loop task — which is only `check_boot_button` — lands core 1 in
        #   an unhandled debug exception and the board boot-loops.
        #   `test_boot.py` fails every time with it on, and the same firmware
        #   restarts perfectly well from the HTTP task in a dozen other tests.
        #
        # Both are the emulator. So `test_ota.py` asks for the setting that
        # makes its own path survivable and nothing else changes.
        if self.single_thread:
            argv += ["-accel", "tcg,thread=single"]
        return argv

    def start(self) -> Board:
        (self.workdir / "qemu.cmd").write_text(" ".join(self.argv()) + "\n")
        # **QEMU's own diagnostics go to a file, never to a pipe.** The
        # esp32s3 machine carries several devices as *unimplemented* rather
        # than absent — the iomux the GPIO configuration touches, and the RMT
        # the LED does — and it warns on every access to one. An unread pipe
        # holds 64 KB and then blocks the writer, so a machine that produced
        # enough of those simply stopped, mid-boot, a few lines after the GPIO
        # configuration, with nothing to say about why. Two of them did, in one
        # full run of this suite, and neither did when run alone.
        #
        # A file cannot fill, and a failed test keeps it beside the console
        # transcript.
        self._stderr = (self.workdir / "qemu.stderr").open("wb")
        # `argv` is this module's own list, built from a binary path the
        # harness resolved and integers it allocated. There is no shell and no
        # caller-supplied string in it.
        self.process = subprocess.Popen(  # noqa: S603
            self.argv(), stdout=subprocess.DEVNULL, stderr=self._stderr
        )
        self.uart = Uart("127.0.0.1", self._serial_port, context=self.state)
        self.qmp = Qmp(self._qmp_path)
        return self

    def state(self) -> str:
        """Whether the emulator is still running, and what it last complained of.

        Attached to every UART timeout. A board that went quiet because its
        emulator exited and one that went quiet because the firmware hung look
        identical from the socket, and they need completely different fixes.
        """
        if self.process is None:
            return "--- qemu: never started ---\n"
        code = self.process.poll()
        lines = [
            "--- qemu: running ---\n"
            if code is None
            else f"--- qemu: EXITED with status {code} ---\n"
        ]
        errors = self.workdir / "qemu.stderr"
        if errors.exists() and errors.stat().st_size:
            lines.append(f"--- qemu stderr ---\n{errors.read_text()[-4000:]}\n")
        return "".join(lines)

    def check_alive(self) -> None:
        """Raise if the emulator has exited, before anything talks to it.

        The session board boots once and every later fixture assumed it stayed
        up. It does not always: a TCG race segfaults QEMU roughly one full run
        in three, and afterwards `Uart.send_line` reports a broken pipe and
        `board_get` a refused connection — fifteen tests failing on socket
        errors with no statement anywhere that the machine is gone.

        `state` already knows. This is the two lines that ask it, from the
        fixtures that hand the board out, so the run says "the emulator exited
        with status -11" once rather than "broken pipe" fifteen times.

        **It does not restart the board.** A machine that has just crashed has
        unknown flash, and a suite that recovers silently is a suite that stops
        reporting how often this happens.
        """
        if self.process is None or self.process.poll() is not None:
            console = "" if self.uart is None else self.uart.text[-4000:]
            raise BoardDied(
                "the emulator is no longer running; the board cannot be reached.\n"
                f"{self.state()}"
                f"--- console transcript ---\n{console}"
            )

    def reset(self) -> None:
        """A machine reset.

        The flash file is not touched, so NVS comes back the way the firmware
        left it: the one claim a laptop-side fake cannot honestly make about
        stored configuration, and the check that the partition table keeps it.
        """
        assert self.qmp is not None
        self.qmp.command("system_reset")

    def stop(self) -> None:
        if self.uart is not None:
            (self.workdir / "console.log").write_text(self.uart.text)
            self.uart.close()
            self.uart = None
        if self.qmp is not None:
            self.qmp.close()
            self.qmp = None
        if self.process is not None:
            self.process.send_signal(signal.SIGKILL)
            self.process.wait(timeout=10)
            self.process = None
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
        shutil.rmtree(self._qmp_dir, ignore_errors=True)

    def __enter__(self) -> Board:
        return self.start()

    def __exit__(self, *_: object) -> None:
        self.stop()
