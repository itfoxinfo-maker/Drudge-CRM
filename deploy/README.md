# Deployment (systemd)

Two separate apps, each its own service:

| Unit | App | Port |
|------|-----|------|
| `pestcrm.service` | CRM — `server.py` (API + SPA) | 8000 |
| `pestcrm-site.service` | Drudge marketing website — `marketing-site/server.py` | 8080 |

> The CRM and the website both have an entry file named `server.py`. Never
> `pkill -f server.py` — it kills both. Manage them by unit instead.

## Install / update

```bash
sudo cp deploy/pestcrm.service deploy/pestcrm-site.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pestcrm.service pestcrm-site.service
```

Both units are enabled (start on boot) and use `Restart=always`.

The units assume the repo lives at `/root/pest-crm`; adjust `WorkingDirectory`
and `ExecStart` paths if you deploy elsewhere.

## Operate

```bash
systemctl status  pestcrm.service        # or pestcrm-site.service
systemctl restart pestcrm.service        # apply new code after a git pull / edit
journalctl -u pestcrm.service -f         # live logs
```

## Backups

The CRM database is SQLite at `data/crm.db`; uploaded files (photos,
signatures, site maps, logo) live in `uploads/`. `backup.py` snapshots both:
a consistent online DB copy plus a compressed `uploads-*.tar.gz`, keeping the
most recent 14 of each in `data/backups`.

Schedule it nightly with the bundled systemd timer (runs at 02:30, and catches
up on next boot if the machine was off):

```bash
sudo cp deploy/pestcrm-backup.service deploy/pestcrm-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pestcrm-backup.timer

systemctl list-timers pestcrm-backup.timer   # confirm next run
systemctl start pestcrm-backup.service        # run one now / verify
journalctl -u pestcrm-backup.service          # backup output
```

Store copies off-box too (e.g. `--dest /mnt/backup` or an `rsync` of
`data/backups` to another host) so a disk failure can't take the backups with it.
