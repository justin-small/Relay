"""Allen & Heath over MIDI on TCP 51325 (#150): Avantis, dLive, GLD and Qu share one codec, the SQ has its own.

From Allen & Heath's published MIDI protocols and the Companion modules for Avantis, SQ and GLD (MIT):

* **Avantis, dLive, GLD, Qu.** A mute is Note On with velocity 0x7F (muted) or 0x3F (unmuted), followed by the same
  note at velocity 0. A fader is NRPN: `BN 63 <ch> BN 62 17 BN 06 <level>`, where level 0x6B is 0 dB and each step is
  half a dB (0x00 is off). Names and colours are SysEx: header, MIDI channel, then `03 <ch> name` to set, `01 <ch>` to
  ask, `02 <ch> name` the answer, and `06`/`04`/`05` for colours. A scene is `BN 00 <bank> CN <program>`. Avantis and
  dLive spread strips over the base channel and the four after it; GLD and Qu use one channel.
* **SQ.** Everything is NRPN with a 14-bit value (`BN 63 MSB BN 62 LSB BN 06 VC BN 26 VF`), and `BN 60 7F` after an
  address asks the desk for its value. The level law is Allen & Heath's "linear taper". The SQ has no names or colours
  over MIDI.
* **AH-Net** (Avantis, port 51321): Allen & Heath's own, undocumented protocol, used only when the operator turns it
  on, to read every input fader and the scene names, which MIDI does not give. It may change with new firmware.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

from .models import OFF_DB, Model

SYSEX = bytes([0xF0, 0x00, 0x00, 0x1A, 0x50, 0x10, 0x01, 0x00])
SYSEX_QU = bytes([0xF0, 0x00, 0x00, 0x1A, 0x50, 0x11, 0x01, 0x00])
COLOURS = ("off", "red", "green", "yellow", "blue", "purple", "cyan", "white")


# -- the MIDI byte stream ----------------------------------------------------------------------------------------
class MidiParser:
    """Turns a TCP byte stream into events, keeping running status and partial messages across reads:
    ("note", ch, note, velocity), ("cc", ch, controller, value), ("pc", ch, program), ("sysex", bytes)."""

    MAX_SYSEX = 512

    def __init__(self) -> None:
        self.buf = bytearray()
        self.status = 0

    def feed(self, data: bytes) -> Iterator[tuple]:
        self.buf += data
        b = self.buf
        i = 0
        while i < len(b):
            x = b[i]
            if x == 0xF0:
                end = b.find(0xF7, i + 1)
                if end < 0:
                    if len(b) - i > self.MAX_SYSEX:
                        i = len(b)          # junk that never ends: drop it
                    break
                yield ("sysex", bytes(b[i:end + 1]))
                i = end + 1
                continue
            if x >= 0xF0:                  # real-time and other system bytes: not ours
                i += 1
                continue
            start = i
            if x & 0x80:
                st = x
                i += 1
            elif self.status:
                st = self.status           # running status
            else:
                i += 1
                continue
            self.status = st
            kind = st & 0xF0
            need = 1 if kind in (0xC0, 0xD0) else 2
            if i + need > len(b):
                i = start                  # wait for the rest
                break
            d = b[i:i + need]
            cut = next((k for k, v in enumerate(d) if v & 0x80), None)
            if cut is not None:            # cut short by a new status byte: read that one next
                i += cut
                continue
            i += need
            ch = st & 0x0F
            if kind == 0x90:
                yield ("note", ch, d[0], d[1])
            elif kind == 0xB0:
                yield ("cc", ch, d[0], d[1])
            elif kind == 0xC0:
                yield ("pc", ch, d[0])
        del b[:i]


def ah_level(db: float) -> int:
    """dB to the 7-bit fader value of Avantis, dLive, GLD and Qu (0x6B is 0 dB, two steps a dB)."""
    if db <= -54.0:
        return 0
    return max(1, min(0x7F, round(107 + 2 * db)))


def ah_db(value: int) -> float:
    return OFF_DB if value <= 0 else (value - 107) / 2


# -- Avantis, dLive, GLD, Qu ---------------------------------------------------------------------------------------
@dataclass(frozen=True)
class Address:
    channel: int          # MIDI channel (0-15), the base already added
    number: int           # the note / NRPN channel byte


class AH:
    """Encode and decode for one Avantis, dLive, GLD or Qu desk on base MIDI channel `base` (1-16)."""

    def __init__(self, model: Model, base: int) -> None:
        self.model = model
        self.base = max(0, min(15, base - 1))
        self.split = model.id in ("avantis", "dlive")       # strips over five MIDI channels
        self.header = SYSEX_QU if model.id.startswith("qu") else SYSEX
        self._back: dict[tuple[int, int], str] = {}
        for kind in ("in", "mix", "main", "dca"):
            for n in range(1, model.count(kind) + 1):
                a = self.address(kind, n)
                if a is not None:
                    self._back[(a.channel, a.number)] = f"{kind}{n}"

    def address(self, kind: str, n: int) -> Address | None:
        b, i = self.base, n - 1
        if self.split:
            got = {"in": (0, i), "mix": (2, i), "main": (4, 0x30 + i), "dca": (4, 0x36 + i)}.get(kind)
        elif self.model.id == "gld":
            got = {"in": (0, 0x20 + i), "mix": (0, 0x60 + i), "dca": (0, 0x10 + i)}.get(kind)
        else:                                                   # Qu
            got = {"in": (0, 0x20 + i), "mix": (0, 0x60 + i), "main": (0, 0x67), "dca": (0, 0x10 + i)}.get(kind)
        if got is None or b + got[0] > 15:
            return None
        return Address(b + got[0], got[1])

    def strip_at(self, channel: int, number: int) -> str | None:
        return self._back.get((channel, number))

    # commands
    def mute(self, a: Address, on: bool) -> bytes:
        st = 0x90 | a.channel
        return bytes([st, a.number, 0x7F if on else 0x3F, st, a.number, 0x00])

    def fader(self, a: Address, db: float) -> bytes:
        cc = 0xB0 | a.channel
        return bytes([cc, 0x63, a.number, cc, 0x62, 0x17, cc, 0x06, ah_level(db)])

    def name(self, a: Address, name: str) -> bytes:
        return self.header + bytes([a.channel, 0x03, a.number]) + name.encode("ascii", "ignore")[:8] + b"\xF7"

    def colour(self, a: Address, colour: str) -> bytes:
        c = COLOURS.index(colour) if colour in COLOURS else 0
        return self.header + bytes([a.channel, 0x06, a.number, c, 0xF7])

    def ask_name(self, a: Address) -> bytes:
        return self.header + bytes([a.channel, 0x01, a.number, 0xF7])

    def ask_colour(self, a: Address) -> bytes:
        return self.header + bytes([a.channel, 0x04, a.number, 0xF7])

    def scene(self, number: int) -> bytes:
        s = number - 1
        return bytes([0xB0 | self.base, 0x00, (s >> 7) & 0x7F, 0xC0 | self.base, s & 0x7F])

    def send(self, inp: int, mix: int, db: float | None) -> bytes | None:
        """An input's send to a mix (None: off, as -inf). Avantis and dLive use SysEx 0D; GLD an NRPN; Qu has none here."""
        a = self.address("in", inp)
        if a is None or not 1 <= mix <= self.model.count("mix"):
            return None
        level = 0 if db is None else ah_level(db)
        if self.split:
            dest = self.address("mix", mix)
            return self.header + bytes([a.channel, 0x0D, a.number, dest.channel, dest.number, level, 0xF7]) if dest else None
        if self.model.id == "gld":
            cc = 0xB0 | a.channel
            return bytes([cc, 0x63, a.number, cc, 0x62, 0x20 + mix - 1, cc, 0x06, level])
        return None

    # what the desk says
    def sysex(self, frame: bytes) -> tuple[str, str, object] | None:
        """("name" | "colour", strip key, value) for a name or colour answer; None for anything else."""
        h = len(self.header)
        if len(frame) < h + 4 or frame[:h] != self.header or frame[-1] != 0xF7:
            return None
        ch, cmd, num = frame[h], frame[h + 1], frame[h + 2]
        key = self.strip_at(ch, num)
        if key is None:
            return None
        body = frame[h + 3:-1]
        if cmd == 0x02:
            return "name", key, body.decode("ascii", "replace").rstrip("\0 ")
        if cmd == 0x05 and len(body) == 1 and body[0] < len(COLOURS):
            return "colour", key, COLOURS[body[0]]
        return None


# -- SQ -----------------------------------------------------------------------------------------------------------
SQ_MUTE = {"in": (0x00, 0x00), "main": (0x00, 0x44), "mix": (0x00, 0x45), "dca": (0x02, 0x00)}
SQ_LEVEL = {"in": (0x40, 0x00), "main": (0x4F, 0x00), "mix": (0x4F, 0x01), "dca": (0x4F, 0x20)}
SQ_SEND_LEVEL = (0x40, 0x44)          # input -> mix, 12 mixes a row
SQ_SEND_ASSIGN = (0x60, 0x44)
SQ_MIXES = 12


def _param(base: tuple[int, int], offset: int) -> tuple[int, int]:
    p = (base[0] << 7 | base[1]) + offset
    return (p >> 7) & 0x7F, p & 0x7F


def sq_level(db: float) -> tuple[int, int]:
    if db <= -89.9:
        return 0, 0
    v = int(15196 + min(10.0, db) * 118.775)
    return (v >> 7) & 0x7F, v & 0x7F


def sq_db(vc: int, vf: int) -> float:
    v = (vc << 7) | vf
    db = (v - 15196) / 118.775
    return OFF_DB if db < -89 else round(min(10.0, db), 1)


class SQ:
    def __init__(self, model: Model, base: int) -> None:
        self.model = model
        self.ch = max(0, min(15, base - 1))
        self._back: dict[tuple[int, int], tuple[str, str]] = {}       # param -> ("mute" | "level", strip)
        self._sends: dict[tuple[int, int], tuple[str, int, int]] = {}  # param -> ("level" | "on", input, mix)
        for kind in ("in", "mix", "main", "dca"):
            for n in range(1, model.count(kind) + 1):
                self._back[_param(SQ_MUTE[kind], n - 1)] = ("mute", f"{kind}{n}")
                self._back[_param(SQ_LEVEL[kind], n - 1)] = ("level", f"{kind}{n}")
        for i in range(1, model.count("in") + 1):
            for m in range(1, model.count("mix") + 1):
                off = (i - 1) * SQ_MIXES + m - 1
                self._sends[_param(SQ_SEND_LEVEL, off)] = ("level", i, m)
                self._sends[_param(SQ_SEND_ASSIGN, off)] = ("on", i, m)

    def _nrpn(self, p: tuple[int, int], vc: int, vf: int) -> bytes:
        b = 0xB0 | self.ch
        return bytes([b, 0x63, p[0], b, 0x62, p[1], b, 0x06, vc, b, 0x26, vf])

    def _get(self, p: tuple[int, int]) -> bytes:
        b = 0xB0 | self.ch
        return bytes([b, 0x63, p[0], b, 0x62, p[1], b, 0x60, 0x7F])

    def mute(self, kind: str, n: int, on: bool) -> bytes:
        return self._nrpn(_param(SQ_MUTE[kind], n - 1), 0, 1 if on else 0)

    def fader(self, kind: str, n: int, db: float) -> bytes:
        return self._nrpn(_param(SQ_LEVEL[kind], n - 1), *sq_level(db))

    def send(self, inp: int, mix: int, db: float | None) -> bytes:
        off = (inp - 1) * SQ_MIXES + mix - 1
        return (self._nrpn(_param(SQ_SEND_ASSIGN, off), 0, 0 if db is None else 1)
                + (self._nrpn(_param(SQ_SEND_LEVEL, off), *sq_level(db)) if db is not None else b""))

    def scene(self, number: int) -> bytes:
        s = number - 1
        return bytes([0xB0 | self.ch, 0x00, (s >> 7) & 0x0F, 0xC0 | self.ch, s & 0x7F])

    def ask_all(self) -> bytes:
        """Get requests for every mute and level, and each input's sends: the SQ answers each with its value."""
        out = bytearray()
        for p in list(self._back) + list(self._sends):
            out += self._get(p)
        return bytes(out)

    def value(self, msb: int, lsb: int, vc: int, vf: int) -> tuple[str, str, object] | None:
        """("mute" | "level", strip, value) or ("send", "in<n>", (mix, on, dB)) for one NRPN the desk sent."""
        got = self._back.get((msb, lsb))
        if got:
            what, key = got
            return (what, key, bool(vf & 1)) if what == "mute" else (what, key, sq_db(vc, vf))
        s = self._sends.get((msb, lsb))
        if s:
            what, i, m = s
            return ("send", f"in{i}", (m, bool(vf & 1), None) if what == "on" else (m, None, sq_db(vc, vf)))
        return None


class NrpnState:
    """Collects one NRPN at a time per MIDI channel from CC 63 / 62 / 06 / 26."""

    def __init__(self) -> None:
        self.msb: dict[int, int] = {}
        self.lsb: dict[int, int] = {}
        self.vc: dict[int, int] = {}

    def cc(self, ch: int, ctl: int, val: int, fine: bool) -> tuple[int, int, int, int] | None:
        """(msb, lsb, vc, vf) when a value is complete. `fine`: the value ends with CC 26 (SQ); else CC 06 ends it."""
        if ctl == 0x63:
            self.msb[ch] = val
        elif ctl == 0x62:
            self.lsb[ch] = val
        elif ctl == 0x06:
            self.vc[ch] = val
            if not fine and ch in self.msb and ch in self.lsb:
                return self.msb[ch], self.lsb[ch], val, 0
        elif ctl == 0x26 and fine and ch in self.msb and ch in self.lsb and ch in self.vc:
            return self.msb[ch], self.lsb[ch], self.vc[ch], val
        return None


# -- AH-Net (Avantis only, optional) -----------------------------------------------------------------------------------
AHNET_PORT = 51321
AHNET_RENEW_S = 24.0                       # the console closes a quiet session after about 30 s
_FADER_HANDLE = 0x2000
_SCENE_DIR = 0x000A


def ahnet_hello(token: bytes = b"\x52\x4C") -> bytes:
    """Open the control session and identify as a surface, as Allen & Heath's Director does."""
    return (bytes([0xE0, 0x00, 0x04, 0x01, 0x03, token[0], token[1], 0xE7])
            + bytes.fromhex("f00001000000080004001753757266616365204964656e74696669636174696f6e00f7"))


def ahnet_hello_ok(frame: bytes) -> bool:
    return len(frame) == 8 and frame[:5] == bytes([0xE0, 0x00, 0x04, 0x02, 0x03]) and frame[7] == 0xE7


def ahnet_fader_subscribe(inp: int) -> bytes:
    h = _FADER_HANDLE + inp - 1
    return bytes([0xF0, 0x00, 0x01, 0x00, 0x28, 0x00, 0x03, 0x00, 0x06, 0x00, 0x0A, 0x00, 0x00, (h >> 8) & 0xFF,
                  h & 0xFF, 0x10, 0x00, 0x2B, inp - 1, 0x00, 0xFF, 0xF7])


def ahnet_fader(frame: bytes) -> tuple[int, float] | None:
    """(input number, dB) for a subscribed input fader's value."""
    if (len(frame) != 14 or frame[0] != 0xF0 or frame[1] != 0x00 or not 1 <= frame[2] <= 5
            or frame[5:11] != bytes([0x00, 0x28, 0x10, 0x00, 0x00, 0x02]) or frame[13] != 0xF7):
        return None
    inp = ((frame[3] << 8) | frame[4]) - _FADER_HANDLE + 1
    if not 1 <= inp <= 128:
        return None
    db = (((frame[11] << 8) | frame[12]) - 0x8000) / 256
    return inp, OFF_DB if db <= OFF_DB else round(db, 2)


def ahnet_scene_ask(index: int) -> bytes:
    return bytes([0xF0, 0x00, 0x01, 0x00, 0x0C, (_SCENE_DIR >> 8) & 0xFF, _SCENE_DIR & 0xFF, 0x01, 0x04, 0x00, 0x02,
                  (index >> 8) & 0xFF, index & 0xFF, 0xF7])


def ahnet_scene_name(frame: bytes) -> str | None:
    if (len(frame) < 13 or frame[0] != 0xF0 or frame[1] != 0x00 or not 1 <= frame[2] <= 5
            or frame[3:11].hex() != "000a000c00020148" or frame[-1] != 0xF7):
        return None
    end = 11
    while end < len(frame) - 1 and frame[end] not in (0x00, 0xFF):
        end += 1
    return frame[11:end].decode("utf-8", "replace").strip()


def ahnet_frames(buf: bytearray) -> Iterator[bytes]:
    """Session (0xE0, 8 bytes) and object (0xF0 ... 0xF7) frames out of the AH-Net stream."""
    while buf:
        starts = [i for i in (buf.find(0xE0), buf.find(0xF0)) if i >= 0]
        if not starts:
            buf.clear()
            return
        del buf[:min(starts)]
        if buf[0] == 0xE0:
            if len(buf) < 8:
                return
            yield bytes(buf[:8])
            del buf[:8]
            continue
        end = buf.find(0xF7, 1)
        if end < 0:
            if len(buf) > 4096:
                buf.clear()
            return
        yield bytes(buf[:end + 1])
        del buf[:end + 1]
