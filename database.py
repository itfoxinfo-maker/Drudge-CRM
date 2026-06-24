"""SQLite database layer for the Pest Control CRM.

Uses only the Python standard library. The schema is created on first run and
demo data is seeded by seed.py.
"""
import sqlite3
import os
import contextlib

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
    lang          TEXT NOT NULL DEFAULT 'en',
    active        INTEGER NOT NULL DEFAULT 1,
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
    reason      TEXT NOT NULL CHECK (reason IN ('purchase','usage','adjustment')),
    reference   TEXT,
    note        TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Unified photo store: client folders, reports, visits, chemicals.
CREATE TABLE IF NOT EXISTS photos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('client','report','visit','chemical')),
    entity_id     INTEGER NOT NULL,
    filename      TEXT NOT NULL,
    original_name TEXT,
    caption       TEXT,
    uploaded_by   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    uploaded_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Invoices and quotes (client finance). doc_type distinguishes the two.
-- status is free-text (app-controlled) to allow quote states (accepted/declined).
CREATE TABLE IF NOT EXISTS invoices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    client_id   INTEGER NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
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
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Key/value settings (company profile, tax rate, SMTP, etc.).
CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
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
    recorded_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_maps_client ON maps(client_id);
CREATE INDEX IF NOT EXISTS idx_markers_map ON map_markers(map_id);
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
"""

# Additive migrations for databases created by an earlier version.
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
    # invoice columns
    for c, ddl in (("contract_id", "INTEGER"), ("doc_type", "TEXT DEFAULT 'invoice'"),
                   ("valid_until", "TEXT")):
        if c not in cols("invoices"):
            conn.execute(f"ALTER TABLE invoices ADD COLUMN {c} {ddl}")
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


def get_conn():
    # timeout lets a writer wait instead of failing instantly on a locked DB.
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL allows concurrent readers alongside a writer (ThreadingHTTPServer);
    # busy_timeout makes contending writers wait up to 5s rather than erroring.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


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
    finally:
        conn.close()


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
    conn = get_conn()
    conn.executescript(SCHEMA)
    _migrate(conn)
    _backfill_marker_events(conn)
    conn.commit()
    conn.close()


def query(sql, params=(), one=False):
    conn = get_conn()
    cur = conn.execute(sql, params)
    rows = cur.fetchall()
    conn.close()
    if one:
        return dict(rows[0]) if rows else None
    return [dict(r) for r in rows]


def execute(sql, params=()):
    """Run an INSERT/UPDATE/DELETE. Returns lastrowid."""
    conn = get_conn()
    cur = conn.execute(sql, params)
    conn.commit()
    last_id = cur.lastrowid
    conn.close()
    return last_id


if __name__ == "__main__":
    init_db()
    print("Database initialized at", DB_PATH)
