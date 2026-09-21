#!/usr/bin/env python3
"""Write config.json from OPENAI_KEY / ADMIN_TOKEN in the environment.

Called by setup.command and setup.bat. The secrets arrive through the
environment rather than argv so they never appear in a process listing or a
shell history file.

An existing config.json is preserved: every other setting the operator has
tuned survives, only the two credentials are replaced. The file is written
0600 and is git-ignored.
"""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config  # noqa: E402


def main() -> int:
    key = (os.environ.get("OPENAI_KEY") or "").strip()
    token = (os.environ.get("ADMIN_TOKEN") or "").strip()
    if not key or not token:
        print("  Both OPENAI_KEY and ADMIN_TOKEN must be set.", file=sys.stderr)
        return 1

    config.ensure_file()
    config.save({"openai_api_key": key, "admin_token": token})
    path = config.CONFIG_PATH
    os.chmod(path, 0o600)
    print(f"Wrote {path} (permissions 0600).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
