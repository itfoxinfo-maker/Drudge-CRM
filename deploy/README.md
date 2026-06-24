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

The CRM database is SQLite at `data/crm.db`. Snapshot it with `backup.py`
(see the main README) and schedule it from cron.
