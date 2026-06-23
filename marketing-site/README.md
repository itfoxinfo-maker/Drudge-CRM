# Drudge — Marketing Website

A standalone, **bilingual (English / العربية)** marketing site for Drudge Pest
Control. It is completely **isolated from the CRM**: separate folder, its own
zero-dependency static server, and a different port. It shares nothing with the
CRM (no database, no auth, no shared code).

## Run it

```bash
cd marketing-site
python3 server.py            # serves on http://0.0.0.0:8080
# python3 server.py 9090     # custom port
```

The CRM runs on its own port (default 8000); this site runs on **8080**.

## What's inside

```
marketing-site/
├── server.py            # tiny stdlib static file server
└── static/
    ├── index.html       # single-page site (sections use data-i18n keys)
    ├── css/styles.css   # Drudge black/white brand + green accent, responsive
    ├── js/site.js       # EN/AR strings, language toggle (RTL), interactions
    └── img/drudge.jpeg  # Drudge logo
```

## Content

Sections: hero, services (the 7 CRM service types + recurring contracts),
"Why Drudge" (certified techs, compliance certificates, digital monitoring,
bilingual reports — mirroring the CRM's real capabilities), industries served,
4-step process, contact form, footer.

- **Language:** toggle EN ⇄ AR; layout flips to RTL for Arabic. Choice persists
  in `localStorage`.
- **Editing copy:** all text lives in `static/js/site.js` (the `I18N` object) —
  edit the English and Arabic strings there.
- **Contact form:** front-end only (shows a confirmation). Wire it to email or
  an endpoint when ready.

> Tip: to expose it publicly on the VPS, open port 8080 in the firewall, or put
> it behind a reverse proxy (e.g. Nginx) on your domain.
