"""The caption font covers every character the caption client keeps.

`caption_glyph_kept` (main/caption_format.h) is the set of code points a stored line may hold, and a code
point the font lacks would draw as nothing. The font is two generated files (`tools/gen_caption_font.sh`):
Montserrat, and DejaVu Sans as its fallback for what Montserrat lacks. This reads the glyph markers the
generator leaves in them and compares the union with the kept set, which is parsed from the header so the
two cannot drift apart. It needs no emulator.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "main"


def _glyphs(name: str) -> set[int]:
    text = (ROOT / "fonts" / name).read_text(encoding="utf-8")
    return {int(m, 16) for m in re.findall(r"/\* U\+([0-9A-F]{4,6}) ", text)}


def _kept() -> set[int]:
    """The ranges in `caption_glyph_kept`, read from the source."""
    body = re.search(
        r"caption_glyph_kept\(uint32_t cp\)\s*\{(.*?)\n\}", (ROOT / "caption_format.h").read_text(), re.S
    )
    assert body, "caption_glyph_kept not found"
    kept: set[int] = set()
    for lo, hi in re.findall(r"cp >= (0x[0-9A-Fa-f]+) && cp <= (0x[0-9A-Fa-f]+)", body.group(1)):
        kept.update(range(int(lo, 16), int(hi, 16) + 1))
    for single in re.findall(r"cp == (0x[0-9A-Fa-f]+)", body.group(1)):
        kept.add(int(single, 16))
    kept.discard(0xAD)  # the soft hyphen is excluded in the source and has no glyph
    return kept


def test_the_font_draws_every_kept_character() -> None:
    have = _glyphs("relay_caption_36.c") | _glyphs("relay_caption_36_more.c")
    missing = sorted(_kept() - have)
    assert not missing, "no glyph for " + " ".join(f"U+{c:04X}" for c in missing)


def test_the_vietnamese_letters_come_from_the_main_face() -> None:
    """Stacked diacritics are the case that matters most; they should not come from the fallback."""
    main = _glyphs("relay_caption_36.c")
    vietnamese = set(range(0x1EA0, 0x1EFA)) | {0x0102, 0x0103, 0x00C2, 0x00CA, 0x00D4, 0x01A0, 0x01A1, 0x01AF, 0x01B0, 0x0110, 0x0111}
    assert vietnamese <= main, sorted(f"U+{c:04X}" for c in vietnamese - main)
