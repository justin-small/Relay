"""The panel's stored configuration, for the emulator.

`main/settings_format.h` is the layout, and this file builds a blob that matches it byte for byte,
so the seeds the emulator boots from are written by the same rules the firmware reads them with.
`build.sh --qemu` runs it, then `nvs_partition_gen` makes each seed into an `nvs` partition image
at the offset `partitions.csv` gives.

Standard library only. `build.sh` runs this under the container's `python3`.

    python3 tests/seeds.py <out-dir>

writes `<out-dir>/seed/<name>.blob` (the blob) and `<out-dir>/seed/<name>.csv` (the
`nvs_partition_gen` input that names it), for every seed in `SEEDS`.

**Why the blob is built here and not in the firmware's test.** The emulated board is seeded, not
provisioned over its own page: the setup network needs a radio the emulator does not have. A seed is
the only way to give the firmware a stored configuration to read back, and the layout has to be the
firmware's, not a copy of it that could drift. `test_seed_layout.py` reads `settings_format.h` and
checks every field's size and the total this file assumes.

Adapted from the VXP compact display's `tests/firmware_compact/seeds.py`.
"""

from __future__ import annotations

import struct
import sys
from pathlib import Path

# `kSettingsMagic` ("RLDP", little-endian) and `kSettingsSchema` in settings_format.h.
MAGIC = 0x50444C52
SCHEMA = 2

# `relay_settings_t`, field by field, in declaration order. The sizes are the array lengths in the
# header, and each text field has room for its terminator.
SSID_BYTES = 33
PASSWORD_BYTES = 65
RELAY_ADDR_BYTES = 129
PANEL_ID_BYTES = 33
FEED_LANG_BYTES = 20

# One struct format, no padding (`<`): the header's layout is pinned by its `static_assert` at 336
# bytes, and every field is a whole number of bytes with no alignment gap, so this format and the C
# struct agree. After the text fields: the address mode, the feed (one byte each), two reserved bytes,
# the feed's language, and five four-byte addresses.
FORMAT = (
    f"<IHH{SSID_BYTES}s{PASSWORD_BYTES}s{RELAY_ADDR_BYTES}s{PANEL_ID_BYTES}s"
    f"BB2x{FEED_LANG_BYTES}s4s4s4s4s4s"
    # Schema 2: the split switch, pane 2's feed, two reserved bytes and pane 2's language.
    f"BB2x{FEED_LANG_BYTES}s"
)
SIZE = 336

FEED_NONE, FEED_TRANSCRIPTION, FEED_TRANSLATION = 0, 1, 2

# A network and panel that exist nowhere: they are the emulator's, not an installation's.
# `tools/image_check.py` refuses an image that carries them, and nothing here is shipped.
PANEL_ID = "0123456789abcdef0123456789abcdef"
PROVISIONED = dict(
    ssid="Relay-QEMU-Net",
    password="relay-qemu-psk",
    relay_addr="http://10.0.2.2:8000",
    panel_id=PANEL_ID,
)


def _text(value: str, field_bytes: int) -> bytes:
    """The firmware's `settings_set_text`: cut to fit, then terminated, then padded."""
    raw = value.encode("ascii")[: field_bytes - 1]
    return raw + b"\x00" * (field_bytes - len(raw))


def _addr(dotted: str) -> bytes:
    """Four bytes for a dotted quad, or four zeros for an empty string (none)."""
    if not dotted:
        return b"\x00\x00\x00\x00"
    return bytes(int(part) for part in dotted.split("."))


def blob(
    *,
    ssid: str = "",
    password: str = "",
    relay_addr: str = "",
    panel_id: str = "",
    rotation: int = 0,
    magic: int = MAGIC,
    schema: int = SCHEMA,
    ip_mode: int = 0,
    feed_stream: int = FEED_NONE,
    feed_lang: str = "",
    feed_split: int = 0,
    feed2_stream: int = FEED_NONE,
    feed2_lang: str = "",
    static: tuple[str, str, str, str, str] = ("", "", "", "", ""),
) -> bytes:
    """A stored blob with these fields, as `settings_save` writes one."""
    data = struct.pack(
        FORMAT,
        magic,
        schema,
        rotation,
        _text(ssid, SSID_BYTES),
        _text(password, PASSWORD_BYTES),
        _text(relay_addr, RELAY_ADDR_BYTES),
        _text(panel_id, PANEL_ID_BYTES),
        ip_mode,
        feed_stream,
        _text(feed_lang, FEED_LANG_BYTES),
        *(_addr(part) for part in static),
        feed_split,
        feed2_stream,
        _text(feed2_lang, FEED_LANG_BYTES),
    )
    assert len(data) == SIZE, f"blob is {len(data)} bytes, expected {SIZE}"
    return data


# Each seed the emulator boots from. The names are the `nvs` images `build.sh` writes, and the tests
# ask for them by name.
SEEDS: dict[str, dict] = {
    # A provisioned board on a network, mirrored: the picture the 180 check reads, and the case where
    # the decision says the station would start. It already has a panel ID and a saved feed.
    "nvs": dict(**PROVISIONED, rotation=180, feed_stream=FEED_TRANSLATION, feed_lang="SPANISH"),
    # A board nobody has configured: no network, upright, and no panel ID yet. The decision says it
    # will not start Wi-Fi, and the boot makes the panel ID.
    "nvs-unprovisioned": dict(rotation=0),
    # A configured board turned upright: the bench picture, upright, for the checks that read it.
    "nvs-upright": dict(**PROVISIONED, rotation=0),
    # A configured board with no panel ID: the boot makes one and saves it.
    "nvs-no-id": dict(ssid=PROVISIONED["ssid"], password=PROVISIONED["password"], rotation=0),
    # The same configuration with a wrong magic. The firmware does not read it.
    "nvs-bad-magic": dict(**PROVISIONED, rotation=180, magic=MAGIC ^ 0x00000001),
    # The same with a schema this firmware does not read.
    "nvs-bad-schema": dict(**PROVISIONED, rotation=180, schema=SCHEMA + 1),
    # A stored rotation that is neither 0 nor 180. It falls back to the configured one, which the
    # emulator build sets to 180 (`sdkconfig.qemu`), so a fallback to 0 is distinguishable from the
    # right answer.
    "nvs-bad-rotation": dict(**PROVISIONED, rotation=7),
}


def write_seeds(out: Path) -> list[Path]:
    """Write every seed's blob and its `nvs_partition_gen` CSV. Returns the CSVs."""
    seed_dir = out / "seed"
    seed_dir.mkdir(parents=True, exist_ok=True)
    csvs = []
    for name, fields in SEEDS.items():
        bin_path = (seed_dir / f"{name}.blob").resolve()
        bin_path.write_bytes(blob(**fields))
        csv = seed_dir / f"{name}.csv"
        # The namespace and key are `settings.cpp`'s `kNamespace` and `kKey`. **The type is `file`,
        # not `data`:** `data` stores the value's own text, so a `binary` data row stores the path,
        # and only `file` reads the bytes at the path. The path is absolute because
        # `nvs_partition_gen` resolves it against its own working directory.
        csv.write_text(f"key,type,encoding,value\nrelay,namespace,,\nconfig,file,binary,{bin_path}\n")
        csvs.append(csv)
    return csvs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: seeds.py <out-dir>", file=sys.stderr)
        return 2
    for csv in write_seeds(Path(argv[1])):
        print(csv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
