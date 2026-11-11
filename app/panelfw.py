"""Panel firmware: one signed image per kind of panel (board), held by Relay for the caption panels.

The operator uploads the application image a release builds (`relay-display-app.bin`,
firmware/relay-display) from the admin page. Relay checks that it is a Relay panel's image at all --
an ESP-IDF application for a board's chip, named `relay-display-app`, with a version it can read, a
whole image whose own checksum holds, and a signature block that belongs to it -- keeps it, and
serves it to the panels of that board on the viewer port.

**Which board an image is for is read from its chip** (`BOARDS`): every board is a different chip, and
the image carries no other board name. A panel says its board in its check-in (#122); one that says
none is a CrowPanel, the only board there was before. A board that is on hold (`on_hold`) is listed
nowhere and its images are refused, so nothing is ever offered to it.

**Relay does not decide whether a signature is good. The panel does.** Images travel over plain HTTP
on the LAN, so the check that keeps a stranger's image off a panel has to be the panel's, against the
public key it was built with. Relay holds no key, and nothing here is a substitute for that check; it
only turns a file somebody picked by mistake (the merged serial image, another board's, a build that
was never signed, a truncated copy) into a sentence on the admin page instead of a failed update on
a wall.

The images are stored beside config.json (so on the state volume in a container) as
`panel-firmware/<board>/`: `panel.bin` and `panel.json`. One image per board: uploading replaces it.
A store from before #122 kept the one image at `panel-firmware/panel.bin`; it is a CrowPanel's and is
moved into place the first time it is read.
"""
from __future__ import annotations

import hashlib
import json
import re
import struct
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path

from . import config

# An application slot in firmware/relay-display/partitions.csv (`app0`, `app1`). A larger file could
# never be written, and the panel refuses it too.
SLOT_BYTES = 0x1E0000
# `project()` in firmware/relay-display/CMakeLists.txt: the name every Relay panel image carries in
# its application descriptor. Another board's image has another name.
PROJECT_NAME = "relay-display-app"
# esp_image_header_t.chip_id for the boards' chips.
CHIP_ESP32_S3 = 0x0009
CHIP_ESP32_P4 = 0x0012


@dataclass(frozen=True)
class Board:
    id: str          # RELAY_BOARD_ID in the board's header (firmware/relay-display/main/boards/)
    label: str       # what the admin page calls it
    chip: int
    chip_name: str
    on_hold: bool = False


# Every board the firmware builds for. The Waveshare 10.1" is on hold (#114): its development is
# paused, so Relay takes no image for it and lists it nowhere. Set `on_hold=False` to bring it back.
BOARDS: dict[str, Board] = {
    "crowpanel-adv7": Board("crowpanel-adv7", 'CrowPanel Advance 7"', CHIP_ESP32_S3, "ESP32-S3"),
    "waveshare-p4-10.1": Board("waveshare-p4-10.1", 'Waveshare ESP32-P4 10.1"', CHIP_ESP32_P4, "ESP32-P4",
                               on_hold=True),
}
DEFAULT_BOARD = "crowpanel-adv7"


def active_boards() -> list[Board]:
    return [b for b in BOARDS.values() if not b.on_hold]


def board_label(board_id: str) -> str:
    b = BOARDS.get(board_id)
    return b.label if b else board_id


def panel_board(reported: str) -> str:
    """The board whose firmware a panel takes: the one it reports, or the CrowPanel for a panel that
    reports none (every panel from before #122). A board Relay does not know, or one on hold, takes
    none: ""."""
    board = reported or DEFAULT_BOARD
    b = BOARDS.get(board)
    return board if b is not None and not b.on_hold else ""

_IMAGE_MAGIC = 0xE9
_APP_DESC_MAGIC = 0xABCD5432
_HEADER_BYTES = 24
_SEGMENT_HEADER_BYTES = 8
_APP_DESC_OFFSET = 32          # the header and the first segment's header sit in front of it
_VERSION_OFFSET = _APP_DESC_OFFSET + 16
_PROJECT_OFFSET = _VERSION_OFFSET + 32
_HEAD_BYTES = _PROJECT_OFFSET + 32
_SECTOR = 4096
_SIG_MAGIC = b"\xe7\x02\x00\x00"   # Secure Boot V2 signature block: magic byte, version 2, two zero bytes
_SIG_DIGEST_OFFSET = 4
_SIG_CRC_OFFSET = 1196             # the block's CRC covers everything before it
_VERSION_RE = re.compile(r"^\d{1,5}\.\d{1,5}\.\d{1,5}$")


class ImageRefused(ValueError):
    """The upload is not an image a panel could install. The text is for the operator."""


@dataclass
class Info:
    version: str
    size: int
    sha256: str
    uploaded_at: float     # wall clock
    filename: str
    board: str = DEFAULT_BOARD

    def public(self) -> dict:
        row = asdict(self)
        row["uploaded_ago_s"] = round(max(0.0, time.time() - self.uploaded_at), 1)
        row["board_label"] = board_label(self.board)
        row.pop("uploaded_at")
        return row


def parse_version(text: object) -> tuple[int, int, int] | None:
    """`X.Y.Z` as numbers, or None for anything else."""
    if not isinstance(text, str) or not _VERSION_RE.match(text):
        return None
    major, minor, patch = (int(part) for part in text.split("."))
    return major, minor, patch


def newer(candidate: str, running: str) -> bool:
    """Whether an image at `candidate` is a step forward from a panel on `running`. A panel only goes
    forward (the panel refuses anything else), so a version either side cannot read is not one."""
    a, b = parse_version(candidate), parse_version(running)
    return a is not None and b is not None and a > b


def _cstr(raw: bytes) -> str:
    return raw.split(b"\x00", 1)[0].decode("ascii", errors="replace")


def _image_end(data: bytes) -> tuple[int, int]:
    """Where the image's checksum byte ends (the SHA-256, if there is one, follows it) and where the
    whole image ends, found by walking its segments as the bootloader does. Raises ImageRefused if
    the walk does not stay inside the file."""
    count = data[1]
    pos = _HEADER_BYTES
    for _ in range(count):
        if pos + _SEGMENT_HEADER_BYTES > len(data):
            raise ImageRefused("that file ends in the middle of its own header")
        (_, length) = struct.unpack_from("<II", data, pos)
        pos += _SEGMENT_HEADER_BYTES + length
        if pos > len(data):
            raise ImageRefused("that file is shorter than its own header says; it was cut off")
    # The checksum byte is the last of a 16-byte block, and the SHA-256 follows it.
    checked = (pos + 1 + 15) // 16 * 16
    end = checked + 32 if data[23] == 1 else checked  # hash_appended
    if end > len(data):
        raise ImageRefused("that file is shorter than its own header says; it was cut off")
    return checked, end


def inspect(data: bytes) -> tuple[str, str]:
    """Check an upload. Returns (version, sha256 of the file) or raises ImageRefused with a sentence
    saying what is wrong with it. `inspect_board` also says which board it is for."""
    version, digest, _ = inspect_board(data)
    return version, digest


def inspect_board(data: bytes, board: str | None = None) -> tuple[str, str, str]:
    """`inspect`, and the board the image is for, read from its chip. With `board`, an image for
    another board is refused."""
    if len(data) == 0:
        raise ImageRefused("The upload was empty.")
    if len(data) > SLOT_BYTES:
        raise ImageRefused("That file is larger than a panel's application slot, so no panel could install it.")
    if len(data) < _HEAD_BYTES or data[0] != _IMAGE_MAGIC:
        raise ImageRefused("That file is not panel firmware. Upload relay-display-app.bin from the release.")
    if struct.unpack_from("<I", data, _APP_DESC_OFFSET)[0] != _APP_DESC_MAGIC:
        raise ImageRefused(
            "That is the serial-flash image (relay-display.bin), which holds a bootloader and a partition "
            "table that an update cannot write. Upload relay-display-app.bin, the application alone, from "
            "the same release.")
    chip = struct.unpack_from("<H", data, 12)[0]
    found = next((b for b in BOARDS.values() if b.chip == chip), None)
    if found is None:
        names = " or ".join(b.chip_name for b in active_boards())
        raise ImageRefused(f"That image is for another chip, not a panel's {names}.")
    if found.on_hold:
        raise ImageRefused(f"That image is for the {found.label}, whose panels are on hold; Relay does not "
                           "keep firmware for them.")
    if board is not None and found.id != board:
        raise ImageRefused(f"That image is for the {found.label}, not the {board_label(board)}.")
    project = _cstr(data[_PROJECT_OFFSET:_PROJECT_OFFSET + 32])
    if project != PROJECT_NAME:
        raise ImageRefused(
            f"That image is for another board ({project or 'unnamed'}); a Relay panel's image is "
            f"named {PROJECT_NAME}.")
    version = _cstr(data[_VERSION_OFFSET:_VERSION_OFFSET + 32])
    if parse_version(version) is None:
        raise ImageRefused(f"That image's version ({version or 'none'}) is not a release number like 0.2.0.")

    checked, end = _image_end(data)
    if data[23] == 1 and hashlib.sha256(data[:checked]).digest() != data[checked:end]:
        raise ImageRefused("That image is damaged: its own checksum does not match. Download it again.")
    if len(data) % _SECTOR != 0 or len(data) < 2 * _SECTOR:
        raise ImageRefused(
            "That image is not signed. A panel only installs an image signed with the release key, and "
            "refuses an unsigned one; use the relay-display-app.bin a release publishes.")
    block = data[-_SECTOR:]
    if block[:4] != _SIG_MAGIC:
        raise ImageRefused(
            "That image is not signed. A panel only installs an image signed with the release key, and "
            "refuses an unsigned one; use the relay-display-app.bin a release publishes.")
    if len(data) >= 2 * _SECTOR and data[-2 * _SECTOR:][:4] == _SIG_MAGIC:
        raise ImageRefused("That image carries two signatures; it was signed twice. Build it again.")
    covered = data[:-_SECTOR]
    if block[_SIG_DIGEST_OFFSET:_SIG_DIGEST_OFFSET + 32] != hashlib.sha256(covered).digest():
        raise ImageRefused("That image's signature is not for these bytes: the file was changed after it was signed.")
    (crc,) = struct.unpack_from("<I", block, _SIG_CRC_OFFSET)
    if crc != zlib.crc32(block[:_SIG_CRC_OFFSET]) & 0xFFFFFFFF:
        raise ImageRefused("That image's signature block is damaged.")
    return version, hashlib.sha256(data).hexdigest(), found.id


# ------------------------------------------------------------------ the store
def directory() -> Path:
    return config.CONFIG_PATH.parent / "panel-firmware"


def _folder(board: str) -> Path:
    if board not in BOARDS:
        raise ValueError(f"unknown board {board!r}")
    return directory() / board


def _image_path(board: str) -> Path:
    return _folder(board) / "panel.bin"


def _meta_path(board: str) -> Path:
    return _folder(board) / "panel.json"


def _migrate() -> None:
    """Move a store from before #122 (`panel-firmware/panel.bin`, the CrowPanel's: it was the only
    board) into the CrowPanel's folder. Nothing to do once it has been moved."""
    old_image, old_meta = directory() / "panel.bin", directory() / "panel.json"
    if not old_meta.exists() and not old_image.exists():
        return
    try:
        folder = _folder(DEFAULT_BOARD)
        if not _meta_path(DEFAULT_BOARD).exists() and old_meta.exists() and old_image.exists():
            folder.mkdir(parents=True, exist_ok=True)
            old_image.replace(_image_path(DEFAULT_BOARD))
            old_meta.replace(_meta_path(DEFAULT_BOARD))
        for path in (old_meta, old_image):
            path.unlink(missing_ok=True)
    except OSError:
        pass


def current(board: str = DEFAULT_BOARD) -> Info | None:
    """The image stored for `board`, or None. A half-written or hand-edited store reads as none."""
    if board not in BOARDS:
        return None
    _migrate()
    try:
        meta = json.loads(_meta_path(board).read_text("utf-8"))
        info = Info(version=str(meta["version"]), size=int(meta["size"]), sha256=str(meta["sha256"]),
                    uploaded_at=float(meta["uploaded_at"]), filename=str(meta.get("filename") or ""),
                    board=board)
        if parse_version(info.version) is None or _image_path(board).stat().st_size != info.size:
            return None
        return info
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(data: bytes, filename: str = "", board: str | None = None) -> Info:
    """Check `data` and keep it as its board's image, replacing the one held. With `board`, an image for
    another board is refused. Raises ImageRefused."""
    version, digest, found = inspect_board(data, board)
    info = Info(version=version, size=len(data), sha256=digest, uploaded_at=time.time(),
                filename="".join(ch for ch in filename if ch.isprintable())[:96], board=found)
    _migrate()
    folder = _folder(found)
    folder.mkdir(parents=True, exist_ok=True)
    # The image first, the description second: `current` reads the description, so a crash between the
    # two leaves the old description, whose size no longer matches, and reads as no image at all.
    image_tmp = folder / "panel.bin.tmp"
    image_tmp.write_bytes(data)
    image_tmp.replace(_image_path(found))
    meta_tmp = folder / "panel.json.tmp"
    meta_tmp.write_text(json.dumps({**asdict(info)}), "utf-8")
    meta_tmp.replace(_meta_path(found))
    return info


def remove(board: str = DEFAULT_BOARD) -> bool:
    """Forget the image stored for `board`. True if there was one."""
    had = current(board) is not None
    if board not in BOARDS:
        return False
    for path in (_meta_path(board), _image_path(board)):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return had


def image_path_for(version: str, board: str = DEFAULT_BOARD) -> Path | None:
    """The image stored for `board` if it is the one at `version`, else None."""
    info = current(board)
    return _image_path(board) if info is not None and info.version == version else None


def image_url(info: Info) -> str:
    """Where a panel fetches `info` on the viewer port."""
    return f"/api/panels/firmware/{info.board}/{info.version}.bin"


# ------------------------------------------------------- the USB flash image
# The browser flasher at `/admin/flash` (copied from VXP's board flasher, #1546 there) writes a new or
# recovered panel's whole flash at 0x0 over USB, from Chrome or Edge, with esptool-js over Web Serial.
# What it writes is the merged image a release attaches (`relay-display.bin`): the bootloader at 0x0,
# the partition table at 0x8000 and the signed application at `APP_OFFSET`. The application image
# above cannot start a blank board, so a board has two files here, kept apart: `panel.bin` is what
# panels are offered over the network, `flash.bin` is never offered to anything.
#
# The merged image is checked by checking the application inside it with `inspect_board`, the same
# check an update's image gets: `build.sh` refuses a merged file whose bytes at `APP_OFFSET` are not
# the signed application exactly, and `merge-bin` does not pad past it, so everything from
# `APP_OFFSET` on is that application. `nvs` sits above both slots (partitions.csv), so the write
# cannot reach a panel's Wi-Fi or settings.

# `app0` in firmware/relay-display/partitions.csv, where a merged image carries the application.
APP_OFFSET = 0x10000
# CONFIG_PARTITION_TABLE_OFFSET's default, and the table's first entry's magic.
_PARTITION_TABLE_OFFSET = 0x8000
_PARTITION_MAGIC = b"\xaa\x50"
FLASH_BYTES = APP_OFFSET + SLOT_BYTES
# The flash a board's module has, which the flasher refuses any other size of.
FLASH_MB = {"crowpanel-adv7": 16, "waveshare-p4-10.1": 16}


def inspect_flash(data: bytes, board: str | None = None) -> tuple[str, str, str]:
    """Check a merged image for the USB flasher. Returns (version, sha256 of the file, board) or raises
    ImageRefused with a sentence for the operator."""
    if len(data) == 0:
        raise ImageRefused("The upload was empty.")
    if len(data) > FLASH_BYTES:
        raise ImageRefused("That file is larger than a panel's bootloader and application slot together.")
    if data[0] == _IMAGE_MAGIC and len(data) >= _HEAD_BYTES and \
            struct.unpack_from("<I", data, _APP_DESC_OFFSET)[0] == _APP_DESC_MAGIC:
        raise ImageRefused(
            "That is the application alone (relay-display-app.bin), which updates a panel that is already "
            "running and cannot start a blank one. Upload relay-display.bin from the same release.")
    if data[0] != _IMAGE_MAGIC or len(data) <= APP_OFFSET or \
            data[_PARTITION_TABLE_OFFSET:_PARTITION_TABLE_OFFSET + 2] != _PARTITION_MAGIC:
        raise ImageRefused("That file is not a panel's USB image. Upload relay-display.bin from the release.")
    try:
        version, _, found = inspect_board(data[APP_OFFSET:], board)
    except ImageRefused as exc:
        # The sentences name the update's file; here the file is the merged one.
        raise ImageRefused(str(exc).replace("relay-display-app.bin", "relay-display.bin")) from None
    return version, hashlib.sha256(data).hexdigest(), found


@dataclass
class FlashInfo:
    version: str
    size: int
    sha256: str
    uploaded_at: float
    filename: str
    board: str = DEFAULT_BOARD

    def public(self) -> dict:
        row = asdict(self)
        row["uploaded_ago_s"] = round(max(0.0, time.time() - self.uploaded_at), 1)
        row["board_label"] = board_label(self.board)
        row.pop("uploaded_at")
        return row

    def manifest(self) -> dict:
        """What the flasher page checks the bytes against before it writes one."""
        return {"version": self.version, "board": self.board, "project": PROJECT_NAME,
                "chip": BOARDS[self.board].chip_name, "flash_mb": FLASH_MB.get(self.board, 16),
                "sha256": self.sha256, "size": self.size, "url": flash_url(self.board)}


def flash_url(board: str) -> str:
    return f"/api/admin/panels/flash/{board}.bin"


def _flash_path(board: str) -> Path:
    return _folder(board) / "flash.bin"


def _flash_meta_path(board: str) -> Path:
    return _folder(board) / "flash.json"


def current_flash(board: str = DEFAULT_BOARD) -> FlashInfo | None:
    """The merged image stored for `board`, or None. A half-written or hand-edited store reads as none."""
    if board not in BOARDS:
        return None
    try:
        meta = json.loads(_flash_meta_path(board).read_text("utf-8"))
        info = FlashInfo(version=str(meta["version"]), size=int(meta["size"]), sha256=str(meta["sha256"]),
                         uploaded_at=float(meta["uploaded_at"]), filename=str(meta.get("filename") or ""),
                         board=board)
        if parse_version(info.version) is None or _flash_path(board).stat().st_size != info.size:
            return None
        return info
    except (OSError, ValueError, KeyError, TypeError):
        return None


def flash_path(board: str) -> Path | None:
    return _flash_path(board) if current_flash(board) is not None else None


def store_flash(data: bytes, filename: str = "", board: str | None = None) -> FlashInfo:
    """Check `data` and keep it as its board's USB image, replacing the one held. Raises ImageRefused."""
    version, digest, found = inspect_flash(data, board)
    info = FlashInfo(version=version, size=len(data), sha256=digest, uploaded_at=time.time(),
                     filename="".join(ch for ch in filename if ch.isprintable())[:96], board=found)
    folder = _folder(found)
    folder.mkdir(parents=True, exist_ok=True)
    # The image first, the description second, for `store`'s reason.
    image_tmp = folder / "flash.bin.tmp"
    image_tmp.write_bytes(data)
    image_tmp.replace(_flash_path(found))
    meta_tmp = folder / "flash.json.tmp"
    meta_tmp.write_text(json.dumps({**asdict(info)}), "utf-8")
    meta_tmp.replace(_flash_meta_path(found))
    return info


def remove_flash(board: str = DEFAULT_BOARD) -> bool:
    """Forget the USB image stored for `board`. True if there was one."""
    if board not in BOARDS:
        return False
    had = current_flash(board) is not None
    for path in (_flash_meta_path(board), _flash_path(board)):
        path.unlink(missing_ok=True)
    return had
