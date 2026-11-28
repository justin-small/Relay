#!/usr/bin/env python3
"""Report unused image resources — a thin wrapper around FengNiao.

FengNiao (onevcat/FengNiao) does the heavy lifting: it extracts resource names
from asset catalogs and greps source files for usages. This wrapper adds the
two things FengNiao lacks for a trustworthy CI report:

1. Extra usage scan: FengNiao only scans its known file types
   (h/m/mm/swift/xib/storyboard/plist) and silently ignores others such as
   json — so an image referenced only from bundled JSON config is falsely
   reported as unused. For every name FengNiao reports, we additionally grep a
   configurable set of globs (default: *.json) for the name and drop it if
   found. Files inside asset catalogs (*.xcassets) are excluded, since a
   Contents.json naturally contains its own image name.
2. A NAME ALLOWLIST: image names are often built dynamically
   (e.g. Image("icon_\\(type)")), which no static scanner can resolve, so
   FengNiao reports them as unused. The allowlist (regex per line) drops those
   known-dynamic names so they don't pollute the report.

This tool only LISTS candidates for human review; it never deletes anything
(it always runs FengNiao with --list-only).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

# Resource extension stripped from a reported path to recover the asset name.
RESOURCE_SUFFIXES = (
    ".imageset", ".appiconset", ".launchimage", ".bundle",
    ".png", ".jpg", ".jpeg", ".gif", ".pdf",
)
# File globs FengNiao cannot scan; the wrapper greps these itself for usages.
DEFAULT_EXTRA_GLOBS = ["*.json"]
# "202 B /path/to/name.imageset"  ->  capture the path
LINE_RE = re.compile(r"^\s*[\d.]+\s*[KMGT]?B\s+(/.+)$")


def asset_name(path: str) -> str:
    name = os.path.basename(path.rstrip("/"))
    for suffix in RESOURCE_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return name


def load_allowlist(path: Path) -> list[re.Pattern]:
    patterns: list[re.Pattern] = []
    if not path.exists():
        return patterns
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            patterns.append(re.compile(line))
    return patterns


def run_fengniao(fengniao: str, project: str) -> str:
    cmd = [fengniao, "-p", project, "--list-only"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and not proc.stdout:
        sys.stderr.write(proc.stderr or "error: FengNiao failed\n")
        raise SystemExit(2)
    return proc.stdout


def names_referenced_in(project: Path, globs: list[str]) -> str:
    """Concatenate text of extra files (outside *.xcassets) for usage lookup."""
    chunks: list[str] = []
    for pattern in globs:
        for path in project.rglob(pattern):
            if any(part.endswith(".xcassets") for part in path.parts):
                continue  # asset catalog internals define names, not usages
            try:
                chunks.append(path.read_text(errors="ignore"))
            except OSError:
                pass
    return "\n".join(chunks)


def is_used_in_extra(name: str, haystack: str) -> bool:
    return re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])",
                     haystack) is not None


def main() -> int:
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", nargs="?", default=".",
                    help="Xcode project root passed to FengNiao -p.")
    ap.add_argument("--allowlist", default=str(here / "image-allowlist.txt"),
                    help="File of regex patterns for known dynamic names.")
    ap.add_argument("--fengniao", default=os.environ.get("FENGNIAO", "fengniao"),
                    help="FengNiao executable (default: fengniao on PATH).")
    ap.add_argument("--extra-glob", action="append", default=None,
                    help="Extra file globs to grep for usages (FengNiao can't "
                         f"scan these). Default: {DEFAULT_EXTRA_GLOBS}.")
    ap.add_argument("--fail-on-found", action="store_true",
                    help="Exit non-zero when unused images remain (for gating).")
    args = ap.parse_args()

    patterns = load_allowlist(Path(args.allowlist))
    globs = args.extra_glob if args.extra_glob is not None else DEFAULT_EXTRA_GLOBS
    extra_text = names_referenced_in(Path(args.project), globs)
    output = run_fengniao(args.fengniao, args.project)

    reported = sorted({asset_name(m.group(1))
                       for line in output.splitlines()
                       if (m := LINE_RE.match(line))})

    allowlisted = [n for n in reported if any(p.search(n) for p in patterns)]
    after_allow = [n for n in reported if n not in allowlisted]
    extra_used = [n for n in after_allow if is_used_in_extra(n, extra_text)]
    genuine = sorted(n for n in after_allow if n not in extra_used)

    def footnote() -> None:
        notes = []
        if allowlisted:
            notes.append(f"allowlisted as dynamic: {', '.join(sorted(set(allowlisted)))}")
        if extra_used:
            notes.append(f"found in extra files ({', '.join(globs)}): "
                         f"{', '.join(sorted(set(extra_used)))}")
        if notes:
            print("\n_(ignored — " + "; ".join(notes) + ")_")

    print("# Unused images report\n")
    if not genuine:
        print("No unused images found. ✅")
        footnote()
        return 0

    print(f"Found {len(genuine)} unused image(s). Review before deleting "
          "(static analysis can miss runtime-built names).\n")
    for name in genuine:
        print(f"- {name}")
    footnote()
    return 1 if args.fail_on_found else 0


if __name__ == "__main__":
    raise SystemExit(main())
