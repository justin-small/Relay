"""Allen & Heath I/O patch from an exported show file (#261): Avantis and dLive. The desks' network protocols do not
expose patching, so the operator exports a show (USB, or Director) and Relay reads which socket feeds each input
channel, and what each patched output carries.

A show is a `.tar.gz` with a `Show/` tree. `Show/Version.dat` names the format (Avantis `Bridge-N`, dLive `13` or `14`)
and `Show/Scenes/StageBoxScene65535.tar.gz` holds the live console state: one `.dat` made of labelled sections, each
`u16be length` (label + NUL + payload), the label, a NUL, the payload. Two sections matter here:

* `<Group> Name Colour Manager`: `[version][k x 9-byte name][k colour bytes]`, names Latin-1, NUL terminated (bytes
  after the NUL are leftovers, ignored). An unnamed strip holds its own number as text.
* `Channel Mapper`: records `[type][u16be index]` after a version byte. Avantis (Bridge-2 to Bridge-4): 1830 records,
  0-95 the input sources, then outputs at fixed record numbers (below). dLive: 128 inputs of `[active][A][B][C][D]`.

Layouts come from the published reverse-engineering notes (github.com/michaelkeithlewis/show-file-skills) and were
checked against five real Avantis exports (Bridge-2, Bridge-3 and Bridge-4): the input patch matches the one the desk
shows. The dLive layout has not been checked against a real export here, so its rows say `verified: False`. A file
this module does not know (another version, another record count, another desk) is refused with a sentence, never
guessed at, and reading never writes anything.
"""
from __future__ import annotations

import gzip
import io
import struct
import tarfile
from typing import Any

MAX_UPLOAD = 64 * 1024 * 1024       # the whole archive as uploaded
MAX_UNPACKED = 192 * 1024 * 1024    # what a gzip stream may expand to before we stop reading it
MAX_SCENE = 4 * 1024 * 1024         # one scene archive
MAX_DAT = 8 * 1024 * 1024           # one scene's payload
MODELS = ("avantis", "dlive")      # the desks whose shows are read
SCENE_NAME = "StageBoxScene65535"
AVANTIS_RECORDS = 1830
AVANTIS_VERSIONS = {"Bridge-2", "Bridge-3", "Bridge-4"}
DLIVE_VERSIONS = {"13", "14"}
DLIVE_INPUTS = 128
DLIVE_MAPPER_LEN = 0x288C
NONE_AVANTIS = 0x11
NONE_DLIVE = 0x14

NAME_LABELS = {
    "in": "Input Channel", "group": "Mono Group Channel", "sgroup": "Stereo Group Channel",
    "aux": "Mono Aux Channel", "saux": "Stereo Aux Channel", "main": "Main Channel",
    "matrix": "Mono Matrix Channel", "smatrix": "Stereo Matrix Channel",
}


class ShowFileError(ValueError):
    """A sentence for the operator: why this file cannot be read."""


class _Capped(io.RawIOBase):
    """A gzip stream that stops (with an error) once it has produced more than `limit` bytes: a decompression bomb."""

    def __init__(self, raw: io.BufferedIOBase, limit: int) -> None:
        self.gz = gzip.GzipFile(fileobj=raw)
        self.left = limit

    def readable(self) -> bool:
        return True

    def readinto(self, b: Any) -> int:
        n = self.gz.readinto(b) or 0
        self.left -= n
        if self.left < 0:
            raise ShowFileError("That archive expands to far more than a show file does.")
        return n


def _walk(data: bytes, wanted, *, limit: int = MAX_UNPACKED) -> dict[str, bytes]:
    """Read the files of a `.tar.gz` that `wanted(name, size)` returns a size cap for (or 0 to skip), in memory only:
    nothing is ever extracted to disk, so a hostile path in the archive goes nowhere."""
    found: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BufferedReader(_Capped(io.BytesIO(data), limit)), mode="r|") as tf:
            for m in tf:
                if not m.isfile():
                    continue
                name = m.name.lstrip("./")
                cap = wanted(name, m.size)
                if cap and m.size <= cap:
                    f = tf.extractfile(m)
                    if f is not None:
                        found[name] = f.read(cap + 1)
    except ShowFileError:
        raise
    except (tarfile.TarError, OSError, EOFError, ValueError, struct.error):
        raise ShowFileError("That is not a show file: it is not a .tar.gz archive Relay can read.") from None
    return found


# -- sections ----------------------------------------------------------------------------------------------------------
def section(dat: bytes, label: str) -> bytes | None:
    """The payload of the section called `label`, or None. The u16be before the label counts label + NUL + payload."""
    key = label.encode("ascii") + b"\0"
    at = dat.find(key)
    while at >= 2:
        n = struct.unpack(">H", dat[at - 2:at])[0]
        if len(key) <= n and at - 2 + 2 + n <= len(dat):
            return dat[at + len(key):at + n]
        at = dat.find(key, at + 1)
    return None


def names(dat: bytes, group: str) -> list[str]:
    """The strip names of a group: 9-byte Latin-1 slots. [] when the group is not in the file."""
    p = section(dat, f"{NAME_LABELS[group]} Name Colour Manager")
    if p is None or len(p) < 11 or (len(p) - 1) % 10:
        return []
    out = []
    for k in range((len(p) - 1) // 10):
        raw = p[1 + 9 * k:10 + 9 * k]
        out.append(raw.split(b"\0", 1)[0].decode("latin-1").strip())
    return out


def _name(table: list[str], n: int) -> str:
    """Strip `n` (1-based) named, or "" when it holds only its own number (what an unnamed strip stores)."""
    got = table[n - 1] if 0 < n <= len(table) else ""
    return "" if got == str(n) else got


# -- Avantis -----------------------------------------------------------------------------------------------------------
def _avantis_source(t: int, i: int) -> dict:
    """An Avantis input source: type + index to what an engineer would call it."""
    if t == 0x00:
        if i < 12:
            return _src("local", None, i + 1, f"Local XLR {i + 1}")
        return _src("local", None, i + 1, f"Local AES {'L' if i == 12 else 'R'}" if i < 14 else f"Local {i + 1}")
    if t in (0x01, 0x02):
        return _src("io_port", t, i + 1, f"I/O Port {t} socket {i + 1}")
    if t == 0x03:
        return _src("slink", None, i + 1, f"SLink socket {i + 1}")
    if t == 0x25:
        return _src("usb", None, i, f"USB {i}")
    if t == 0x26:
        return _src("generator", None, i, "Signal generator")
    return _src("other", None, i, f"Source type 0x{t:02X} index {i}")


def _src(kind: str, port: int | None, socket: int, label: str) -> dict:
    return {"kind": kind, "port": port, "socket": socket, "label": label}


def _stereo(i: int) -> tuple[int, str]:
    return i // 2 + 1, "L" if i % 2 == 0 else "R"


def _bus_label(t: int, i: int, tables: dict[str, list[str]]) -> str:
    """What an Avantis output record plays: a bus (with its name), an input's direct out, PAFL, and so on."""
    def named(base: str, n: int, table: str, leg: str = "") -> str:
        nm = _name(tables.get(table, []), n)
        return f"{base} {n}{' ' + leg if leg else ''}" + (f" ({nm})" if nm else "")

    if t == 0x09:
        return named("Mono Group", i + 1, "group")
    if t == 0x0A:
        n, leg = _stereo(i)
        return named("Stereo Group", n, "sgroup", leg)
    if t == 0x0B:
        return named("Mono Aux", i + 1, "aux")
    if t == 0x0C:
        n, leg = _stereo(i)
        return named("Stereo Aux", n, "saux", leg)
    if t == 0x0D:
        return {0: "Main L", 1: "Main R", 2: "Main M"}.get(i, f"Main {i}")
    if t == 0x0E:
        return named("Mono Matrix", i + 1, "matrix")
    if t == 0x0F:
        n, leg = _stereo(i)
        return named("Stereo Matrix", n, "smatrix", leg)
    if t == 0x04:
        return named("Direct out of input", i + 1, "in")
    if t in (0x05, 0x06):
        return named(f"Insert {t - 4} send of input", i + 1, "in")
    if t == 0x10:
        return f"PAFL {'2 ' if i >= 2 else ''}{'LR'[i % 2]}"
    if t == 0x1B:
        return f"Monitor bus {i + 1}"
    if t == 0x19:
        return f"FX rack output {i + 1}"
    return f"type 0x{t:02X} index {i}"


# Output destinations: (record of socket 1, sockets, name). Record N of the first = base + N, so Local out 1 is 1061.
AVANTIS_OUTPUTS = (
    ("local", 1061, 16, "Local out"),
    ("slink", 1077, 128, "SLink out"),
    ("io_port", 1242, 128, "I/O Port 1 out"),
    ("io_port", 1370, 128, "I/O Port 2 out"),
    ("me", 1498, 40, "ME out"),
)


def _avantis(dat: bytes, version: str) -> dict:
    p = section(dat, "Channel Mapper")
    if p is None:
        raise ShowFileError("This scene has no Channel Mapper section, so there is no patch to read.")
    if len(p) != 1 + 3 * AVANTIS_RECORDS:
        raise ShowFileError(f"This Avantis show ({version}) stores {(len(p) - 1) // 3} patch records, not the "
                            f"{AVANTIS_RECORDS} Relay knows. It is probably from newer firmware: nothing was read.")
    rec = [(p[1 + 3 * k], (p[2 + 3 * k] << 8) | p[3 + 3 * k]) for k in range(AVANTIS_RECORDS)]
    tables = {g: names(dat, g) for g in NAME_LABELS}
    inputs = []
    for n in range(1, 97):
        t, i = rec[n - 1]
        if t == NONE_AVANTIS:
            continue
        nm = _name(tables["in"], n)
        inputs.append({"channel": n, "name": nm or str(n), "named": bool(nm), "source": _avantis_source(t, i)})
    outputs = []
    for kind, first, count, label in AVANTIS_OUTPUTS:
        port = 1 if "Port 1" in label else 2 if "Port 2" in label else None
        for s in range(1, count + 1):
            t, i = rec[first + s - 1]
            if t == NONE_AVANTIS:
                continue
            outputs.append({"destination": f"{label} {s}", "kind": kind, "port": port, "socket": s,
                            "carries": _bus_label(t, i, tables)})
    return {"family": "avantis", "firmware": version, "verified": True, "inputs": inputs, "outputs": outputs}


# -- dLive -------------------------------------------------------------------------------------------------------------
def _dlive_source(t: int, i: int) -> dict:
    if t == 0x00:
        return _src("stagebox", None, i + 1, f"MixRack socket {i + 1}")
    if t == 0x01:
        if i < 12:
            return _src("local", None, i + 1, f"Surface local {i + 1}")
        if i < 44:
            return _src("stagebox", None, i - 11, f"Surface DX 5/6 socket {i - 11}")
        if i < 172:
            return _src("io_port", 4, i - 43, f"Surface I/O Port 4 socket {i - 43}")
        if i < 300:
            return _src("io_port", 5, i - 171, f"Surface I/O Port 5 socket {i - 171}")
        return _src("usb", None, i - 299, f"USB {i - 299}")
    if 0x02 <= t <= 0x04:
        return _src("io_port", t - 1, i + 1, f"MixRack I/O Port {t - 1} socket {i + 1}")
    if t in (0x05, 0x06):
        return _src("stagebox", None, i + 1, f"DX {1 if t == 5 else 3}/{2 if t == 5 else 4} socket {i + 1}")
    if t == 0x2A:
        return _src("generator", None, i, "Signal generator")
    return _src("other", None, i, f"Source type 0x{t:02X} index {i}")


def _dlive(dat: bytes, version: str) -> dict:
    p = section(dat, "Channel Mapper")
    if p is None:
        raise ShowFileError("This scene has no Channel Mapper section, so there is no patch to read.")
    if len(p) != DLIVE_MAPPER_LEN or p[0] != 6:
        raise ShowFileError(f"This dLive show ({version}) has a patch section Relay does not know (version "
                            f"{p[0] if p else '?'}, {len(p)} bytes): nothing was read.")
    table = names(dat, "in")
    inputs = []
    for n in range(1, DLIVE_INPUTS + 1):
        at = 1 + 13 * (n - 1)
        active = p[at]
        refs = [(p[at + 1 + 3 * k], (p[at + 2 + 3 * k] << 8) | p[at + 3 + 3 * k]) for k in range(4)]
        t, i = refs[active - 1 if 1 <= active <= 4 else 0]
        if t == NONE_DLIVE:
            continue
        nm = _name(table, n)
        inputs.append({"channel": n, "name": nm or str(n), "named": bool(nm), "source": _dlive_source(t, i)})
    return {"family": "dlive", "firmware": version, "verified": False, "inputs": inputs, "outputs": []}


# -- entry -------------------------------------------------------------------------------------------------------------
def _scene(data: bytes) -> bytes:
    got = _walk(data, lambda n, size: MAX_DAT if n.endswith(".dat") else 0, limit=MAX_DAT * 2)
    if not got:
        raise ShowFileError("That scene archive holds no scene data.")
    return next(iter(got.values()))


def read(data: bytes) -> dict:
    """The patch of an uploaded show, as `{family, firmware, verified, inputs, outputs}`; ShowFileError if it is not
    one Relay can read. Takes the whole show, a `.tar.gz` with `Show/Version.dat` in it."""
    if not data:
        raise ShowFileError("Nothing was sent.")
    if len(data) > MAX_UPLOAD:
        raise ShowFileError("That file is too large to be a show.")
    if data[:2] != b"\x1f\x8b":
        raise ShowFileError("That is not a show file: Allen & Heath shows are .tar.gz archives. Zip files and Qu "
                            "shows are not supported yet.")

    def wanted(name: str, size: int) -> int:
        if name == "Show/Version.dat":
            return 64
        if name == f"Show/Scenes/{SCENE_NAME}.tar.gz":
            return MAX_SCENE
        if name.endswith(f"{SCENE_NAME}.dat"):
            return MAX_DAT
        return 0

    files = _walk(data, wanted)
    version = files["Show/Version.dat"].decode("ascii", "ignore").strip() if "Show/Version.dat" in files else ""
    if f"Show/Scenes/{SCENE_NAME}.tar.gz" in files:
        dat = _scene(files[f"Show/Scenes/{SCENE_NAME}.tar.gz"])
    else:
        dat = next((v for k, v in files.items() if k.endswith(".dat") and SCENE_NAME in k), None)
        if dat is None and files.get("Show/Version.dat") is not None:
            raise ShowFileError("This show has no live scene (StageBoxScene65535), so there is no patch to read. "
                                "Save the show again on the desk and export it.")
        if dat is None:
            raise ShowFileError("That archive is not an Avantis or dLive show: it has no Show/Version.dat. Qu shows "
                                "are not supported yet.")
    if len(dat) > MAX_DAT:
        raise ShowFileError("That scene is too large to be a show's.")
    if version in AVANTIS_VERSIONS:
        return _avantis(dat, version)
    if version in DLIVE_VERSIONS:
        return _dlive(dat, version)
    if version.startswith("Bridge-"):
        raise ShowFileError(f"This Avantis show is format {version}, which Relay has not been checked against "
                            f"(it reads {', '.join(sorted(AVANTIS_VERSIONS))}). Nothing was read.")
    if not version:
        raise ShowFileError("A scene file alone does not say which desk it is from. Upload the whole show "
                            "(the .tar.gz with Show/Version.dat in it).")
    raise ShowFileError(f"Show format {version[:20]!r} is not one Relay reads (Avantis Bridge-2 to Bridge-4, "
                        "dLive 13 and 14). Nothing was read.")


# -- what is kept ------------------------------------------------------------------------------------------------------
def _s(v: Any, n: int = 80) -> str:
    return v[:n] if isinstance(v, str) else ""


def _n(v: Any, hi: int) -> int:
    return v if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= hi else 0


def clean_patch(raw: Any) -> dict | None:
    """A stored patch read back from the config file: the same shape `read` makes, bounded, or None."""
    if not isinstance(raw, dict) or raw.get("family") not in ("avantis", "dlive"):
        return None
    ins, outs = [], []
    for r in (raw.get("inputs") if isinstance(raw.get("inputs"), list) else [])[:DLIVE_INPUTS]:
        s = r.get("source") if isinstance(r, dict) and isinstance(r.get("source"), dict) else None
        if s is not None:
            ins.append({"channel": _n(r.get("channel"), 512), "name": _s(r.get("name"), 16), "named": bool(r.get("named")),
                        "source": {"kind": _s(s.get("kind"), 16), "port": s["port"] if _n(s.get("port"), 9) else None,
                                   "socket": _n(s.get("socket"), 4096), "label": _s(s.get("label"))}})
    for r in (raw.get("outputs") if isinstance(raw.get("outputs"), list) else [])[:512]:
        if isinstance(r, dict):
            outs.append({"destination": _s(r.get("destination")), "kind": _s(r.get("kind"), 16),
                         "port": r["port"] if _n(r.get("port"), 9) else None, "socket": _n(r.get("socket"), 4096),
                         "carries": _s(r.get("carries"))})
    return {"family": raw["family"], "firmware": _s(raw.get("firmware"), 20), "verified": bool(raw.get("verified")),
            "file": _s(raw.get("file"), 120), "read_at": raw["read_at"] if isinstance(raw.get("read_at"), (int, float))
            and not isinstance(raw.get("read_at"), bool) else 0, "inputs": ins, "outputs": outs}
