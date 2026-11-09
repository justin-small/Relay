"""The seed generator's layout is the firmware's: every size and the total are read from
`main/settings_format.h`, so a change to the struct that forgets `seeds.py` fails here, on a laptop,
with no emulator.

This file runs without QEMU or a build; it is not skipped by the suite's `--firmware` gate.
"""

from __future__ import annotations

import re
import struct
from pathlib import Path

import seeds

HEADER = Path(__file__).resolve().parents[1] / "main" / "settings_format.h"


def _header() -> str:
    return HEADER.read_text()


def _array(name: str) -> int:
    match = re.search(rf"\bchar {name}\[(\d+)\]", _header())
    assert match, f"{name} not found in settings_format.h"
    return int(match.group(1))


def test_the_text_fields_are_the_sizes_the_seed_packs() -> None:
    assert _array("wifi_ssid") == seeds.SSID_BYTES
    assert _array("wifi_password") == seeds.PASSWORD_BYTES
    assert _array("relay_addr") == seeds.RELAY_ADDR_BYTES
    assert _array("panel_id") == seeds.PANEL_ID_BYTES
    assert _array("feed_lang") == seeds.FEED_LANG_BYTES
    assert _array("feed2_lang") == seeds.FEED_LANG_BYTES


def test_the_total_is_the_firmwares_static_assert() -> None:
    match = re.search(r"static_assert\(sizeof\(relay_settings_t\) == (\d+)", _header())
    assert match
    assert int(match.group(1)) == seeds.SIZE == struct.calcsize(seeds.FORMAT)


def test_the_magic_and_schema_are_the_firmwares() -> None:
    header = _header()
    magic = re.search(r"kSettingsMagic = (0x[0-9A-Fa-f]+)", header)
    schema = re.search(r"kSettingsSchema = (\d+)", header)
    assert magic and schema
    assert int(magic.group(1), 16) == seeds.MAGIC
    assert int(schema.group(1)) == seeds.SCHEMA
    # "RLDP", little-endian in the blob.
    assert struct.pack("<I", seeds.MAGIC) == b"RLDP"


def test_the_feed_codes_are_the_firmwares() -> None:
    header = _header()
    for name, value in (
        ("kFeedNone", seeds.FEED_NONE),
        ("kFeedTranscription", seeds.FEED_TRANSCRIPTION),
        ("kFeedTranslation", seeds.FEED_TRANSLATION),
    ):
        match = re.search(rf"{name} = (\d+)", header)
        assert match and int(match.group(1)) == value, name


def test_every_seed_packs_to_the_right_size() -> None:
    for name, fields in seeds.SEEDS.items():
        assert len(seeds.blob(**fields)) == seeds.SIZE, name
