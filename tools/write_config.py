#!/usr/bin/env python3
"""Write config.json from OPENAI_KEY / ADMIN_TOKEN in the environment.

Called by setup.command and setup.bat, through `docker compose run` so the
image's Python is the only one needed on the host. The secrets arrive through
the environment rather than argv so they never appear in a process listing or a
shell history file.

RELAY_ADMIN_FQDN is optional and is stored, not secret: it is the hostname the
panel certificate should carry, and it has to survive container recreation.

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

    updates = {"openai_api_key": key, "admin_token": token}
    # Written even when blank: an operator who clears the hostname at a second
    # run means "certify the IP only", and that has to overwrite the old value.
    updates["admin_fqdn"] = (os.environ.get("RELAY_ADMIN_FQDN") or "").strip().strip(".")

    config.ensure_file()
    config.save(updates)
    path = config.CONFIG_PATH
    os.chmod(path, 0o600)
    print(f"Wrote {path} (permissions 0600).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
