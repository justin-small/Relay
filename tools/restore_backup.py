#!/usr/bin/env python3
"""Restore a Relay database backup (#278). Stop Relay first.

    python tools/restore_backup.py <backups/relay-YYYYMMDD-HHMMSS.db.enc> [--passphrase] [--to <relay.db>]

Backups are encrypted. On the computer that made one nothing needs typing: its key is in this computer's keychain
(or the state folder's secret.key on Linux). On another computer, --passphrase asks for the owner's backup
passphrase. The database being replaced is kept beside it as relay.db.before-restore. The installed app runs the
same thing as `relay --restore-backup <file>`.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import backup  # noqa: E402

if __name__ == "__main__":
    sys.exit(backup.main(sys.argv))
