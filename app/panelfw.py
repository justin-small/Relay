"""Panel firmware: the one signed image Relay holds for the caption panels.

The operator uploads the application image a release builds (`relay-display-app.bin`,
firmware/relay-display) from the admin page. Relay checks that it is a Relay panel's image at all --
an ESP-IDF application for the ESP32-S3, named `relay-display-app`, with a version it can read, a
whole image whose own checksum holds, and a signature block that belongs to it -- keeps it, and
serves it to the panels on the viewer port.

**Relay does not decide whether a signature is good. The panel does.** Images travel over plain HTTP
on the LAN, so the check that keeps a stranger's image off a panel has to be the panel's, against the
public key it was built with. Relay holds no key, and nothing here is a substitute for that check; it
only turns a file somebody picked by mistake (the merged serial image, another board's, a build that
was never signed, a truncated copy) into a sentence on the admin page instead of a failed update on
a wall.

The image is stored beside config.json (so on the state volume in a container) as `panel-firmware/`:
`panel.bin` and `panel.json`. One image at a time: uploading replaces it.
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
# esp_image_header_t.chip_id for the ESP32-S3.
CHIP_ESP32_S3 = 0x0009

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

    def public(self) -> dict:
        row = asdict(self)
        row["uploaded_ago_s"] = round(max(0.0, time.time() - self.uploaded_at), 1)
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
    saying what is wrong with it."""
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
    if struct.unpack_from("<H", data, 12)[0] != CHIP_ESP32_S3:
        raise ImageRefused("That image is for another chip, not the panel's ESP32-S3.")
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
    return version, hashlib.sha256(data).hexdigest()


# ------------------------------------------------------------------ the store
def directory() -> Path:
    return config.CONFIG_PATH.parent / "panel-firmware"


def _image_path() -> Path:
    return directory() / "panel.bin"


def _meta_path() -> Path:
    return directory() / "panel.json"


def current() -> Info | None:
    """The stored image's description, or None. A half-written or hand-edited store reads as none."""
    try:
        meta = json.loads(_meta_path().read_text("utf-8"))
        info = Info(version=str(meta["version"]), size=int(meta["size"]), sha256=str(meta["sha256"]),
                    uploaded_at=float(meta["uploaded_at"]), filename=str(meta.get("filename") or ""))
        if parse_version(info.version) is None or _image_path().stat().st_size != info.size:
            return None
        return info
    except (OSError, ValueError, KeyError, TypeError):
        return None


def store(data: bytes, filename: str = "") -> Info:
    """Check `data` and keep it as the panels' image, replacing the one held. Raises ImageRefused."""
    version, digest = inspect(data)
    info = Info(version=version, size=len(data), sha256=digest, uploaded_at=time.time(),
                filename="".join(ch for ch in filename if ch.isprintable())[:96])
    folder = directory()
    folder.mkdir(parents=True, exist_ok=True)
    # The image first, the description second: `current` reads the description, so a crash between the
    # two leaves the old description, whose size no longer matches, and reads as no image at all.
    image_tmp = folder / "panel.bin.tmp"
    image_tmp.write_bytes(data)
    image_tmp.replace(_image_path())
    meta_tmp = folder / "panel.json.tmp"
    meta_tmp.write_text(json.dumps({**asdict(info)}), "utf-8")
    meta_tmp.replace(_meta_path())
    return info


def remove() -> bool:
    """Forget the stored image. True if there was one."""
    had = current() is not None
    for path in (_meta_path(), _image_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass
    return had


def image_path_for(version: str) -> Path | None:
    """The stored image's file if it is the one at `version`, else None."""
    info = current()
    return _image_path() if info is not None and info.version == version else None
