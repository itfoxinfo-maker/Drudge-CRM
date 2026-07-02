#!/usr/bin/env python3
"""Online backup of the CRM SQLite database, with retention.

Uses SQLite's online backup API, which produces a consistent snapshot even
while the server is running (safe under WAL). Standard library only.

Usage:
    python3 backup.py                 # back up to data/backups, keep last 14
    python3 backup.py --keep 30       # keep the last 30 backups
    python3 backup.py --dest /mnt/bk  # write backups elsewhere
    python3 backup.py --uploads       # also snapshot the uploads/ folder

Honors PESTCRM_DATA_DIR (same as the server) to locate crm.db.

Schedule it nightly with the bundled systemd timer (deploy/pestcrm-backup.*),
or from cron, e.g. at 02:30:
    30 2 * * *  cd /path/to/pest-crm && /usr/bin/python3 backup.py --uploads >> data/backup.log 2>&1
"""
import os
import sys
import glob
import time
import tarfile
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("PESTCRM_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "crm.db")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
DEFAULT_DEST = os.path.join(DATA_DIR, "backups")
DEFAULT_KEEP = 14


def _prune(dest, pattern, keep):
    """Keep only the most recent `keep` files matching pattern (0 = keep all)."""
    if not keep or keep <= 0:
        return
    for old in sorted(glob.glob(os.path.join(dest, pattern)))[:-keep]:
        os.remove(old)
        print(f"Pruned old backup: {os.path.basename(old)}")


def run(dest=DEFAULT_DEST, keep=DEFAULT_KEEP, uploads=False):
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}", file=sys.stderr)
        return 1
    os.makedirs(dest, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M%S')

    out = os.path.join(dest, f"crm-{stamp}.db")
    src = sqlite3.connect(DB_PATH)
    dst = sqlite3.connect(out)
    try:
        with dst:
            src.backup(dst)              # consistent online snapshot
    finally:
        dst.close()
        src.close()
    print(f"Backup written: {out} ({os.path.getsize(out) / 1024:.0f} KB)")
    _prune(dest, "crm-*.db", keep)

    # Uploaded files (photos, signatures, site maps, logo) live outside the DB,
    # so a DB-only backup would lose them. Snapshot them as a compressed tar.
    if uploads and os.path.isdir(UPLOAD_DIR):
        tar_out = os.path.join(dest, f"uploads-{stamp}.tar.gz")
        with tarfile.open(tar_out, "w:gz") as tf:
            tf.add(UPLOAD_DIR, arcname="uploads")
        print(f"Uploads archived: {tar_out} ({os.path.getsize(tar_out) / 1024:.0f} KB)")
        _prune(dest, "uploads-*.tar.gz", keep)

    return 0


def _parse_args(argv):
    dest, keep, uploads = DEFAULT_DEST, DEFAULT_KEEP, False
    i = 0
    while i < len(argv):
        if argv[i] == "--keep" and i + 1 < len(argv):
            keep = int(argv[i + 1]); i += 2
        elif argv[i] == "--dest" and i + 1 < len(argv):
            dest = argv[i + 1]; i += 2
        elif argv[i] == "--uploads":
            uploads = True; i += 1
        else:
            print(__doc__); sys.exit(2)
    return dest, keep, uploads


if __name__ == "__main__":
    d, k, u = _parse_args(sys.argv[1:])
    sys.exit(run(d, k, uploads=u))
