"""SQLite database layer for the Pest Control CRM.

Uses only the Python standard library. The schema is created on first run and
demo data is seeded by seed.py.
"""
import sqlite3
import os
import contextlib
import threading

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("PESTCRM_DATA_DIR", os.path.join(BASE_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "crm.db")

SCHEMA = """
PRAGMA foreign_keys = ON;

-- Users: staff (admin/manager/agent) and client portal users.
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    full_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('admin','manager','agent','client')),
    phone         TEXT,
    -- For role='client': which client company they belong to.
    client_id     INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    -- Agent profile extras (NULL for non-agents).
    specialization TEXT,
    hire_date     TEXT,
    -- Technician licensing/certification (shown on audit packs).
    license_no    TEXT,
    license_expiry TEXT,
    lang          TEXT NOT NULL DEFAULT 'en',
    active        INTEGER NOT NULL DEFAULT 1,
    -- bumped to invalidate a user's outstanding session tokens (see auth.py)
    token_version INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Client companies. Each row is the "company folder".
CREATE TABLE IF NOT EXISTS clients (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en        TEXT NOT NULL,
    name_ar        TEXT,
    contact_person TEXT,
    phone          TEXT,
    email          TEXT,
    address_en     TEXT,
    address_ar     TEXT,
    city           TEXT,
    notes          TEXT,
    status         TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','inactive')),
    created_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sites/locations belonging to a client (optional, a client can have many).
CREATE TABLE IF NOT EXISTS sites (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id  INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    address    TEXT,
    area       TEXT,
    map_image  TEXT,   -- uploaded floor-plan / map-design picture for this site
    lat        REAL,   -- geographic coordinates (for dispatch / route optimization)
    lng        REAL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Service catalog (bilingual).
CREATE TABLE IF NOT EXISTS service_types (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en TEXT NOT NULL,
    name_ar TEXT
);

-- Scheduled / completed agent visits.
CREATE TABLE IF NOT EXISTS visits (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    site_id         INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    agent_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    service_type_id INTEGER REFERENCES service_types(id) ON DELETE SET NULL,
    scheduled_start TEXT NOT NULL,
    scheduled_end   TEXT,
    status          TEXT NOT NULL DEFAULT 'scheduled'
                    CHECK (status IN ('scheduled','in_progress','completed','cancelled')),
    location        TEXT,
    notes           TEXT,
    visit_number    INTEGER,   -- the visit's number within the year/month (1–12)
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);

-- One report per visit.
CREATE TABLE IF NOT EXISTS reports (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id        INTEGER NOT NULL UNIQUE REFERENCES visits(id) ON DELETE CASCADE,
    summary         TEXT,
    pests_found     TEXT,
    findings        TEXT,
    recommendations TEXT,
    severity        TEXT DEFAULT 'low' CHECK (severity IN ('low','medium','high','critical')),
    next_visit_due  TEXT,
    customer_name        TEXT,
    customer_signature   TEXT,   -- filename of captured signature image
    technician_signature TEXT,
    -- Engineer service log (per the field visit tracker): parts & materials
    -- consumed during the visit, plus any branch issue found.
    spare_parts_changed  TEXT,
    lamps_used           REAL NOT NULL DEFAULT 0,
    cables_used          REAL NOT NULL DEFAULT 0,
    transformers_used    REAL NOT NULL DEFAULT 0,
    light_sheets_used    REAL NOT NULL DEFAULT 0,
    fipronil_ml          REAL NOT NULL DEFAULT 0,
    imidacloprid_gm      REAL NOT NULL DEFAULT 0,
    baits_count          REAL NOT NULL DEFAULT 0,
    glo_pieces           REAL NOT NULL DEFAULT 0,
    flybase_bags         REAL NOT NULL DEFAULT 0,
    branch_issue         TEXT,
    -- 'draft' while the agent is still filling it in (auto-saved); 'complete'
    -- once the core fields + both signatures are captured.
    status          TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','complete')),
    completed_at    TEXT,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Chemical / product inventory.
CREATE TABLE IF NOT EXISTS chemicals (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en           TEXT NOT NULL,
    name_ar           TEXT,
    active_ingredient TEXT,
    unit              TEXT NOT NULL DEFAULT 'L',
    quantity_in_stock REAL NOT NULL DEFAULT 0,
    reorder_level     REAL NOT NULL DEFAULT 0,
    hazard_class      TEXT,
    reg_no            TEXT,
    cost_per_unit     REAL NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Chemicals consumed during a specific visit.
CREATE TABLE IF NOT EXISTS chemical_usage (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id     INTEGER NOT NULL REFERENCES visits(id) ON DELETE CASCADE,
    chemical_id  INTEGER NOT NULL REFERENCES chemicals(id) ON DELETE CASCADE,
    quantity     REAL NOT NULL,
    area_treated TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Stock movements (purchases / adjustments / usage log).
CREATE TABLE IF NOT EXISTS inventory_transactions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chemical_id INTEGER NOT NULL REFERENCES chemicals(id) ON DELETE CASCADE,
    change      REAL NOT NULL,
    reason      TEXT NOT NULL CHECK (reason IN ('purchase','usage','adjustment','issue')),
    reference   TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Engineer material issues: stock an engineer checks out of inventory (e.g. the
-- week's materials). Each issue deducts stock; line items hold the materials.
CREATE TABLE IF NOT EXISTS engineer_issues (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    note        TEXT,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS engineer_issue_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER NOT NULL REFERENCES engineer_issues(id) ON DELETE CASCADE,
    chemical_id INTEGER NOT NULL REFERENCES chemicals(id) ON DELETE CASCADE,
    quantity    REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_issue_items ON engineer_issue_items(issue_id);
CREATE INDEX IF NOT EXISTS idx_issues_agent ON engineer_issues(agent_id);

-- Unified photo store: client folders, reports, visits, chemicals.
CREATE TABLE IF NOT EXISTS photos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('client','report','visit','chemical')),
    entity_id     INTEGER NOT NULL,
    filename      TEXT NOT NULL,
    original_name TEXT,
    caption       TEXT,
    -- When set, the attachment is a "Business plan" and is surfaced on the report.
    is_business_plan INTEGER NOT NULL DEFAULT 0,
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Invoices and quotes (client finance). doc_type distinguishes the two.
-- status is free-text (app-controlled) to allow quote states (accepted/declined).
CREATE TABLE IF NOT EXISTS invoices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    site_id     INTEGER REFERENCES sites(id) ON DELETE SET NULL,  -- location this doc belongs to
    visit_id    INTEGER REFERENCES visits(id) ON DELETE SET NULL,
    contract_id INTEGER REFERENCES contracts(id) ON DELETE SET NULL,
    doc_type    TEXT NOT NULL DEFAULT 'invoice',   -- 'invoice' | 'quote'
    number      TEXT NOT NULL,
    issue_date  TEXT NOT NULL,
    due_date    TEXT,
    valid_until TEXT,                               -- quotes
    amount      REAL NOT NULL DEFAULT 0,
    tax         REAL NOT NULL DEFAULT 0,
    total       REAL NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'draft',
    notes       TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Line items for an invoice / quote.
CREATE TABLE IF NOT EXISTS invoice_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id  INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    description TEXT NOT NULL,
    quantity    REAL NOT NULL DEFAULT 1,
    unit_price  REAL NOT NULL DEFAULT 0,
    amount      REAL NOT NULL DEFAULT 0
);

-- Recurring service contracts that auto-generate visits.
CREATE TABLE IF NOT EXISTS contracts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id       INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    site_id         INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    service_type_id INTEGER REFERENCES service_types(id) ON DELETE SET NULL,
    agent_id        INTEGER REFERENCES users(id) ON DELETE SET NULL,
    frequency       TEXT NOT NULL DEFAULT 'monthly'
                    CHECK (frequency IN ('weekly','biweekly','monthly','quarterly','semiannual','annual')),
    start_date      TEXT NOT NULL,
    end_date        TEXT,
    next_run_date   TEXT NOT NULL,
    price           REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active','paused','ended')),
    notes           TEXT,
    -- Recurring billing: when auto_invoice=1 an invoice is generated every
    -- bill_every cadence (NULL falls back to `frequency`) on next_bill_date.
    bill_every      TEXT,
    next_bill_date  TEXT,
    auto_invoice    INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per-site lines on a contract: each covered location, its Google Maps
-- location (a maps URL or "lat,lng" string) and the price for that site.
-- A contract's overall price is the sum of its site rows.
CREATE TABLE IF NOT EXISTS contract_sites (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    contract_id  INTEGER NOT NULL REFERENCES contracts(id) ON DELETE CASCADE,
    site_id      INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    map_location TEXT,
    price        REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_contract_sites_contract ON contract_sites(contract_id);

-- Self-service visit requests submitted by clients from their portal. Staff
-- approve a request (which creates a real visit) or decline it.
CREATE TABLE IF NOT EXISTS visit_requests (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id      INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    site_id        INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    preferred_date TEXT,
    note           TEXT,
    status         TEXT NOT NULL DEFAULT 'pending'
                   CHECK (status IN ('pending','approved','declined')),
    visit_id       INTEGER REFERENCES visits(id) ON DELETE SET NULL,
    created_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    handled_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    handled_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_visit_requests_client ON visit_requests(client_id);

-- Online-payment attempts. One row per "pay this invoice" click. Gateway-
-- agnostic: `provider` selects the adapter, `provider_ref` holds the gateway's
-- order/transaction id (equals `token` for the built-in manual provider), and
-- `token` is our opaque id used in checkout/return URLs. Marked 'paid' by the
-- provider's callback, which also writes the matching payments row.
CREATE TABLE IF NOT EXISTS payment_intents (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id   INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    client_id    INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    provider     TEXT NOT NULL,
    provider_ref TEXT,
    token        TEXT NOT NULL UNIQUE,
    amount       REAL NOT NULL,
    currency     TEXT NOT NULL DEFAULT 'EGP',
    status       TEXT NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending','paid','failed','cancelled')),
    payment_id   INTEGER REFERENCES payments(id) ON DELETE SET NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_payment_intents_invoice ON payment_intents(invoice_id);

-- Price book: reusable service/line-item catalog for quotes & invoices.
CREATE TABLE IF NOT EXISTS price_book (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name_en     TEXT NOT NULL,
    name_ar     TEXT,
    description TEXT,
    unit_price  REAL NOT NULL DEFAULT 0,
    active      INTEGER NOT NULL DEFAULT 1,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Post-visit customer satisfaction (one rating per visit).
CREATE TABLE IF NOT EXISTS visit_ratings (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    visit_id    INTEGER NOT NULL UNIQUE REFERENCES visits(id) ON DELETE CASCADE,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    stars       INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5),
    comment     TEXT,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Sales leads: from the marketing website's booking form or entered manually.
CREATE TABLE IF NOT EXISTS leads (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    name           TEXT NOT NULL,
    company        TEXT,
    phone          TEXT,
    email          TEXT,
    sector         TEXT,
    message        TEXT,
    preferred_date TEXT,
    source         TEXT NOT NULL DEFAULT 'manual',
    status         TEXT NOT NULL DEFAULT 'new'
                   CHECK (status IN ('new','contacted','quoted','won','lost')),
    note           TEXT,
    client_id      INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    handled_by     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status);

-- Purchase orders: stock-in with supplier + cost tracking.
CREATE TABLE IF NOT EXISTS purchase_orders (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    supplier    TEXT,
    reference   TEXT,
    note        TEXT,
    total_cost  REAL NOT NULL DEFAULT 0,
    created_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS purchase_order_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id       INTEGER NOT NULL REFERENCES purchase_orders(id) ON DELETE CASCADE,
    chemical_id INTEGER NOT NULL REFERENCES chemicals(id) ON DELETE CASCADE,
    quantity    REAL NOT NULL,
    unit_cost   REAL NOT NULL DEFAULT 0
);

-- Key/value settings (company profile, tax rate, SMTP, etc.).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Cache of machine translations (report text auto-translated EN<->AR for
-- display/printing). Keyed by a hash of the source text + target language.
CREATE TABLE IF NOT EXISTS translations (
    src_hash   TEXT NOT NULL,
    target     TEXT NOT NULL,
    src_lang   TEXT,
    text       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (src_hash, target)
);

-- In-app notifications / reminders.
CREATE TABLE IF NOT EXISTS notifications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    body        TEXT,
    link_view   TEXT,
    link_id     INTEGER,
    is_read     INTEGER NOT NULL DEFAULT 0,
    dedup_key   TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS payments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
    amount     REAL NOT NULL,
    method     TEXT NOT NULL DEFAULT 'cash',
    paid_at    TEXT NOT NULL DEFAULT (datetime('now')),
    note       TEXT
);

-- Site/branch floor-plan maps per client, with placeable device markers.
CREATE TABLE IF NOT EXISTS maps (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
    site_id     INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    name        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    uploaded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Markers placed on a map (traps, bait stations, monitors, …). x/y are percents.
CREATE TABLE IF NOT EXISTS map_markers (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    map_id     INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    type       TEXT NOT NULL DEFAULT 'other',
    label      TEXT,
    x          REAL NOT NULL,
    y          REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'ok',
    notes      TEXT,
    qr_token   TEXT,                       -- unguessable id printed as a QR label
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Status history for map markers — powers pest-activity trend analytics.
-- One row per recorded inspection / status change of a device.
CREATE TABLE IF NOT EXISTS marker_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    marker_id   INTEGER NOT NULL REFERENCES map_markers(id) ON DELETE CASCADE,
    map_id      INTEGER NOT NULL REFERENCES maps(id) ON DELETE CASCADE,
    client_id   INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    type        TEXT,                       -- device type at time of record
    status      TEXT NOT NULL,              -- ok / needs_service / activity / missing
    note        TEXT,
    source      TEXT NOT NULL DEFAULT 'manual', -- 'scan' = recorded via QR tap-to-inspect
    lat         REAL,                       -- geo-stamp captured at scan time
    lng         REAL,
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_maps_client ON maps(client_id);
CREATE INDEX IF NOT EXISTS idx_markers_map ON map_markers(map_id);
-- NOTE: the UNIQUE index on map_markers(qr_token) is created in _migrate(), not
-- here, so it isn't attempted against a pre-existing table before the column is
-- added by the migration.
CREATE INDEX IF NOT EXISTS idx_marker_events_client ON marker_events(client_id, recorded_at);
CREATE INDEX IF NOT EXISTS idx_marker_events_marker ON marker_events(marker_id);
CREATE INDEX IF NOT EXISTS idx_visits_agent ON visits(agent_id);
CREATE INDEX IF NOT EXISTS idx_visits_client ON visits(client_id);
CREATE INDEX IF NOT EXISTS idx_photos_entity ON photos(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_invoices_client ON invoices(client_id);
CREATE INDEX IF NOT EXISTS idx_items_invoice ON invoice_items(invoice_id);
CREATE INDEX IF NOT EXISTS idx_contracts_client ON contracts(client_id);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, is_read);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notif_dedup ON notifications(dedup_key);

-- RBAC: per-role default permissions. A row overrides the code default for
-- (role, perm). Absence of a row means "use the built-in default".
CREATE TABLE IF NOT EXISTS role_permissions (
    role    TEXT NOT NULL CHECK (role IN ('admin','manager','agent','client')),
    perm    TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (role, perm)
);

-- RBAC: per-user overrides. A row overrides whatever the user's role resolves
-- to for (user_id, perm). Absence of a row means "inherit from role".
CREATE TABLE IF NOT EXISTS user_permissions (
    user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    perm    TEXT NOT NULL,
    allowed INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, perm)
);

-- Audit trail: who did what, on which record. Append-only.
CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    user_name   TEXT,                          -- denormalized for history
    action      TEXT NOT NULL,                 -- e.g. invoice.create
    entity      TEXT,                          -- e.g. invoice
    entity_id   TEXT,
    detail      TEXT,                          -- short human-readable note
    ip          TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log(created_at);

-- QR-coded field devices (light traps, glue/bait stations, fly traps). Admin
-- generates a batch of sequential codes (LIT0001…), assigns them to a client,
-- and prints them. Agents scan the printed code on a visit to file its report.
CREATE TABLE IF NOT EXISTS devices (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code       TEXT NOT NULL UNIQUE,           -- e.g. LIT0001 (printed as the QR)
    type       TEXT NOT NULL,                  -- light_trap|glue_station|bait_station|fly_trap
    client_id  INTEGER REFERENCES clients(id) ON DELETE SET NULL,
    site_id    INTEGER REFERENCES sites(id) ON DELETE SET NULL,
    label      TEXT,                           -- optional friendly location ("Kitchen")
    status     TEXT NOT NULL DEFAULT 'ok',     -- last-known: ok|activity|needs_service|missing
    active     INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_devices_client ON devices(client_id);
CREATE INDEX IF NOT EXISTS idx_devices_type ON devices(type);

-- One row per scan: a device's inspection during a visit (proof-of-presence +
-- the per-device part of the visit report). Time- and geo-stamped.
CREATE TABLE IF NOT EXISTS device_inspections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id   INTEGER NOT NULL REFERENCES devices(id) ON DELETE CASCADE,
    visit_id    INTEGER REFERENCES visits(id) ON DELETE SET NULL,
    client_id   INTEGER REFERENCES clients(id) ON DELETE CASCADE,
    status      TEXT NOT NULL,                 -- ok|activity|needs_service|missing
    findings    TEXT,                          -- what the agent recorded for this device
    note        TEXT,
    source      TEXT NOT NULL DEFAULT 'scan',
    lat         REAL,
    lng         REAL,
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dinsp_device ON device_inspections(device_id);
CREATE INDEX IF NOT EXISTS idx_dinsp_visit ON device_inspections(visit_id);
"""

# Additive migrations for databases created by an earlier version.
def _parse_latlng(text):
    """Best-effort extract (lat, lng) from a free-text location: a bare
    "lat,lng" pair or a Google Maps URL with an @lat,lng / q=lat,lng segment.
    Returns None when nothing plausible is found."""
    import re
    if not text:
        return None
    for pat in (r"@(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)",
                r"[?&]q=(-?\d{1,3}\.\d+),(-?\d{1,3}\.\d+)",
                r"^\s*(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\s*$"):
        m = re.search(pat, text)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180:
                return (lat, lng)
    return None


def _migrate(conn):
    def cols(table):
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    # report signature columns
    for c, ddl in (("customer_name", "TEXT"), ("customer_signature", "TEXT"),
                   ("technician_signature", "TEXT")):
        if c not in cols("reports"):
            conn.execute(f"ALTER TABLE reports ADD COLUMN {c} {ddl}")
    # engineer service-log columns (materials & parts consumed per visit)
    for c, ddl in (("spare_parts_changed", "TEXT"),
                   ("lamps_used", "REAL NOT NULL DEFAULT 0"),
                   ("cables_used", "REAL NOT NULL DEFAULT 0"),
                   ("transformers_used", "REAL NOT NULL DEFAULT 0"),
                   ("light_sheets_used", "REAL NOT NULL DEFAULT 0"),
                   ("fipronil_ml", "REAL NOT NULL DEFAULT 0"),
                   ("imidacloprid_gm", "REAL NOT NULL DEFAULT 0"),
                   ("baits_count", "REAL NOT NULL DEFAULT 0"),
                   ("glo_pieces", "REAL NOT NULL DEFAULT 0"),
                   ("flybase_bags", "REAL NOT NULL DEFAULT 0"),
                   ("branch_issue", "TEXT")):
        if c not in cols("reports"):
            conn.execute(f"ALTER TABLE reports ADD COLUMN {c} {ddl}")
    # report draft/complete workflow: agents auto-save drafts; a report is only
    # "complete" once the core fields + both signatures are present.
    if "status" not in cols("reports"):
        conn.execute("ALTER TABLE reports ADD COLUMN status TEXT NOT NULL DEFAULT 'draft'")
        # Legacy rows predate the workflow — treat them as complete so we don't
        # nag agents to re-finish historical reports.
        conn.execute("UPDATE reports SET status='complete'")
    if "completed_at" not in cols("reports"):
        conn.execute("ALTER TABLE reports ADD COLUMN completed_at TEXT")
        conn.execute("UPDATE reports SET completed_at=created_at "
                     "WHERE completed_at IS NULL AND status='complete'")
    # business-plan flag on attachments (surfaced on the printed report)
    if "is_business_plan" not in cols("photos"):
        conn.execute("ALTER TABLE photos ADD COLUMN is_business_plan INTEGER NOT NULL DEFAULT 0")
    # per-site uploaded map-design picture
    if "map_image" not in cols("sites"):
        conn.execute("ALTER TABLE sites ADD COLUMN map_image TEXT")
    # visit number within the year/month (1–12)
    if "visit_number" not in cols("visits"):
        conn.execute("ALTER TABLE visits ADD COLUMN visit_number INTEGER")
    # invoice columns
    for c, ddl in (("contract_id", "INTEGER"), ("doc_type", "TEXT DEFAULT 'invoice'"),
                   ("valid_until", "TEXT")):
        if c not in cols("invoices"):
            conn.execute(f"ALTER TABLE invoices ADD COLUMN {c} {ddl}")
    # invoice -> location link (per-location finance). On first add, backfill from
    # the linked visit's site so existing visit-invoices attribute to that location.
    if "site_id" not in cols("invoices"):
        conn.execute("ALTER TABLE invoices ADD COLUMN site_id INTEGER")
        conn.execute("UPDATE invoices SET site_id=(SELECT v.site_id FROM visits v WHERE v.id=invoices.visit_id) "
                     "WHERE visit_id IS NOT NULL")
    # token_version: bump to revoke a user's existing session tokens.
    # try/except guards against a race when two instances init the same DB at once.
    if "token_version" not in cols("users"):
        try:
            conn.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
        except sqlite3.OperationalError:
            pass  # another process added it first
    # Drop the old CHECK on invoices.status (rebuild) if present.
    ddl_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='invoices'").fetchone()
    if ddl_row and "CHECK (status IN" in (ddl_row[0] or ""):
        conn.executescript("""
            PRAGMA foreign_keys=OFF;
            CREATE TABLE invoices_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                client_id INTEGER NOT NULL, visit_id INTEGER, contract_id INTEGER,
                doc_type TEXT NOT NULL DEFAULT 'invoice', number TEXT NOT NULL,
                issue_date TEXT NOT NULL, due_date TEXT, valid_until TEXT,
                amount REAL NOT NULL DEFAULT 0, tax REAL NOT NULL DEFAULT 0,
                total REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'draft',
                notes TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO invoices_new (id,client_id,visit_id,contract_id,doc_type,number,
                issue_date,due_date,valid_until,amount,tax,total,status,notes,created_at)
                SELECT id,client_id,visit_id,contract_id,doc_type,number,issue_date,due_date,
                valid_until,amount,tax,total,status,notes,created_at FROM invoices;
            DROP TABLE invoices; ALTER TABLE invoices_new RENAME TO invoices;
            PRAGMA foreign_keys=ON;
        """)
    # Allow the 'issue' reason on inventory_transactions (engineer material
    # issues). Rebuild the table if its CHECK predates that value.
    inv_row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='inventory_transactions'").fetchone()
    if inv_row and "'issue'" not in (inv_row[0] or ""):
        conn.executescript("""
            PRAGMA foreign_keys=OFF;
            CREATE TABLE inventory_transactions_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chemical_id INTEGER NOT NULL REFERENCES chemicals(id) ON DELETE CASCADE,
                change REAL NOT NULL,
                reason TEXT NOT NULL CHECK (reason IN ('purchase','usage','adjustment','issue')),
                reference TEXT, note TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now')));
            INSERT INTO inventory_transactions_new (id,chemical_id,change,reason,reference,note,created_at)
                SELECT id,chemical_id,change,reason,reference,note,created_at FROM inventory_transactions;
            DROP TABLE inventory_transactions; ALTER TABLE inventory_transactions_new RENAME TO inventory_transactions;
            PRAGMA foreign_keys=ON;
        """)
    # Consumable materials (UV lamps, glue boards, etc.) are real inventory items
    # so they can be issued to engineers and tracked against the report counters
    # that record their use. material_key ties an inventory row to its report
    # column. Only ensure them here when the catalog already has rows (existing
    # DBs); on a fresh DB seed.py adds them AFTER the real chemicals so ids stay
    # in seed order.
    if "material_key" not in cols("chemicals"):
        conn.execute("ALTER TABLE chemicals ADD COLUMN material_key TEXT")
    if conn.execute("SELECT COUNT(*) FROM chemicals").fetchone()[0] > 0:
        ensure_material_items(conn)
    # QR-coded devices: every marker gets an unguessable token printed as a QR
    # label. Scanning it deep-links to /scan/<token> for 2-second tap-to-inspect.
    if "qr_token" not in cols("map_markers"):
        conn.execute("ALTER TABLE map_markers ADD COLUMN qr_token TEXT")
        # Backfill a random token for existing devices (randomblob = 32 hex chars).
        conn.execute("UPDATE map_markers SET qr_token=lower(hex(randomblob(16))) "
                     "WHERE qr_token IS NULL")
    # Created here (not in SCHEMA) so it runs only after the column is guaranteed
    # to exist — on both fresh and migrated databases.
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_markers_qr ON map_markers(qr_token)")
    # Per-type follow-up fields captured when a device is scanned (JSON blob:
    # e.g. bait_status/consumption_pct for bait stations, fly_count for light
    # traps …). Nullable — legacy status-only inspections leave it NULL.
    if "details" not in cols("device_inspections"):
        conn.execute("ALTER TABLE device_inspections ADD COLUMN details TEXT")
    # Recurring billing columns on contracts. Default OFF for existing contracts
    # (auto_invoice=0, next_bill_date NULL) so the migration never retro-bills
    # history — billing is opt-in per contract via the contract form.
    ccols = cols("contracts")
    if "bill_every" not in ccols:
        conn.execute("ALTER TABLE contracts ADD COLUMN bill_every TEXT")
    if "next_bill_date" not in ccols:
        conn.execute("ALTER TABLE contracts ADD COLUMN next_bill_date TEXT")
    if "auto_invoice" not in ccols:
        conn.execute("ALTER TABLE contracts ADD COLUMN auto_invoice INTEGER NOT NULL DEFAULT 0")
    # Tamper-evident scan trail: where (geo) and how (scan vs manual) each
    # inspection was recorded.
    me_cols = cols("marker_events")
    if "source" not in me_cols:
        conn.execute("ALTER TABLE marker_events ADD COLUMN source TEXT NOT NULL DEFAULT 'manual'")
    if "lat" not in me_cols:
        conn.execute("ALTER TABLE marker_events ADD COLUMN lat REAL")
    if "lng" not in me_cols:
        conn.execute("ALTER TABLE marker_events ADD COLUMN lng REAL")
    # Technician licensing/certification (surfaced on auditor Audit Packs).
    u_cols = cols("users")
    if "license_no" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN license_no TEXT")
    if "license_expiry" not in u_cols:
        conn.execute("ALTER TABLE users ADD COLUMN license_expiry TEXT")
    # Fast per-file upload authorization: /uploads/<file> looks the file up by
    # name to decide who may read it, so index the column.
    conn.execute("CREATE INDEX IF NOT EXISTS idx_photos_filename ON photos(filename)")
    # Site geo-coordinates for smart dispatch + route optimization. Backfill from
    # any contract_sites.map_location already stored as a "lat,lng" string.
    s_cols = cols("sites")
    if "lat" not in s_cols:
        conn.execute("ALTER TABLE sites ADD COLUMN lat REAL")
    if "lng" not in s_cols:
        conn.execute("ALTER TABLE sites ADD COLUMN lng REAL")
        for cs in conn.execute(
                "SELECT site_id, map_location FROM contract_sites "
                "WHERE site_id IS NOT NULL AND map_location IS NOT NULL").fetchall():
            ll = _parse_latlng(cs[1])
            if ll:
                conn.execute("UPDATE sites SET lat=?, lng=? WHERE id=? AND lat IS NULL",
                             (ll[0], ll[1], cs[0]))


# Consumable inventory items, keyed to their report counter column.
MATERIAL_ITEMS = (
    ("lamps_used",        "UV Lamp",                  "مصباح UV",      "pcs"),
    ("cables_used",       "Cable",                    "كابل",          "pcs"),
    ("transformers_used", "Transformer",              "محول",          "pcs"),
    ("light_sheets_used", "Light Sheet (Glue Board)", "لوح لاصق",      "pcs"),
    ("glo_pieces",        "Glo Board Piece",          "قطعة جلو",      "pcs"),
    ("flybase_bags",      "Flybase Bag",              "كيس فلاي بيس",  "bag"),
)


def ensure_material_items(conn):
    """Idempotently create the consumable inventory items (keyed on material_key)."""
    for key, en, ar, unit in MATERIAL_ITEMS:
        if not conn.execute("SELECT 1 FROM chemicals WHERE material_key=?", (key,)).fetchone():
            conn.execute(
                "INSERT INTO chemicals(name_en,name_ar,unit,quantity_in_stock,reorder_level,material_key) "
                "VALUES(?,?,?,0,0,?)", (en, ar, unit, key))


_local = threading.local()


def _new_conn():
    # timeout lets a writer wait instead of failing instantly on a locked DB.
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # busy_timeout makes contending writers wait up to 5s rather than erroring.
    # (journal_mode=WAL is persisted in the DB file by init_db, so it need not
    # be re-issued per connection — that just adds latency to every query.)
    conn.execute("PRAGMA busy_timeout = 5000")
    # synchronous=NORMAL is durable under WAL and markedly faster on writes.
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_conn():
    """One SQLite connection reused per thread. The server is threaded (one
    thread per keep-alive connection, each serving many queries), so caching the
    connection avoids reconnecting + re-issuing PRAGMAs on every single query.
    query()/execute()/transaction() always commit or roll back, so a cached
    connection is never left mid-transaction between calls. The connection is
    closed automatically when its thread ends (the threading.local is dropped)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = _new_conn()
        _local.conn = conn
    return conn


def close_conn():
    """Close and drop this thread's cached connection (if any)."""
    conn = getattr(_local, "conn", None)
    if conn is not None:
        try:
            conn.close()
        finally:
            _local.conn = None


@contextlib.contextmanager
def transaction():
    """Run several statements on one connection, committed atomically.

    Usage:
        with db.transaction() as cx:
            cx.execute(...); cx.execute(...)
    On any exception the whole block is rolled back. cx.execute returns the
    cursor, so cx.execute(...).lastrowid gives the new id.
    """
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _backfill_marker_events(conn):
    """Seed demo monitoring history once, so pest-trend charts are meaningful
    on existing databases. Only runs when there are markers but no events yet.
    Real events are recorded going forward whenever a device status is set."""
    has_events = conn.execute("SELECT 1 FROM marker_events LIMIT 1").fetchone()
    markers = conn.execute(
        "SELECT k.id, k.map_id, k.type, k.status, m.client_id "
        "FROM map_markers k JOIN maps m ON m.id=k.map_id").fetchall()
    if has_events or not markers:
        return
    import random
    random.seed(42)
    for mk in markers:
        # 6 monthly inspections; devices currently showing activity get
        # occasional historical detections so a trend emerges.
        prone = mk["status"] in ("activity", "needs_service")
        for months_ago in range(6, 0, -1):
            if prone and random.random() < 0.45:
                st = "activity"
            elif random.random() < 0.12:
                st = "needs_service"
            else:
                st = "ok"
            conn.execute(
                "INSERT INTO marker_events(marker_id,map_id,client_id,type,status,note,recorded_at) "
                "VALUES(?,?,?,?,?,?,datetime('now',?))",
                (mk["id"], mk["map_id"], mk["client_id"], mk["type"], st,
                 "Routine inspection", f"-{months_ago} months"))
        # a current-state event reflecting the device's present status
        conn.execute(
            "INSERT INTO marker_events(marker_id,map_id,client_id,type,status,note) "
            "VALUES(?,?,?,?,?,?)",
            (mk["id"], mk["map_id"], mk["client_id"], mk["type"], mk["status"], "Latest inspection"))


def init_db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    # Use a throwaway connection (not the thread-local cache) so the one-off
    # setup close doesn't invalidate a cached connection for this thread.
    conn = _new_conn()
    # WAL persists in the DB file; setting it once here lets every later
    # connection inherit it (concurrent readers alongside a writer).
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    _backfill_marker_events(conn)
    conn.commit()
    conn.close()


def query(sql, params=(), one=False):
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        rows = cur.fetchall()
    except Exception:
        # Leave the reused connection clean for the next call on this thread.
        conn.rollback()
        raise
    if one:
        return dict(rows[0]) if rows else None
    return [dict(r) for r in rows]


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE. Returns lastrowid."""
    conn = get_conn()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise


if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
