#!/usr/bin/env python3
"""Find duplicate images in an Xcode project (asset-catalog aware).

Detection strategy (precision-first, no similarity threshold):
- Decode each image and hash its NORMALISED pixels (canonical RGBA buffer +
  size). Two files are "duplicates" only if they are pixel-for-pixel identical
  after normalisation. This catches "same image, different file encoding"
  (re-saved PNG, RGB vs opaque-RGBA) that a raw byte hash would miss, while
  never flagging merely-similar images the way an MSE/threshold tool would.
- Scale variants (@1x/@2x/@3x) inside ONE imageset have different pixel
  dimensions, so they hash differently and are never flagged against each
  other. Identical matches that live entirely within a single imageset are
  skipped defensively as well.
- Formats Pillow cannot decode here (pdf/svg/heic/gif) fall back to a raw byte
  hash, so only byte-identical copies are reported for them.

This tool only REPORTS candidates for human review; it never deletes anything.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections import defaultdict
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.stderr.write("error: Pillow is required (pip install Pillow)\n")
    raise SystemExit(2)

# Extensions we attempt to decode with Pillow for a pixel-level hash.
PIXEL_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".tif"}
# Vector / undecodable formats — byte-hashed only (exact copies). Kept in sync
# with the image formats scripts/check_image_size.sh recognises.
BYTE_EXTS = {".pdf", ".svg", ".heic", ".gif"}
IMAGE_EXTS = PIXEL_EXTS | BYTE_EXTS

# Asset-catalog leaf containers. Slots inside ONE of these (e.g. the multiple
# sizes of an .appiconset, or @1x/@2x/@3x of an .imageset) are expected to
# repeat and must not be flagged against each other.
CATALOG_SET_SUFFIXES = {
    ".imageset", ".appiconset", ".launchimage", ".symbolset",
    ".stickersequence", ".imagestack", ".imagestacklayer",
    ".cubetextureset", ".textureset", ".mipmapset", ".brandassets",
}


def catalog_set_of(path: Path) -> Path | None:
    """Return the nearest ancestor asset-catalog set dir, or None if loose."""
    for parent in path.parents:
        if parent.suffix in CATALOG_SET_SUFFIXES:
            return parent
    return None


def content_key(path: Path) -> tuple[str, str]:
    """Return (kind, hexdigest) identifying the image content.

    kind is "pixel" or "byte" so we never collide a pixel hash with a byte hash.
    """
    ext = path.suffix.lower()
    if ext in PIXEL_EXTS:
        try:
            with Image.open(path) as img:
                norm = img.convert("RGBA")
                digest = hashlib.sha256()
                digest.update(f"{norm.size[0]}x{norm.size[1]}".encode())
                digest.update(norm.tobytes())
                return ("pixel", digest.hexdigest())
        except Exception:
            pass  # fall through to byte hash for corrupt / undecodable files
    return ("byte", hashlib.sha256(path.read_bytes()).hexdigest())


def label(path: Path, root: Path) -> str:
    """Human label: the catalog-set name if inside one, else the relative path."""
    cset = catalog_set_of(path)
    if cset is not None:
        return f"{cset.relative_to(root)} ({path.name})"
    return str(path.relative_to(root))


def find_images(roots: list[Path]) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if root.is_file():
            if root.suffix.lower() in IMAGE_EXTS:
                found.append(root)
            continue
        for p in sorted(root.rglob("*")):
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
                found.append(p)
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("paths", nargs="*", default=["."],
                    help="Project root(s) or asset catalog(s) to scan.")
    ap.add_argument("--root", default=".",
                    help="Base path used to shorten labels (default: cwd).")
    ap.add_argument("--fail-on-found", action="store_true",
                    help="Exit non-zero when duplicates are found (for gating).")
    ap.add_argument("--changed-list",
                    help="File of changed paths (one per line). With "
                         "--fail-on-found, only fail for duplicate groups that "
                         "include a changed file (i.e. introduced by this PR).")
    args = ap.parse_args()

    roots = [Path(p).resolve() for p in (args.paths or ["."])]
    base = Path(args.root).resolve()

    changed: set[Path] | None = None
    if args.changed_list:
        changed = {Path(line.strip()).resolve()
                   for line in Path(args.changed_list).read_text().splitlines()
                   if line.strip()}

    groups: dict[tuple[str, str], list[Path]] = defaultdict(list)
    for img in find_images(roots):
        groups[content_key(img)].append(img)

    duplicates = []
    for _, files in groups.items():
        if len(files) < 2:
            continue
        # Skip groups confined to a single catalog set (expected repeated slots
        # / scale variants, e.g. inside one .appiconset or .imageset).
        sets = {catalog_set_of(f) for f in files}
        if len(sets) == 1 and None not in sets:
            continue
        duplicates.append(sorted(files))

    # A group is "introduced" if it contains a file changed by this PR.
    def introduced(group: list[Path]) -> bool:
        return changed is not None and any(f in changed for f in group)

    print("# Duplicate images report\n")
    if not duplicates:
        print("No duplicate images found. ✅")
        return 0

    total = sum(len(g) - 1 for g in duplicates)
    print(f"Found {len(duplicates)} duplicate group(s), "
          f"~{total} redundant file(s). Review before deleting.\n")
    new_groups = 0
    for i, group in enumerate(sorted(duplicates), 1):
        is_new = introduced(group)
        new_groups += is_new
        tag = " ⚠️ introduced by this change" if is_new else ""
        print(f"## Group {i}{tag}")
        for f in group:
            try:
                lbl = label(f, base)
            except ValueError:
                lbl = str(f)
            print(f"- {lbl}")
        print()

    if not args.fail_on_found:
        return 0
    # When given a changed-list, only gate on newly-introduced duplicates so
    # pre-existing ones don't block unrelated PRs.
    return 1 if (new_groups if changed is not None else duplicates) else 0


if __name__ == "__main__":
    raise SystemExit(main())
