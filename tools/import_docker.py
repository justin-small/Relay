#!/usr/bin/env python3
"""Import the settings of a v2.0 Docker install of Relay into this computer's state folder.

    python tools/import_docker.py <old Relay folder or its docker-config folder> [--to <state folder>] [--force]

Copies config.json, blocklist.txt, recordings/ and panel-firmware/; rewrites container paths, ports and an
old PulseAudio input; never touches the old folder. Run it with Relay stopped. Running it twice is safe.
The state folder is the one Relay itself uses (RELAY_HOME, else this OS's per-user folder) unless --to.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import importdocker, paths  # noqa: E402


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("source", help="the old Relay folder, or its docker-config folder")
    ap.add_argument("--to", help="state folder to import into (default: the one Relay uses)")
    ap.add_argument("--force", action="store_true",
                    help="replace this Relay's own config.json (kept as config.json.before-import)")
    args = ap.parse_args(argv[1:])
    dest = Path(args.to) if args.to else paths.state_dir()
    try:
        report = importdocker.import_from(args.source, dest, force=args.force)
    except importdocker.ImportRefused as exc:
        print(f"  {exc}", file=sys.stderr)
        return 1
    print(f"  From: {report.source}\n  To:   {dest.resolve()}")
    for what in report.copied:
        print(f"  copied     {what}")
    for what in report.unchanged:
        print(f"  unchanged  {what}")
    for note in report.notes:
        print(f"  note: {note}")
    print("\n  Next: start Relay, open the operator panel on this computer and create the Owner with your "
          "old admin token (it is used once, then retired). The old folder was not changed.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
