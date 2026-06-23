# 🐜 PestCare CRM

A complete, **bilingual (English / العربية)** CRM for a pest-control company.
Built with **zero external dependencies** — pure Python 3 standard library on the
backend and vanilla HTML/CSS/JS on the frontend.

## Run it

```bash
cd pest-crm
python3 server.py            # serves on http://localhost:8000
# python3 server.py 9000     # custom port
```

The database (`data/crm.db`) is created and seeded with demo data on first launch.

## Demo logins

| Role | Email | Password |
|------|-------|----------|
| Admin | `admin@pestcrm.com` | `admin123` |
| Manager | `manager@pestcrm.com` | `manager123` |
| Agent (technician) | `agent1@pestcrm.com` / `agent2@pestcrm.com` | `agent123` |
| Client portal | `client@alnoor.com` | `client123` |

## Features

- **Bilingual UI** — full English & Arabic with automatic **RTL** layout; toggle anytime.
- **Roles & permissions** — Admin, Manager, Agent, Client portal — each sees only what it should.
- **Client company folders** — every client is a folder you open: details, sites/locations,
  finance summary, recent visits and a **photo gallery** (upload from the browser).
- **Visit scheduling** — assign agents, service type, date/time, status workflow
  (scheduled → in progress → completed / cancelled), with filters by status / agent / date.
- **Visit reports** — summary, pests found, findings, recommendations, severity, next-visit
  due date, plus report photos. Each report also carries an **engineer service log** (from the
  field visit tracker): spare parts changed, lamps / cables / transformers / light sheets used,
  Fipronil (ml), Imidacloprid (gm), baits, Glo pieces, Flybase bags and any branch issue —
  entered by the technician on the visit and carried onto the service certificate.
- **Chemicals & inventory** — stock levels, reorder alerts (low-stock), hazard class,
  registration no., cost; stock purchases/adjustments are logged.
- **Chemicals used per visit** — record consumption per visit; stock auto-decrements and the
  movement is recorded in the inventory ledger.
- **Invoices & finance** — bilingual invoices, VAT/tax, payments, auto "paid" status,
  outstanding balances; clients see their own finance in the portal.
- **Photo uploads** — for client folders, visits and reports (images stored in `uploads/`).
- **Global search** — across clients, visits, chemicals and invoices (scoped by role).
- **Dashboard** — KPIs tailored to staff vs. client.
- **Company settings & branding** — editable company name/address/VAT/logo + default tax rate
  (feeds every invoice/quote PDF); optional SMTP config for email reminders.
- **Quotes & line-item invoices** — multi-line invoices/quotes with qty × unit price and auto
  VAT; quotes convert to invoices in one click. Tabs separate Invoices and Quotes.
- **Recurring contracts** — weekly→annual service contracts that auto-generate scheduled visits
  (runs on startup and on demand via "Generate Due Visits").
- **Calendar** — month grid of visits, agent filter, click-through to a visit; plus the existing
  list/schedule view.
- **Notifications & reminders** — in-app bell (upcoming visits for agents, overdue invoices for
  managers), auto-generated and de-duplicated; sends email too if SMTP is configured.
- **E-signatures** — capture customer & technician signatures on a visit via touch/mouse canvas;
  stored as images and shown on the report.
- **Analytics** — monthly invoiced-vs-paid, receivables aging, agent productivity, chemical
  consumption and service mix (rendered as bar charts). Per-client analytics also includes a
  **materials-consumed rollup** — totals of lamps, cables, transformers, light sheets, baits,
  Glo pieces, Flybase bags, Fipronil and Imidacloprid summed across the client's visits
  (from the engineer service log) — and a **pest-activity trends** section.
- **Site / branch maps** — upload a floor-plan image per client (optionally per site) and place
  **device markers** on it (bait stations, rodent traps, insect light traps, monitoring points,
  treatment areas). Each marker has a label, status (OK / needs service / pest activity / missing)
  and notes. Agents open the map in the field, see all devices, and update them; clients view
  their own maps read-only.
- **Compliance / service certificates** — one-click printable **Pest Control Service Certificate**
  per completed visit (bilingual EN/AR, print-to-PDF from the browser). Pulls in service details,
  report findings, severity, chemicals applied and captured customer/technician signatures — the
  document food-safety/HACCP audits ask for.
- **Pest-activity trends** — every device (bait station, trap, monitor…) status update is recorded
  as a monitoring event, and each client's analytics page gains a **trends** section: monthly
  inspections-vs-detections curve, detections by device type, and an **activity hotspots** table.
  Included in the analytics PDF export.
- **Deployment & data** — Dockerfile (zero-dependency image), CSV export of clients/visits/
  invoices/chemicals/payments, and a 27-check automated test suite.

## Docker

```bash
docker build -t pestcare-crm .
docker run -p 8000:8000 -v "$PWD/data:/app/data" -v "$PWD/uploads:/app/uploads" pestcare-crm
```

## Tests

```bash
python3 test_api.py     # spins up an isolated server + DB, runs 27 end-to-end checks
```

## Project layout

```
pest-crm/
├── server.py        # HTTP server + REST API + routing + permissions
├── database.py      # SQLite schema & helpers
├── auth.py          # password hashing (PBKDF2) + signed tokens
├── multipart.py     # multipart/form-data parser (photo uploads)
├── seed.py          # demo data
├── test_api.py      # 27-check end-to-end API test suite
├── Dockerfile       # zero-dependency container image
├── data/            # SQLite DB + secret key (created at runtime)
├── uploads/         # uploaded photos
└── static/          # frontend (index.html, css/, js/)
```

## Extending it

The schema already includes `sites`, `service_types`, `inventory_transactions`,
`invoice`/`payments`, and a unified `photos` table keyed by `(entity_type, entity_id)`,
so adding new modules (contracts, recurring schedules, SLAs, e-signatures, PDF export,
multi-company tenancy, etc.) is mostly additive. API routes are registered with a simple
`@route(method, pattern)` decorator in `server.py`.
