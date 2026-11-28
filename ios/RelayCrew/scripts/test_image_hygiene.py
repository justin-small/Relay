#!/usr/bin/env python3
"""Self-test for the image-hygiene tools.

Builds a throwaway fixture project that exercises every realistic duplicate /
unused-image case, runs find_duplicate_images.py and find_unused_images.py
against it, and asserts the reports flag exactly the expected items.

Run locally:  FENGNIAO=~/.mint/bin/fengniao python3 scripts/test_image_hygiene.py
Exit code is non-zero if any case fails (so it can gate CI).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
PY = sys.executable
FENGNIAO = os.environ.get("FENGNIAO", "fengniao")

results: list[tuple[bool, str]] = []


def check(cond: bool, msg: str) -> None:
    results.append((bool(cond), msg))


def solid(path: Path, size, color, mode="RGB", **save_kwargs) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, color).save(path, **save_kwargs)


def contents_json(imageset: Path, filenames: list[str]) -> None:
    images = [{"filename": fn, "idiom": "universal",
               "scale": f"{i + 1}x"} for i, fn in enumerate(filenames)]
    (imageset / "Contents.json").write_text(json.dumps(
        {"images": images, "info": {"author": "xcode", "version": 1}}, indent=2))


# --------------------------------------------------------------------------- #
# Duplicate-image fixtures + assertions
# --------------------------------------------------------------------------- #
def test_duplicates(base: Path) -> None:
    root = base / "dup"
    cat = root / "Assets.xcassets"

    # D1 + D5: byte-identical across two imagesets AND a loose copy.
    x = (37, 90, 12)
    solid(cat / "logo_a.imageset" / "logo_a.png", (16, 16), x)
    contents_json(cat / "logo_a.imageset", ["logo_a.png"])
    solid(cat / "logo_b.imageset" / "logo_b.png", (16, 16), x)
    contents_json(cat / "logo_b.imageset", ["logo_b.png"])
    solid(root / "Resources" / "loose_logo.png", (16, 16), x)

    # D2: same pixels, DIFFERENT png encoding (compress level) -> byte hash
    # would miss this, pixel hash must catch it.
    y = (200, 10, 50)
    solid(cat / "banner_1.imageset" / "banner_1.png", (24, 24), y,
          compress_level=0)
    contents_json(cat / "banner_1.imageset", ["banner_1.png"])
    solid(cat / "banner_2.imageset" / "banner_2.png", (24, 24), y,
          compress_level=9)
    contents_json(cat / "banner_2.imageset", ["banner_2.png"])

    # D3: scale variants in ONE imageset (different dims) -> NOT flagged.
    z = (5, 5, 5)
    iset = cat / "scaled.imageset"
    solid(iset / "scaled.png", (10, 10), z)
    solid(iset / "scaled@2x.png", (20, 20), z)
    solid(iset / "scaled@3x.png", (30, 30), z)
    contents_json(iset, ["scaled.png", "scaled@2x.png", "scaled@3x.png"])

    # D4: visually different images -> NOT flagged.
    solid(cat / "unique_a.imageset" / "unique_a.png", (16, 16), (1, 2, 3))
    contents_json(cat / "unique_a.imageset", ["unique_a.png"])
    solid(cat / "unique_b.imageset" / "unique_b.png", (16, 16), (250, 240, 230))
    contents_json(cat / "unique_b.imageset", ["unique_b.png"])

    # D6: RGB vs opaque RGBA, identical visible pixels -> flagged via
    # normalisation.
    w = (44, 55, 66)
    solid(cat / "rgb_one.imageset" / "rgb_one.png", (12, 12), w, mode="RGB")
    contents_json(cat / "rgb_one.imageset", ["rgb_one.png"])
    solid(cat / "rgba_two.imageset" / "rgba_two.png", (12, 12), w + (255,),
          mode="RGBA")
    contents_json(cat / "rgba_two.imageset", ["rgba_two.png"])

    # D7: identical-pixel files inside a SINGLE imageset -> NOT flagged
    # (defensive intra-imageset skip).
    same = cat / "samewithinset.imageset"
    solid(same / "a.png", (8, 8), (9, 9, 9))
    solid(same / "b.png", (8, 8), (9, 9, 9))
    contents_json(same, ["a.png", "b.png"])

    # D8: byte-identical PDFs across imagesets -> flagged via byte fallback.
    pdf_bytes = b"%PDF-1.4 fake vector payload identical"
    (cat / "vec_a.imageset").mkdir(parents=True)
    (cat / "vec_a.imageset" / "vec.pdf").write_bytes(pdf_bytes)
    contents_json(cat / "vec_a.imageset", ["vec.pdf"])
    (cat / "vec_b.imageset").mkdir(parents=True)
    (cat / "vec_b.imageset" / "vec.pdf").write_bytes(pdf_bytes)
    contents_json(cat / "vec_b.imageset", ["vec.pdf"])

    # D9: repeated identical slots inside ONE .appiconset -> NOT flagged
    # (app icons legitimately reuse the same image across sizes).
    icon = (7, 7, 7)
    solid(cat / "AppIcon.appiconset" / "icon-40.png", (40, 40), icon)
    solid(cat / "AppIcon.appiconset" / "icon-60.png", (40, 40), icon)

    # D10: identical slot across two DIFFERENT .appiconsets -> flagged.
    brand = (8, 9, 10)
    solid(cat / "BrandA.appiconset" / "a.png", (50, 50), brand)
    solid(cat / "BrandB.appiconset" / "b.png", (50, 50), brand)

    # D11: identical webp across imagesets -> flagged (Pillow decodes webp).
    solid(cat / "promo_a.imageset" / "promo_a.webp", (20, 20), (3, 140, 90))
    contents_json(cat / "promo_a.imageset", ["promo_a.webp"])
    solid(cat / "promo_b.imageset" / "promo_b.webp", (20, 20), (3, 140, 90))
    contents_json(cat / "promo_b.imageset", ["promo_b.webp"])

    # D12: byte-identical svg across imagesets -> flagged via byte hash.
    svg = '<svg xmlns="http://www.w3.org/2000/svg"><rect width="9" height="9"/></svg>\n'
    (cat / "vsa.imageset").mkdir(parents=True)
    (cat / "vsa.imageset" / "vsa.svg").write_text(svg)
    contents_json(cat / "vsa.imageset", ["vsa.svg"])
    (cat / "vsb.imageset").mkdir(parents=True)
    (cat / "vsb.imageset" / "vsb.svg").write_text(svg)
    contents_json(cat / "vsb.imageset", ["vsb.svg"])

    # D13: byte-identical heic across imagesets -> flagged via byte fallback.
    heic = b"\x00\x00\x00\x18ftypheic identical-payload"
    (cat / "ph_a.imageset").mkdir(parents=True)
    (cat / "ph_a.imageset" / "ph.heic").write_bytes(heic)
    contents_json(cat / "ph_a.imageset", ["ph.heic"])
    (cat / "ph_b.imageset").mkdir(parents=True)
    (cat / "ph_b.imageset" / "ph.heic").write_bytes(heic)
    contents_json(cat / "ph_b.imageset", ["ph.heic"])

    out = subprocess.run([PY, str(HERE / "find_duplicate_images.py"),
                          str(root), "--root", str(root)],
                         capture_output=True, text=True).stdout

    # Parse "## Group" blocks into lists of label lines.
    groups: list[str] = re.split(r"^## Group \d+", out, flags=re.M)[1:]

    def grouped_together(*names: str) -> bool:
        return any(all(n in g for n in names) for g in groups)

    def flagged(name: str) -> bool:
        return any(name in g for g in groups)

    check(grouped_together("logo_a", "logo_b", "loose_logo"),
          "D1+D5 byte-identical across imagesets + loose copy are grouped")
    check(grouped_together("banner_1", "banner_2"),
          "D2 same pixels / different png encoding flagged (pixel hash)")
    check(not flagged("scaled"),
          "D3 scale variants in one imageset NOT flagged")
    check(not flagged("unique_a") and not flagged("unique_b"),
          "D4 visually different images NOT flagged")
    check(grouped_together("rgb_one", "rgba_two"),
          "D6 RGB vs opaque RGBA identical flagged (normalisation)")
    check(not flagged("samewithinset"),
          "D7 identical files inside one imageset NOT flagged (defensive)")
    check(grouped_together("vec_a", "vec_b"),
          "D8 byte-identical PDFs flagged via byte fallback")
    check(not flagged("AppIcon"),
          "D9 repeated slots inside one .appiconset NOT flagged")
    check(grouped_together("BrandA", "BrandB"),
          "D10 identical slot across two .appiconsets flagged")
    check(grouped_together("promo_a", "promo_b"),
          "D11 identical webp flagged (pixel path)")
    check(grouped_together("vsa", "vsb"),
          "D12 byte-identical svg flagged (byte fallback)")
    check(grouped_together("ph_a", "ph_b"),
          "D13 byte-identical heic flagged (byte fallback)")


# --------------------------------------------------------------------------- #
# Unused-image fixtures + assertions
# --------------------------------------------------------------------------- #
def test_unused(base: Path) -> None:
    root = base / "unused"
    cat = root / "Assets.xcassets"
    src = root / "Sources"
    src.mkdir(parents=True)

    def imageset(name: str) -> None:
        solid(cat / f"{name}.imageset" / f"{name}.png", (8, 8), (20, 20, 20))
        contents_json(cat / f"{name}.imageset", [f"{name}.png"])

    for n in ["used_static", "used_uikit", "used_in_xib", "used_in_json",
              "icon_home", "icon_settings", "truly_unused"]:
        imageset(n)

    (src / "View.swift").write_text(
        'import SwiftUI\n'
        'struct V: View {\n'
        '  var name = "home"\n'
        '  var body: some View {\n'
        '    Image("used_static")\n'
        '    Image("icon_\\(name)")\n'   # dynamic -> allowlist must rescue
        '  }\n'
        '}\n')
    (src / "Legacy.swift").write_text(
        'import UIKit\n'
        'let img = UIImage(named: "used_uikit")\n')
    (src / "Main.xib").write_text(
        '<?xml version="1.0"?>\n<document>\n'
        '  <imageView image="used_in_xib"/>\n</document>\n')
    (root / "config.json").write_text(
        json.dumps({"hero": "used_in_json", "items": []}, indent=2))

    allowlist = root / "allowlist.txt"
    allowlist.write_text("^icon_\n")

    out = subprocess.run(
        [PY, str(HERE / "find_unused_images.py"), str(root),
         "--allowlist", str(allowlist), "--fengniao", FENGNIAO],
        capture_output=True, text=True).stdout

    listed = {ln[2:].strip() for ln in out.splitlines() if ln.startswith("- ")}

    check(listed == {"truly_unused"},
          f"U: only 'truly_unused' reported (got {sorted(listed)})")
    check("used_static" not in listed,
          "U2 Image(\"name\") usage NOT flagged")
    check("used_uikit" not in listed,
          "U3 UIImage(named:) usage NOT flagged")
    check("used_in_xib" not in listed,
          "U6 xib usage NOT flagged")
    check("used_in_json" not in listed,
          "U5 json usage NOT flagged (wrapper greps extra globs)")
    check("icon_home" not in listed and "icon_settings" not in listed,
          "U4 dynamic icon_* rescued by allowlist")


def test_gate(base: Path) -> None:
    """PR gate: only newly-introduced duplicates should fail the check."""
    root = base / "gate"
    cat = root / "Assets.xcassets"

    pre = (11, 22, 33)  # a duplicate that already exists in the repo
    solid(cat / "preA.imageset" / "preA.png", (16, 16), pre)
    contents_json(cat / "preA.imageset", ["preA.png"])
    solid(cat / "preB.imageset" / "preB.png", (16, 16), pre)
    contents_json(cat / "preB.imageset", ["preB.png"])

    new = (44, 55, 66)  # a duplicate a PR would add
    newA = cat / "newA.imageset" / "newA.png"
    newB = cat / "newB.imageset" / "newB.png"
    solid(newA, (16, 16), new); contents_json(cat / "newA.imageset", ["newA.png"])
    solid(newB, (16, 16), new); contents_json(cat / "newB.imageset", ["newB.png"])

    other = cat / "solo.imageset" / "solo.png"  # unrelated, not a duplicate
    solid(other, (16, 16), (1, 2, 3))
    contents_json(cat / "solo.imageset", ["solo.png"])

    def gate(changed) -> int:
        argv = [PY, str(HERE / "find_duplicate_images.py"), str(root),
                "--root", str(root), "--fail-on-found"]
        if changed is not None:
            cl = root / "changed.txt"
            cl.write_text("\n".join(str(p) for p in changed) + "\n")
            argv += ["--changed-list", str(cl)]
        return subprocess.run(argv, capture_output=True, text=True).returncode

    check(gate([newA, newB]) == 1,
          "GATE fails when the PR introduces a new duplicate")
    check(gate([other]) == 0,
          "GATE passes when the PR's changes add no new duplicate "
          "(pre-existing duplicate does not block)")
    check(gate(None) == 1,
          "GATE (whole-repo, no --changed-list) fails on any duplicate")


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        test_duplicates(base)
        test_unused(base)
        test_gate(base)

    passed = sum(1 for ok, _ in results if ok)
    print(f"\nImage-hygiene self-test: {passed}/{len(results)} checks passed\n")
    for ok, msg in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {msg}")
    failed = [m for ok, m in results if not ok]
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
