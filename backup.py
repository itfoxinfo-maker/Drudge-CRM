#!/usr/bin/env python3
"""Online backup of the CRM SQLite database, with retention.

Uses SQLite's online backup API, which produces a consistent snapshot even
while the server is running (safe under WAL). Standard library only.

Usage:
    python3 backup.py                 # back up to data/backups, keep last 14
    python3 backup.py --keep 30       # keep the last 30 backups
    python3 backup.py --dest /mnt/bk  # write backups elsewhere

Honors PESTCRM_DATA_DIR (same as the server) to locate crm.db.

Schedule it from cron, e.g. nightly at 02:30:
    30 2 * * *  cd /path/to/pest-crm && /usr/bin/python3 backup.py >> data/backup.log 2>&1
"""
import os
import sys
import glob
import time
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("PESTCRM_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "crm.db")
DEFAULT_DEST = os.path.join(DATA_DIR, "backups")
DEFAULT_KEEP = 14


def run(dest=DEFAULT_DEST, keep=DEFAULT_KEEP):
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}", file=sys.stderr)
        return 1
    os.makedirs(dest, exist_ok=True)
    out = os.path.join(dest, f"crm-{time.strftime('%Y%m%d-%H%M%S')}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(out)
    try:
        with dst:
            src.backup(dst)              # consistent online snapshot
    finally:
        dst.close()
        src.close()
    size = os.path.getsize(out)
    print(f"Backup written: {out} ({size / 1024:.0f} KB)")

    # Retention: keep the most recent `keep` backups (0 = keep all).
    if keep and keep > 0:
        backups = sorted(glob.glob(os.path.join(dest, "crm-*.db")))
        for old in backups[:-keep]:
            os.remove(old)
            print(f"Pruned old backup: {os.path.basename(old)}")
    return 0


def _parse_args(argv):
    dest, keep = DEFAULT_DEST, DEFAULT_KEEP
    i = 0
    while i < len(argv):
        if argv[i] == "--keep" and i + 1 < len(argv):
            keep = int(argv[i + 1]); i += 2
        elif argv[i] == "--dest" and i + 1 < len(argv):
            dest = argv[i + 1]; i += 2
        else:
            print(__doc__); sys.exit(2)
    return dest, keep


if __name__ == "__main__":
    d, k = _parse_args(sys.argv[1:])
    sys.exit(run(d, k))
