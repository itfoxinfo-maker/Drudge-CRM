#!/usr/bin/env python3
"""Health monitor for the PestCare CRM. Standard library only.

Checks, in order:
  1. HTTP liveness   — GET /api/health (retries); if the server is up but not
                       answering, restarts pestcrm.service and re-checks.
  2. Disk space      — alerts when the data partition runs low.
  3. DB integrity    — PRAGMA quick_check on a read-only connection.
  4. Backup freshness— alerts when the newest file in data/backups is stale.

Problems are raised as in-app notifications to every active admin (written
straight into the notifications table, so they surface even while the HTTP
server is unhappy) and printed to stdout for the journal. Alerts are deduped
via dedup_key so a persistent condition nags once per day, not every run.

Usage:
    python3 monitor.py                # full check (may restart the service)
    python3 monitor.py --no-restart   # never touch systemd (tests/dry runs)

Honors PESTCRM_DATA_DIR (same as the server) and PESTCRM_URL
(default http://127.0.0.1:8000). Schedule with deploy/pestcrm-monitor.timer.
"""
import os
import sys
import glob
import json
import time
import sqlite3
import subprocess
import urllib.request

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("PESTCRM_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "crm.db")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
URL = os.environ.get("PESTCRM_URL", "http://127.0.0.1:8000").rstrip("/")
SERVICE = os.environ.get("PESTCRM_SERVICE", "pestcrm.service")

HTTP_TRIES = 3          # liveness attempts before declaring the server down
HTTP_RETRY_WAIT = 5     # seconds between attempts
DISK_MIN_FREE_MB = 1024  # alert below this many MB free ...
DISK_MIN_FREE_PCT = 5    # ... or below this % free
BACKUP_MAX_AGE_H = 26   # nightly backup + 2h grace


def _conn():
    cx = sqlite3.connect(DB_PATH, timeout=15)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA busy_timeout=15000")
    return cx


def alert(kind, title, body, per="day"):
    """In-app notification to all active admins, deduped per day (or hour)."""
    stamp = time.strftime("%Y-%m-%d" if per == "day" else "%Y-%m-%d %H")
    print(f"ALERT [{kind}] {title}: {body}")
    try:
        with _conn() as cx:
            for u in cx.execute("SELECT id FROM users WHERE role='admin' AND active=1").fetchall():
                dk = f"monitor:{kind}:{stamp}:{u['id']}"
                if cx.execute("SELECT 1 FROM notifications WHERE dedup_key=?", (dk,)).fetchone():
                    continue
                cx.execute("INSERT INTO notifications(user_id,type,title,body,dedup_key) "
                           "VALUES(?,?,?,?,?)", (u["id"], "monitor_" + kind, title, body, dk))
    except Exception as e:
        print(f"  (could not write notification: {e})")


def http_alive():
    try:
        with urllib.request.urlopen(URL + "/api/health", timeout=10) as r:
            return r.status == 200 and json.load(r).get("ok") is True
    except Exception:
        return False


def check_http(allow_restart):
    for i in range(HTTP_TRIES):
        if http_alive():
            print(f"http: ok ({URL})")
            return True
        if i < HTTP_TRIES - 1:
            time.sleep(HTTP_RETRY_WAIT)
    if not allow_restart:
        alert("down", "CRM unreachable", f"{URL}/api/health did not answer "
              f"after {HTTP_TRIES} attempts (restart skipped).", per="hour")
        return False
    print(f"http: DOWN after {HTTP_TRIES} attempts — restarting {SERVICE}")
    subprocess.run(["systemctl", "restart", SERVICE], check=False)
    time.sleep(8)
    if http_alive():
        alert("restarted", "CRM was restarted by the monitor",
              f"{URL}/api/health stopped answering; {SERVICE} was restarted "
              "and is healthy again.", per="hour")
        return True
    alert("down", "CRM DOWN — restart did not help",
          f"{URL}/api/health still not answering after restarting {SERVICE}. "
          "Manual attention needed.", per="hour")
    return False


def check_disk():
    try:
        st = os.statvfs(DATA_DIR)
    except OSError as e:
        alert("disk", "Disk check failed", str(e))
        return
    free_mb = st.f_bavail * st.f_frsize // (1024 * 1024)
    pct = st.f_bavail * 100 // (st.f_blocks or 1)
    if free_mb < DISK_MIN_FREE_MB or pct < DISK_MIN_FREE_PCT:
        alert("disk", "Low disk space",
              f"Only {free_mb} MB ({pct}%) free on the CRM data partition.")
    else:
        print(f"disk: ok ({free_mb} MB / {pct}% free)")


def check_integrity():
    try:
        cx = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=15)
        try:
            res = cx.execute("PRAGMA quick_check").fetchone()[0]
        finally:
            cx.close()
    except Exception as e:
        res = f"error: {e}"
    if res == "ok":
        print("integrity: ok")
    else:
        alert("integrity", "Database integrity problem",
              f"PRAGMA quick_check on crm.db returned: {res}. "
              "Restore from data/backups may be needed.")


def check_backups():
    files = glob.glob(os.path.join(BACKUP_DIR, "*"))
    newest = max((os.path.getmtime(f) for f in files), default=0)
    age_h = (time.time() - newest) / 3600 if newest else None
    if age_h is not None and age_h <= BACKUP_MAX_AGE_H:
        print(f"backups: ok (newest {age_h:.1f}h old)")
        return
    detail = ("no backups found in data/backups" if age_h is None
              else f"newest backup is {age_h:.0f}h old")
    alert("backup", "Backups are stale",
          f"{detail} (expected one every {BACKUP_MAX_AGE_H}h). "
          "Check pestcrm-backup.timer.")


def main():
    allow_restart = "--no-restart" not in sys.argv
    if not os.path.exists(DB_PATH):
        print(f"No database at {DB_PATH}", file=sys.stderr)
        sys.exit(2)
    check_http(allow_restart)
    check_disk()
    check_integrity()
    check_backups()


if __name__ == "__main__":
    main()
