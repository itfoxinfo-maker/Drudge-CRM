#!/usr/bin/env python3
"""Pest Control CRM — standard-library HTTP server.

Serves a JSON REST API + a bilingual (AR/EN) single-page frontend, with photo
uploads stored on disk. Run:  python3 server.py [port]
"""
import json
import os
import re
import sys
import uuid
import time
import threading
import traceback
import mimetypes
import gzip
import math
import hashlib
import urllib.request
from urllib.parse import urlparse, parse_qs, quote
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import database as db
import auth
from multipart import parse_multipart

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
mimetypes.add_type("application/manifest+json", ".webmanifest")  # PWA manifest
mimetypes.add_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx")
mimetypes.add_type("application/vnd.ms-excel", ".xls")
ALLOWED_IMG = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
ALLOWED_DOC = {".pdf", ".xls", ".xlsx"}          # report attachments
ALLOWED_UPLOAD = ALLOWED_IMG | ALLOWED_DOC
MAX_UPLOAD_BYTES = 25 * 1024 * 1024   # 25 MB per uploaded file
# Content types worth gzip-compressing (text assets); images are already packed.
_COMPRESSIBLE = {
    "text/html", "text/css", "text/plain", "text/javascript",
    "application/javascript", "application/json", "image/svg+xml",
    "application/manifest+json",
}


def _sniff_image(data: bytes) -> bool:
    """True if the bytes start with a known image signature (JPEG/PNG/GIF/WEBP)."""
    if len(data) < 12:
        return False
    if data[:3] == b"\xff\xd8\xff":                      # JPEG
        return True
    if data[:8] == b"\x89PNG\r\n\x1a\n":                  # PNG
        return True
    if data[:6] in (b"GIF87a", b"GIF89a"):               # GIF
        return True
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":    # WEBP
        return True
    return False


def _sniff_doc(ext: str, data: bytes) -> bool:
    """True if the bytes match the expected signature for a PDF or Excel file."""
    if ext == ".pdf":
        return data[:5] == b"%PDF-"
    if ext == ".xlsx":                              # OOXML = zip container
        return data[:4] == b"PK\x03\x04"
    if ext == ".xls":                               # legacy OLE2 compound file
        return data[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
    return False


def _validate_image_upload(f):
    """Validate an uploaded image dict from parse_multipart: extension, size and
    actual content signature. Raises ApiError on any problem."""
    ext = os.path.splitext(f["filename"])[1].lower()
    if ext not in ALLOWED_IMG:
        raise ApiError(400, "Only image files are allowed")
    data = f["data"]
    if not data:
        raise ApiError(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    if not _sniff_image(data):
        raise ApiError(400, "File is not a valid image")
    return ext


def _validate_upload(f):
    """Validate an attachment: image, PDF or Excel. Checks extension, size and the
    actual content signature so a renamed/forged file can't slip through."""
    ext = os.path.splitext(f["filename"])[1].lower()
    if ext not in ALLOWED_UPLOAD:
        raise ApiError(400, "Only image, PDF or Excel files are allowed")
    data = f["data"]
    if not data:
        raise ApiError(400, "Empty file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise ApiError(413, f"File too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB)")
    ok = _sniff_image(data) if ext in ALLOWED_IMG else _sniff_doc(ext, data)
    if not ok:
        raise ApiError(400, "File content does not match its type")
    return ext


class ApiError(Exception):
    def __init__(self, status, message):
        self.status = status
        self.message = message


class Ctx:
    def __init__(self, user, params, query, body, raw_body, content_type, ip=None):
        self.user = user
        self.params = params          # regex path groups
        self.query = query            # dict of query string (single values)
        self.body = body              # parsed JSON dict (or {})
        self.raw_body = raw_body      # raw bytes (for uploads)
        self.content_type = content_type
        self.ip = ip                  # client IP (for login throttling)


ROUTES = []


def route(method, pattern, auth_required=True):
    regex = re.compile("^" + pattern + "$")

    def deco(fn):
        ROUTES.append((method, regex, fn, auth_required))
        return fn
    return deco


# --------------------------------------------------------------------------
# permission helpers
# --------------------------------------------------------------------------
def client_scope_id(user):
    """For client users, the client_id they are limited to."""
    return user.get("client_id") if user["role"] == "client" else None


# --------------------------------------------------------------------------
# RBAC: feature catalog, role defaults and permission resolution
# --------------------------------------------------------------------------
# The catalog drives both the admin matrix UI and backend enforcement.
# Each module exposes a set of actions; a permission key is "module.action".
PERMISSION_CATALOG = [
    {"module": "dashboard",    "actions": ["view"]},
    {"module": "clients",      "actions": ["view", "create", "edit", "delete"]},
    {"module": "leads",        "actions": ["view", "create", "edit", "delete"]},
    {"module": "visits",       "actions": ["view", "create", "edit", "delete"]},
    {"module": "calendar",     "actions": ["view"]},
    {"module": "chemicals",    "actions": ["view", "create", "edit", "delete"]},
    {"module": "issues",       "actions": ["view", "create", "delete"]},
    {"module": "invoices",     "actions": ["view", "create", "edit", "delete"]},
    {"module": "payments",     "actions": ["view", "create", "delete"]},
    {"module": "contracts",    "actions": ["view", "create", "edit", "delete"]},
    {"module": "analytics",    "actions": ["view"]},
    {"module": "transport",    "actions": ["view"]},
    {"module": "certificates", "actions": ["view"]},
    {"module": "maps",         "actions": ["view", "create", "edit", "delete"]},
    {"module": "users",        "actions": ["view", "create", "edit", "delete"]},
    {"module": "settings",     "actions": ["view", "edit"]},
    {"module": "permissions",  "actions": ["view", "edit"]},
]

ROLES = ["admin", "manager", "agent", "client"]


def all_perms():
    return [f"{m['module']}.{a}" for m in PERMISSION_CATALOG for a in m["actions"]]


def _expand(spec):
    """Build a {perm: bool} map for a role from a compact spec.
    spec maps module -> True (all actions) | False (none) | list-of-actions.
    Any module not mentioned defaults to no access.
    """
    out = {}
    for m in PERMISSION_CATALOG:
        rule = spec.get(m["module"], False)
        for a in m["actions"]:
            if rule is True:
                out[f"{m['module']}.{a}"] = True
            elif rule is False:
                out[f"{m['module']}.{a}"] = False
            else:
                out[f"{m['module']}.{a}"] = a in rule
    return out


# Built-in per-role defaults. admin is a superuser (also hard-bypassed below).
ROLE_DEFAULTS = {
    "admin": _expand({m["module"]: True for m in PERMISSION_CATALOG}),
    "manager": _expand({
        "dashboard": True, "clients": True, "leads": True, "visits": True, "calendar": True,
        "chemicals": True, "issues": True, "invoices": True, "payments": True,
        "contracts": True, "analytics": True, "certificates": True, "maps": True,
        "transport": True, "users": True, "settings": True, "permissions": False,
    }),
    "agent": _expand({
        "dashboard": ["view"], "clients": ["view"], "visits": ["view", "edit"],
        "calendar": ["view"], "chemicals": ["view"], "certificates": ["view"],
        "issues": ["view", "create"], "maps": ["view", "create", "edit"],
        "transport": ["view"],
    }),
    "client": _expand({
        "dashboard": ["view"], "visits": ["view"], "invoices": ["view"],
        "contracts": ["view"], "certificates": ["view"],
    }),
}


def _role_overrides(role):
    return {r["perm"]: bool(r["allowed"])
            for r in db.query("SELECT perm, allowed FROM role_permissions WHERE role=?", (role,))}


def _user_overrides(user_id):
    return {r["perm"]: bool(r["allowed"])
            for r in db.query("SELECT perm, allowed FROM user_permissions WHERE user_id=?", (user_id,))}


def effective_role_perms(role):
    """Resolved {perm: bool} for a role = defaults overlaid with role overrides."""
    perms = dict(ROLE_DEFAULTS.get(role, {}))
    perms.update(_role_overrides(role))
    return perms


def effective_user_perms(user):
    """Resolved {perm: bool} for a specific user = role perms + user overrides.
    admin is always granted everything."""
    if user["role"] == "admin":
        return {p: True for p in all_perms()}
    perms = effective_role_perms(user["role"])
    perms.update(_user_overrides(user["id"]))
    return perms


def has_perm(user, perm):
    if user["role"] == "admin":
        return True
    return effective_user_perms(user).get(perm, False)


def require_perm(user, perm):
    if not has_perm(user, perm):
        raise ApiError(403, "You do not have permission for this action")


def audit(ctx, action, entity=None, entity_id=None, detail=None):
    """Append an entry to the audit trail. Never raises (best-effort)."""
    try:
        u = ctx.user or {}
        db.execute(
            "INSERT INTO audit_log(user_id,user_name,action,entity,entity_id,detail,ip) "
            "VALUES(?,?,?,?,?,?,?)",
            (u.get("id"), u.get("full_name"), action, entity,
             None if entity_id is None else str(entity_id), detail, getattr(ctx, "ip", None)))
    except Exception:
        traceback.print_exc()


def _paginate(ctx, base_sql, params, order_sql, default_limit=25, max_limit=200):
    """Page a query when the request asks for it.

    base_sql: a complete SELECT (with any WHERE) but no ORDER BY / LIMIT.
    order_sql: the ORDER BY clause string.
    Returns the plain row list when neither ?page nor ?limit is given (keeps
    existing callers/dropdowns working); otherwise an envelope
    {items, total, page, pages, limit}.
    """
    params = list(params)
    q = ctx.query
    if "page" not in q and "limit" not in q:
        return db.query(f"{base_sql} {order_sql}", params)
    try:
        limit = min(max(int(q.get("limit", default_limit)), 1), max_limit)
    except (TypeError, ValueError):
        limit = default_limit
    try:
        page = max(int(q.get("page", 1)), 1)
    except (TypeError, ValueError):
        page = 1
    total = db.query(f"SELECT COUNT(*) c FROM ({base_sql})", params, one=True)["c"]
    rows = db.query(f"{base_sql} {order_sql} LIMIT ? OFFSET ?", params + [limit, (page - 1) * limit])
    pages = (total + limit - 1) // limit if limit else 1
    return {"items": rows, "total": total, "page": page, "pages": pages, "limit": limit}


# A photo's required permission depends on the entity it is attached to.
_PHOTO_ENTITY_PERM = {
    "client": "clients.edit", "report": "visits.edit",
    "visit": "visits.edit", "chemical": "chemicals.edit",
}


# --------------------------------------------------------------------------
# AUTH
# --------------------------------------------------------------------------
# In-memory login throttle: lock an (email|ip) key after repeated failures.
LOGIN_MAX_FAILS = 5
LOGIN_WINDOW = 300       # seconds to accumulate failures
LOGIN_LOCK_SECS = 300    # lockout duration once the threshold is hit
_login_lock = threading.Lock()
_login_attempts = {}     # key -> [fail_count, window_start, locked_until]


def _login_locked_for(key):
    """Return seconds remaining if the key is currently locked, else 0."""
    now = time.time()
    with _login_lock:
        rec = _login_attempts.get(key)
        if rec and rec[2] > now:
            return int(rec[2] - now) + 1
    return 0


def _login_register_fail(key):
    now = time.time()
    with _login_lock:
        # Opportunistically drop stale entries so the map can't grow unbounded
        # under a spray of distinct email|ip keys.
        if len(_login_attempts) > 256:
            for k in [k for k, v in _login_attempts.items()
                      if v[2] < now and now - v[1] > LOGIN_WINDOW]:
                _login_attempts.pop(k, None)
        rec = _login_attempts.get(key)
        if not rec or now - rec[1] > LOGIN_WINDOW:
            rec = [0, now, 0]
        rec[0] += 1
        if rec[0] >= LOGIN_MAX_FAILS:
            rec[2] = now + LOGIN_LOCK_SECS
        _login_attempts[key] = rec


def _login_register_ok(key):
    with _login_lock:
        _login_attempts.pop(key, None)


# Generic fixed-window, per-key rate limiter (in-memory, best-effort). Blunts
# abuse of unauthenticated endpoints such as the payment webhook. Login has its
# own lockout logic above; authenticated endpoints (e.g. /api/scan/*) are gated
# by session + permission and are intentionally NOT throttled here, so shared
# office/NAT IPs don't lock out legitimate field agents.
_rate_lock = threading.Lock()
_rate_hits = {}          # key -> [count, window_start]


def rate_limit(key, max_hits, window=60):
    """Return True if `key` is still within its budget for the window, else
    False (caller should reject with 429). Evicts stale keys to bound memory."""
    now = time.time()
    with _rate_lock:
        if len(_rate_hits) > 4096:
            for k in [k for k, v in _rate_hits.items() if now - v[1] > window]:
                _rate_hits.pop(k, None)
        rec = _rate_hits.get(key)
        if not rec or now - rec[1] > window:
            rec = [0, now]
        rec[0] += 1
        _rate_hits[key] = rec
        return rec[0] <= max_hits


@route("GET", r"/api/health", auth_required=False)
def health(ctx):
    """Liveness probe for monitor.py: proves the HTTP loop and DB both answer.
    Unauthenticated by design; returns no business data."""
    db.query("SELECT 1 AS ok", one=True)
    return {"ok": True, "db": "ok"}


@route("POST", r"/api/auth/login", auth_required=False)
def login(ctx):
    email = (ctx.body.get("email") or "").strip().lower()
    password = ctx.body.get("password") or ""
    key = f"{email}|{ctx.ip or '?'}"
    locked = _login_locked_for(key)
    if locked:
        raise ApiError(429, f"Too many failed attempts. Try again in {locked} seconds.")
    user = db.query("SELECT * FROM users WHERE lower(email)=? AND active=1", (email,), one=True)
    if not user or not auth.verify_password(password, user["password_hash"]):
        _login_register_fail(key)
        raise ApiError(401, "Invalid email or password")
    _login_register_ok(key)
    token = auth.make_token(user["id"], user["token_version"] or 0)
    return {"token": token, "user": _me_payload(user)}


@route("GET", r"/api/auth/me")
def me(ctx):
    return _me_payload(ctx.user)


@route("POST", r"/api/auth/logout")
def logout(ctx):
    # Bump token_version so every outstanding token for this user is rejected.
    db.execute("UPDATE users SET token_version = token_version + 1 WHERE id=?", (ctx.user["id"],))
    return {"ok": True}


def _me_payload(u):
    """Public profile plus the user's resolved permission map (for UI gating)."""
    return _public_user(u) | {"permissions": effective_user_perms(u)}


def _public_user(u):
    return {k: u[k] for k in ("id", "full_name", "email", "role", "phone",
                              "client_id", "specialization", "license_no",
                              "license_expiry", "lang")}


# --------------------------------------------------------------------------
# DASHBOARD
# --------------------------------------------------------------------------
@route("GET", r"/api/dashboard")
def dashboard(ctx):
    u = ctx.user
    if u["role"] == "client":
        cid = u["client_id"]
        return {
            "role": "client",
            "upcoming_visits": db.query(
                "SELECT COUNT(*) c FROM visits WHERE client_id=? AND status IN('scheduled','in_progress')",
                (cid,), one=True)["c"],
            "completed_visits": db.query(
                "SELECT COUNT(*) c FROM visits WHERE client_id=? AND status='completed'", (cid,), one=True)["c"],
            "outstanding": _client_outstanding(cid),
            "open_invoices": db.query(
                "SELECT COUNT(*) c FROM invoices WHERE client_id=? AND status IN('sent','overdue')",
                (cid,), one=True)["c"],
        }
    stats = {
        "role": "staff",
        "clients": db.query("SELECT COUNT(*) c FROM clients", one=True)["c"],
        "agents": db.query("SELECT COUNT(*) c FROM users WHERE role='agent' AND active=1", one=True)["c"],
        "visits_today": db.query(
            "SELECT COUNT(*) c FROM visits WHERE date(scheduled_start)=date('now')", one=True)["c"],
        "scheduled": db.query(
            "SELECT COUNT(*) c FROM visits WHERE status IN('scheduled','in_progress')", one=True)["c"],
        "low_stock": db.query(
            "SELECT COUNT(*) c FROM chemicals WHERE quantity_in_stock <= reorder_level", one=True)["c"],
        "outstanding": db.query(
            "SELECT COALESCE(SUM(total),0)-COALESCE((SELECT SUM(amount) FROM payments),0) v "
            "FROM invoices WHERE status IN('sent','overdue','paid')", one=True)["v"],
    }
    if u["role"] == "agent":
        stats["my_visits"] = db.query(
            "SELECT COUNT(*) c FROM visits WHERE agent_id=? AND status IN('scheduled','in_progress')",
            (u["id"],), one=True)["c"]
    # Owner cockpit: a single-glance KPI block (revenue, overdue billing, SLA
    # health, technician utilization) for admins/managers who can see finance +
    # analytics. Agents/clients never get it.
    if has_perm(u, "analytics.view"):
        stats["cockpit"] = _owner_cockpit()
    stats["devices"] = _device_overview()
    return stats


# A device is "stale" (overdue for a scan) when it's active + assigned and its
# last inspection is older than this many days — or it was never scanned.
STALE_SCAN_DAYS = 30
_STALE_PRED = ("d.active=1 AND d.client_id IS NOT NULL AND COALESCE("
               "(SELECT MAX(recorded_at) FROM device_inspections di WHERE di.device_id=d.id),'0')"
               " < datetime('now','-%d days')" % STALE_SCAN_DAYS)


def _stale_count(scope="", params=()):
    return db.query("SELECT COUNT(*) c FROM devices d WHERE " + _STALE_PRED + scope,
                    tuple(params), one=True)["c"]


def _stale_devices(limit=None, scope="", params=()):
    """Assigned devices overdue for a scan, oldest (incl. never-scanned) first."""
    sql = ("SELECT d.id, d.code, d.type, d.label loc, c.name_en client_en, "
           "c.name_ar client_ar, s.name site_name, "
           "(SELECT MAX(recorded_at) FROM device_inspections di WHERE di.device_id=d.id) last_seen "
           "FROM devices d LEFT JOIN clients c ON c.id=d.client_id "
           "LEFT JOIN sites s ON s.id=d.site_id WHERE " + _STALE_PRED + scope +
           " ORDER BY last_seen IS NOT NULL, last_seen ASC")
    if limit:
        sql += " LIMIT %d" % int(limit)
    return db.query(sql, tuple(params))


def _device_overview():
    """QR device fleet health for the staff dashboard: how many devices exist,
    how many need service, pest-activity detections this month, what share of
    the fleet was scanned this month (coverage), and how many are overdue."""
    one = lambda q: db.query(q, one=True)["c"]
    total = one("SELECT COUNT(*) c FROM devices WHERE active=1 AND client_id IS NOT NULL")
    scanned = one("SELECT COUNT(DISTINCT device_id) c FROM device_inspections "
                  "WHERE strftime('%Y-%m',recorded_at)=strftime('%Y-%m','now')")
    return {
        "total": total,
        "needs_service": one("SELECT COUNT(*) c FROM devices WHERE active=1 AND status='needs_service'"),
        "activity_month": one("SELECT COUNT(*) c FROM device_inspections "
                              "WHERE status='activity' AND strftime('%Y-%m',recorded_at)=strftime('%Y-%m','now')"),
        "coverage": round(100.0 * scanned / total) if total else None,
        "stale": _stale_count(),
        "stale_days": STALE_SCAN_DAYS,
    }


def _owner_cockpit():
    """Aggregate owner KPIs: revenue (this vs last month), overdue receivables,
    SLA health, and per-technician utilization for the current month."""
    rev_month = db.query(
        "SELECT COALESCE(SUM(amount),0) v FROM payments "
        "WHERE strftime('%Y-%m', paid_at)=strftime('%Y-%m','now')", one=True)["v"]
    rev_prev = db.query(
        "SELECT COALESCE(SUM(amount),0) v FROM payments "
        "WHERE strftime('%Y-%m', paid_at)=strftime('%Y-%m','now','start of month','-1 month')",
        one=True)["v"]
    overdue = db.query(
        "SELECT COUNT(*) c, COALESCE(SUM(total - COALESCE("
        "(SELECT SUM(amount) FROM payments p WHERE p.invoice_id=i.id),0)),0) amt "
        "FROM invoices i WHERE doc_type='invoice' AND status IN('sent','overdue') "
        "AND due_date IS NOT NULL AND date(due_date) < date('now')", one=True)
    sla_counts = {"ok": 0, "due_soon": 0, "overdue": 0}
    for r in _sla_rows():
        sla_counts[r["status"]] += 1
    # Technician utilization for the current calendar month: assigned vs completed.
    util = []
    for a in db.query(
            "SELECT u.id, u.full_name, COUNT(v.id) total, "
            "SUM(CASE WHEN v.status='completed' THEN 1 ELSE 0 END) completed, "
            "(SELECT ROUND(AVG(vr.stars),1) FROM visit_ratings vr "
            " JOIN visits v2 ON v2.id=vr.visit_id WHERE v2.agent_id=u.id) rating "
            "FROM users u LEFT JOIN visits v ON v.agent_id=u.id "
            "AND strftime('%Y-%m', v.scheduled_start)=strftime('%Y-%m','now') "
            "WHERE u.role='agent' AND u.active=1 GROUP BY u.id ORDER BY total DESC, completed DESC"):
        total = a["total"] or 0
        completed = a["completed"] or 0
        util.append({
            "agent_id": a["id"], "name": a["full_name"], "total": total,
            "completed": completed, "rating": a["rating"],
            "rate": round(completed * 100.0 / total) if total else 0,
        })
    return {
        "revenue_month": rev_month, "revenue_prev": rev_prev,
        "overdue_invoices": overdue["c"], "overdue_amount": overdue["amt"],
        "sla": sla_counts, "utilization": util,
    }


# --------------------------------------------------------------------------
# USERS / AGENTS
# --------------------------------------------------------------------------
@route("GET", r"/api/users")
def list_users(ctx):
    require_perm(ctx.user, "users.view")
    role = ctx.query.get("role")
    if role:
        rows = db.query("SELECT * FROM users WHERE role=? ORDER BY full_name", (role,))
    else:
        rows = db.query("SELECT * FROM users ORDER BY role, full_name")
    return [_public_user(r) | {"active": r["active"], "hire_date": r["hire_date"]} for r in rows]


@route("GET", r"/api/agents")
def list_agents(ctx):
    require_perm(ctx.user, "users.view")
    rows = db.query("SELECT * FROM users WHERE role='agent' AND active=1 ORDER BY full_name")
    return [_public_user(r) | {"hire_date": r["hire_date"]} for r in rows]


@route("POST", r"/api/users")
def create_user(ctx):
    require_perm(ctx.user, "users.create")
    b = ctx.body
    if not b.get("email") or not b.get("password") or not b.get("full_name"):
        raise ApiError(400, "Name, email and password are required")
    if b.get("role") not in ("admin", "manager", "agent", "client"):
        raise ApiError(400, "Invalid role")
    # Only an admin may mint another admin (prevents privilege escalation by a
    # manager who merely holds users.create).
    if b["role"] == "admin" and ctx.user["role"] != "admin":
        raise ApiError(403, "Only an admin can create an admin user")
    if db.query("SELECT id FROM users WHERE lower(email)=?", (b["email"].lower(),), one=True):
        raise ApiError(409, "Email already in use")
    uid = db.execute(
        "INSERT INTO users(full_name,email,password_hash,role,phone,client_id,specialization,"
        "hire_date,license_no,license_expiry,lang) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (b["full_name"], b["email"], auth.hash_password(b["password"]), b["role"],
         b.get("phone"), b.get("client_id"), b.get("specialization"), b.get("hire_date"),
         b.get("license_no"), b.get("license_expiry"), b.get("lang", "en")))
    audit(ctx, "user.create", "user", uid, f"{b['role']} {b['email']}")
    return _public_user(db.query("SELECT * FROM users WHERE id=?", (uid,), one=True))


@route("PUT", r"/api/users/(\d+)")
def update_user(ctx):
    require_perm(ctx.user, "users.edit")
    uid = int(ctx.params[0])
    b = ctx.body
    target = db.query("SELECT role FROM users WHERE id=?", (uid,), one=True)
    if not target:
        raise ApiError(404, "User not found")
    # Guard the admin role: only an admin may edit an admin account or grant the
    # admin role — otherwise a manager could elevate themselves / reset the admin.
    if ctx.user["role"] != "admin":
        if target["role"] == "admin":
            raise ApiError(403, "Only an admin can modify an admin account")
        if b.get("role") == "admin":
            raise ApiError(403, "Only an admin can grant the admin role")
    fields, vals = [], []
    for col in ("full_name", "phone", "role", "client_id", "specialization", "hire_date",
                "license_no", "license_expiry", "lang", "active"):
        if col in b:
            fields.append(f"{col}=?")
            vals.append(b[col])
    if b.get("password"):
        fields.append("password_hash=?")
        vals.append(auth.hash_password(b["password"]))
        # Changing the password revokes the user's existing session tokens.
        fields.append("token_version=token_version+1")
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(uid)
    db.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", vals)
    audit(ctx, "user.update", "user", uid,
          ("password reset; " if b.get("password") else "") + ", ".join(f for f in b if f != "password"))
    return _public_user(db.query("SELECT * FROM users WHERE id=?", (uid,), one=True))


@route("DELETE", r"/api/users/(\d+)")
def deactivate_user(ctx):
    require_perm(ctx.user, "users.delete")
    uid = int(ctx.params[0])
    target = db.query("SELECT role FROM users WHERE id=?", (uid,), one=True)
    if target and target["role"] == "admin" and ctx.user["role"] != "admin":
        raise ApiError(403, "Only an admin can deactivate an admin account")
    db.execute("UPDATE users SET active=0 WHERE id=?", (uid,))
    audit(ctx, "user.delete", "user", uid, "deactivated")
    return {"ok": True}


# --------------------------------------------------------------------------
# RBAC — roles & per-user permission management (admin only)
# --------------------------------------------------------------------------
@route("GET", r"/api/permissions/catalog")
def permissions_catalog(ctx):
    require_perm(ctx.user, "permissions.view")
    return {
        "catalog": PERMISSION_CATALOG,
        "roles": ROLES,
        "defaults": ROLE_DEFAULTS,
        # Effective (defaults + saved overrides) matrix for every role.
        "roles_effective": {r: effective_role_perms(r) for r in ROLES},
        "role_overrides": {r: _role_overrides(r) for r in ROLES},
    }


@route("PUT", r"/api/permissions/roles/(\w+)")
def update_role_permissions(ctx):
    require_perm(ctx.user, "permissions.edit")
    role = ctx.params[0]
    if role not in ROLES:
        raise ApiError(400, "Invalid role")
    if role == "admin":
        raise ApiError(400, "The admin role always has full access and cannot be edited")
    valid = set(all_perms())
    perms = ctx.body.get("perms") or {}
    defaults = ROLE_DEFAULTS.get(role, {})
    for perm, val in perms.items():
        if perm not in valid:
            continue
        allowed = 1 if val else 0
        # Store only true overrides; if it matches the built-in default, drop it.
        if (perm in defaults) and (bool(allowed) == defaults[perm]):
            db.execute("DELETE FROM role_permissions WHERE role=? AND perm=?", (role, perm))
        else:
            db.execute(
                "INSERT INTO role_permissions(role,perm,allowed) VALUES(?,?,?) "
                "ON CONFLICT(role,perm) DO UPDATE SET allowed=excluded.allowed",
                (role, perm, allowed))
    audit(ctx, "permissions.role.update", "role", role,
          f"set {len(perms)} permission(s) for role '{role}'")
    return {"role": role, "effective": effective_role_perms(role),
            "overrides": _role_overrides(role)}


@route("GET", r"/api/permissions/users/(\d+)")
def get_user_permissions(ctx):
    require_perm(ctx.user, "permissions.view")
    uid = int(ctx.params[0])
    u = db.query("SELECT id, full_name, role FROM users WHERE id=?", (uid,), one=True)
    if not u:
        raise ApiError(404, "User not found")
    return {
        "user": u,
        "role_effective": effective_role_perms(u["role"]),  # what they inherit
        "effective": effective_user_perms(u),               # final resolved
        "overrides": _user_overrides(uid),                  # per-user only
    }


@route("PUT", r"/api/permissions/users/(\d+)")
def update_user_permissions(ctx):
    require_perm(ctx.user, "permissions.edit")
    uid = int(ctx.params[0])
    u = db.query("SELECT id, role FROM users WHERE id=?", (uid,), one=True)
    if not u:
        raise ApiError(404, "User not found")
    if u["role"] == "admin":
        raise ApiError(400, "Admin users always have full access and cannot be edited")
    valid = set(all_perms())
    # perms maps perm -> true | false | null. null clears the override (inherit).
    perms = ctx.body.get("perms") or {}
    for perm, val in perms.items():
        if perm not in valid:
            continue
        if val is None:
            db.execute("DELETE FROM user_permissions WHERE user_id=? AND perm=?", (uid, perm))
        else:
            db.execute(
                "INSERT INTO user_permissions(user_id,perm,allowed) VALUES(?,?,?) "
                "ON CONFLICT(user_id,perm) DO UPDATE SET allowed=excluded.allowed",
                (uid, perm, 1 if val else 0))
    full = db.query("SELECT * FROM users WHERE id=?", (uid,), one=True)
    audit(ctx, "permissions.user.update", "user", uid,
          f"set {len(perms)} per-user override(s)")
    return {"user_id": uid, "effective": effective_user_perms(full),
            "overrides": _user_overrides(uid)}


@route("GET", r"/api/audit")
def list_audit(ctx):
    require_perm(ctx.user, "permissions.view")
    limit = min(int(ctx.query.get("limit", 100) or 100), 500)
    return db.query("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))


# --------------------------------------------------------------------------
# CLIENTS (company folders)
# --------------------------------------------------------------------------
@route("GET", r"/api/clients")
def list_clients(ctx):
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "clients.view")
    cid = client_scope_id(ctx.user)
    q = ctx.query.get("q", "").strip()
    sql = "SELECT * FROM clients"
    params = []
    where = []
    if cid:
        where.append("id=?")
        params.append(cid)
    if q:
        where.append("(name_en LIKE ? OR name_ar LIKE ? OR city LIKE ? OR contact_person LIKE ?)")
        params += [f"%{q}%"] * 4
    if where:
        sql += " WHERE " + " AND ".join(where)
    return _paginate(ctx, sql, params, "ORDER BY name_en")


@route("GET", r"/api/clients/(\d+)")
def get_client(ctx):
    cid = int(ctx.params[0])
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "clients.view")
    _assert_client_access(ctx.user, cid)
    client = db.query("SELECT * FROM clients WHERE id=?", (cid,), one=True)
    if not client:
        raise ApiError(404, "Client not found")
    client["sites"] = db.query("SELECT * FROM sites WHERE client_id=? ORDER BY name", (cid,))
    client["photos"] = db.query(
        "SELECT * FROM photos WHERE entity_type='client' AND entity_id=? ORDER BY uploaded_at DESC", (cid,))
    client["recent_visits"] = db.query(
        "SELECT v.*, s.name_en service_en, s.name_ar service_ar, u.full_name agent_name "
        "FROM visits v LEFT JOIN service_types s ON s.id=v.service_type_id "
        "LEFT JOIN users u ON u.id=v.agent_id WHERE v.client_id=? "
        "ORDER BY v.scheduled_start DESC LIMIT 10", (cid,))
    # Finance is money data: clients see their own; staff need invoices.view
    # (so an agent with only clients.view doesn't see the company's finances).
    if ctx.user["role"] == "client" or has_perm(ctx.user, "invoices.view"):
        client["finance"] = _finance_summary(cid)
    else:
        client["finance"] = None
    if ctx.user["role"] == "client":
        client["users"] = []
    else:
        client["users"] = [_public_user(r) for r in
                           db.query("SELECT * FROM users WHERE client_id=?", (cid,))]
    return client


@route("POST", r"/api/clients")
def create_client(ctx):
    require_perm(ctx.user, "clients.create")
    b = ctx.body
    if not b.get("name_en"):
        raise ApiError(400, "Company name (English) is required")
    cid = db.execute(
        "INSERT INTO clients(name_en,name_ar,contact_person,phone,email,address_en,address_ar,city,notes,status) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (b["name_en"], b.get("name_ar"), b.get("contact_person"), b.get("phone"), b.get("email"),
         b.get("address_en"), b.get("address_ar"), b.get("city"), b.get("notes"),
         b.get("status", "active")))
    return db.query("SELECT * FROM clients WHERE id=?", (cid,), one=True)


@route("PUT", r"/api/clients/(\d+)")
def update_client(ctx):
    require_perm(ctx.user, "clients.edit")
    cid = int(ctx.params[0])
    b = ctx.body
    cols = ("name_en", "name_ar", "contact_person", "phone", "email",
            "address_en", "address_ar", "city", "notes", "status")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(cid)
    db.execute(f"UPDATE clients SET {','.join(fields)} WHERE id=?", vals)
    return db.query("SELECT * FROM clients WHERE id=?", (cid,), one=True)


@route("DELETE", r"/api/clients/(\d+)")
def delete_client(ctx):
    require_perm(ctx.user, "clients.delete")
    db.execute("DELETE FROM clients WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


@route("GET", r"/api/sites")
def list_all_sites(ctx):
    """Every site/location across all clients (powers the Locations sidebar view)."""
    u = ctx.user
    if u["role"] != "client":
        require_perm(u, "clients.view")
    where, params = [], []
    if u["role"] == "client":
        where.append("s.client_id=?"); params.append(u["client_id"])
    if ctx.query.get("client"):
        where.append("s.client_id=?"); params.append(ctx.query["client"])
    sql = ("SELECT s.id, s.client_id, s.name, s.address, s.area, s.map_image, "
           "s.lat, s.lng, s.created_at, c.name_en client_en, c.name_ar client_ar "
           "FROM sites s JOIN clients c ON c.id=s.client_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.name_en, s.name"
    return db.query(sql, params)


@route("POST", r"/api/clients/(\d+)/sites")
def add_site(ctx):
    require_perm(ctx.user, "clients.edit")
    cid = int(ctx.params[0])
    b = ctx.body
    if not b.get("name"):
        raise ApiError(400, "Site name is required")
    lat, lng = _coerce_latlng(b)
    sid = db.execute("INSERT INTO sites(client_id,name,address,area,lat,lng) VALUES(?,?,?,?,?,?)",
                     (cid, b["name"], b.get("address"), b.get("area"), lat, lng))
    return db.query("SELECT * FROM sites WHERE id=?", (sid,), one=True)


@route("PUT", r"/api/sites/(\d+)")
def update_site(ctx):
    require_perm(ctx.user, "clients.edit")
    sid = int(ctx.params[0])
    if not db.query("SELECT 1 FROM sites WHERE id=?", (sid,), one=True):
        raise ApiError(404, "Site not found")
    b = ctx.body
    fields, vals = [], []
    for col in ("name", "address", "area"):
        if col in b:
            fields.append(f"{col}=?"); vals.append(b[col])
    if "lat" in b or "lng" in b or "geo" in b:
        lat, lng = _coerce_latlng(b)
        fields += ["lat=?", "lng=?"]; vals += [lat, lng]
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(sid)
    db.execute(f"UPDATE sites SET {','.join(fields)} WHERE id=?", vals)
    return db.query("SELECT * FROM sites WHERE id=?", (sid,), one=True)


@route("DELETE", r"/api/sites/(\d+)")
def delete_site(ctx):
    require_perm(ctx.user, "clients.edit")
    db.execute("DELETE FROM sites WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


@route("POST", r"/api/sites/(\d+)/map")
def upload_site_map(ctx):
    """Upload (or replace) the map-design picture for a site."""
    require_perm(ctx.user, "clients.edit")
    sid = int(ctx.params[0])
    site = db.query("SELECT * FROM sites WHERE id=?", (sid,), one=True)
    if not site:
        raise ApiError(404, "Site not found")
    _fields, files = parse_multipart(ctx.raw_body, ctx.content_type)
    if not files:
        raise ApiError(400, "No file uploaded")
    f = files[0]
    ext = _validate_image_upload(f)
    fname = f"sitemap_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as out:
        out.write(f["data"])
    # remove the previous image file, if any
    if site.get("map_image"):
        try:
            os.remove(os.path.join(UPLOAD_DIR, site["map_image"]))
        except OSError:
            pass
    db.execute("UPDATE sites SET map_image=? WHERE id=?", (fname, sid))
    return db.query("SELECT * FROM sites WHERE id=?", (sid,), one=True)


@route("DELETE", r"/api/sites/(\d+)/map")
def delete_site_map(ctx):
    require_perm(ctx.user, "clients.edit")
    sid = int(ctx.params[0])
    site = db.query("SELECT * FROM sites WHERE id=?", (sid,), one=True)
    if site and site.get("map_image"):
        try:
            os.remove(os.path.join(UPLOAD_DIR, site["map_image"]))
        except OSError:
            pass
    db.execute("UPDATE sites SET map_image=NULL WHERE id=?", (sid,))
    return {"ok": True}


# --------------------------------------------------------------------------
# SERVICE TYPES
# --------------------------------------------------------------------------
@route("GET", r"/api/service-types")
def list_service_types(ctx):
    return db.query("SELECT * FROM service_types ORDER BY name_en")


@route("POST", r"/api/service-types")
def create_service_type(ctx):
    require_perm(ctx.user, "settings.edit")
    b = ctx.body
    if not b.get("name_en"):
        raise ApiError(400, "Name is required")
    sid = db.execute("INSERT INTO service_types(name_en,name_ar) VALUES(?,?)",
                     (b["name_en"], b.get("name_ar")))
    return db.query("SELECT * FROM service_types WHERE id=?", (sid,), one=True)


# --------------------------------------------------------------------------
# VISITS & SCHEDULE
# --------------------------------------------------------------------------
@route("GET", r"/api/visits")
def list_visits(ctx):
    u = ctx.user
    require_perm(u, "visits.view")
    where, params = [], []
    if u["role"] == "client":
        where.append("v.client_id=?")
        params.append(u["client_id"])
    elif u["role"] == "agent":
        where.append("v.agent_id=?")
        params.append(u["id"])
    for key, col in (("client", "v.client_id"), ("agent", "v.agent_id"),
                     ("status", "v.status")):
        if ctx.query.get(key):
            where.append(f"{col}=?")
            params.append(ctx.query[key])
    if ctx.query.get("from"):
        where.append("date(v.scheduled_start) >= date(?)")
        params.append(ctx.query["from"])
    if ctx.query.get("to"):
        where.append("date(v.scheduled_start) <= date(?)")
        params.append(ctx.query["to"])
    sql = ("SELECT v.*, c.name_en client_en, c.name_ar client_ar, "
           "s.name_en service_en, s.name_ar service_ar, u.full_name agent_name, st.name site_name, "
           "st.lat site_lat, st.lng site_lng, "
           "EXISTS(SELECT 1 FROM reports r WHERE r.visit_id=v.id AND r.status='complete') has_report "
           "FROM visits v JOIN clients c ON c.id=v.client_id "
           "LEFT JOIN service_types s ON s.id=v.service_type_id "
           "LEFT JOIN sites st ON st.id=v.site_id "
           "LEFT JOIN users u ON u.id=v.agent_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return _paginate(ctx, sql, params, "ORDER BY v.scheduled_start DESC")


@route("GET", r"/api/visits/(\d+)")
def get_visit(ctx):
    vid = int(ctx.params[0])
    v = db.query(
        "SELECT v.*, c.name_en client_en, c.name_ar client_ar, c.id client_id, "
        "s.name_en service_en, s.name_ar service_ar, u.full_name agent_name, "
        "st.name site_name, st.map_image site_map_image "
        "FROM visits v JOIN clients c ON c.id=v.client_id "
        "LEFT JOIN service_types s ON s.id=v.service_type_id "
        "LEFT JOIN users u ON u.id=v.agent_id "
        "LEFT JOIN sites st ON st.id=v.site_id WHERE v.id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    require_perm(ctx.user, "visits.view")
    _assert_visit_access(ctx.user, v)
    v["report"] = db.query("SELECT * FROM reports WHERE visit_id=?", (vid,), one=True)
    # Clients only ever see a finished report — a draft is still being worked on.
    if ctx.user["role"] == "client" and v["report"] and v["report"]["status"] != "complete":
        v["report"] = None
    # The transportation invoice is internal: never sent to client accounts.
    if ctx.user["role"] == "client" and v["report"]:
        for k in ("transport_vehicle", "transport_cost"):
            v["report"].pop(k, None)
    v["rating"] = db.query("SELECT stars, comment, created_at FROM visit_ratings "
                           "WHERE visit_id=?", (vid,), one=True)
    v["chemicals"] = db.query(
        "SELECT cu.*, ch.name_en, ch.name_ar, ch.unit FROM chemical_usage cu "
        "JOIN chemicals ch ON ch.id=cu.chemical_id WHERE cu.visit_id=?", (vid,))
    v["photos"] = db.query(
        "SELECT * FROM photos WHERE entity_type='visit' AND entity_id=? ORDER BY uploaded_at DESC", (vid,))
    if v["report"]:
        v["report_photos"] = db.query(
            "SELECT * FROM photos WHERE entity_type='report' AND entity_id=? ORDER BY uploaded_at DESC",
            (v["report"]["id"],))
    else:
        v["report_photos"] = []
    # Optional auto-translation of the report text for display/printing.
    if ctx.query.get("lang") in ("en", "ar"):
        _translate_report(v["report"], ctx.query["lang"])
    return v


def _visit_conflict(agent_id, start, end, exclude_vid=None):
    """The agent's overlapping scheduled/in_progress visit, if any. Visits with
    no scheduled_end are assumed to run 60 minutes (back-to-back hourly slots
    therefore do NOT clash)."""
    if not agent_id or not start:
        return None
    q = ("SELECT v.id, v.scheduled_start, c.name_en client FROM visits v "
         "JOIN clients c ON c.id=v.client_id "
         "WHERE v.agent_id=? AND v.status IN ('scheduled','in_progress') "
         "AND datetime(v.scheduled_start) < datetime(?) "
         "AND datetime(COALESCE(v.scheduled_end, datetime(v.scheduled_start,'+60 minutes')))"
         " > datetime(?)")
    params = [agent_id, end or start, start]
    if not end:   # compare against the new visit's assumed 60-minute window
        q = q.replace("datetime(?) ", "datetime(?,'+60 minutes') ", 1)
    if exclude_vid:
        q += " AND v.id!=?"
        params.append(exclude_vid)
    return db.query(q, params, one=True)


def _check_visit_conflict(b, merged, exclude_vid=None):
    """Raise 409 when the (merged) visit double-books its agent, unless the
    caller confirmed with ignore_conflict."""
    if b.get("ignore_conflict"):
        return
    cf = _visit_conflict(merged.get("agent_id"), merged.get("scheduled_start"),
                         merged.get("scheduled_end"), exclude_vid)
    if cf:
        raise ApiError(409, f"agent_busy|{cf['client']}|{cf['scheduled_start']}")


@route("POST", r"/api/visits")
def create_visit(ctx):
    require_perm(ctx.user, "visits.create")
    b = ctx.body
    if not b.get("client_id") or not b.get("scheduled_start"):
        raise ApiError(400, "Client and scheduled date are required")
    _check_visit_conflict(b, b)
    # If the client has locations defined, a visit must be assigned to one so its
    # report rolls up to the right location.
    if not b.get("site_id"):
        has_sites = db.query("SELECT 1 FROM sites WHERE client_id=? LIMIT 1", (b["client_id"],), one=True)
        if has_sites:
            raise ApiError(400, "Please choose a location for this visit")
    vid = db.execute(
        "INSERT INTO visits(client_id,site_id,agent_id,service_type_id,scheduled_start,"
        "scheduled_end,status,location,notes,visit_number) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (b["client_id"], b.get("site_id"), b.get("agent_id"), b.get("service_type_id"),
         b["scheduled_start"], b.get("scheduled_end"), b.get("status", "scheduled"),
         b.get("location"), b.get("notes"), b.get("visit_number") or None))
    return db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)


@route("PUT", r"/api/visits/(\d+)")
def update_visit(ctx):
    vid = int(ctx.params[0])
    v = db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    u = ctx.user
    b = ctx.body
    require_perm(u, "visits.edit")
    # Agents may only update status of their own visits; managers update everything.
    if u["role"] == "agent":
        if v["agent_id"] != u["id"]:
            raise ApiError(403, "Not your visit")
        allowed = {"status", "notes", "completed_at", "visit_number"}
        b = {k: val for k, val in b.items() if k in allowed}
    if "site_id" in b and not b["site_id"]:
        b["site_id"] = None     # blank -> unassigned location (NULL), not ""
    if "visit_number" in b and not b["visit_number"]:
        b["visit_number"] = None     # blank -> unset
    # Re-check agent availability when the assignment or the time slot changes.
    if any(k in b for k in ("agent_id", "scheduled_start", "scheduled_end")):
        merged = {k: b.get(k, v[k]) for k in ("agent_id", "scheduled_start", "scheduled_end")}
        _check_visit_conflict(b, merged, exclude_vid=vid)
    cols = ("client_id", "site_id", "agent_id", "service_type_id", "scheduled_start",
            "scheduled_end", "status", "location", "notes", "visit_number", "completed_at")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if b.get("status") == "completed" and "completed_at" not in b:
        fields.append("completed_at=datetime('now')")
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(vid)
    db.execute(f"UPDATE visits SET {','.join(fields)} WHERE id=?", vals)
    # Notify managers/admins when a visit is started or ended.
    new_status = b.get("status")
    if new_status and new_status != v["status"] and new_status in ("in_progress", "completed"):
        info = db.query("SELECT c.name_en client, u2.full_name agent FROM visits vv "
                        "JOIN clients c ON c.id=vv.client_id LEFT JOIN users u2 ON u2.id=vv.agent_id "
                        "WHERE vv.id=?", (vid,), one=True)
        who = (info["agent"] or "Agent")
        if new_status == "in_progress":
            _notify_roles(("admin", "manager"), "visit_started", "Visit started",
                          f"{who} started the visit at {info['client']}", "visit", vid,
                          f"vstart:{vid}", exclude=u["id"])
        else:
            _notify_roles(("admin", "manager"), "visit_completed", "Visit completed",
                          f"{who} completed the visit at {info['client']}", "visit", vid,
                          f"vdone:{vid}", exclude=u["id"])
    return db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)


@route("DELETE", r"/api/visits/(\d+)")
def delete_visit(ctx):
    require_perm(ctx.user, "visits.delete")
    db.execute("DELETE FROM visits WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


# --------------------------------------------------------------------------
# REPORTS
# --------------------------------------------------------------------------
@route("POST", r"/api/visits/(\d+)/report")
def upsert_report(ctx):
    vid = int(ctx.params[0])
    v = db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    u = ctx.user
    require_perm(u, "visits.edit")
    if u["role"] == "agent" and v["agent_id"] != u["id"]:
        raise ApiError(403, "Not your visit")
    b = ctx.body
    existing = db.query("SELECT * FROM reports WHERE visit_id=?", (vid,), one=True)
    text_cols = ("summary", "pests_found", "findings", "recommendations", "severity",
                 "next_visit_due", "spare_parts_changed", "branch_issue",
                 "transport_vehicle")
    # Engineer service log — quantities of parts/materials used during the visit,
    # plus the internal transportation cost (never shown to clients).
    num_cols = ("lamps_used", "cables_used", "transformers_used", "light_sheets_used",
                "fipronil_ml", "imidacloprid_gm", "baits_count", "glo_pieces", "flybase_bags",
                "transport_cost")

    def num(v):
        try:
            return float(v) if str(v).strip() != "" else 0
        except (TypeError, ValueError):
            return 0

    # The agent finalising the report sends status=complete; everything else
    # (auto-save while typing) is stored as a draft and can't be finalised until
    # the required fields + both signatures are present.
    want_complete = (b.get("status") or "").lower() == "complete"

    if existing:
        fields, vals = [], []
        for c in text_cols:
            if c in b:
                fields.append(f"{c}=?"); vals.append(b[c])
        for c in num_cols:
            if c in b:
                fields.append(f"{c}=?"); vals.append(num(b[c]))
        if fields:
            vals.append(vid)
            db.execute(f"UPDATE reports SET {','.join(fields)} WHERE visit_id=?", vals)
    else:
        cols = ("visit_id",) + text_cols + num_cols
        vals = [vid] + [b.get(c) for c in text_cols] + [num(b.get(c)) for c in num_cols]
        # severity must not be NULL (CHECK constraint) — default it.
        vals[list(cols).index("severity")] = b.get("severity") or "low"
        placeholders = ",".join("?" * len(cols))
        db.execute(f"INSERT INTO reports({','.join(cols)}) VALUES({placeholders})", vals)

    # Re-read the merged row to validate / set the status against final values.
    rep = db.query("SELECT * FROM reports WHERE visit_id=?", (vid,), one=True)
    if want_complete:
        missing = _report_incomplete_fields(rep)
        if missing:
            raise ApiError(400, "report_incomplete:" + ",".join(missing))
        db.execute("UPDATE reports SET status='complete', completed_at=datetime('now') "
                   "WHERE visit_id=? AND status!='complete'", (vid,))
        # First completion -> invite the client to rate the visit.
        if rep["status"] != "complete":
            _notify_client_users(v["client_id"], "rate_visit", "How was your service?",
                                 "Your service report is ready — tap to view it and rate the visit.",
                                 "visit", vid, f"rate:{vid}")
    elif rep["status"] != "complete":
        # keep it a draft (clear any stale completed_at)
        db.execute("UPDATE reports SET status='draft', completed_at=NULL WHERE visit_id=?", (vid,))
    return db.query("SELECT * FROM reports WHERE visit_id=?", (vid,), one=True)


@route("POST", r"/api/visits/(\d+)/rating")
def rate_visit(ctx):
    """Client rates a completed visit (1-5 stars + optional comment), once."""
    vid = int(ctx.params[0])
    v = db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    u = ctx.user
    if u["role"] != "client" or v["client_id"] != u["client_id"]:
        raise ApiError(403, "Only the client can rate their visit")
    if v["status"] != "completed":
        raise ApiError(400, "Only a completed visit can be rated")
    if db.query("SELECT 1 FROM visit_ratings WHERE visit_id=?", (vid,), one=True):
        raise ApiError(400, "This visit is already rated")
    try:
        stars = int(ctx.body.get("stars"))
    except (TypeError, ValueError):
        raise ApiError(400, "Stars must be 1-5")
    if not 1 <= stars <= 5:
        raise ApiError(400, "Stars must be 1-5")
    comment = str(ctx.body.get("comment") or "").strip()[:1000] or None
    db.execute("INSERT INTO visit_ratings(visit_id,client_id,stars,comment,created_by) "
               "VALUES(?,?,?,?,?)", (vid, v["client_id"], stars, comment, u["id"]))
    info = db.query("SELECT c.name_en client, u2.full_name agent FROM visits vv "
                    "JOIN clients c ON c.id=vv.client_id LEFT JOIN users u2 ON u2.id=vv.agent_id "
                    "WHERE vv.id=?", (vid,), one=True)
    _notify_roles(("admin", "manager"), "visit_rated", "Visit rated",
                  f"{info['client']} rated {info['agent'] or 'the visit'}: {'⭐' * stars}"
                  + (f" — {comment}" if comment else ""),
                  "visit", vid, f"rated:{vid}")
    audit(ctx, "visit.rate", "visit", vid, f"{stars} stars")
    return db.query("SELECT stars, comment, created_at FROM visit_ratings WHERE visit_id=?",
                    (vid,), one=True)


# Fields (and signatures) required before a report can be marked complete.
REPORT_REQUIRED_FIELDS = ("summary", "findings", "pests_found")


def _report_incomplete_fields(rep):
    """Return the list of required-but-missing field keys for a report row."""
    missing = [f for f in REPORT_REQUIRED_FIELDS if not (rep.get(f) or "").strip()]
    if not rep.get("customer_signature"):
        missing.append("customer_signature")
    if not rep.get("technician_signature"):
        missing.append("technician_signature")
    return missing


# Report free-text fields that get auto-translated for display/printing.
REPORT_TRANSLATABLE = ("summary", "pests_found", "findings", "recommendations",
                       "spare_parts_changed", "branch_issue")


def _translate(text, target):
    """Translate text into target ('en'|'ar'), cached in the translations table.

    Uses the free Google endpoint. On any failure (or if the text is already in
    the target language) the original text is returned, so callers degrade
    gracefully and never block on the network."""
    text = (text or "").strip()
    if not text or target not in ("en", "ar"):
        return text
    h = hashlib.sha1((target + "::" + text).encode("utf-8")).hexdigest()
    row = db.query("SELECT text FROM translations WHERE src_hash=? AND target=?",
                   (h, target), one=True)
    if row:
        return row["text"]
    try:
        url = ("https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl="
               + target + "&dt=t&q=" + quote(text))
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=8) as r:
            data = json.loads(r.read().decode("utf-8"))
        src_lang = data[2] if len(data) > 2 else None
        out = text if src_lang == target else "".join(
            seg[0] for seg in data[0] if seg and seg[0])
        db.execute("INSERT OR IGNORE INTO translations(src_hash,target,src_lang,text) "
                   "VALUES(?,?,?,?)", (h, target, src_lang, out))
        return out
    except Exception as e:
        print("translate failed:", e)
        return text


def _translate_report(rep, target):
    """Translate a report dict's free-text fields in place (best-effort)."""
    if rep and target in ("en", "ar"):
        for f in REPORT_TRANSLATABLE:
            if rep.get(f):
                rep[f] = _translate(rep[f], target)
    return rep


def _draft_reports_for(user):
    """Unfinished (draft) reports the user is responsible for.

    Agents see their own; managers/admins see all open drafts so they can chase."""
    sql = ("SELECT r.visit_id, r.created_at, v.agent_id, u.full_name agent_name, "
           "c.name_en, c.name_ar, v.scheduled_start "
           "FROM reports r JOIN visits v ON v.id=r.visit_id "
           "JOIN clients c ON c.id=v.client_id LEFT JOIN users u ON u.id=v.agent_id "
           "WHERE r.status='draft' ")
    params = ()
    if user["role"] == "agent":
        sql += "AND v.agent_id=? "
        params = (user["id"],)
    sql += "ORDER BY r.created_at DESC"
    return db.query(sql, params)


@route("GET", r"/api/reports/drafts")
def list_draft_reports(ctx):
    require_perm(ctx.user, "visits.view")
    return {"items": _draft_reports_for(ctx.user)}


@route("GET", r"/api/reports")
def list_reports(ctx):
    """Central report list for admin/owner, filterable by agent, client,
    location, severity, status and date range."""
    u = ctx.user
    require_perm(u, "visits.view")
    where, params = [], []
    if u["role"] == "client":
        where.append("v.client_id=?"); params.append(u["client_id"])
    elif u["role"] == "agent":
        where.append("v.agent_id=?"); params.append(u["id"])
    for key, col in (("client", "v.client_id"), ("agent", "v.agent_id"),
                     ("severity", "r.severity"), ("status", "r.status")):
        if ctx.query.get(key):
            where.append(f"{col}=?"); params.append(ctx.query[key])
    site = ctx.query.get("site")
    if site:
        if str(site) in ("none", "0"):
            where.append("v.site_id IS NULL")
        else:
            where.append("v.site_id=?"); params.append(site)
    if ctx.query.get("from"):
        where.append("date(v.scheduled_start) >= date(?)"); params.append(ctx.query["from"])
    if ctx.query.get("to"):
        where.append("date(v.scheduled_start) <= date(?)"); params.append(ctx.query["to"])
    sql = ("SELECT r.id, r.visit_id, r.severity, r.status, r.summary, r.pests_found, "
           "r.recommendations, r.created_at, r.completed_at, "
           "v.scheduled_start, v.agent_id, v.client_id, v.site_id, "
           "c.name_en client_en, c.name_ar client_ar, st.name site_name, "
           "u.full_name agent_name "
           "FROM reports r JOIN visits v ON v.id=r.visit_id "
           "JOIN clients c ON c.id=v.client_id "
           "LEFT JOIN sites st ON st.id=v.site_id "
           "LEFT JOIN users u ON u.id=v.agent_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    res = _paginate(ctx, sql, params, "ORDER BY r.created_at DESC")
    lang = ctx.query.get("lang")
    if lang in ("en", "ar"):
        for r in (res["items"] if isinstance(res, dict) else res):
            if r.get("summary"):
                r["summary"] = _translate(r["summary"], lang)
    return res


# --------------------------------------------------------------------------
# TRANSPORTATION — internal travel-cost log recorded on visit reports.
# Never exposed to client accounts (no transport.view perm, plus a hard block).
# Agents are scoped to their own trips; managers/admins see everyone and get
# cost totals grouped by branch, client and agent.
# --------------------------------------------------------------------------
@route("GET", r"/api/transport")
def list_transport(ctx):
    u = ctx.user
    require_perm(u, "transport.view")
    if u["role"] == "client":
        raise ApiError(403, "Not available to client accounts")
    # Only reports where the agent actually filled the transportation section.
    where = ["(COALESCE(r.transport_cost,0) > 0 OR COALESCE(r.transport_vehicle,'') <> '')"]
    params = []
    if u["role"] == "agent":
        where.append("v.agent_id=?"); params.append(u["id"])
    for key, col in (("client", "v.client_id"), ("agent", "v.agent_id")):
        if ctx.query.get(key):
            where.append(f"{col}=?"); params.append(ctx.query[key])
    site = ctx.query.get("site")
    if site:
        if str(site) in ("none", "0"):
            where.append("v.site_id IS NULL")
        else:
            where.append("v.site_id=?"); params.append(site)
    if ctx.query.get("from"):
        where.append("date(v.scheduled_start) >= date(?)"); params.append(ctx.query["from"])
    if ctx.query.get("to"):
        where.append("date(v.scheduled_start) <= date(?)"); params.append(ctx.query["to"])
    base = ("FROM reports r JOIN visits v ON v.id=r.visit_id "
            "JOIN clients c ON c.id=v.client_id "
            "LEFT JOIN sites st ON st.id=v.site_id "
            "LEFT JOIN users u2 ON u2.id=v.agent_id "
            "WHERE " + " AND ".join(where))
    res = _paginate(
        ctx,
        "SELECT r.visit_id, r.transport_vehicle, r.transport_cost, "
        "v.scheduled_start, v.agent_id, v.client_id, v.site_id, "
        "c.name_en client_en, c.name_ar client_ar, st.name site_name, "
        "u2.full_name agent_name " + base,
        params, "ORDER BY v.scheduled_start DESC")
    out = res if isinstance(res, dict) else {"items": res}
    tot = db.query("SELECT COUNT(*) trips, COALESCE(SUM(r.transport_cost),0) total "
                   + base, params, one=True)
    out["trips"] = tot["trips"]
    out["total"] = tot["total"]
    # Cost roll-ups for the owner/CEO view (grouped over the same filter scope).
    out["by_branch"] = db.query(
        "SELECT c.name_en client_en, c.name_ar client_ar, st.name site_name, "
        "COUNT(*) trips, COALESCE(SUM(r.transport_cost),0) total "
        + base + " GROUP BY v.client_id, v.site_id ORDER BY total DESC", params)
    out["by_client"] = db.query(
        "SELECT c.name_en client_en, c.name_ar client_ar, "
        "COUNT(*) trips, COALESCE(SUM(r.transport_cost),0) total "
        + base + " GROUP BY v.client_id ORDER BY total DESC", params)
    out["by_agent"] = db.query(
        "SELECT u2.full_name agent_name, "
        "COUNT(*) trips, COALESCE(SUM(r.transport_cost),0) total "
        + base + " GROUP BY v.agent_id ORDER BY total DESC", params)
    return out


# --------------------------------------------------------------------------
# CHEMICALS / INVENTORY
# --------------------------------------------------------------------------
@route("GET", r"/api/chemicals")
def list_chemicals(ctx):
    require_perm(ctx.user, "chemicals.view")
    q = ctx.query.get("q", "").strip()
    if q:
        return db.query(
            "SELECT * FROM chemicals WHERE name_en LIKE ? OR name_ar LIKE ? OR active_ingredient LIKE ? "
            "ORDER BY name_en", (f"%{q}%", f"%{q}%", f"%{q}%"))
    return db.query("SELECT * FROM chemicals ORDER BY name_en")


@route("POST", r"/api/chemicals")
def create_chemical(ctx):
    require_perm(ctx.user, "chemicals.create")
    b = ctx.body
    if not b.get("name_en"):
        raise ApiError(400, "Name is required")
    cid = db.execute(
        "INSERT INTO chemicals(name_en,name_ar,active_ingredient,unit,quantity_in_stock,"
        "reorder_level,hazard_class,reg_no,cost_per_unit) VALUES(?,?,?,?,?,?,?,?,?)",
        (b["name_en"], b.get("name_ar"), b.get("active_ingredient"), b.get("unit", "L"),
         b.get("quantity_in_stock", 0), b.get("reorder_level", 0), b.get("hazard_class"),
         b.get("reg_no"), b.get("cost_per_unit", 0)))
    return db.query("SELECT * FROM chemicals WHERE id=?", (cid,), one=True)


@route("PUT", r"/api/chemicals/(\d+)")
def update_chemical(ctx):
    require_perm(ctx.user, "chemicals.edit")
    cid = int(ctx.params[0])
    b = ctx.body
    cols = ("name_en", "name_ar", "active_ingredient", "unit", "reorder_level",
            "hazard_class", "reg_no", "cost_per_unit")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(cid)
    db.execute(f"UPDATE chemicals SET {','.join(fields)} WHERE id=?", vals)
    return db.query("SELECT * FROM chemicals WHERE id=?", (cid,), one=True)


@route("DELETE", r"/api/chemicals/(\d+)")
def delete_chemical(ctx):
    require_perm(ctx.user, "chemicals.delete")
    db.execute("DELETE FROM chemicals WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


@route("POST", r"/api/chemicals/(\d+)/stock")
def adjust_stock(ctx):
    require_perm(ctx.user, "chemicals.edit")
    cid = int(ctx.params[0])
    b = ctx.body
    change = float(b.get("change", 0))
    reason = b.get("reason", "adjustment")
    if reason not in ("purchase", "adjustment"):
        raise ApiError(400, "Invalid reason")
    with db.transaction() as cx:
        cx.execute("UPDATE chemicals SET quantity_in_stock = quantity_in_stock + ? WHERE id=?", (change, cid))
        cx.execute("INSERT INTO inventory_transactions(chemical_id,change,reason,note) VALUES(?,?,?,?)",
                   (cid, change, reason, b.get("note")))
    audit(ctx, "stock.adjust", "chemical", cid, f"{reason} {change:+g}")
    return db.query("SELECT * FROM chemicals WHERE id=?", (cid,), one=True)


@route("GET", r"/api/purchase-orders")
def list_purchase_orders(ctx):
    require_perm(ctx.user, "chemicals.view")
    pos = db.query("SELECT po.*, u.full_name created_by_name FROM purchase_orders po "
                   "LEFT JOIN users u ON u.id=po.created_by "
                   "ORDER BY po.created_at DESC LIMIT 100")
    for po in pos:
        po["items"] = db.query(
            "SELECT pi.*, c.name_en, c.name_ar, c.unit FROM purchase_order_items pi "
            "JOIN chemicals c ON c.id=pi.chemical_id WHERE pi.po_id=?", (po["id"],))
    return pos


@route("POST", r"/api/purchase-orders")
def create_purchase_order(ctx):
    """Stock-in: record a purchase and increment inventory in one transaction."""
    require_perm(ctx.user, "chemicals.edit")
    b = ctx.body
    items = b.get("items") or []
    lines = []
    for it in items:
        try:
            chem_id = int(it.get("chemical_id"))
            qty = float(it.get("quantity"))
            cost = float(it.get("unit_cost") or 0)
        except (TypeError, ValueError):
            raise ApiError(400, "Invalid item line")
        if qty <= 0 or cost < 0:
            raise ApiError(400, "Quantity must be positive")
        if not db.query("SELECT 1 FROM chemicals WHERE id=?", (chem_id,), one=True):
            raise ApiError(404, f"Chemical {chem_id} not found")
        lines.append((chem_id, qty, cost))
    if not lines:
        raise ApiError(400, "At least one item is required")
    total = round(sum(q * c for _, q, c in lines), 2)
    with db.transaction() as cx:
        poid = cx.execute(
            "INSERT INTO purchase_orders(supplier,reference,note,total_cost,created_by) "
            "VALUES(?,?,?,?,?)",
            (b.get("supplier"), b.get("reference"), b.get("note"), total,
             ctx.user["id"])).lastrowid
        for chem_id, qty, cost in lines:
            cx.execute("INSERT INTO purchase_order_items(po_id,chemical_id,quantity,unit_cost) "
                       "VALUES(?,?,?,?)", (poid, chem_id, qty, cost))
            cx.execute("UPDATE chemicals SET quantity_in_stock = quantity_in_stock + ? "
                       "WHERE id=?", (qty, chem_id))
            cx.execute("INSERT INTO inventory_transactions(chemical_id,change,reason,reference,note) "
                       "VALUES(?,?,'purchase',?,?)",
                       (chem_id, qty, f"PO-{poid}", b.get("supplier")))
    audit(ctx, "purchase.create", "purchase_order", poid,
          f"{len(lines)} item(s), total {total:g}" + (f" from {b['supplier']}" if b.get("supplier") else ""))
    po = db.query("SELECT * FROM purchase_orders WHERE id=?", (poid,), one=True)
    po["items"] = db.query("SELECT * FROM purchase_order_items WHERE po_id=?", (poid,))
    return po


@route("GET", r"/api/chemicals/(\d+)/transactions")
def chemical_transactions(ctx):
    require_perm(ctx.user, "chemicals.view")
    return db.query("SELECT * FROM inventory_transactions WHERE chemical_id=? ORDER BY created_at DESC",
                    (int(ctx.params[0]),))


@route("POST", r"/api/visits/(\d+)/usage")
def record_usage(ctx):
    vid = int(ctx.params[0])
    v = db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    u = ctx.user
    require_perm(u, "visits.edit")
    if u["role"] == "agent" and v["agent_id"] != u["id"]:
        raise ApiError(403, "Not your visit")
    b = ctx.body
    chem_id = b.get("chemical_id")
    qty = float(b.get("quantity", 0))
    if not chem_id or qty <= 0:
        raise ApiError(400, "Chemical and a positive quantity are required")
    chem = db.query("SELECT material_key FROM chemicals WHERE id=?", (chem_id,), one=True)
    if not chem:
        raise ApiError(400, "Unknown chemical")
    if chem["material_key"]:
        # Consumable materials are tracked via the report's counter fields; logging
        # them here too would double-count against the engineer's on-hand balance.
        raise ApiError(400, "This material is recorded on the report, not as chemical usage")
    # Material consumed on a visit comes out of the engineer's issued on-hand
    # balance (issued − used), NOT central warehouse stock — that was already
    # decremented when the material was issued to the engineer. So we only
    # record the usage; we do not touch chemicals.quantity_in_stock here.
    uid = db.execute("INSERT INTO chemical_usage(visit_id,chemical_id,quantity,area_treated) VALUES(?,?,?,?)",
                     (vid, chem_id, qty, b.get("area_treated")))
    return db.query(
        "SELECT cu.*, ch.name_en, ch.name_ar, ch.unit FROM chemical_usage cu "
        "JOIN chemicals ch ON ch.id=cu.chemical_id WHERE cu.id=?", (uid,), one=True)


@route("DELETE", r"/api/usage/(\d+)")
def delete_usage(ctx):
    require_perm(ctx.user, "visits.edit")
    uid = int(ctx.params[0])
    row = db.query("SELECT * FROM chemical_usage WHERE id=?", (uid,), one=True)
    if not row:
        raise ApiError(404, "Not found")
    # Usage no longer touches central stock (see record_usage), so removing it
    # simply restores the engineer's on-hand balance — nothing to credit back
    # to the warehouse.
    db.execute("DELETE FROM chemical_usage WHERE id=?", (uid,))
    return {"ok": True}


# --------------------------------------------------------------------------
# ENGINEER MATERIAL ISSUES (stock an engineer checks out of inventory)
# --------------------------------------------------------------------------
def _issue_with_items(iid):
    issue = db.query(
        "SELECT ei.*, u.full_name AS agent_name FROM engineer_issues ei "
        "JOIN users u ON u.id=ei.agent_id WHERE ei.id=?", (iid,), one=True)
    if issue:
        issue["items"] = db.query(
            "SELECT it.*, ch.name_en, ch.name_ar, ch.unit FROM engineer_issue_items it "
            "JOIN chemicals ch ON ch.id=it.chemical_id WHERE it.issue_id=? ORDER BY it.id", (iid,))
    return issue


@route("GET", r"/api/issues")
def list_issues(ctx):
    require_perm(ctx.user, "issues.view")
    u = ctx.user
    where, params = [], []
    if u["role"] == "agent":                       # engineers see only their own
        where.append("ei.agent_id=?"); params.append(u["id"])
    elif ctx.query.get("agent_id"):
        where.append("ei.agent_id=?"); params.append(int(ctx.query["agent_id"]))
    sql = ("SELECT ei.id, ei.agent_id, ei.note, ei.created_at, u.full_name AS agent_name, "
           "(SELECT COUNT(*) FROM engineer_issue_items it WHERE it.issue_id=ei.id) AS item_count "
           "FROM engineer_issues ei JOIN users u ON u.id=ei.agent_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ei.created_at DESC"
    return db.query(sql, tuple(params))


@route("GET", r"/api/issues/balance")
def issues_balance(ctx):
    """Per-engineer materials balance: what each engineer was ISSUED minus what
    they USED (chemicals consumed on their own visits) = what's left on hand.
    Derived live from existing data, so 'remaining' drops as visits log usage.
    Agents see only their own; managers/admins can filter with ?agent_id=."""
    u = ctx.user
    require_perm(u, "issues.view")
    agent_filter = u["id"] if u["role"] == "agent" else (
        int(ctx.query["agent_id"]) if ctx.query.get("agent_id") else None)
    iss_where, iss_params = "", []
    use_where, use_params = "", []
    if agent_filter is not None:
        iss_where = " WHERE ei.agent_id=?"; iss_params = [agent_filter]
        use_where = " AND v.agent_id=?"; use_params = [agent_filter]
    issued = db.query(
        "SELECT ei.agent_id, it.chemical_id, SUM(it.quantity) qty "
        "FROM engineer_issues ei JOIN engineer_issue_items it ON it.issue_id=ei.id"
        + iss_where + " GROUP BY ei.agent_id, it.chemical_id", iss_params)
    used = db.query(
        "SELECT v.agent_id, cu.chemical_id, SUM(cu.quantity) qty "
        "FROM chemical_usage cu JOIN visits v ON v.id=cu.visit_id "
        "WHERE v.agent_id IS NOT NULL" + use_where
        + " GROUP BY v.agent_id, cu.chemical_id", use_params)
    chems = {c["id"]: c for c in db.query("SELECT id,name_en,name_ar,unit FROM chemicals")}
    names = {a["id"]: a["full_name"] for a in db.query("SELECT id,full_name FROM users")}
    eng = {}  # agent_id -> {chemical_id -> {"issued":x, "used":y}}
    for r in issued:
        eng.setdefault(r["agent_id"], {}).setdefault(
            r["chemical_id"], {"issued": 0.0, "used": 0.0})["issued"] = r["qty"] or 0
    for r in used:
        eng.setdefault(r["agent_id"], {}).setdefault(
            r["chemical_id"], {"issued": 0.0, "used": 0.0})["used"] = r["qty"] or 0
    # Consumable materials (lamps, glue boards, ...) are recorded as report
    # counters rather than chemical_usage rows, so fold those into "used" too.
    # material_key values are server-defined (see _migrate), never user input,
    # so building the column list from them is safe.
    mat_items = db.query("SELECT id, material_key FROM chemicals WHERE material_key IS NOT NULL")
    if mat_items:
        cols_sql = ",".join(f"COALESCE(SUM(r.{m['material_key']}),0) {m['material_key']}"
                            for m in mat_items)
        mat_rows = db.query(
            "SELECT v.agent_id, " + cols_sql +
            " FROM reports r JOIN visits v ON v.id=r.visit_id "
            "WHERE v.agent_id IS NOT NULL" + use_where + " GROUP BY v.agent_id", use_params)
        for r in mat_rows:
            for m in mat_items:
                q = r[m["material_key"]] or 0
                if q:
                    eng.setdefault(r["agent_id"], {}).setdefault(
                        m["id"], {"issued": 0.0, "used": 0.0})["used"] += q
    out = []
    for aid, mats in eng.items():
        materials = []
        for cid, bal in mats.items():
            ch = chems.get(cid, {})
            issued_q, used_q = round(bal["issued"], 3), round(bal["used"], 3)
            materials.append({
                "chemical_id": cid, "name_en": ch.get("name_en"),
                "name_ar": ch.get("name_ar"), "unit": ch.get("unit"),
                "issued": issued_q, "used": used_q,
                "remaining": round(issued_q - used_q, 3)})
        materials.sort(key=lambda m: (m["name_en"] or "").lower())
        out.append({"agent_id": aid, "agent_name": names.get(aid, "?"), "materials": materials})
    out.sort(key=lambda e: (e["agent_name"] or "").lower())
    return {"engineers": out}


@route("GET", r"/api/issues/(\d+)")
def get_issue(ctx):
    require_perm(ctx.user, "issues.view")
    issue = _issue_with_items(int(ctx.params[0]))
    if not issue:
        raise ApiError(404, "Issue not found")
    if ctx.user["role"] == "agent" and issue["agent_id"] != ctx.user["id"]:
        raise ApiError(403, "Not your issue")
    return issue


@route("POST", r"/api/issues")
def create_issue(ctx):
    require_perm(ctx.user, "issues.create")
    u = ctx.user
    b = ctx.body
    # who the materials are issued to (agents may only issue to themselves)
    agent_id = u["id"]
    if u["role"] in ("admin", "manager") and b.get("agent_id"):
        agent_id = int(b["agent_id"])
    target = db.query("SELECT id FROM users WHERE id=? AND active=1", (agent_id,), one=True)
    if not target:
        raise ApiError(400, "Engineer not found")
    # normalise + validate line items
    clean = []
    for it in (b.get("items") or []):
        cid = it.get("chemical_id")
        qty = float(it.get("quantity", 0) or 0)
        if cid and qty > 0:
            clean.append((int(cid), qty))
    if not clean:
        raise ApiError(400, "Add at least one material with a positive quantity")
    # check stock up-front (sum per chemical in case it appears twice)
    needed = {}
    for cid, qty in clean:
        needed[cid] = needed.get(cid, 0) + qty
    for cid, qty in needed.items():
        chem = db.query("SELECT name_en, unit, quantity_in_stock FROM chemicals WHERE id=?", (cid,), one=True)
        if not chem:
            raise ApiError(400, "Unknown material")
        if qty > chem["quantity_in_stock"]:
            raise ApiError(400, f"Not enough {chem['name_en']} in stock "
                                f"({chem['quantity_in_stock']:g} {chem['unit']} available, {qty:g} requested)")
    with db.transaction() as cx:
        iid = cx.execute("INSERT INTO engineer_issues(agent_id,note,created_by) VALUES(?,?,?)",
                         (agent_id, b.get("note"), u["id"])).lastrowid
        for cid, qty in clean:
            cx.execute("INSERT INTO engineer_issue_items(issue_id,chemical_id,quantity) VALUES(?,?,?)",
                       (iid, cid, qty))
            cx.execute("UPDATE chemicals SET quantity_in_stock = quantity_in_stock - ? WHERE id=?", (qty, cid))
            cx.execute("INSERT INTO inventory_transactions(chemical_id,change,reason,reference) VALUES(?,?,?,?)",
                       (cid, -qty, "issue", f"issue:{iid}"))
    audit(ctx, "issue.create", "engineer_issue", iid, f"agent:{agent_id} items:{len(clean)}")
    return _issue_with_items(iid)


@route("DELETE", r"/api/issues/(\d+)")
def delete_issue(ctx):
    require_perm(ctx.user, "issues.delete")
    iid = int(ctx.params[0])
    issue = db.query("SELECT * FROM engineer_issues WHERE id=?", (iid,), one=True)
    if not issue:
        raise ApiError(404, "Issue not found")
    items = db.query("SELECT * FROM engineer_issue_items WHERE issue_id=?", (iid,))
    with db.transaction() as cx:
        for it in items:                            # return the materials to stock
            cx.execute("UPDATE chemicals SET quantity_in_stock = quantity_in_stock + ? WHERE id=?",
                       (it["quantity"], it["chemical_id"]))
            cx.execute("INSERT INTO inventory_transactions(chemical_id,change,reason,reference) VALUES(?,?,?,?)",
                       (it["chemical_id"], it["quantity"], "adjustment", f"issue-reversal:{iid}"))
        cx.execute("DELETE FROM engineer_issues WHERE id=?", (iid,))   # items cascade
    audit(ctx, "issue.delete", "engineer_issue", iid, f"reversed {len(items)} items")
    return {"ok": True}


# --------------------------------------------------------------------------
# INVOICES / FINANCE
# --------------------------------------------------------------------------
@route("GET", r"/api/invoices")
def list_invoices(ctx):
    u = ctx.user
    if u["role"] != "client":
        require_perm(u, "invoices.view")
    where, params = [], []
    if u["role"] == "client":
        where.append("i.client_id=?")
        params.append(u["client_id"])
        # Drafts are internal working documents — never shown to the client.
        where.append("i.status!='draft'")
    if ctx.query.get("client"):
        where.append("i.client_id=?")
        params.append(ctx.query["client"])
    if ctx.query.get("status"):
        where.append("i.status=?")
        params.append(ctx.query["status"])
    dt = ctx.query.get("doc_type", "invoice")
    if dt != "all":
        where.append("i.doc_type=?")
        params.append(dt)
    sql = ("SELECT i.*, c.name_en client_en, c.name_ar client_ar, "
           "COALESCE((SELECT SUM(amount) FROM payments p WHERE p.invoice_id=i.id),0) paid "
           "FROM invoices i JOIN clients c ON c.id=i.client_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return _paginate(ctx, sql, params, "ORDER BY i.issue_date DESC")


@route("GET", r"/api/invoices/(\d+)")
def get_invoice(ctx):
    iid = int(ctx.params[0])
    inv = db.query("SELECT i.*, c.name_en client_en, c.name_ar client_ar, "
                   "c.contact_person client_contact, c.phone client_phone, c.email client_email, "
                   "c.city client_city, c.address_en client_address_en, c.address_ar client_address_ar "
                   "FROM invoices i JOIN clients c ON c.id=i.client_id WHERE i.id=?", (iid,), one=True)
    if not inv:
        raise ApiError(404, "Invoice not found")
    if ctx.user["role"] == "client":
        if inv["client_id"] != ctx.user["client_id"] or inv["status"] == "draft":
            raise ApiError(403, "No permission")
    else:
        require_perm(ctx.user, "invoices.view")
    inv["payments"] = db.query("SELECT * FROM payments WHERE invoice_id=? ORDER BY paid_at DESC", (iid,))
    inv["paid"] = sum(p["amount"] for p in inv["payments"])
    inv["items"] = db.query("SELECT * FROM invoice_items WHERE invoice_id=? ORDER BY id", (iid,))
    return inv


def _save_items(iid, items, cx=None):
    """Replace an invoice's line items; return the amount computed from them.
    Pass cx to run inside an existing transaction; otherwise it self-commits."""
    ex = cx.execute if cx is not None else db.execute
    ex("DELETE FROM invoice_items WHERE invoice_id=?", (iid,))
    total = 0.0
    for it in items or []:
        qty = float(it.get("quantity", 1) or 1)
        price = float(it.get("unit_price", 0) or 0)
        amt = round(qty * price, 2)
        total += amt
        ex("INSERT INTO invoice_items(invoice_id,description,quantity,unit_price,amount) "
           "VALUES(?,?,?,?,?)", (iid, it.get("description", ""), qty, price, amt))
    return round(total, 2)


@route("POST", r"/api/invoices")
def create_invoice(ctx):
    require_perm(ctx.user, "invoices.create")
    b = ctx.body
    if not b.get("client_id") or not b.get("issue_date"):
        raise ApiError(400, "Client and issue date are required")
    doc_type = b.get("doc_type", "invoice")
    items = b.get("items")
    number = b.get("number") or _next_invoice_number(doc_type)
    tax = float(b.get("tax", 0))
    with db.transaction() as cx:
        iid = cx.execute(
            "INSERT INTO invoices(client_id,site_id,visit_id,contract_id,doc_type,number,issue_date,due_date,"
            "valid_until,amount,tax,total,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (b["client_id"], b.get("site_id") or None, b.get("visit_id"), b.get("contract_id"),
             doc_type, number, b["issue_date"], b.get("due_date"), b.get("valid_until"), 0, 0, 0,
             b.get("status", "draft"), b.get("notes"))).lastrowid
        # amount: from items if provided, else flat amount
        amount = _save_items(iid, items, cx) if items else float(b.get("amount", 0))
        if b.get("tax_rate"):  # percentage helper
            tax = round(amount * float(b["tax_rate"]) / 100, 2)
        cx.execute("UPDATE invoices SET amount=?, tax=?, total=? WHERE id=?",
                   (amount, tax, amount + tax, iid))
    audit(ctx, "invoice.create", "invoice", iid, f"{doc_type} {number} total {amount + tax:g}")
    if doc_type == "quote" and b.get("status") == "sent":
        _notify_client_users(b["client_id"], "quote_new", "New quotation",
                             f"Quotation {number} is ready for your review",
                             "invoice", iid, f"quotesent:{iid}")
    return db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)


@route("PUT", r"/api/invoices/(\d+)")
def update_invoice(ctx):
    require_perm(ctx.user, "invoices.edit")
    iid = int(ctx.params[0])
    b = ctx.body
    cur = db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)
    if not cur:
        raise ApiError(404, "Not found")
    if "site_id" in b and not b["site_id"]:
        b["site_id"] = None     # blank -> unassigned location (NULL), not ""
    with db.transaction() as cx:
        if "items" in b:                       # items drive the amount
            b["amount"] = _save_items(iid, b["items"], cx)
        if "amount" in b or "tax" in b:
            amount = float(b.get("amount", cur["amount"]))
            tax = float(b.get("tax", cur["tax"]))
            b["amount"], b["tax"], b["total"] = amount, tax, amount + tax
        cols = ("site_id", "visit_id", "issue_date", "due_date", "valid_until", "amount", "tax",
                "total", "status", "notes", "number", "doc_type")
        fields = [f"{c}=?" for c in cols if c in b]
        vals = [b[c] for c in cols if c in b]
        if fields:
            vals.append(iid)
            cx.execute(f"UPDATE invoices SET {','.join(fields)} WHERE id=?", vals)
    audit(ctx, "invoice.update", "invoice", iid, ", ".join(k for k in b if k != "items"))
    if cur["doc_type"] == "quote" and b.get("status") == "sent" and cur["status"] != "sent":
        _notify_client_users(cur["client_id"], "quote_new", "New quotation",
                             f"Quotation {cur['number']} is ready for your review",
                             "invoice", iid, f"quotesent:{iid}")
    return db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)


@route("DELETE", r"/api/invoices/(\d+)")
def delete_invoice(ctx):
    require_perm(ctx.user, "invoices.delete")
    iid = int(ctx.params[0])
    # An invoice with money recorded against it is an accounting record —
    # deleting it would erase the payment history. Cancel it instead.
    if db.query("SELECT 1 FROM payments WHERE invoice_id=? LIMIT 1", (iid,), one=True):
        raise ApiError(400, "This invoice has recorded payments and cannot be deleted. Cancel it instead.")
    db.execute("DELETE FROM invoices WHERE id=?", (iid,))
    audit(ctx, "invoice.delete", "invoice", iid)
    return {"ok": True}


@route("POST", r"/api/invoices/(\d+)/payments")
def add_payment(ctx):
    require_perm(ctx.user, "payments.create")
    iid = int(ctx.params[0])
    b = ctx.body
    try:
        amount = float(b.get("amount", 0))
    except (TypeError, ValueError):
        raise ApiError(400, "Invalid payment amount")
    if not math.isfinite(amount) or amount <= 0:
        raise ApiError(400, "Payment amount must be positive")
    if not db.query("SELECT id FROM invoices WHERE id=?", (iid,), one=True):
        raise ApiError(404, "Invoice not found")
    with db.transaction() as cx:
        # Guard against typos: a payment may not exceed the outstanding balance.
        row = cx.execute(
            "SELECT total - COALESCE((SELECT SUM(amount) FROM payments p "
            "WHERE p.invoice_id=i.id),0) rem FROM invoices i WHERE i.id=?", (iid,)).fetchone()
        if amount > row["rem"] + 0.01:
            raise ApiError(400, f"Payment exceeds the outstanding balance ({row['rem']:g})")
        cx.execute("INSERT INTO payments(invoice_id,amount,method,note) VALUES(?,?,?,?)",
                   (iid, amount, b.get("method", "cash"), b.get("note")))
        # auto-mark paid if fully covered
        inv = cx.execute("SELECT total, status FROM invoices WHERE id=?", (iid,)).fetchone()
        paid = cx.execute("SELECT COALESCE(SUM(amount),0) p FROM payments WHERE invoice_id=?",
                          (iid,)).fetchone()["p"]
        if inv and paid >= inv["total"] and inv["status"] != "cancelled":
            cx.execute("UPDATE invoices SET status='paid' WHERE id=?", (iid,))
    audit(ctx, "payment.create", "invoice", iid, f"{amount:g} via {b.get('method', 'cash')}")
    return get_invoice(Ctx(ctx.user, [str(iid)], {}, {}, b"", ""))


@route("GET", r"/api/clients/(\d+)/finance")
def client_finance(ctx):
    cid = int(ctx.params[0])
    _assert_client_access(ctx.user, cid)
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "invoices.view")
    return _finance_summary(cid)


@route("GET", r"/api/clients/(\d+)/statement")
def client_statement(ctx):
    """Ledger for one client: invoices (debit) and payments (credit) in date
    order with a running balance, plus the opening balance before `from`."""
    cid = int(ctx.params[0])
    _assert_client_access(ctx.user, cid)
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "invoices.view")
    client = db.query("SELECT * FROM clients WHERE id=?", (cid,), one=True)
    if not client:
        raise ApiError(404, "Client not found")
    dfrom, dto = ctx.query.get("from"), ctx.query.get("to")
    inv_w = "client_id=? AND doc_type='invoice' AND status!='cancelled'"
    pay_w = ("invoice_id IN (SELECT id FROM invoices WHERE client_id=? "
             "AND doc_type='invoice' AND status!='cancelled')")
    opening = 0.0
    if dfrom:
        opening = (db.query(f"SELECT COALESCE(SUM(total),0) v FROM invoices WHERE {inv_w} "
                            "AND date(issue_date) < date(?)", (cid, dfrom), one=True)["v"]
                   - db.query(f"SELECT COALESCE(SUM(amount),0) v FROM payments WHERE {pay_w} "
                              "AND date(paid_at) < date(?)", (cid, dfrom), one=True)["v"])
    entries = []
    inv_q, inv_p = f"SELECT id, number, issue_date d, total FROM invoices WHERE {inv_w}", [cid]
    if dfrom:
        inv_q += " AND date(issue_date) >= date(?)"; inv_p.append(dfrom)
    if dto:
        inv_q += " AND date(issue_date) <= date(?)"; inv_p.append(dto)
    for r in db.query(inv_q, inv_p):
        entries.append({"date": r["d"], "kind": "invoice", "ref": r["number"],
                        "link_id": r["id"], "debit": r["total"], "credit": 0})
    pay_q = (f"SELECT p.paid_at d, p.amount, p.method, i.number, i.id iid "
             f"FROM payments p JOIN invoices i ON i.id=p.invoice_id WHERE p.{pay_w}")
    pay_p = [cid]
    if dfrom:
        pay_q += " AND date(p.paid_at) >= date(?)"; pay_p.append(dfrom)
    if dto:
        pay_q += " AND date(p.paid_at) <= date(?)"; pay_p.append(dto)
    for r in db.query(pay_q, pay_p):
        entries.append({"date": r["d"], "kind": "payment", "ref": r["number"],
                        "link_id": r["iid"], "method": r["method"],
                        "debit": 0, "credit": r["amount"]})
    entries.sort(key=lambda e: (str(e["date"] or ""), e["kind"]))
    bal = opening
    for e in entries:
        bal = round(bal + e["debit"] - e["credit"], 2)
        e["balance"] = bal
    return {"client": {"id": cid, "name_en": client["name_en"], "name_ar": client["name_ar"]},
            "from": dfrom, "to": dto, "opening": round(opening, 2), "closing": bal,
            "total_debit": round(sum(e["debit"] for e in entries), 2),
            "total_credit": round(sum(e["credit"] for e in entries), 2),
            "entries": entries}


def _invoice_from_quote(cx, q, status, due_days):
    """Inside a transaction: copy a quote row + its line items into a new
    invoice and mark the quote accepted. Returns the new invoice id."""
    iid = cx.execute(
        "INSERT INTO invoices(client_id,site_id,visit_id,contract_id,doc_type,number,issue_date,due_date,"
        "amount,tax,total,status,notes) VALUES(?,?,?,?,?,?,date('now'),date('now',?),?,?,?,?,?)",
        (q["client_id"], q["site_id"], q["visit_id"], q["contract_id"], "invoice",
         _next_invoice_number("invoice"), f"+{int(due_days)} days",
         q["amount"], q["tax"], q["total"], status, q["notes"])).lastrowid
    for it in cx.execute("SELECT * FROM invoice_items WHERE invoice_id=?", (q["id"],)).fetchall():
        cx.execute("INSERT INTO invoice_items(invoice_id,description,quantity,unit_price,amount) "
                   "VALUES(?,?,?,?,?)", (iid, it["description"], it["quantity"], it["unit_price"], it["amount"]))
    cx.execute("UPDATE invoices SET status='accepted' WHERE id=?", (q["id"],))
    return iid


@route("POST", r"/api/invoices/(\d+)/convert")
def convert_quote(ctx):
    """Convert an accepted quote into a draft invoice (copies line items)."""
    require_perm(ctx.user, "invoices.edit")
    qid = int(ctx.params[0])
    q = db.query("SELECT * FROM invoices WHERE id=?", (qid,), one=True)
    if not q or q["doc_type"] != "quote":
        raise ApiError(400, "Not a quote")
    if q["status"] not in ("draft", "sent"):
        raise ApiError(400, "Quote already converted or closed")
    with db.transaction() as cx:
        iid = _invoice_from_quote(cx, q, "draft", 15)
    return db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)


def _quote_for_decision(ctx):
    """Load a quote a client (own) or staff (invoices.edit) may act on."""
    q = db.query("SELECT * FROM invoices WHERE id=?", (int(ctx.params[0]),), one=True)
    if not q or q["doc_type"] != "quote":
        raise ApiError(400, "Not a quote")
    if ctx.user["role"] == "client":
        if q["client_id"] != ctx.user["client_id"]:
            raise ApiError(403, "No permission")
    else:
        require_perm(ctx.user, "invoices.edit")
    if q["status"] != "sent":
        raise ApiError(400, "Only a sent quote can be approved or declined")
    return q


@route("POST", r"/api/invoices/(\d+)/approve")
def approve_quote(ctx):
    """Client accepts a quote from the portal -> immediately payable invoice."""
    q = _quote_for_decision(ctx)
    terms = int(float(get_settings().get("payment_terms_days") or 14))
    with db.transaction() as cx:
        iid = _invoice_from_quote(cx, q, "sent", terms)
    inv = db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)
    _notify_roles(("admin", "manager"), "quote_approved", "Quote approved",
                  f"Quote {q['number']} was approved — invoice {inv['number']} created",
                  "invoice", iid, f"quoteok:{q['id']}", exclude=ctx.user["id"])
    audit(ctx, "quote.approve", "invoice", q["id"], f"{q['number']} -> {inv['number']}")
    return inv


@route("POST", r"/api/invoices/(\d+)/decline")
def decline_quote(ctx):
    """Client declines a quote from the portal (optional reason)."""
    q = _quote_for_decision(ctx)
    reason = str(ctx.body.get("reason") or "").strip()[:500]
    notes = (q["notes"] or "")
    if reason:
        notes = (notes + "\n" if notes else "") + f"Declined: {reason}"
    db.execute("UPDATE invoices SET status='declined', notes=? WHERE id=?", (notes, q["id"]))
    _notify_roles(("admin", "manager"), "quote_declined", "Quote declined",
                  f"Quote {q['number']} was declined" + (f": {reason}" if reason else ""),
                  "invoice", q["id"], f"quoteno:{q['id']}", exclude=ctx.user["id"])
    audit(ctx, "quote.decline", "invoice", q["id"], q["number"])
    return db.query("SELECT * FROM invoices WHERE id=?", (q["id"],), one=True)


# --------------------------------------------------------------------------
# PRICE BOOK — reusable service catalog for quote/invoice line items
# --------------------------------------------------------------------------
@route("GET", r"/api/price-book")
def list_price_book(ctx):
    if ctx.user["role"] == "client":       # internal catalog — staff only
        raise ApiError(403, "No permission")
    require_perm(ctx.user, "invoices.view")
    if ctx.query.get("all"):        # manage UI shows inactive items too
        return db.query("SELECT * FROM price_book ORDER BY active DESC, name_en")
    return db.query("SELECT * FROM price_book WHERE active=1 ORDER BY name_en")


@route("POST", r"/api/price-book")
def create_price_item(ctx):
    require_perm(ctx.user, "invoices.edit")
    b = ctx.body
    if not (b.get("name_en") or "").strip():
        raise ApiError(400, "Name is required")
    pid = db.execute("INSERT INTO price_book(name_en,name_ar,description,unit_price) VALUES(?,?,?,?)",
                     (b["name_en"].strip(), b.get("name_ar"), b.get("description"),
                      float(b.get("unit_price") or 0)))
    audit(ctx, "pricebook.create", "price_book", pid, b["name_en"].strip())
    return db.query("SELECT * FROM price_book WHERE id=?", (pid,), one=True)


@route("PUT", r"/api/price-book/(\d+)")
def update_price_item(ctx):
    require_perm(ctx.user, "invoices.edit")
    pid = int(ctx.params[0])
    if not db.query("SELECT 1 FROM price_book WHERE id=?", (pid,), one=True):
        raise ApiError(404, "Not found")
    b = ctx.body
    if "unit_price" in b:
        b["unit_price"] = float(b["unit_price"] or 0)
    if "active" in b:
        b["active"] = 1 if b["active"] in (1, "1", True, "true") else 0
    cols = ("name_en", "name_ar", "description", "unit_price", "active")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(pid)
    db.execute(f"UPDATE price_book SET {','.join(fields)} WHERE id=?", vals)
    return db.query("SELECT * FROM price_book WHERE id=?", (pid,), one=True)


@route("DELETE", r"/api/price-book/(\d+)")
def delete_price_item(ctx):
    require_perm(ctx.user, "invoices.delete")
    db.execute("DELETE FROM price_book WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


# --------------------------------------------------------------------------
# LEADS — sales pipeline (website booking form + manual entry)
# --------------------------------------------------------------------------
LEAD_STATUSES = ("new", "contacted", "quoted", "won", "lost")


@route("GET", r"/api/leads")
def list_leads(ctx):
    require_perm(ctx.user, "leads.view")
    where, params = [], []
    if ctx.query.get("status") and ctx.query["status"] in LEAD_STATUSES:
        where.append("l.status=?")
        params.append(ctx.query["status"])
    sql = ("SELECT l.*, c.name_en client_en, c.name_ar client_ar, u.full_name handled_by_name "
           "FROM leads l LEFT JOIN clients c ON c.id=l.client_id "
           "LEFT JOIN users u ON u.id=l.handled_by")
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = _paginate(ctx, sql, params, "ORDER BY l.created_at DESC")
    counts = {r["status"]: r["c"] for r in
              db.query("SELECT status, COUNT(*) c FROM leads GROUP BY status")}
    if isinstance(rows, dict):
        rows["counts"] = counts
        return rows
    return {"items": rows, "counts": counts}


@route("POST", r"/api/leads")
def create_lead(ctx):
    require_perm(ctx.user, "leads.create")
    b = ctx.body
    if not (b.get("name") or "").strip():
        raise ApiError(400, "Name is required")
    lid = db.execute(
        "INSERT INTO leads(name,company,phone,email,sector,message,preferred_date,source,status,note) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (b["name"].strip(), b.get("company"), b.get("phone"), b.get("email"), b.get("sector"),
         b.get("message"), b.get("preferred_date"), "manual",
         b.get("status") if b.get("status") in LEAD_STATUSES else "new", b.get("note")))
    audit(ctx, "lead.create", "lead", lid, b["name"].strip())
    return db.query("SELECT * FROM leads WHERE id=?", (lid,), one=True)


@route("PUT", r"/api/leads/(\d+)")
def update_lead(ctx):
    require_perm(ctx.user, "leads.edit")
    lid = int(ctx.params[0])
    cur = db.query("SELECT * FROM leads WHERE id=?", (lid,), one=True)
    if not cur:
        raise ApiError(404, "Lead not found")
    b = ctx.body
    if "status" in b and b["status"] not in LEAD_STATUSES:
        raise ApiError(400, "Invalid status")
    cols = ("name", "company", "phone", "email", "sector", "message",
            "preferred_date", "status", "note")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if not fields:
        raise ApiError(400, "Nothing to update")
    fields += ["handled_by=?", "updated_at=datetime('now')"]
    vals += [ctx.user["id"], lid]
    db.execute(f"UPDATE leads SET {','.join(fields)} WHERE id=?", vals)
    audit(ctx, "lead.update", "lead", lid, ", ".join(k for k in b))
    return db.query("SELECT * FROM leads WHERE id=?", (lid,), one=True)


@route("DELETE", r"/api/leads/(\d+)")
def delete_lead(ctx):
    require_perm(ctx.user, "leads.delete")
    db.execute("DELETE FROM leads WHERE id=?", (int(ctx.params[0]),))
    audit(ctx, "lead.delete", "lead", int(ctx.params[0]))
    return {"ok": True}


@route("POST", r"/api/leads/(\d+)/convert")
def convert_lead(ctx):
    """Won lead -> real client record (the lead keeps a link to it)."""
    require_perm(ctx.user, "leads.edit")
    require_perm(ctx.user, "clients.create")
    lid = int(ctx.params[0])
    lead = db.query("SELECT * FROM leads WHERE id=?", (lid,), one=True)
    if not lead:
        raise ApiError(404, "Lead not found")
    if lead["client_id"]:
        raise ApiError(400, "Lead already converted")
    with db.transaction() as cx:
        cid = cx.execute(
            "INSERT INTO clients(name_en,name_ar,contact_person,phone,email,notes) VALUES(?,?,?,?,?,?)",
            (lead["company"] or lead["name"], lead["company"] or lead["name"], lead["name"],
             lead["phone"], lead["email"],
             ("Lead from website. " if lead["source"] == "website" else "") + (lead["message"] or ""))).lastrowid
        cx.execute("UPDATE leads SET status='won', client_id=?, handled_by=?, "
                   "updated_at=datetime('now') WHERE id=?", (cid, ctx.user["id"], lid))
    audit(ctx, "lead.convert", "lead", lid, f"-> client {cid}")
    return db.query("SELECT * FROM clients WHERE id=?", (cid,), one=True)


@route("POST", r"/api/public/lead", auth_required=False)
def public_lead(ctx):
    """Unauthenticated booking/contact form on the marketing website.
    Rate-limited per IP; the hidden 'website' field is a bot honeypot."""
    b = ctx.body
    if b.get("website"):                       # honeypot filled -> bot; pretend success
        return {"ok": True}
    name = str(b.get("name") or "").strip()[:120]
    phone = str(b.get("phone") or "").strip()[:40]
    email = str(b.get("email") or "").strip()[:120]
    if not name or not (phone or email):
        raise ApiError(400, "Name and a phone or email are required")
    if not rate_limit(f"lead:{ctx.ip or '?'}", 5, 3600):
        raise ApiError(429, "Too many requests. Please try again later.")
    lid = db.execute(
        "INSERT INTO leads(name,company,phone,email,sector,message,preferred_date,source) "
        "VALUES(?,?,?,?,?,?,?,'website')",
        (name, str(b.get("company") or "").strip()[:120] or None, phone or None, email or None,
         str(b.get("sector") or "").strip()[:120] or None,
         str(b.get("message") or "").strip()[:2000] or None,
         str(b.get("preferred_date") or "").strip()[:40] or None))
    _notify_roles(("admin", "manager"), "lead_new", "New website lead",
                  f"{name}" + (f" — {phone}" if phone else "") + (f" — {email}" if email else ""),
                  "leads", lid, f"lead:{lid}")
    return {"ok": True}


# --------------------------------------------------------------------------
# ONLINE PAYMENTS — gateway-agnostic adapters + checkout/callback flow
# --------------------------------------------------------------------------
# Each provider implements two steps of the standard hosted-checkout dance:
#   create_intent(intent, invoice, return_url) -> (checkout_url, provider_ref)
#   parse_callback(ctx)                         -> (provider_ref, status, raw)
# Drop a new class in PAYMENT_PROVIDERS and select it via the payment_provider
# setting to support Paymob / Fawry / Stripe / etc. once merchant keys + HTTPS
# are in place. The built-in "manual" provider needs neither and is used to
# exercise the whole loop end-to-end (it confirms via an in-app prompt).
class PaymentProvider:
    name = "base"

    def create_intent(self, intent, invoice, return_url):
        raise ApiError(501, "Payment provider not implemented")

    def parse_callback(self, ctx):
        raise ApiError(501, "Payment provider not implemented")


class ManualProvider(PaymentProvider):
    """Sandbox provider: no external gateway. The client confirms in-app and the
    callback marks the intent paid. Stands in until a real gateway is wired."""
    name = "manual"

    def create_intent(self, intent, invoice, return_url):
        return return_url, intent["token"]

    def parse_callback(self, ctx):
        tok = ctx.body.get("token") or ctx.query.get("token")
        if not tok:
            raise ApiError(400, "token required")
        return tok, "paid", json.dumps(ctx.body)[:2000]


class PaymobProvider(PaymentProvider):
    """Skeleton for Paymob (Egypt). The flow is: auth token -> register order ->
    request a payment key -> redirect to the iframe; success arrives on the
    callback as an HMAC-signed transaction. Wire the TODOs once keys + HTTPS are
    available; until then it fails clearly rather than pretending to work."""
    name = "paymob"

    def create_intent(self, intent, invoice, return_url):
        s = get_settings()
        if not s.get("paymob_api_key") or not s.get("paymob_integration_id"):
            raise ApiError(400, "Paymob keys not configured in Settings")
        # TODO: POST /api/auth/tokens -> POST /api/ecommerce/orders ->
        #       POST /api/acceptance/payment_keys -> build the iframe URL.
        raise ApiError(501, "Paymob integration not finished — keys present, flow pending HTTPS")

    def parse_callback(self, ctx):
        s = get_settings()
        secret = s.get("paymob_hmac")
        if not secret:
            raise ApiError(400, "Paymob HMAC not configured")
        # TODO: recompute the HMAC over the ordered Paymob fields and compare to
        #       ctx.query['hmac']; map obj.success -> 'paid'/'failed'.
        raise ApiError(501, "Paymob callback verification not finished")


PAYMENT_PROVIDERS = {p.name: p for p in (ManualProvider(), PaymobProvider())}


def _active_payment_provider():
    name = (get_settings().get("payment_provider") or "manual").strip()
    return PAYMENT_PROVIDERS.get(name, PAYMENT_PROVIDERS["manual"])


@route("POST", r"/api/invoices/(\d+)/pay")
def start_payment(ctx):
    """Begin an online payment for an invoice. Clients may pay their own
    invoices; staff need payments.create (e.g. to hand a customer a pay link).
    Returns a checkout_url to send the payer to (or, for the manual provider, an
    in-app confirm path) plus the intent token to poll."""
    iid = int(ctx.params[0])
    inv = db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)
    if not inv:
        raise ApiError(404, "Invoice not found")
    if ctx.user["role"] == "client":
        _assert_client_access(ctx.user, inv["client_id"])
    else:
        require_perm(ctx.user, "payments.create")
    if inv["doc_type"] != "invoice":
        raise ApiError(400, "Only invoices can be paid")
    if inv["status"] == "cancelled":
        raise ApiError(400, "Invoice is cancelled")
    paid = db.query("SELECT COALESCE(SUM(amount),0) p FROM payments WHERE invoice_id=?",
                    (iid,), one=True)["p"]
    remaining = round(inv["total"] - paid, 2)
    if remaining <= 0:
        raise ApiError(400, "Invoice already paid")
    provider = _active_payment_provider()
    currency = get_settings().get("currency") or "EGP"
    token = uuid.uuid4().hex
    intent_id = db.execute(
        "INSERT INTO payment_intents(invoice_id,client_id,provider,token,amount,currency,status) "
        "VALUES(?,?,?,?,?,?, 'pending')",
        (iid, inv["client_id"], provider.name, token, remaining, currency))
    intent = db.query("SELECT * FROM payment_intents WHERE id=?", (intent_id,), one=True)
    checkout_url, provider_ref = provider.create_intent(intent, inv, f"/pay/{token}")
    db.execute("UPDATE payment_intents SET provider_ref=? WHERE id=?", (provider_ref, intent_id))
    audit(ctx, "payment.intent", "invoice", iid, f"{provider.name} {remaining:g} {currency}")
    return {"token": token, "provider": provider.name, "checkout_url": checkout_url,
            "amount": remaining, "currency": currency}


@route("GET", r"/api/payment-intents/([0-9a-fA-F]+)")
def get_payment_intent(ctx):
    """Poll a payment intent's status (after returning from checkout)."""
    intent = db.query("SELECT * FROM payment_intents WHERE token=?", (ctx.params[0],), one=True)
    if not intent:
        raise ApiError(404, "Not found")
    if ctx.user["role"] == "client":
        _assert_client_access(ctx.user, intent["client_id"])
    else:
        require_perm(ctx.user, "invoices.view")
    return {"token": intent["token"], "status": intent["status"], "amount": intent["amount"],
            "invoice_id": intent["invoice_id"]}


@route("POST", r"/api/payments/callback/(\w+)", auth_required=False)
def payment_callback(ctx):
    """Gateway callback / webhook (unauthenticated — the provider calls it). The
    adapter validates the payload; on success we record the payment, mark the
    invoice paid when fully covered, and flag the intent. Idempotent."""
    # Unauthenticated (the provider calls it) -> cap per-IP to blunt abuse.
    if not rate_limit(f"paycb:{ctx.ip}", 60, 60):
        raise ApiError(429, "Too many requests")
    provider = PAYMENT_PROVIDERS.get(ctx.params[0])
    if not provider:
        raise ApiError(404, "Unknown payment provider")
    ref, status, raw = provider.parse_callback(ctx)
    intent = db.query("SELECT * FROM payment_intents WHERE provider_ref=? OR token=?",
                      (ref, ref), one=True)
    if not intent:
        raise ApiError(404, "Unknown payment reference")
    if intent["status"] == "paid":
        return {"ok": True, "status": "paid"}      # already processed
    if status != "paid":
        db.execute("UPDATE payment_intents SET status=?, updated_at=datetime('now') WHERE id=?",
                   (status if status in ("failed", "cancelled") else "failed", intent["id"]))
        return {"ok": True, "status": status}
    iid = intent["invoice_id"]
    with db.transaction() as cx:
        pid = cx.execute(
            "INSERT INTO payments(invoice_id,amount,method,note) VALUES(?,?,?,?)",
            (iid, intent["amount"], f"online:{provider.name}",
             f"Online payment {intent['token'][:8]}")).lastrowid
        inv = cx.execute("SELECT number, total, status FROM invoices WHERE id=?", (iid,)).fetchone()
        tot = cx.execute("SELECT COALESCE(SUM(amount),0) p FROM payments WHERE invoice_id=?",
                         (iid,)).fetchone()["p"]
        if inv and tot >= inv["total"] and inv["status"] != "cancelled":
            cx.execute("UPDATE invoices SET status='paid' WHERE id=?", (iid,))
        cx.execute("UPDATE payment_intents SET status='paid', payment_id=?, "
                   "updated_at=datetime('now') WHERE id=?", (pid, intent["id"]))
    _notify_roles(("admin", "manager"), "payment_received", "Payment received",
                  f"Invoice {inv['number'] if inv else iid} paid online "
                  f"({intent['amount']:g} {intent['currency']})", "invoices", iid,
                  f"paid:{intent['id']}")
    return {"ok": True, "status": "paid"}


# --------------------------------------------------------------------------
# SETTINGS (company profile / branding)
# --------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "company_name_en": "PestCare Pest Control Co.", "company_name_ar": "شركة بيست كير لمكافحة الآفات",
    "address_en": "Riyadh, Saudi Arabia", "address_ar": "الرياض، المملكة العربية السعودية",
    "phone": "+966 11 000 0000", "email": "billing@pestcare.com", "vat_no": "300000000000003",
    "currency": "EGP", "tax_rate": "14", "payment_terms_days": "14", "logo": "",
    # Online payments. payment_provider selects the active gateway adapter
    # ("manual" = built-in sandbox that records a payment on confirm, no keys).
    # Real adapters (paymob/…) read their keys from these slots once supplied.
    "payment_provider": "manual",
    "paymob_api_key": "", "paymob_integration_id": "", "paymob_iframe_id": "", "paymob_hmac": "",
    "google_maps_api_key": "",   # enables Places search on contract site locations
    "company_geo": "",   # depot "lat,lng" — start point for route optimization
    # Service-certificate template (editable in Settings, used on every certificate).
    "cert_statement_en": "This is to certify that pest control services were carried out at the "
                         "premises detailed below, in accordance with professional standards and applicable regulations.",
    "cert_statement_ar": "نشهد بموجب هذه الوثيقة بأنه تم تنفيذ أعمال مكافحة الآفات في الموقع الموضح أدناه، "
                         "وفقاً للمعايير المهنية واللوائح المعمول بها.",
    "cert_footer_en": "This certificate is issued based on the service performed on the date stated above. "
                      "Continued protection is subject to the recommended next service date.",
    "cert_footer_ar": "تصدر هذه الشهادة بناءً على الخدمة المنفذة في التاريخ المذكور أعلاه. "
                      "تستمر الحماية وفقاً لموعد الخدمة القادم الموصى به.",
    "cert_license_no": "",
}


def get_settings():
    s = dict(DEFAULT_SETTINGS)
    for r in db.query("SELECT key, value FROM settings"):
        s[r["key"]] = r["value"]
    return s


# Settings that must never be exposed to non-admin users (credentials).
_SECRET_SETTING_KEYS = {"smtp_host", "smtp_port", "smtp_user", "smtp_pass",
                        "smtp_from", "smtp_tls", "paymob_api_key", "paymob_hmac"}


@route("GET", r"/api/settings")
def read_settings(ctx):
    s = get_settings()
    # Internal counters (invoice/quote sequences) are not user-facing settings.
    for k in [k for k in s if k.startswith("seq_")]:
        s.pop(k, None)
    # Everyone can read branding/cert/company fields the UI needs, but secrets
    # (SMTP credentials) are only returned to users who can manage settings.
    if not has_perm(ctx.user, "settings.view"):
        for k in _SECRET_SETTING_KEYS:
            s.pop(k, None)
        if ctx.user["role"] == "client":
            s.pop("google_maps_api_key", None)
    return s


@route("PUT", r"/api/settings")
def write_settings(ctx):
    require_perm(ctx.user, "settings.edit")
    for k, v in ctx.body.items():
        if k.startswith("seq_"):
            continue  # internal invoice/quote counters — not editable here
        db.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                   "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
    return get_settings()


@route("POST", r"/api/settings/logo")
def upload_logo(ctx):
    require_perm(ctx.user, "settings.edit")
    _fields, files = parse_multipart(ctx.raw_body, ctx.content_type)
    if not files:
        raise ApiError(400, "No file uploaded")
    f = files[0]
    ext = _validate_image_upload(f)
    fname = f"logo_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as out:
        out.write(f["data"])
    db.execute("INSERT INTO settings(key,value) VALUES('logo',?) "
               "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (fname,))
    return get_settings()


# --------------------------------------------------------------------------
# CONTRACTS (recurring schedules)
# --------------------------------------------------------------------------
FREQ_DAYS = {"weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91,
             "semiannual": 182, "annual": 365}


@route("GET", r"/api/contracts")
def list_contracts(ctx):
    u = ctx.user
    if u["role"] != "client":
        require_perm(u, "contracts.view")
    where, params = [], []
    if u["role"] == "client":
        where.append("ct.client_id=?")
        params.append(u["client_id"])
    elif u["role"] == "agent":
        where.append("ct.agent_id=?")
        params.append(u["id"])
    if ctx.query.get("client"):
        where.append("ct.client_id=?")
        params.append(ctx.query["client"])
    sql = ("SELECT ct.*, c.name_en client_en, c.name_ar client_ar, "
           "s.name_en service_en, s.name_ar service_ar, u.full_name agent_name "
           "FROM contracts ct JOIN clients c ON c.id=ct.client_id "
           "LEFT JOIN service_types s ON s.id=ct.service_type_id "
           "LEFT JOIN users u ON u.id=ct.agent_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY ct.next_run_date"
    rows = db.query(sql, params)
    # Attach each contract's per-site lines (location + Google Maps URL + price).
    for ct in rows:
        ct["sites"] = db.query(
            "SELECT cs.id, cs.site_id, cs.map_location, cs.price, s.name site_name "
            "FROM contract_sites cs LEFT JOIN sites s ON s.id=cs.site_id "
            "WHERE cs.contract_id=? ORDER BY cs.id", (ct["id"],))
    return rows


def _save_contract_sites(cid, sites):
    """Replace a contract's per-site lines. Each entry: {site_id, map_location, price}."""
    db.execute("DELETE FROM contract_sites WHERE contract_id=?", (cid,))
    for s in (sites or []):
        if not s.get("site_id") and not s.get("map_location") and not s.get("price"):
            continue  # skip empty rows
        db.execute(
            "INSERT INTO contract_sites(contract_id,site_id,map_location,price) VALUES(?,?,?,?)",
            (cid, s.get("site_id") or None, s.get("map_location") or None,
             float(s.get("price") or 0)))


@route("POST", r"/api/contracts")
def create_contract(ctx):
    require_perm(ctx.user, "contracts.create")
    b = ctx.body
    if not b.get("client_id") or not b.get("start_date") or not b.get("frequency"):
        raise ApiError(400, "Client, start date and frequency are required")
    sites = b.get("sites") or []
    # The contract's headline site/price are derived from the site rows (first
    # location, summed price) so existing lists and visit-generation still work.
    site_id = (sites[0].get("site_id") or None) if sites else b.get("site_id")
    price = sum(float(s.get("price") or 0) for s in sites) if sites else float(b.get("price", 0))
    auto = 1 if b.get("auto_invoice") in (1, "1", True, "true", "on") else 0
    next_bill = (b.get("next_bill_date") or b["start_date"]) if auto else None
    cid = db.execute(
        "INSERT INTO contracts(client_id,site_id,service_type_id,agent_id,frequency,start_date,"
        "end_date,next_run_date,price,status,notes,bill_every,next_bill_date,auto_invoice) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (b["client_id"], site_id, b.get("service_type_id"), b.get("agent_id"),
         b["frequency"], b["start_date"], b.get("end_date"), b["start_date"],
         price, b.get("status", "active"), b.get("notes"),
         b.get("bill_every") or None, next_bill, auto))
    _save_contract_sites(cid, sites)
    cl = db.query("SELECT name_en FROM clients WHERE id=?", (b["client_id"],), one=True)
    _notify_roles(("admin", "manager"), "contract_new", "New contract",
                  f"New {b['frequency']} contract for {cl['name_en'] if cl else ''}",
                  "contracts", cid, f"contract:{cid}", exclude=ctx.user["id"])
    return db.query("SELECT * FROM contracts WHERE id=?", (cid,), one=True)


@route("PUT", r"/api/contracts/(\d+)")
def update_contract(ctx):
    require_perm(ctx.user, "contracts.edit")
    cid = int(ctx.params[0])
    b = ctx.body
    sites = b.get("sites")
    if sites is not None:
        # Derive headline site/price from the rows, then persist the rows below.
        b["site_id"] = (sites[0].get("site_id") or None) if sites else None
        b["price"] = sum(float(s.get("price") or 0) for s in sites)
    if "auto_invoice" in b:
        b["auto_invoice"] = 1 if b["auto_invoice"] in (1, "1", True, "true", "on") else 0
        if b["auto_invoice"] and not b.get("next_bill_date"):
            cur = db.query("SELECT next_bill_date, start_date FROM contracts WHERE id=?",
                           (cid,), one=True) or {}
            b["next_bill_date"] = cur.get("next_bill_date") or cur.get("start_date") \
                or db.query("SELECT date('now') d", one=True)["d"]
    if b.get("bill_every") == "":
        b["bill_every"] = None
    cols = ("site_id", "service_type_id", "agent_id", "frequency", "start_date",
            "end_date", "next_run_date", "price", "status", "notes",
            "bill_every", "next_bill_date", "auto_invoice")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if not fields and sites is None:
        raise ApiError(400, "Nothing to update")
    if fields:
        vals.append(cid)
        db.execute(f"UPDATE contracts SET {','.join(fields)} WHERE id=?", vals)
    if sites is not None:
        _save_contract_sites(cid, sites)
    return db.query("SELECT * FROM contracts WHERE id=?", (cid,), one=True)


@route("DELETE", r"/api/contracts/(\d+)")
def delete_contract(ctx):
    require_perm(ctx.user, "contracts.delete")
    db.execute("DELETE FROM contracts WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


@route("POST", r"/api/contracts/run")
def run_contracts(ctx):
    """Generate visits for all active contracts due within the look-ahead window."""
    require_perm(ctx.user, "contracts.edit")
    created = _generate_due_visits()
    return {"created": created}


@route("POST", r"/api/contracts/bill")
def run_billing(ctx):
    """Generate any due recurring invoices for auto-billed contracts."""
    require_perm(ctx.user, "invoices.create")
    return {"created": _generate_due_invoices()}


def _generate_due_visits(lookahead_days=14):
    """Create visits for contracts whose next_run_date falls within the window.
    Advances next_run_date by the frequency. Returns number of visits created."""
    horizon = f"+{lookahead_days} days"
    due = db.query(
        "SELECT * FROM contracts WHERE status='active' AND date(next_run_date) <= date('now',?) "
        "AND (end_date IS NULL OR date(next_run_date) <= date(end_date))", (horizon,))
    count = 0
    for ct in due:
        days = FREQ_DAYS.get(ct["frequency"], 30)
        run = ct["next_run_date"]
        # generate every occurrence already due up to the horizon (avoids gaps)
        for _ in range(60):  # safety cap
            row = db.query("SELECT date(?) d, date('now',?) h", (run, horizon), one=True)
            if row["d"] > row["h"]:
                break
            if ct["end_date"]:
                er = db.query("SELECT date(?) d, date(?) e", (run, ct["end_date"]), one=True)
                if er["d"] > er["e"]:
                    break
            # skip if a visit for this contract already exists on that date
            exists = db.query(
                "SELECT id FROM visits WHERE client_id=? AND date(scheduled_start)=date(?) "
                "AND service_type_id IS ?", (ct["client_id"], run, ct["service_type_id"]), one=True)
            if not exists:
                db.execute(
                    "INSERT INTO visits(client_id,site_id,agent_id,service_type_id,scheduled_start,"
                    "status,notes) VALUES(?,?,?,?,?,?,?)",
                    (ct["client_id"], ct["site_id"], ct["agent_id"], ct["service_type_id"],
                     run + " 09:00:00", "scheduled", f"Auto-generated from contract #{ct['id']}"))
                count += 1
            nxt = db.query("SELECT date(?, ?) n", (run, f"+{days} days"), one=True)["n"]
            run = nxt
        db.execute("UPDATE contracts SET next_run_date=? WHERE id=?", (run, ct["id"]))
    return count


def _contract_invoice_items(ct):
    """Build invoice line items for a contract's billing cycle. One line per
    per-site row when present (so each location is itemized), else a single line
    at the contract's headline price."""
    svc = db.query("SELECT name_en FROM service_types WHERE id=?",
                   (ct["service_type_id"],), one=True) if ct["service_type_id"] else None
    label = (svc["name_en"] if svc else "Pest control") + f" — {ct['frequency']} service"
    sites = db.query(
        "SELECT cs.price, s.name site_name FROM contract_sites cs "
        "LEFT JOIN sites s ON s.id=cs.site_id WHERE cs.contract_id=? AND cs.price > 0 "
        "ORDER BY cs.id", (ct["id"],))
    if sites:
        return [{"description": f"{label}" + (f" — {s['site_name']}" if s["site_name"] else ""),
                 "quantity": 1, "unit_price": s["price"]} for s in sites]
    return [{"description": label, "quantity": 1, "unit_price": ct["price"]}]


def _generate_due_invoices(lookahead_days=0):
    """Auto-generate invoices for contracts opted into recurring billing whose
    next_bill_date has arrived. Advances next_bill_date by the billing cadence
    (bill_every, falling back to the service frequency). Idempotent: skips a
    cycle that already has a non-cancelled invoice for that contract + date."""
    horizon = f"+{lookahead_days} days"
    s = get_settings()
    try:
        tax_rate = float(s.get("tax_rate") or 0)
    except (TypeError, ValueError):
        tax_rate = 0.0
    try:
        terms = int(float(s.get("payment_terms_days") or 14))
    except (TypeError, ValueError):
        terms = 14
    due = db.query(
        "SELECT * FROM contracts WHERE status='active' AND auto_invoice=1 "
        "AND next_bill_date IS NOT NULL AND date(next_bill_date) <= date('now',?) "
        "AND (end_date IS NULL OR date(next_bill_date) <= date(end_date))", (horizon,))
    count = 0
    for ct in due:
        days = FREQ_DAYS.get(ct["bill_every"] or ct["frequency"], 30)
        bill = ct["next_bill_date"]
        for _ in range(36):  # safety cap on catch-up
            row = db.query("SELECT date(?) d, date('now',?) h", (bill, horizon), one=True)
            if row["d"] > row["h"]:
                break
            if ct["end_date"]:
                er = db.query("SELECT date(?) d, date(?) e", (bill, ct["end_date"]), one=True)
                if er["d"] > er["e"]:
                    break
            exists = db.query(
                "SELECT id FROM invoices WHERE contract_id=? AND doc_type='invoice' "
                "AND date(issue_date)=date(?) AND status!='cancelled'",
                (ct["id"], bill), one=True)
            if not exists:
                items = _contract_invoice_items(ct)
                number = _next_invoice_number("invoice")
                due_date = db.query("SELECT date(?, ?) n", (bill, f"+{terms} days"), one=True)["n"]
                with db.transaction() as cx:
                    iid = cx.execute(
                        "INSERT INTO invoices(client_id,site_id,contract_id,doc_type,number,issue_date,"
                        "due_date,amount,tax,total,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                        (ct["client_id"], ct["site_id"], ct["id"], "invoice", number, bill,
                         due_date, 0, 0, 0, "sent",
                         f"Auto-generated from contract #{ct['id']}")).lastrowid
                    amount = _save_items(iid, items, cx)
                    tax = round(amount * tax_rate / 100, 2)
                    cx.execute("UPDATE invoices SET amount=?, tax=?, total=? WHERE id=?",
                               (amount, tax, amount + tax, iid))
                count += 1
                # surface the new invoice to the client portal
                cl = db.query("SELECT name_en FROM clients WHERE id=?", (ct["client_id"],), one=True)
                for cu in db.query(
                        "SELECT id FROM users WHERE role='client' AND client_id=? AND active=1",
                        (ct["client_id"],)):
                    _notify(cu["id"], "invoice_new", "New invoice",
                            f"{number} — {amount + tax:g}", "invoices", iid,
                            f"autoinv:{iid}:{cu['id']}")
            bill = db.query("SELECT date(?, ?) n", (bill, f"+{days} days"), one=True)["n"]
        db.execute("UPDATE contracts SET next_bill_date=? WHERE id=?", (bill, ct["id"]))
    return count


# --------------------------------------------------------------------------
# SMART DISPATCH — route optimization + SLA tracking
# --------------------------------------------------------------------------
DISPATCH_SLOT_MIN = 90   # minutes between visits when applying an optimized route


def _coerce_latlng(b):
    """Pull (lat, lng) from a request body: a free-text `geo` field (lat,lng or
    a maps URL) or explicit numeric lat/lng. Returns (None, None) when absent."""
    geo = b.get("geo")
    if geo:
        ll = db._parse_latlng(geo)
        if ll:
            return ll
    lat, lng = b.get("lat"), b.get("lng")
    if lat in (None, "") or lng in (None, ""):
        return (None, None)
    try:
        lat, lng = float(lat), float(lng)
    except (TypeError, ValueError):
        return (None, None)
    if -90 <= lat <= 90 and -180 <= lng <= 180:
        return (lat, lng)
    return (None, None)


def _haversine(a, b):
    """Great-circle distance in km between two (lat, lng) tuples."""
    R = 6371.0
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    dlat, dlng = lat2 - lat1, lng2 - lng1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _visit_geo(v):
    """Resolve a visit's coordinates: its site's lat/lng, else parse the visit's
    free-text location. Returns (lat, lng) or None."""
    if v.get("site_lat") is not None and v.get("site_lng") is not None:
        return (v["site_lat"], v["site_lng"])
    return db._parse_latlng(v.get("location"))


def _route_km(points):
    """Total path length (km) visiting `points` (list of (lat,lng)) in order."""
    return sum(_haversine(points[i], points[i + 1]) for i in range(len(points) - 1))


@route("POST", r"/api/dispatch/optimize")
def dispatch_optimize(ctx):
    """Order one agent's visits for a day to minimize driving (nearest-neighbour
    on site coordinates). Preview by default; pass apply=true to rewrite the
    visits' scheduled times into the optimized sequence."""
    require_perm(ctx.user, "visits.edit")
    b = ctx.body
    agent_id, date = b.get("agent_id"), b.get("date")
    if not agent_id or not date:
        raise ApiError(400, "agent_id and date are required")
    rows = db.query(
        "SELECT v.id, v.scheduled_start, v.scheduled_end, v.location, "
        "c.name_en client_en, c.name_ar client_ar, st.name site_name, "
        "st.lat site_lat, st.lng site_lng "
        "FROM visits v JOIN clients c ON c.id=v.client_id "
        "LEFT JOIN sites st ON st.id=v.site_id "
        "WHERE v.agent_id=? AND date(v.scheduled_start)=date(?) "
        "AND v.status IN ('scheduled','in_progress') ORDER BY v.scheduled_start",
        (agent_id, date))
    geo, ungeo = [], []
    for v in rows:
        g = _visit_geo(v)
        if g:
            v["lat"], v["lng"] = g
            geo.append(v)
        else:
            ungeo.append(v)
    # start point: explicit in body, else company HQ coords, else the first stop
    start = _coerce_latlng(b)
    if start == (None, None):
        start = db._parse_latlng(get_settings().get("company_geo"))
    if not start and geo:
        start = (geo[0]["lat"], geo[0]["lng"])
    pre = ([start] if start else [])

    def length(seq):
        return _route_km(pre + [(g["lat"], g["lng"]) for g in seq])

    km_before = length(geo)
    # nearest-neighbour ordering
    remaining, optimized = geo[:], []
    cur = start or (remaining[0]["lat"], remaining[0]["lng"]) if remaining else None
    while remaining:
        nxt = min(remaining, key=lambda g: _haversine(cur, (g["lat"], g["lng"])))
        optimized.append(nxt); cur = (nxt["lat"], nxt["lng"]); remaining.remove(nxt)
    km_after = length(optimized)

    final = optimized + ungeo
    applied = False
    if b.get("apply") and final:
        starts = [v["scheduled_start"] for v in rows if v.get("scheduled_start")]
        first_t = min(starts)[11:16] if starts else "09:00"
        import datetime as _dt
        try:
            base = _dt.datetime.strptime(f"{date} {first_t}", "%Y-%m-%d %H:%M")
        except ValueError:
            base = _dt.datetime.strptime(f"{date} 09:00", "%Y-%m-%d %H:%M")
        with db.transaction() as cx:
            for i, v in enumerate(final):
                ns = (base + _dt.timedelta(minutes=i * DISPATCH_SLOT_MIN)).strftime("%Y-%m-%d %H:%M:00")
                cx.execute("UPDATE visits SET scheduled_start=? WHERE id=?", (ns, v["id"]))
        applied = True
        audit(ctx, "dispatch.optimize", "user", int(agent_id),
              f"{len(final)} visits on {date}, saved {round(km_before - km_after, 1)} km")

    def card(v, i):
        return {"id": v["id"], "seq": i + 1, "client_en": v["client_en"],
                "client_ar": v["client_ar"], "site_name": v["site_name"],
                "scheduled_start": v["scheduled_start"],
                "lat": v.get("lat"), "lng": v.get("lng")}
    return {
        "agent_id": int(agent_id), "date": date, "applied": applied,
        "km_before": round(km_before, 2), "km_after": round(km_after, 2),
        "km_saved": round(max(0, km_before - km_after), 2),
        "stops": len(geo), "ungeocoded": len(ungeo),
        "order": [card(v, i) for i, v in enumerate(final)],
        "has_start": bool(start),
    }


def _sla_rows():
    """Per active-contract SLA status. Tiered: ok / due_soon (period end
    approaching) / overdue (past period end + ~20% grace). Compares the contract
    cadence against the last COMPLETED visit for that client (+ site if set)."""
    out = []
    for ct in db.query(
            "SELECT ct.*, c.name_en client_en, c.name_ar client_ar, s.name site_name "
            "FROM contracts ct JOIN clients c ON c.id=ct.client_id "
            "LEFT JOIN sites s ON s.id=ct.site_id WHERE ct.status='active'"):
        period = FREQ_DAYS.get(ct["frequency"], 30)
        site_clause, sp = "", []
        if ct["site_id"]:
            site_clause = " AND site_id=?"; sp = [ct["site_id"]]
        last = db.query(
            "SELECT MAX(date(COALESCE(completed_at, scheduled_start))) d FROM visits "
            "WHERE client_id=? AND status='completed'" + site_clause,
            (ct["client_id"], *sp), one=True)["d"]
        ref = last or ct["start_date"]
        days_since = db.query("SELECT CAST(julianday('now') - julianday(?) AS INT) d",
                              (ref,), one=True)["d"] or 0
        if days_since < period * 0.8:
            status = "ok"
        elif days_since <= period * 1.2:
            status = "due_soon"
        else:
            status = "overdue"
        out.append({
            "contract_id": ct["id"], "client_id": ct["client_id"],
            "client_en": ct["client_en"], "client_ar": ct["client_ar"],
            "site_name": ct["site_name"], "frequency": ct["frequency"],
            "period_days": period, "last_service": last, "days_since": days_since,
            "days_overdue": max(0, days_since - period), "next_run_date": ct["next_run_date"],
            "status": status,
        })
    out.sort(key=lambda r: (-r["days_since"]))
    return out


@route("GET", r"/api/dispatch/sla")
def dispatch_sla(ctx):
    require_perm(ctx.user, "contracts.view")
    rows = _sla_rows()
    if ctx.user["role"] == "client":
        rows = [r for r in rows if r["client_id"] == ctx.user["client_id"]]
    counts = {"ok": 0, "due_soon": 0, "overdue": 0}
    for r in rows:
        counts[r["status"]] += 1
    return {"items": rows, "counts": counts}


# --------------------------------------------------------------------------
# VISIT REQUESTS — client self-service ("request a visit")
# --------------------------------------------------------------------------
@route("GET", r"/api/visit-requests")
def list_visit_requests(ctx):
    u = ctx.user
    where, params = [], []
    if u["role"] == "client":
        where.append("vr.client_id=?"); params.append(u["client_id"])
    else:
        require_perm(u, "visits.view")
        if ctx.query.get("status"):
            where.append("vr.status=?"); params.append(ctx.query["status"])
    sql = ("SELECT vr.*, c.name_en client_en, c.name_ar client_ar, s.name site_name, "
           "u.full_name requested_by FROM visit_requests vr "
           "JOIN clients c ON c.id=vr.client_id "
           "LEFT JOIN sites s ON s.id=vr.site_id "
           "LEFT JOIN users u ON u.id=vr.created_by")
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY CASE vr.status WHEN 'pending' THEN 0 ELSE 1 END, vr.created_at DESC"
    return db.query(sql, params)


@route("POST", r"/api/visit-requests")
def create_visit_request(ctx):
    u, b = ctx.user, ctx.body
    if u["role"] == "client":
        cid = u["client_id"]
    else:
        require_perm(u, "visits.create")
        cid = b.get("client_id")
    if not cid:
        raise ApiError(400, "Client is required")
    _assert_client_access(u, cid)
    site_id = b.get("site_id") or None
    if site_id and not db.query("SELECT 1 FROM sites WHERE id=? AND client_id=?", (site_id, cid), one=True):
        raise ApiError(400, "Invalid location for this client")
    rid = db.execute(
        "INSERT INTO visit_requests(client_id,site_id,preferred_date,note,created_by) "
        "VALUES(?,?,?,?,?)", (cid, site_id, b.get("preferred_date"), b.get("note"), u["id"]))
    cl = db.query("SELECT name_en FROM clients WHERE id=?", (cid,), one=True)
    _notify_roles(("admin", "manager"), "visit_request", "New visit request",
                  f"{cl['name_en'] if cl else 'A client'} requested a visit"
                  + (f" for {b['preferred_date']}" if b.get("preferred_date") else ""),
                  "requests", rid, f"vreq:{rid}", exclude=u["id"])
    return db.query("SELECT * FROM visit_requests WHERE id=?", (rid,), one=True)


def _get_request_or_404(rid):
    r = db.query("SELECT * FROM visit_requests WHERE id=?", (rid,), one=True)
    if not r:
        raise ApiError(404, "Request not found")
    return r


@route("POST", r"/api/visit-requests/(\d+)/approve")
def approve_visit_request(ctx):
    require_perm(ctx.user, "visits.create")
    r = _get_request_or_404(int(ctx.params[0]))
    if r["status"] != "pending":
        raise ApiError(400, "Request already handled")
    b = ctx.body
    day = r["preferred_date"] or db.query("SELECT date('now') d", one=True)["d"]
    start = b.get("scheduled_start") or (day + " 09:00:00")
    site_id = b.get("site_id", r["site_id"]) or None
    with db.transaction() as cx:
        vid = cx.execute(
            "INSERT INTO visits(client_id,site_id,agent_id,service_type_id,scheduled_start,"
            "status,notes) VALUES(?,?,?,?,?,?,?)",
            (r["client_id"], site_id, b.get("agent_id"), b.get("service_type_id"), start,
             "scheduled", r["note"])).lastrowid
        cx.execute("UPDATE visit_requests SET status='approved', visit_id=?, handled_by=?, "
                   "handled_at=datetime('now') WHERE id=?", (vid, ctx.user["id"], r["id"]))
    audit(ctx, "visit_request.approve", "visit", vid, f"from request #{r['id']}")
    # tell the requester their request was scheduled
    if r["created_by"]:
        _notify(r["created_by"], "request_approved", "Visit scheduled",
                f"Your visit request was scheduled for {start[:16]}", "visit", vid,
                f"vreqok:{r['id']}")
    return db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)


@route("POST", r"/api/visit-requests/(\d+)/decline")
def decline_visit_request(ctx):
    require_perm(ctx.user, "visits.create")
    r = _get_request_or_404(int(ctx.params[0]))
    if r["status"] != "pending":
        raise ApiError(400, "Request already handled")
    db.execute("UPDATE visit_requests SET status='declined', handled_by=?, handled_at=datetime('now') "
               "WHERE id=?", (ctx.user["id"], r["id"]))
    audit(ctx, "visit_request.decline", "visit_request", r["id"])
    if r["created_by"]:
        _notify(r["created_by"], "request_declined", "Visit request declined",
                ctx.body.get("reason") or "Your visit request was declined.", "requests", r["id"],
                f"vreqno:{r['id']}")
    return {"ok": True}


# --------------------------------------------------------------------------
# NOTIFICATIONS / REMINDERS
# --------------------------------------------------------------------------
@route("GET", r"/api/notifications")
def list_notifications(ctx):
    if ctx.user["role"] in ("admin", "manager", "agent"):
        _maybe_generate_reminders()  # keep the bell live without a manual trigger
    rows = db.query("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                    (ctx.user["id"],))
    # Notification text is stored in English; show it in the user's language.
    # _translate caches per string, so 60s polls don't keep hitting the network.
    lang = ctx.query.get("lang")
    if lang in ("en", "ar"):
        for r in rows:
            r["title"] = _translate(r.get("title"), lang)
            if r.get("body"):
                r["body"] = _translate(r["body"], lang)
    unread = sum(1 for r in rows if not r["is_read"])
    return {"items": rows, "unread": unread}


@route("POST", r"/api/notifications/read")
def mark_notifications_read(ctx):
    ids = ctx.body.get("ids")
    if ids:
        q = ",".join("?" * len(ids))
        db.execute(f"UPDATE notifications SET is_read=1 WHERE user_id=? AND id IN ({q})",
                   [ctx.user["id"], *ids])
    else:
        db.execute("UPDATE notifications SET is_read=1 WHERE user_id=?", (ctx.user["id"],))
    return {"ok": True}


@route("POST", r"/api/notifications/generate")
def gen_notifications(ctx):
    require_perm(ctx.user, "visits.view")
    return {"created": _generate_reminders()}


def _notify(user_id, ntype, title, body, link_view=None, link_id=None, dedup_key=None):
    if dedup_key and db.query("SELECT id FROM notifications WHERE dedup_key=?", (dedup_key,), one=True):
        return False
    db.execute("INSERT INTO notifications(user_id,type,title,body,link_view,link_id,dedup_key) "
               "VALUES(?,?,?,?,?,?,?)", (user_id, ntype, title, body, link_view, link_id, dedup_key))
    _maybe_send_email(user_id, title, body)
    return True


def _notify_roles(roles, ntype, title, body, link_view=None, link_id=None, dedup_key=None, exclude=None):
    """Notify every active user holding one of `roles` (dedup key is made unique
    per recipient). `exclude` skips one user id (e.g. the actor)."""
    ph = ",".join("?" * len(roles))
    n = 0
    for usr in db.query(f"SELECT id FROM users WHERE role IN ({ph}) AND active=1", tuple(roles)):
        if exclude and usr["id"] == exclude:
            continue
        dk = f"{dedup_key}:{usr['id']}" if dedup_key else None
        if _notify(usr["id"], ntype, title, body, link_view, link_id, dk):
            n += 1
    return n


def _notify_client_users(client_id, ntype, title, body, link_view=None, link_id=None, dedup_key=None):
    """Notify every active portal user of a client company."""
    n = 0
    for cu in db.query("SELECT id FROM users WHERE role='client' AND client_id=? AND active=1",
                       (client_id,)):
        dk = f"{dedup_key}:{cu['id']}" if dedup_key else None
        if _notify(cu["id"], ntype, title, body, link_view, link_id, dk):
            n += 1
    return n


def _maybe_send_email(user_id, subject, body):
    """Send email if SMTP is configured in settings; otherwise no-op (in-app only)."""
    s = get_settings()
    host = s.get("smtp_host")
    if not host:
        return
    try:
        import smtplib
        from email.message import EmailMessage
        user = db.query("SELECT email FROM users WHERE id=?", (user_id,), one=True)
        if not user or not user["email"]:
            return
        msg = EmailMessage()
        msg["From"] = s.get("smtp_from", s.get("email", "noreply@pestcare.com"))
        msg["To"] = user["email"]
        msg["Subject"] = subject
        msg.set_content(body or subject)
        port = int(s.get("smtp_port", 587))
        with smtplib.SMTP(host, port, timeout=10) as srv:
            if s.get("smtp_tls", "1") == "1":
                srv.starttls()
            if s.get("smtp_user"):
                srv.login(s["smtp_user"], s.get("smtp_pass", ""))
            srv.send_message(msg)
    except Exception as e:
        print("email send failed:", e)


# The notification bell is polled by every signed-in staff browser every 60s.
# Running the full reminder scan on each poll means repeated table scans + writes
# (DB contention + latency). Throttle it so the scan runs at most once per window
# no matter how many users are polling; the result is identical (it's idempotent
# and de-duplicated), just not recomputed needlessly.
_reminder_lock = threading.Lock()
_reminder_last = 0.0
REMINDER_MIN_INTERVAL = 120  # seconds (keeps "time to start" reminders timely)


def _maybe_generate_reminders():
    global _reminder_last
    now = time.time()
    with _reminder_lock:
        if now - _reminder_last < REMINDER_MIN_INTERVAL:
            return
        _reminder_last = now
    try:
        _generate_due_invoices()  # recurring billing, alongside reminders (idempotent)
        _generate_reminders()
    except Exception as e:
        print("reminder gen:", e)


def _generate_reminders():
    """Scan upcoming visits and overdue invoices and create de-duplicated reminders."""
    created = 0
    # upcoming visits in next 2 days -> notify assigned agent
    for v in db.query(
        "SELECT v.id, v.agent_id, v.scheduled_start, c.name_en FROM visits v "
        "JOIN clients c ON c.id=v.client_id WHERE v.status='scheduled' AND v.agent_id IS NOT NULL "
        "AND date(v.scheduled_start) BETWEEN date('now') AND date('now','+2 days')"):
        if _notify(v["agent_id"], "visit_reminder", "Upcoming visit",
                   f"{v['name_en']} on {v['scheduled_start'][:16]}", "visit", v["id"],
                   f"visitrem:{v['id']}:{v['scheduled_start'][:10]}"):
            created += 1
    # visits about to start (<=15 min away, still not started) -> remind the agent
    # it's time to start the visit.
    for v in db.query(
        "SELECT v.id, v.agent_id, v.scheduled_start, c.name_en FROM visits v "
        "JOIN clients c ON c.id=v.client_id WHERE v.status='scheduled' AND v.agent_id IS NOT NULL "
        "AND datetime(v.scheduled_start) <= datetime('now','+15 minutes') "
        "AND datetime(v.scheduled_start) >= datetime('now','-3 hours')"):
        if _notify(v["agent_id"], "visit_due", "Time to start your visit",
                   f"Your visit at {v['name_en']} starts at {v['scheduled_start'][11:16]}",
                   "visit", v["id"], f"visitstart:{v['id']}"):
            created += 1
    # unfinished (draft) reports left by agents -> remind the agent to complete &
    # save. Only nag once the draft has sat for >10 min (i.e. they likely left the
    # visit / logged out mid-report rather than still actively typing).
    for r in db.query(
        "SELECT r.visit_id, v.agent_id, c.name_en FROM reports r "
        "JOIN visits v ON v.id=r.visit_id JOIN clients c ON c.id=v.client_id "
        "WHERE r.status='draft' AND v.agent_id IS NOT NULL "
        "AND datetime(r.created_at) < datetime('now','-10 minutes')"):
        if _notify(r["agent_id"], "report_draft", "Unfinished report",
                   f"Complete & save your report for {r['name_en']}", "visit", r["visit_id"],
                   f"draftrep:{r['visit_id']}"):
            created += 1
    # overdue invoices -> notify managers/admins
    managers = db.query("SELECT id FROM users WHERE role IN ('admin','manager') AND active=1")
    for inv in db.query(
        "SELECT i.id, i.number, i.total, c.name_en FROM invoices i JOIN clients c ON c.id=i.client_id "
        "WHERE i.doc_type='invoice' AND i.status IN ('sent','overdue') AND i.due_date IS NOT NULL "
        "AND date(i.due_date) < date('now')"):
        db.execute("UPDATE invoices SET status='overdue' WHERE id=? AND status='sent'", (inv["id"],))
        for m in managers:
            if _notify(m["id"], "invoice_overdue", "Invoice overdue",
                       f"{inv['number']} — {inv['name_en']} ({inv['total']})", "invoice", inv["id"],
                       f"ovd:{inv['id']}:{m['id']}"):
                created += 1
    # SLA breaches: a contract site whose service has slipped past its cadence
    # (period + grace) -> alert managers/admins. Dedup per contract per month so
    # a standing breach re-alerts monthly rather than every scan.
    bucket = db.query("SELECT strftime('%Y-%m','now') m", one=True)["m"]
    for r in _sla_rows():
        if r["status"] != "overdue":
            continue
        name = r["client_en"] + (f" — {r['site_name']}" if r["site_name"] else "")
        for m in managers:
            if _notify(m["id"], "sla_breach", "SLA breach — service overdue",
                       f"{name}: {r['frequency']} service is {r['days_overdue']} days overdue",
                       "dispatch", r["contract_id"], f"sla:{r['contract_id']}:{bucket}:{m['id']}"):
                created += 1
    # low-stock items -> alert managers/admins to reorder. Only flag items that
    # have a reorder level set (reorder_level=0 means "not tracked"). Dedup per
    # item per month so a standing shortage re-alerts monthly, not every scan.
    for ch in db.query(
        "SELECT id, name_en, unit, quantity_in_stock, reorder_level FROM chemicals "
        "WHERE reorder_level > 0 AND quantity_in_stock <= reorder_level"):
        for m in managers:
            if _notify(m["id"], "low_stock", "Low stock — reorder",
                       f"{ch['name_en']}: {ch['quantity_in_stock']:g} {ch['unit']} left "
                       f"(reorder at {ch['reorder_level']:g} {ch['unit']})",
                       "chemicals", ch["id"], f"restock:{ch['id']}:{bucket}:{m['id']}"):
                created += 1
    # Devices overdue for a scan (not inspected in STALE_SCAN_DAYS, or never)
    # -> monthly summary alert to managers/admins so routine service gaps get
    # chased. One notification per month, not per device, to avoid spam.
    stale_n = _stale_count()
    if stale_n:
        for m in managers:
            if _notify(m["id"], "devices_stale", "Devices overdue for service",
                       f"{stale_n} device(s) not scanned in {STALE_SCAN_DAYS}+ days",
                       "devices", None, f"staledev:{bucket}:{m['id']}"):
                created += 1
    return created


# --------------------------------------------------------------------------
# ANALYTICS
# --------------------------------------------------------------------------
@route("GET", r"/api/analytics")
def analytics(ctx):
    """Company-wide analytics, optionally scoped to one client and/or one of its
    sites, over a [from,to] date range (default: last 12 months). Finance +
    operations + collection KPIs + a fleet/pest rollup from the QR device scans."""
    require_perm(ctx.user, "analytics.view")
    import datetime
    q = ctx.query
    d_to = q.get("to") or datetime.date.today().isoformat()
    d_from = q.get("from") or (datetime.date.today() - datetime.timedelta(days=365)).isoformat()
    dr = (d_from, d_to)
    cid = int(q["client_id"]) if (q.get("client_id") or "").isdigit() else None
    if cid:
        _assert_client_access(ctx.user, cid)
    site_id = q.get("site_id") or None      # numeric id | "none" | None(all)
    labels = _months_between(d_from, d_to)
    r1 = lambda x: round(x, 1) if x is not None else None

    def scope(client_col, site_col):
        """AND-clause + params scoping to the selected client and/or site."""
        cl, pr = "", []
        if cid:
            cl += " AND %s=?" % client_col
            pr.append(cid)
        sf, sp = _site_filter(site_id, site_col)
        return cl + sf, pr + list(sp)

    DI_SITE = "(SELECT site_id FROM devices WHERE id=device_inspections.device_id)"

    # --- revenue trend: invoiced vs collected, per month over the range ---
    sc, sp = scope("client_id", "site_id")
    inv_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',issue_date) m, SUM(total) v FROM invoices "
        "WHERE doc_type='invoice' AND date(issue_date) BETWEEN ? AND ?" + sc + " GROUP BY m", (*dr, *sp))}
    sc, sp = scope("i.client_id", "i.site_id")
    paid_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',p.paid_at) m, SUM(p.amount) v FROM payments p "
        "JOIN invoices i ON i.id=p.invoice_id WHERE date(p.paid_at) BETWEEN ? AND ?" + sc + " GROUP BY m", (*dr, *sp))}
    months = [{"m": k, "total": round(inv_by.get(k, 0) or 0, 2),
               "paid": round(paid_by.get(k, 0) or 0, 2)} for k in labels]

    # AR aging: point-in-time snapshot of open invoices (client/site scoped).
    sc, sp = scope("i.client_id", "i.site_id")
    ar_aging = db.query(
        "SELECT CASE WHEN due_date IS NULL OR date(due_date)>=date('now') THEN 'current' "
        "WHEN date(due_date)>=date('now','-30 days') THEN '1-30' "
        "WHEN date(due_date)>=date('now','-60 days') THEN '31-60' ELSE '60+' END bucket, "
        "SUM(total - COALESCE((SELECT SUM(amount) FROM payments p WHERE p.invoice_id=i.id),0)) due "
        "FROM invoices i WHERE doc_type='invoice' AND status NOT IN('paid','cancelled')" + sc + " GROUP BY bucket", sp)
    sc, sp = scope("v.client_id", "v.site_id")
    agents = db.query(
        "SELECT u.full_name, COUNT(v.id) total, "
        "SUM(CASE WHEN v.status='completed' THEN 1 ELSE 0 END) completed "
        "FROM users u LEFT JOIN visits v ON v.agent_id=u.id "
        "AND date(v.scheduled_start) BETWEEN ? AND ?" + sc +
        " WHERE u.role='agent' GROUP BY u.id ORDER BY completed DESC", (*dr, *sp))
    # chemicals: scope via the usage's visit when a client/site is selected.
    sc, sp = scope("v.client_id", "v.site_id")
    cu_extra = (" AND cu.visit_id IN (SELECT v.id FROM visits v WHERE 1=1" + sc + ")") if sc else ""
    chemicals = db.query(
        "SELECT ch.name_en, ch.name_ar, ch.unit, COALESCE(SUM(cu.quantity),0) used "
        "FROM chemicals ch LEFT JOIN chemical_usage cu ON cu.chemical_id=ch.id "
        "AND date(cu.created_at) BETWEEN ? AND ?" + cu_extra +
        " GROUP BY ch.id HAVING used > 0 ORDER BY used DESC LIMIT 10", (*dr, *sp))
    sc, sp = scope("v.client_id", "v.site_id")
    services = db.query(
        "SELECT s.name_en, s.name_ar, COUNT(v.id) cnt FROM service_types s "
        "LEFT JOIN visits v ON v.service_type_id=s.id "
        "AND date(v.scheduled_start) BETWEEN ? AND ?" + sc +
        " GROUP BY s.id HAVING cnt>0 ORDER BY cnt DESC", (*dr, *sp))

    # --- totals + operational / collection KPIs (range + client/site scoped) ---
    sc, sp = scope("v.client_id", "v.site_id")
    vis = db.query(
        "SELECT COUNT(*) total, SUM(status='completed') completed, "
        "SUM(status='cancelled') cancelled FROM visits v "
        "WHERE date(v.scheduled_start) BETWEEN ? AND ?" + sc, (*dr, *sp), one=True)
    sc, sp = scope("client_id", "site_id")
    invoiced = db.query("SELECT COALESCE(SUM(total),0) v FROM invoices "
                        "WHERE doc_type='invoice' AND date(issue_date) BETWEEN ? AND ?" + sc, (*dr, *sp), one=True)["v"]
    sc, sp = scope("i.client_id", "i.site_id")
    revenue = db.query("SELECT COALESCE(SUM(p.amount),0) v FROM payments p "
                       "JOIN invoices i ON i.id=p.invoice_id WHERE date(p.paid_at) BETWEEN ? AND ?" + sc, (*dr, *sp), one=True)["v"]
    sla = {"ok": 0, "due_soon": 0, "overdue": 0}
    site_num = int(site_id) if (site_id or "").isdigit() else None
    for r in _sla_rows():
        if cid and r["client_id"] != cid:
            continue
        if site_num is not None and r["site_id"] != site_num:
            continue
        sla[r["status"]] = sla.get(r["status"], 0) + 1
    vtotal, vcomp = vis["total"] or 0, vis["completed"] or 0
    nc_extra, nc_p = (" AND id=?", [cid]) if cid else ("", [])
    ac_extra, ac_p = (" AND client_id=?", [cid]) if cid else ("", [])
    totals = {
        "revenue": revenue, "invoiced": invoiced,
        "visits_total": vtotal, "visits_completed": vcomp,
        "visits_cancelled": vis["cancelled"] or 0,
        "completion_rate": round(vcomp * 100.0 / vtotal) if vtotal else 0,
        "collection_rate": round(revenue * 100.0 / invoiced) if invoiced else 0,
        "revenue_per_visit": round(revenue / vcomp, 2) if vcomp else 0,
        "new_clients": db.query("SELECT COUNT(*) c FROM clients WHERE date(created_at) BETWEEN ? AND ?" + nc_extra, (*dr, *nc_p), one=True)["c"],
        "active_contracts": db.query("SELECT COUNT(*) c FROM contracts WHERE status='active'" + ac_extra, ac_p, one=True)["c"],
        "sla_overdue": sla["overdue"], "sla_due_soon": sla["due_soon"],
    }

    # --- fleet & pest rollup from the QR device scans (client/site scoped) ---
    dsc, dsp = scope("client_id", "site_id")                 # devices d (bare cols)
    total_dev = db.query("SELECT COUNT(*) c FROM devices WHERE active=1 AND client_id IS NOT NULL" + dsc, dsp, one=True)["c"]
    isc, isp = scope("client_id", DI_SITE)                   # device_inspections (bare)
    di_by = {r["m"]: r for r in db.query(
        "SELECT strftime('%Y-%m',recorded_at) m, COUNT(*) inspections, "
        "COUNT(DISTINCT device_id) scanned, SUM(status='activity') detections, "
        "AVG(CAST(COALESCE(json_extract(details,'$.fly_count'),"
        "json_extract(details,'$.fly_density')) AS REAL)) fly, "
        "AVG(CAST(json_extract(details,'$.consumption_pct') AS REAL)) bait_pct "
        "FROM device_inspections WHERE date(recorded_at) BETWEEN ? AND ?" + isc + " GROUP BY m", (*dr, *isp))}
    fleet_months = [{"m": k,
                     "inspections": (di_by[k]["inspections"] if k in di_by else 0),
                     "detections": (di_by[k]["detections"] if k in di_by else 0),
                     "coverage": (round(100.0 * di_by[k]["scanned"] / total_dev) if (k in di_by and total_dev) else 0),
                     "fly": r1(di_by[k]["fly"]) if k in di_by else None,
                     "bait_pct": r1(di_by[k]["bait_pct"]) if k in di_by else None} for k in labels]
    scanned_month = db.query("SELECT COUNT(DISTINCT device_id) c FROM device_inspections "
                             "WHERE strftime('%Y-%m',recorded_at)=strftime('%Y-%m','now')" + isc, isp, one=True)["c"]
    tsc, tsp = scope("di.client_id", "(SELECT site_id FROM devices WHERE id=di.device_id)")
    top_clients = db.query(
        "SELECT c.name_en, c.name_ar, COUNT(*) detections FROM device_inspections di "
        "JOIN clients c ON c.id=di.client_id WHERE di.status='activity' "
        "AND date(di.recorded_at) BETWEEN ? AND ?" + tsc + " GROUP BY di.client_id "
        "ORDER BY detections DESC LIMIT 8", (*dr, *tsp))
    rep = db.query(
        "SELECT SUM(json_extract(details,'$.lamp_status')='replaced') lamps, "
        "SUM(json_extract(details,'$.sheet_status')='replaced') sheets, "
        "SUM(json_extract(details,'$.glue_status')='changed') glue_boards, "
        "SUM(json_extract(details,'$.bait_status')='changed') baits "
        "FROM device_inspections WHERE date(recorded_at) BETWEEN ? AND ?" + isc, (*dr, *isp), one=True)
    st_sc, st_sp = scope("d.client_id", "d.site_id")
    fleet = {
        "kpi": {
            "devices": total_dev,
            "needs_service": db.query("SELECT COUNT(*) c FROM devices WHERE active=1 AND status='needs_service'" + dsc, dsp, one=True)["c"],
            "activity": db.query("SELECT COUNT(*) c FROM device_inspections WHERE status='activity' AND date(recorded_at) BETWEEN ? AND ?" + isc, (*dr, *isp), one=True)["c"],
            "coverage": round(100.0 * scanned_month / total_dev) if total_dev else None,
        },
        "months": fleet_months, "top_clients": top_clients,
        "replaced": {"lamps": rep["lamps"] or 0, "sheets": rep["sheets"] or 0,
                     "glue_boards": rep["glue_boards"] or 0, "baits": rep["baits"] or 0},
        "stale": _stale_devices(20, st_sc, st_sp), "stale_count": _stale_count(st_sc, st_sp),
        "stale_days": STALE_SCAN_DAYS,
    }
    return {"range": {"from": d_from, "to": d_to},
            "client_id": cid, "site_id": site_id, "months": months,
            "ar_aging": ar_aging, "agents": agents, "chemicals": chemicals,
            "services": services, "totals": totals, "fleet": fleet}


def _month_labels(n=12):
    import datetime
    today = datetime.date.today().replace(day=1)
    out, y, m = [], today.year, today.month
    for i in range(n - 1, -1, -1):
        mm, yy = m - i, y
        while mm <= 0:
            mm += 12; yy -= 1
        out.append(f"{yy:04d}-{mm:02d}")
    return out


@route("GET", r"/api/clients/(\d+)/analytics")
def client_analytics(ctx):
    """Everything we know about one client, shaped for curve + 3D charts."""
    cid = int(ctx.params[0])
    _assert_client_access(ctx.user, cid)
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "analytics.view")
    site_id = ctx.query.get("site_id")
    vf, vp = _site_filter(site_id, "v.site_id")     # visit-derived queries
    inf, inp = _site_filter(site_id, "site_id")     # bare invoices table
    iif, iip = _site_filter(site_id, "i.site_id")   # invoices via alias i
    labels = _month_labels(12)
    # build continuous 12-month series (fill gaps with zero) for smooth curves
    inv_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',issue_date) m, SUM(total) v FROM invoices "
        "WHERE client_id=? AND doc_type='invoice'" + inf + " GROUP BY m", (cid, *inp))}
    paid_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',p.paid_at) m, SUM(p.amount) v FROM payments p "
        "JOIN invoices i ON i.id=p.invoice_id WHERE i.client_id=?" + iif + " GROUP BY m", (cid, *iip))}
    vis_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',scheduled_start) m, COUNT(*) v FROM visits v "
        "WHERE client_id=?" + vf + " GROUP BY m", (cid, *vp))}
    months = [{"m": k, "invoiced": round(inv_by.get(k, 0) or 0, 2),
               "paid": round(paid_by.get(k, 0) or 0, 2), "visits": vis_by.get(k, 0) or 0}
              for k in labels]
    status = db.query("SELECT status, COUNT(*) cnt FROM visits v WHERE client_id=?" + vf + " GROUP BY status", (cid, *vp))
    services = db.query(
        "SELECT s.name_en, s.name_ar, COUNT(v.id) cnt FROM service_types s "
        "JOIN visits v ON v.service_type_id=s.id WHERE v.client_id=?" + vf + " GROUP BY s.id "
        "HAVING cnt>0 ORDER BY cnt DESC", (cid, *vp))
    severity = db.query(
        "SELECT r.severity, COUNT(*) cnt FROM reports r JOIN visits v ON v.id=r.visit_id "
        "WHERE v.client_id=?" + vf + " AND r.severity IS NOT NULL GROUP BY r.severity", (cid, *vp))
    chemicals = db.query(
        "SELECT ch.name_en, ch.name_ar, ch.unit, SUM(cu.quantity) used FROM chemical_usage cu "
        "JOIN chemicals ch ON ch.id=cu.chemical_id JOIN visits v ON v.id=cu.visit_id "
        "WHERE v.client_id=?" + vf + " GROUP BY ch.id ORDER BY used DESC LIMIT 8", (cid, *vp))
    # Engineer service-log materials consumed across the selected location's visits.
    mat_cols = ("lamps_used", "cables_used", "transformers_used", "light_sheets_used",
                "fipronil_ml", "imidacloprid_gm", "baits_count", "glo_pieces", "flybase_bags")
    mat_sum = db.query(
        "SELECT " + ",".join(f"COALESCE(SUM(r.{c}),0) {c}" for c in mat_cols) +
        " FROM reports r JOIN visits v ON v.id=r.visit_id WHERE v.client_id=?" + vf, (cid, *vp), one=True)
    materials = [{"key": c, "total": round(mat_sum[c] or 0, 2)} for c in mat_cols if (mat_sum[c] or 0) > 0]
    fin = _finance_summary(cid, site_id)
    totals = {
        "visits": db.query("SELECT COUNT(*) c FROM visits v WHERE client_id=?" + vf, (cid, *vp), one=True)["c"],
        "completed": db.query("SELECT COUNT(*) c FROM visits v WHERE client_id=?" + vf + " AND status='completed'", (cid, *vp), one=True)["c"],
        "invoiced": fin["total_invoiced"], "paid": fin["total_paid"], "outstanding": fin["outstanding"],
        "contracts": _active_contracts_for_site(cid, site_id),
    }
    sites = db.query("SELECT id, name FROM sites WHERE client_id=? ORDER BY name", (cid,))
    return {"months": months, "status": status, "services": services,
            "severity": severity, "chemicals": chemicals, "materials": materials,
            "totals": totals, "sites": sites, "site_id": site_id or ""}


@route("GET", r"/api/clients/(\d+)/pest-trends")
def client_pest_trends(ctx):
    """Device-monitoring trends for one client, built from the QR device scans
    (device_inspections + devices). Monthly pest-activity + pest-pressure
    (fly counts, bait consumption), device-type breakdown, hotspots, and
    follow-up KPIs (catch rate, consumables replaced). For audit/HACCP."""
    cid = int(ctx.params[0])
    _assert_client_access(ctx.user, cid)
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "analytics.view")
    site_id = ctx.query.get("site_id")
    # Inspections carry no site_id; scope through the device's location.
    df, dp = _site_filter(site_id, "d.site_id")
    r1 = lambda x: round(x, 1) if x is not None else None
    labels = _month_labels(12)
    DI = "device_inspections di JOIN devices d ON d.id=di.device_id"

    mrows = db.query(
        "SELECT strftime('%Y-%m',di.recorded_at) m, COUNT(*) inspections, "
        "SUM(CASE WHEN di.status='activity' THEN 1 ELSE 0 END) detections, "
        "AVG(CAST(COALESCE(json_extract(di.details,'$.fly_count'),"
        "json_extract(di.details,'$.fly_density')) AS REAL)) fly, "
        "AVG(CAST(json_extract(di.details,'$.consumption_pct') AS REAL)) bait_pct "
        "FROM " + DI + " WHERE di.client_id=?" + df + " GROUP BY m", (cid, *dp))
    by_m = {r["m"]: r for r in mrows}
    months = [{"m": k,
               "inspections": (by_m[k]["inspections"] if k in by_m else 0),
               "detections": (by_m[k]["detections"] if k in by_m else 0),
               "fly": r1(by_m[k]["fly"]) if k in by_m else None,
               "bait_pct": r1(by_m[k]["bait_pct"]) if k in by_m else None}
              for k in labels]
    by_type = db.query(
        "SELECT d.type, COUNT(*) detections FROM " + DI +
        " WHERE di.client_id=? AND di.status='activity'" + df +
        " GROUP BY d.type ORDER BY detections DESC", (cid, *dp))
    # Hotspots keyed by device (code + friendly location) rather than map marker.
    hotspots = db.query(
        "SELECT d.id, d.code label, d.label loc, d.type, d.status, "
        "COUNT(di.id) detections, MAX(di.recorded_at) last_seen "
        "FROM " + DI + " WHERE di.client_id=? AND di.status='activity'" + df +
        " GROUP BY di.device_id ORDER BY detections DESC, last_seen DESC LIMIT 8", (cid, *dp))

    # Follow-up KPIs from the scan details JSON (over the scoped inspections).
    k = db.query(
        "SELECT AVG(CAST(COALESCE(json_extract(di.details,'$.fly_count'),"
        "json_extract(di.details,'$.fly_density')) AS REAL)) fly_avg, "
        "AVG(CAST(json_extract(di.details,'$.consumption_pct') AS REAL)) bait_pct_avg, "
        "SUM(json_extract(di.details,'$.caught')='yes') caught, "
        "SUM(json_extract(di.details,'$.caught') IS NOT NULL) glue_checks, "
        "SUM(json_extract(di.details,'$.lamp_status')='replaced') lamps, "
        "SUM(json_extract(di.details,'$.sheet_status')='replaced') sheets, "
        "SUM(json_extract(di.details,'$.glue_status')='changed') glue_boards, "
        "SUM(json_extract(di.details,'$.bait_status')='changed') baits "
        "FROM " + DI + " WHERE di.client_id=?" + df, (cid, *dp), one=True)
    glue_checks = k["glue_checks"] or 0
    kpis = {
        "fly_avg": r1(k["fly_avg"]),
        "bait_pct_avg": r1(k["bait_pct_avg"]),
        "catch_rate": round(100.0 * (k["caught"] or 0) / glue_checks) if glue_checks else None,
        "replaced": {"lamps": k["lamps"] or 0, "sheets": k["sheets"] or 0,
                     "glue_boards": k["glue_boards"] or 0, "baits": k["baits"] or 0},
    }

    def dev_count(extra=""):
        return db.query("SELECT COUNT(*) c FROM devices d WHERE d.client_id=? AND d.active=1"
                        + df + extra, (cid, *dp), one=True)["c"]
    totals = {
        "inspections": db.query("SELECT COUNT(*) c FROM " + DI + " WHERE di.client_id=?" + df, (cid, *dp), one=True)["c"],
        "detections": db.query("SELECT COUNT(*) c FROM " + DI + " WHERE di.client_id=? AND di.status='activity'" + df, (cid, *dp), one=True)["c"],
        "devices": dev_count(),
        "active_now": dev_count(" AND d.status='activity'"),
        "needs_service": dev_count(" AND d.status='needs_service'"),
        "missing": dev_count(" AND d.status='missing'"),
    }
    last = db.query("SELECT MAX(di.recorded_at) v FROM " + DI + " WHERE di.client_id=?" + df, (cid, *dp), one=True)
    return {"months": months, "by_type": by_type, "hotspots": hotspots,
            "totals": totals, "kpis": kpis, "last_inspection": last["v"] if last else None}


def _months_between(d_from, d_to, cap=24):
    """Continuous 'YYYY-MM' buckets spanning [d_from, d_to], capped to the most
    recent `cap` months so an open-ended range can't produce a huge series."""
    import datetime
    a = datetime.date.fromisoformat(d_from).replace(day=1)
    b = datetime.date.fromisoformat(d_to).replace(day=1)
    out, y, m = [], a.year, a.month
    while (y, m) <= (b.year, b.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1; y += 1
    return out[-cap:]


@route("GET", r"/api/clients/(\d+)/audit-pack")
def client_audit_pack(ctx):
    """One aggregated payload for the auditor "Audit Pack" binder: per-site
    service history, device/pest-activity trends, the chemical usage log (with
    application rates + SDS/label attachments), technician licence/cert numbers,
    and corrective actions derived from report findings + flagged devices. The
    frontend renders this into a single branded printable PDF."""
    cid = int(ctx.params[0])
    _assert_client_access(ctx.user, cid)
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "analytics.view")
    site_id = ctx.query.get("site_id")
    import datetime
    today = datetime.date.today()

    def _d(s, default):
        try:
            return datetime.date.fromisoformat((s or "")[:10]).isoformat()
        except (ValueError, TypeError):
            return default
    try:
        year_ago = today.replace(year=today.year - 1).isoformat()
    except ValueError:           # Feb 29 guard
        year_ago = today.replace(month=today.month, day=28, year=today.year - 1).isoformat()
    d_from = _d(ctx.query.get("from"), year_ago)
    d_to = _d(ctx.query.get("to"), today.isoformat())
    vf, vp = _site_filter(site_id, "v.site_id")
    dr = " AND date(v.scheduled_start) BETWEEN ? AND ?"
    drp = (d_from, d_to)

    client = db.query(
        "SELECT id,name_en,name_ar,contact_person,phone,email,address_en,address_ar,city "
        "FROM clients WHERE id=?", (cid,), one=True)
    if not client:
        raise ApiError(404, "Client not found")
    site = None
    if site_id and site_id not in ("none", "0", ""):
        site = db.query("SELECT id,name,address,area FROM sites WHERE id=? AND client_id=?",
                        (site_id, cid), one=True)

    # --- service history (per site): each visit + its report summary -----------
    history = db.query(
        "SELECT v.id, v.scheduled_start, v.status, v.visit_number, "
        "s.name site_name, st.name_en svc_en, st.name_ar svc_ar, u.full_name agent, "
        "r.severity, r.summary, r.findings, r.recommendations, r.pests_found, "
        "r.branch_issue, r.status report_status, "
        "r.customer_signature, r.technician_signature "
        "FROM visits v LEFT JOIN sites s ON s.id=v.site_id "
        "LEFT JOIN service_types st ON st.id=v.service_type_id "
        "LEFT JOIN users u ON u.id=v.agent_id "
        "LEFT JOIN reports r ON r.visit_id=v.id "
        "WHERE v.client_id=?" + vf + dr + " ORDER BY v.scheduled_start DESC",
        (cid, *vp, *drp))

    # --- chemical usage log (application rate = quantity / area_treated) -------
    chem_log = db.query(
        "SELECT v.scheduled_start, s.name site_name, u.full_name agent, "
        "ch.name_en, ch.name_ar, ch.active_ingredient, ch.reg_no, ch.hazard_class, "
        "cu.quantity, ch.unit, cu.area_treated "
        "FROM chemical_usage cu JOIN visits v ON v.id=cu.visit_id "
        "JOIN chemicals ch ON ch.id=cu.chemical_id "
        "LEFT JOIN sites s ON s.id=v.site_id LEFT JOIN users u ON u.id=v.agent_id "
        "WHERE v.client_id=?" + vf + dr + " ORDER BY v.scheduled_start DESC",
        (cid, *vp, *drp))

    # distinct products used + their SDS / label attachments (chemical photos)
    products = db.query(
        "SELECT DISTINCT ch.id, ch.name_en, ch.name_ar, ch.active_ingredient, "
        "ch.reg_no, ch.hazard_class, ch.unit "
        "FROM chemical_usage cu JOIN chemicals ch ON ch.id=cu.chemical_id "
        "JOIN visits v ON v.id=cu.visit_id "
        "WHERE v.client_id=?" + vf + dr + " ORDER BY ch.name_en", (cid, *vp, *drp))
    for p in products:
        p["attachments"] = db.query(
            "SELECT filename, original_name, caption FROM photos "
            "WHERE entity_type='chemical' AND entity_id=? ORDER BY uploaded_at", (p["id"],))

    # --- technicians who serviced + their licence / certification -------------
    technicians = db.query(
        "SELECT u.id, u.full_name, u.specialization, u.license_no, u.license_expiry, "
        "COUNT(v.id) visits, MAX(v.scheduled_start) last_visit "
        "FROM visits v JOIN users u ON u.id=v.agent_id "
        "WHERE v.client_id=?" + vf + dr + " GROUP BY u.id ORDER BY u.full_name",
        (cid, *vp, *drp))

    # --- corrective actions, derived from report findings ---------------------
    corrective = db.query(
        "SELECT v.scheduled_start, s.name site_name, u.full_name agent, "
        "r.severity, r.findings, r.pests_found, r.recommendations, r.branch_issue, "
        "v.status visit_status "
        "FROM reports r JOIN visits v ON v.id=r.visit_id "
        "LEFT JOIN sites s ON s.id=v.site_id LEFT JOIN users u ON u.id=v.agent_id "
        "WHERE v.client_id=?" + vf + dr +
        " AND (COALESCE(r.recommendations,'')<>'' OR COALESCE(r.branch_issue,'')<>'' "
        "      OR r.severity IN ('high','critical')) "
        "ORDER BY v.scheduled_start DESC", (cid, *vp, *drp))

    # QR devices currently flagged (needs service / live activity) — open actions
    daf, dap = _site_filter(site_id, "d.site_id")
    device_alerts = db.query(
        "SELECT d.code label, d.label loc, d.type, d.status, "
        "(SELECT MAX(recorded_at) FROM device_inspections di WHERE di.device_id=d.id) last_seen "
        "FROM devices d WHERE d.client_id=? AND d.active=1" + daf +
        " AND d.status IN ('needs_service','activity') ORDER BY d.status DESC", (cid, *dap))

    # --- pest-activity trend over the range (from QR device scans) ------------
    months = _months_between(d_from, d_to)
    ef, ep = _site_filter(site_id, "d.site_id")
    DIA = "device_inspections di JOIN devices d ON d.id=di.device_id"
    md = " AND date(di.recorded_at) BETWEEN ? AND ?"
    insp_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',di.recorded_at) m, COUNT(*) v FROM " + DIA +
        " WHERE di.client_id=?" + ef + md + " GROUP BY m", (cid, *ep, d_from, d_to))}
    act_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',di.recorded_at) m, COUNT(*) v FROM " + DIA +
        " WHERE di.client_id=? AND di.status='activity'" + ef + md + " GROUP BY m",
        (cid, *ep, d_from, d_to))}
    trend_months = [{"m": k, "inspections": insp_by.get(k, 0) or 0,
                     "detections": act_by.get(k, 0) or 0} for k in months]
    by_type = db.query(
        "SELECT d.type, COUNT(*) detections FROM " + DIA +
        " WHERE di.client_id=? AND di.status='activity'" + ef + md +
        " GROUP BY d.type ORDER BY detections DESC", (cid, *ep, d_from, d_to))

    completed = sum(1 for h in history if h["status"] == "completed")
    signed = sum(1 for h in history if h["customer_signature"] and h["technician_signature"])
    summary = {
        "visits": len(history), "completed": completed, "signed": signed,
        "products": len(products), "chem_records": len(chem_log),
        "technicians": len(technicians), "corrective": len(corrective),
        "detections": sum(m["detections"] for m in trend_months),
        "open_devices": len(device_alerts),
    }
    return {
        "client": client, "site": site,
        "range": {"from": d_from, "to": d_to},
        "summary": summary,
        "history": history, "chem_log": chem_log, "products": products,
        "technicians": technicians, "corrective": corrective,
        "device_alerts": device_alerts,
        "trend": {"months": trend_months, "by_type": by_type},
    }


# --------------------------------------------------------------------------
# E-SIGNATURE (capture on report)
# --------------------------------------------------------------------------
@route("POST", r"/api/visits/(\d+)/signature")
def save_signature(ctx):
    vid = int(ctx.params[0])
    v = db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    u = ctx.user
    require_perm(u, "visits.edit")
    if u["role"] == "agent" and v["agent_id"] != u["id"]:
        raise ApiError(403, "Not your visit")
    b = ctx.body
    which = b.get("which")  # 'customer' | 'technician'
    data_url = b.get("data", "")
    if which not in ("customer", "technician") or "," not in data_url:
        raise ApiError(400, "which and data (data URL) required")
    import base64
    raw = base64.b64decode(data_url.split(",", 1)[1])
    fname = f"sig_{which}_{uuid.uuid4().hex}.png"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as out:
        out.write(raw)
    # ensure a report row exists
    rep = db.query("SELECT * FROM reports WHERE visit_id=?", (vid,), one=True)
    if not rep:
        db.execute("INSERT INTO reports(visit_id) VALUES(?)", (vid,))
    col = "customer_signature" if which == "customer" else "technician_signature"
    db.execute(f"UPDATE reports SET {col}=? WHERE visit_id=?", (fname, vid))
    if which == "customer" and b.get("customer_name"):
        db.execute("UPDATE reports SET customer_name=? WHERE visit_id=?", (b["customer_name"], vid))
    return db.query("SELECT * FROM reports WHERE visit_id=?", (vid,), one=True)


# --------------------------------------------------------------------------
# CSV EXPORT
# --------------------------------------------------------------------------
@route("GET", r"/api/export/(clients|visits|invoices|chemicals|payments)\.csv")
def export_csv(ctx):
    entity = ctx.params[0]
    u = ctx.user
    # Clients may only export their own visits & invoices; staff need the perm.
    if u["role"] == "client":
        if entity not in ("visits", "invoices"):
            raise ApiError(403, "You do not have permission for this action")
    else:
        require_perm(u, entity + ".view")
    queries = {
        "clients": "SELECT id,name_en,name_ar,contact_person,phone,email,city,status,created_at FROM clients",
        "visits": "SELECT v.id,c.name_en client,v.scheduled_start,v.status,u.full_name agent "
                  "FROM visits v JOIN clients c ON c.id=v.client_id LEFT JOIN users u ON u.id=v.agent_id",
        "invoices": "SELECT i.number,c.name_en client,i.doc_type,i.issue_date,i.due_date,i.total,i.status "
                    "FROM invoices i JOIN clients c ON c.id=i.client_id",
        "chemicals": "SELECT name_en,name_ar,active_ingredient,unit,quantity_in_stock,reorder_level,reg_no FROM chemicals",
        "payments": "SELECT p.id,i.number invoice,p.amount,p.method,p.paid_at FROM payments p "
                    "JOIN invoices i ON i.id=p.invoice_id",
    }
    # Row-scope the export to match what each role may see in the app:
    # clients -> only their own company; agents -> only their own visits.
    where, params = [], []
    if entity == "visits":
        if u["role"] == "client":
            where.append("v.client_id=?"); params.append(u["client_id"])
        elif u["role"] == "agent":
            where.append("v.agent_id=?"); params.append(u["id"])
    elif entity == "invoices" and u["role"] == "client":
        where.append("i.client_id=?"); params.append(u["client_id"])
    elif entity == "payments" and u["role"] == "client":
        where.append("i.client_id=?"); params.append(u["client_id"])
    elif entity == "clients" and u["role"] == "client":
        where.append("id=?"); params.append(u["client_id"])
    sql = queries[entity]
    if where:
        sql += " WHERE " + " AND ".join(where)
    rows = db.query(sql, params)
    return {"_csv": rows, "_filename": entity + ".csv"}


# --------------------------------------------------------------------------
# PHOTOS / UPLOADS
# --------------------------------------------------------------------------
@route("GET", r"/api/photos")
def list_photos(ctx):
    et = ctx.query.get("entity_type")
    eid = ctx.query.get("entity_id")
    if not et or not eid:
        raise ApiError(400, "entity_type and entity_id required")
    try:
        eid = int(eid)
    except (TypeError, ValueError):
        raise ApiError(400, "entity_id must be numeric")
    # Enforce the same per-company / per-agent isolation as the parent record,
    # so users can't enumerate other tenants' photos by id.
    _assert_photo_access(ctx.user, et, eid)
    return db.query("SELECT * FROM photos WHERE entity_type=? AND entity_id=? ORDER BY uploaded_at DESC",
                    (et, eid))


@route("POST", r"/api/photos")
def upload_photo(ctx):
    fields, files = parse_multipart(ctx.raw_body, ctx.content_type)
    if not files:
        raise ApiError(400, "No file uploaded")
    et = fields.get("entity_type")
    eid = fields.get("entity_id")
    if et not in ("client", "report", "visit", "chemical") or not eid:
        raise ApiError(400, "Valid entity_type and entity_id required")
    require_perm(ctx.user, _PHOTO_ENTITY_PERM[et])
    _assert_entity_write_access(ctx.user, et, int(eid))
    f = files[0]
    ext = _validate_upload(f)
    fname = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as out:
        out.write(f["data"])
    bp = 1 if str(fields.get("business_plan", "")).lower() in ("1", "true", "on", "yes") else 0
    pid = db.execute(
        "INSERT INTO photos(entity_type,entity_id,filename,original_name,caption,is_business_plan,uploaded_by) "
        "VALUES(?,?,?,?,?,?,?)",
        (et, int(eid), fname, f["filename"], fields.get("caption"), bp, ctx.user["id"]))
    return db.query("SELECT * FROM photos WHERE id=?", (pid,), one=True)


@route("DELETE", r"/api/photos/(\d+)")
def delete_photo(ctx):
    pid = int(ctx.params[0])
    row = db.query("SELECT * FROM photos WHERE id=?", (pid,), one=True)
    if row:
        require_perm(ctx.user, _PHOTO_ENTITY_PERM.get(row["entity_type"], "clients.edit"))
        _assert_entity_write_access(ctx.user, row["entity_type"], row["entity_id"])
        try:
            os.remove(os.path.join(UPLOAD_DIR, row["filename"]))
        except OSError:
            pass
        db.execute("DELETE FROM photos WHERE id=?", (pid,))
    return {"ok": True}


# --------------------------------------------------------------------------
# SITE MAPS + DEVICE MARKERS (traps, bait stations, monitors …)
# --------------------------------------------------------------------------
@route("GET", r"/api/maps")
def list_all_maps(ctx):
    """Every map the user may see (staff: all; client: own), with client/site
    names and device counts. Powers the central Devices / QR-labels page."""
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "maps.view")
    where, params = "", []
    cid = client_scope_id(ctx.user)
    if cid is not None:
        where, params = " WHERE m.client_id=?", [cid]
    return db.query(
        "SELECT m.*, c.name_en client_en, c.name_ar client_ar, s.name site_name, "
        "(SELECT COUNT(*) FROM map_markers k WHERE k.map_id=m.id) marker_count "
        "FROM maps m JOIN clients c ON c.id=m.client_id "
        "LEFT JOIN sites s ON s.id=m.site_id" + where +
        " ORDER BY c.name_en, m.created_at DESC", params)


@route("GET", r"/api/clients/(\d+)/maps")
def list_maps(ctx):
    cid = int(ctx.params[0])
    _assert_client_access(ctx.user, cid)
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "maps.view")
    return db.query(
        "SELECT m.*, s.name site_name, "
        "(SELECT COUNT(*) FROM map_markers k WHERE k.map_id=m.id) marker_count "
        "FROM maps m LEFT JOIN sites s ON s.id=m.site_id WHERE m.client_id=? "
        "ORDER BY m.created_at DESC", (cid,))


@route("GET", r"/api/maps/(\d+)")
def get_map(ctx):
    mid = int(ctx.params[0])
    m = db.query("SELECT m.*, c.name_en client_en, c.name_ar client_ar, s.name site_name "
                 "FROM maps m JOIN clients c ON c.id=m.client_id "
                 "LEFT JOIN sites s ON s.id=m.site_id WHERE m.id=?", (mid,), one=True)
    if not m:
        raise ApiError(404, "Map not found")
    _assert_client_access(ctx.user, m["client_id"])
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "maps.view")
    m["markers"] = db.query("SELECT * FROM map_markers WHERE map_id=? ORDER BY id", (mid,))
    return m


@route("POST", r"/api/maps")
def upload_map(ctx):
    require_perm(ctx.user, "maps.create")
    fields, files = parse_multipart(ctx.raw_body, ctx.content_type)
    if not files:
        raise ApiError(400, "No map image uploaded")
    cid = fields.get("client_id")
    if not cid:
        raise ApiError(400, "client_id required")
    f = files[0]
    ext = _validate_image_upload(f)
    fname = f"map_{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as out:
        out.write(f["data"])
    mid = db.execute(
        "INSERT INTO maps(client_id,site_id,name,filename,uploaded_by) VALUES(?,?,?,?,?)",
        (int(cid), fields.get("site_id") or None, fields.get("name") or "Site map",
         fname, ctx.user["id"]))
    return db.query("SELECT * FROM maps WHERE id=?", (mid,), one=True)


@route("DELETE", r"/api/maps/(\d+)")
def delete_map(ctx):
    require_perm(ctx.user, "maps.delete")
    mid = int(ctx.params[0])
    row = db.query("SELECT * FROM maps WHERE id=?", (mid,), one=True)
    if row:
        try:
            os.remove(os.path.join(UPLOAD_DIR, row["filename"]))
        except OSError:
            pass
        db.execute("DELETE FROM maps WHERE id=?", (mid,))
    return {"ok": True}


def _log_marker_event(marker_id, map_id, status, note, user_id,
                      source="manual", lat=None, lng=None):
    """Append a monitoring record for a device (feeds pest-trend analytics).
    source='scan' marks a record made via the QR tap-to-inspect flow; lat/lng
    geo-stamp where the technician was standing when they scanned."""
    client_id = db.query("SELECT client_id FROM maps WHERE id=?", (map_id,), one=True)
    mk = db.query("SELECT type FROM map_markers WHERE id=?", (marker_id,), one=True)
    db.execute(
        "INSERT INTO marker_events(marker_id,map_id,client_id,type,status,note,"
        "source,lat,lng,recorded_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
        (marker_id, map_id, client_id["client_id"] if client_id else None,
         mk["type"] if mk else None, status, note, source, lat, lng, user_id))


@route("POST", r"/api/maps/(\d+)/markers")
def add_marker(ctx):
    require_perm(ctx.user, "maps.create")
    mid = int(ctx.params[0])
    if not db.query("SELECT id FROM maps WHERE id=?", (mid,), one=True):
        raise ApiError(404, "Map not found")
    b = ctx.body
    status = b.get("status", "ok")
    nid = db.execute(
        "INSERT INTO map_markers(map_id,type,label,x,y,status,notes,qr_token) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (mid, b.get("type", "other"), b.get("label"), float(b.get("x", 0)),
         float(b.get("y", 0)), status, b.get("notes"), uuid.uuid4().hex))
    _log_marker_event(nid, mid, status, b.get("notes") or "Device installed", ctx.user["id"])
    return db.query("SELECT * FROM map_markers WHERE id=?", (nid,), one=True)


@route("PUT", r"/api/markers/(\d+)")
def update_marker(ctx):
    require_perm(ctx.user, "maps.edit")
    nid = int(ctx.params[0])
    b = ctx.body
    existing = db.query("SELECT * FROM map_markers WHERE id=?", (nid,), one=True)
    if not existing:
        raise ApiError(404, "Marker not found")
    cols = ("type", "label", "x", "y", "status", "notes")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(nid)
    db.execute(f"UPDATE map_markers SET {','.join(fields)} WHERE id=?", vals)
    # Record an inspection event when status is (re)set — even if unchanged,
    # an agent re-confirming a device is a valid monitoring record.
    if "status" in b:
        _log_marker_event(nid, existing["map_id"], b["status"],
                          b.get("notes", existing.get("notes")), ctx.user["id"])
    return db.query("SELECT * FROM map_markers WHERE id=?", (nid,), one=True)


@route("DELETE", r"/api/markers/(\d+)")
def delete_marker(ctx):
    require_perm(ctx.user, "maps.delete")
    db.execute("DELETE FROM map_markers WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


# --------------------------------------------------------------------------
# QR-CODED DEVICES — scan a station's label to inspect it in two seconds.
# A marker's qr_token is printed as a QR that deep-links to /scan/<token>.
# --------------------------------------------------------------------------
_SCAN_STATUSES = ("ok", "needs_service", "activity", "missing")


def _marker_by_token(token):
    """Resolve a QR token to its device + map/client/site context (or None)."""
    return db.query(
        "SELECT k.*, m.client_id, m.site_id, m.name map_name, m.filename map_filename, "
        "c.name_en client_en, c.name_ar client_ar, s.name site_name "
        "FROM map_markers k JOIN maps m ON m.id=k.map_id "
        "JOIN clients c ON c.id=m.client_id LEFT JOIN sites s ON s.id=m.site_id "
        "WHERE k.qr_token=?", (token,), one=True)


@route("GET", r"/api/scan/([0-9a-fA-F]{32})")
def scan_device(ctx):
    """Landing data for a scanned device: identity + recent inspection trail."""
    mk = _marker_by_token(ctx.params[0])
    if not mk:
        raise ApiError(404, "Unknown device code")
    _assert_client_access(ctx.user, mk["client_id"])
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "maps.view")
    mk["history"] = db.query(
        "SELECT e.status, e.note, e.source, e.lat, e.lng, e.recorded_at, u.full_name recorded_by_name "
        "FROM marker_events e LEFT JOIN users u ON u.id=e.recorded_by "
        "WHERE e.marker_id=? ORDER BY e.recorded_at DESC, e.id DESC LIMIT 20", (mk["id"],))
    return mk


@route("POST", r"/api/scan/([0-9a-fA-F]{32})")
def scan_inspect(ctx):
    """Tap-to-inspect: record one auto time- & geo-stamped inspection event and
    set the device's current status. The fast path a technician uses on site."""
    mk = _marker_by_token(ctx.params[0])
    if not mk:
        raise ApiError(404, "Unknown device code")
    _assert_client_access(ctx.user, mk["client_id"])
    require_perm(ctx.user, "maps.edit")
    b = ctx.body
    status = b.get("status", "ok")
    if status not in _SCAN_STATUSES:
        raise ApiError(400, "Invalid status")

    def _coord(v):
        try:
            f = float(v)
            return f if math.isfinite(f) else None
        except (TypeError, ValueError):
            return None
    lat, lng = _coord(b.get("lat")), _coord(b.get("lng"))
    with db.transaction() as cx:
        cx.execute("UPDATE map_markers SET status=? WHERE id=?", (status, mk["id"]))
        cx.execute(
            "INSERT INTO marker_events(marker_id,map_id,client_id,type,status,note,"
            "source,lat,lng,recorded_by) VALUES(?,?,?,?,?,?,'scan',?,?,?)",
            (mk["id"], mk["map_id"], mk["client_id"], mk["type"], status,
             (b.get("note") or "").strip() or None, lat, lng, ctx.user["id"]))
    audit(ctx, "device.inspect", "marker", mk["id"], f"{mk.get('label') or mk['type']} -> {status}")
    return {"ok": True, "status": status, "marker_id": mk["id"]}


# --------------------------------------------------------------------------
# QR-CODED DEVICES — standalone codes (LIT0001…) printed onto traps. Admin
# generates a batch per type + assigns to a client; agents scan on a visit to
# file each device's report (proof-of-presence). Independent of site maps.
# --------------------------------------------------------------------------
DEVICE_TYPES = {                       # device type -> printed code prefix
    "light_trap":   "LIT",
    "glue_station": "GLU",
    "bait_station": "BAI",
    "fly_trap":     "FLY",
}
_DEV_STATUSES = ("ok", "activity", "needs_service", "missing")

# The per-type follow-up fields captured on a scan (mirrors the printed follow-up
# form). Values are validated only for shape here — the client renders the inputs
# from an equivalent DEVICE_FIELDS map. Keys not listed for a type are dropped.
# Numeric fields are coerced with _finite; everything else is stored as a string.
DEVICE_FIELD_KEYS = {
    "bait_station": {"bait_status", "consumption_pct", "station_condition", "cleaned"},
    "fly_trap":     {"washed", "water_refilled", "trap_condition", "fly_density"},
    "glue_station": {"glue_status", "caught", "station_condition", "cleaned"},
    "light_trap":   {"trap_condition", "electricity", "lamp_status", "sheet_status", "fly_count"},
}
_DEVICE_NUM_FIELDS = {"consumption_pct", "fly_density", "fly_count"}


def _clean_device_details(dtype, raw):
    """Keep only the whitelisted follow-up fields for this device type, coercing
    numeric fields. Returns a JSON string, or None when nothing usable is given."""
    if not isinstance(raw, dict):
        return None
    allowed = DEVICE_FIELD_KEYS.get(dtype, set())
    out = {}
    for k in allowed:
        if k not in raw:
            continue
        v = raw[k]
        if v is None or v == "":
            continue
        if k in _DEVICE_NUM_FIELDS:
            n = _finite(v)
            if n is not None:
                out[k] = n
        else:
            out[k] = str(v)[:80]
    return json.dumps(out) if out else None


def _finite(v):
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _device_by_code(code):
    return db.query(
        "SELECT d.*, c.name_en client_en, c.name_ar client_ar, s.name site_name "
        "FROM devices d LEFT JOIN clients c ON c.id=d.client_id "
        "LEFT JOIN sites s ON s.id=d.site_id WHERE d.code=?", (code.upper(),), one=True)


def _agent_active_visit(user, client_id):
    """The visit a scan attaches to: an in-progress visit at this client,
    preferring one owned by the scanning agent. None if there isn't one."""
    if not client_id:
        return None
    rows = db.query("SELECT id, agent_id FROM visits WHERE client_id=? AND "
                    "status='in_progress' ORDER BY scheduled_start DESC", (client_id,))
    if not rows:
        return None
    own = next((r for r in rows if r["agent_id"] == user["id"]), None)
    return (own or rows[0])["id"]


@route("GET", r"/api/devices")
def list_devices(ctx):
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "maps.view")
    where, params = [], []
    cid = client_scope_id(ctx.user)
    if cid is not None:
        where.append("d.client_id=?"); params.append(cid)
    q = ctx.query
    if q.get("client_id"):
        where.append("d.client_id=?"); params.append(int(q["client_id"]))
    if q.get("type") in DEVICE_TYPES:
        where.append("d.type=?"); params.append(q["type"])
    if q.get("unassigned") == "1":
        where.append("d.client_id IS NULL")
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    return db.query(
        "SELECT d.*, c.name_en client_en, c.name_ar client_ar, s.name site_name, "
        "(SELECT MAX(recorded_at) FROM device_inspections di WHERE di.device_id=d.id) last_seen "
        "FROM devices d LEFT JOIN clients c ON c.id=d.client_id "
        "LEFT JOIN sites s ON s.id=d.site_id" + clause + " ORDER BY d.type, d.code", params)


@route("POST", r"/api/devices/generate")
def generate_devices(ctx):
    """Mint the next N sequential codes for a device type (LIT0001, LIT0002…).
    Numbers continue from the highest existing code and are never reused."""
    require_perm(ctx.user, "maps.create")
    b = ctx.body
    dtype = b.get("type")
    if dtype not in DEVICE_TYPES:
        raise ApiError(400, "Invalid device type")
    try:
        count = int(b.get("count", 0))
    except (TypeError, ValueError):
        count = 0
    if count < 1 or count > 500:
        raise ApiError(400, "Count must be between 1 and 500")
    prefix = DEVICE_TYPES[dtype]
    cid = b.get("client_id") or None
    sid = b.get("site_id") or None
    if cid is not None:
        cid = int(cid); _assert_client_access(ctx.user, cid)
    created = []
    with db.transaction() as cx:
        row = cx.execute(
            "SELECT MAX(CAST(substr(code,?) AS INTEGER)) m FROM devices WHERE type=?",
            (len(prefix) + 1, dtype)).fetchone()
        start = (row["m"] or 0) + 1
        for i in range(start, start + count):
            code = f"{prefix}{i:04d}"
            cx.execute("INSERT INTO devices(code,type,client_id,site_id) VALUES(?,?,?,?)",
                       (code, dtype, cid, sid))
            created.append(code)
    audit(ctx, "device.generate", "device", None,
          f"{count}x {dtype} ({created[0]}..{created[-1]})")
    return {"ok": True, "type": dtype, "codes": created}


@route("POST", r"/api/devices/assign")
def assign_devices(ctx):
    require_perm(ctx.user, "maps.edit")
    b = ctx.body
    ids = b.get("ids") or []
    if not isinstance(ids, list) or not ids:
        raise ApiError(400, "No devices selected")
    if not b.get("client_id"):
        raise ApiError(400, "client_id required")
    cid = int(b["client_id"]); _assert_client_access(ctx.user, cid)
    sid = b.get("site_id") or None
    with db.transaction() as cx:
        for did in ids:
            cx.execute("UPDATE devices SET client_id=?, site_id=? WHERE id=?",
                       (cid, sid, int(did)))
    audit(ctx, "device.assign", "device", None, f"{len(ids)} -> client {cid}")
    return {"ok": True, "count": len(ids)}


@route("PUT", r"/api/devices/(\d+)")
def update_device(ctx):
    require_perm(ctx.user, "maps.edit")
    did = int(ctx.params[0])
    if not db.query("SELECT id FROM devices WHERE id=?", (did,), one=True):
        raise ApiError(404, "Device not found")
    b = ctx.body
    nullable = ("label", "client_id", "site_id")
    fields, vals = [], []
    for c in ("label", "status", "client_id", "site_id", "active"):
        if c in b:
            fields.append(f"{c}=?")
            vals.append((b[c] or None) if c in nullable else b[c])
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(did)
    db.execute(f"UPDATE devices SET {','.join(fields)} WHERE id=?", vals)
    return db.query("SELECT * FROM devices WHERE id=?", (did,), one=True)


@route("DELETE", r"/api/devices/(\d+)")
def delete_device(ctx):
    require_perm(ctx.user, "maps.delete")
    db.execute("DELETE FROM devices WHERE id=?", (int(ctx.params[0]),))
    return {"ok": True}


@route("GET", r"/api/scan/([A-Za-z]{3}\d{4,})")
def scan_device_code(ctx):
    """Landing data when a printed device code is scanned: identity + recent
    inspections + which in-progress visit this scan would file against."""
    d = _device_by_code(ctx.params[0])
    if not d:
        raise ApiError(404, "Unknown device code")
    if d["client_id"]:
        _assert_client_access(ctx.user, d["client_id"])
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "maps.view")
    d["history"] = db.query(
        "SELECT di.status, di.findings, di.note, di.source, di.lat, di.lng, di.recorded_at, "
        "di.visit_id, di.details, u.full_name recorded_by_name FROM device_inspections di "
        "LEFT JOIN users u ON u.id=di.recorded_by WHERE di.device_id=? "
        "ORDER BY di.recorded_at DESC, di.id DESC LIMIT 20", (d["id"],))
    d["active_visit_id"] = (_agent_active_visit(ctx.user, d["client_id"])
                            if ctx.user["role"] != "client" else None)
    return d


@route("POST", r"/api/scan/([A-Za-z]{3}\d{4,})")
def inspect_device_code(ctx):
    """File one device's inspection for a visit (the scan-to-report action)."""
    d = _device_by_code(ctx.params[0])
    if not d:
        raise ApiError(404, "Unknown device code")
    if not d["client_id"]:
        raise ApiError(409, "Device not assigned to a client yet")
    _assert_client_access(ctx.user, d["client_id"])
    require_perm(ctx.user, "maps.edit")
    b = ctx.body
    status = b.get("status", "ok")
    if status not in _DEV_STATUSES:
        raise ApiError(400, "Invalid status")
    vid = b.get("visit_id") or _agent_active_visit(ctx.user, d["client_id"])
    if vid:
        v = db.query("SELECT client_id FROM visits WHERE id=?", (int(vid),), one=True)
        if not v or v["client_id"] != d["client_id"]:
            vid = None                      # ignore a visit that isn't this client's
    lat, lng = _finite(b.get("lat")), _finite(b.get("lng"))
    details = _clean_device_details(d["type"], b.get("details"))
    with db.transaction() as cx:
        cx.execute("UPDATE devices SET status=? WHERE id=?", (status, d["id"]))
        cx.execute(
            "INSERT INTO device_inspections(device_id,visit_id,client_id,status,findings,"
            "note,source,lat,lng,recorded_by,details) VALUES(?,?,?,?,?,?,'scan',?,?,?,?)",
            (d["id"], int(vid) if vid else None, d["client_id"], status,
             (b.get("findings") or "").strip() or None,
             (b.get("note") or "").strip() or None, lat, lng, ctx.user["id"], details))
    audit(ctx, "device.inspect", "device", d["id"], f"{d['code']} -> {status}")
    return {"ok": True, "status": status, "code": d["code"],
            "visit_id": int(vid) if vid else None}


@route("GET", r"/api/visits/(\d+)/devices")
def visit_device_coverage(ctx):
    """Per-visit device coverage: how many of the client's devices were scanned
    on this visit, and which are still pending."""
    vid = int(ctx.params[0])
    v = db.query("SELECT * FROM visits WHERE id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    _assert_visit_access(ctx.user, v)
    conds, cparams = ["d.client_id=?", "d.active=1"], [v["client_id"]]
    if v["site_id"]:
        conds.append("(d.site_id=? OR d.site_id IS NULL)"); cparams.append(v["site_id"])
    devs = db.query(
        "SELECT d.id, d.code, d.type, d.label, d.status, "
        "(SELECT MAX(recorded_at) FROM device_inspections di "
        " WHERE di.device_id=d.id AND di.visit_id=?) scanned_at "
        "FROM devices d WHERE " + " AND ".join(conds) + " ORDER BY d.type, d.code",
        [vid] + cparams)
    scanned = sum(1 for d in devs if d["scanned_at"])
    return {"total": len(devs), "scanned": scanned, "devices": devs}


@route("GET", r"/api/visits/(\d+)/followup")
def visit_followup(ctx):
    """Data for the printable follow-up report: visit/client header + each device
    scanned on this visit, grouped by type, with its latest inspection's fields."""
    vid = int(ctx.params[0])
    v = db.query(
        "SELECT v.id, v.scheduled_start, v.visit_number, v.client_id, v.site_id, "
        "c.name_en client_en, c.name_ar client_ar, u.full_name agent_name, "
        "st.name site_name FROM visits v JOIN clients c ON c.id=v.client_id "
        "LEFT JOIN users u ON u.id=v.agent_id "
        "LEFT JOIN sites st ON st.id=v.site_id WHERE v.id=?", (vid,), one=True)
    if not v:
        raise ApiError(404, "Visit not found")
    require_perm(ctx.user, "visits.view")
    _assert_visit_access(ctx.user, v)
    # Latest inspection per device recorded on THIS visit (proof-of-visit).
    rows = db.query(
        "SELECT d.code, d.type, d.label, di.status, di.findings, di.details, di.recorded_at "
        "FROM device_inspections di JOIN devices d ON d.id=di.device_id "
        "WHERE di.visit_id=? AND di.id=("
        "  SELECT MAX(di2.id) FROM device_inspections di2 "
        "  WHERE di2.device_id=di.device_id AND di2.visit_id=di.visit_id) "
        "ORDER BY d.type, d.code", (vid,))
    groups = {}
    for r in rows:
        try:
            r["details"] = json.loads(r["details"]) if r.get("details") else {}
        except (ValueError, TypeError):
            r["details"] = {}
        groups.setdefault(r["type"], []).append(r)
    return {"visit": v, "groups": groups}


# --------------------------------------------------------------------------
# GLOBAL SEARCH
# --------------------------------------------------------------------------
@route("GET", r"/api/search")
def search(ctx):
    q = ctx.query.get("q", "").strip()
    if not q:
        return {"clients": [], "visits": [], "chemicals": [], "invoices": []}
    like = f"%{q}%"
    u = ctx.user
    res = {"clients": [], "visits": [], "chemicals": [], "invoices": []}
    if u["role"] == "client":
        cid = u["client_id"]
        res["clients"] = db.query(
            "SELECT id,name_en,name_ar,city FROM clients WHERE id=? AND (name_en LIKE ? OR name_ar LIKE ?)",
            (cid, like, like))
        res["visits"] = db.query(
            "SELECT v.id,v.scheduled_start,v.status,c.name_en client_en FROM visits v "
            "JOIN clients c ON c.id=v.client_id WHERE v.client_id=? AND "
            "(v.location LIKE ? OR v.notes LIKE ?)", (cid, like, like))
        res["invoices"] = db.query(
            "SELECT id,number,total,status FROM invoices WHERE client_id=? AND number LIKE ?", (cid, like))
        return res
    res["clients"] = db.query(
        "SELECT id,name_en,name_ar,city FROM clients WHERE name_en LIKE ? OR name_ar LIKE ? "
        "OR city LIKE ? OR contact_person LIKE ? LIMIT 20", (like, like, like, like))
    vsql = ("SELECT v.id,v.scheduled_start,v.status,c.name_en client_en FROM visits v "
            "JOIN clients c ON c.id=v.client_id WHERE (v.location LIKE ? OR v.notes LIKE ? "
            "OR c.name_en LIKE ?)")
    vparams = [like, like, like]
    if u["role"] == "agent":
        vsql += " AND v.agent_id=?"
        vparams.append(u["id"])
    res["visits"] = db.query(vsql + " LIMIT 20", vparams)
    if has_perm(u, "chemicals.view"):
        res["chemicals"] = db.query(
            "SELECT id,name_en,name_ar,quantity_in_stock,unit FROM chemicals "
            "WHERE name_en LIKE ? OR name_ar LIKE ? OR active_ingredient LIKE ? LIMIT 20",
            (like, like, like))
    if has_perm(u, "invoices.view"):
        res["invoices"] = db.query(
            "SELECT i.id,i.number,i.total,i.status,c.name_en client_en FROM invoices i "
            "JOIN clients c ON c.id=i.client_id WHERE i.number LIKE ? OR c.name_en LIKE ? LIMIT 20",
            (like, like))
    return res


# --------------------------------------------------------------------------
# internal helpers
# --------------------------------------------------------------------------
def _assert_client_access(user, client_id):
    if user["role"] == "client" and user["client_id"] != client_id:
        raise ApiError(403, "No permission")


def _assert_visit_access(user, visit):
    if user["role"] == "client" and visit["client_id"] != user["client_id"]:
        raise ApiError(403, "No permission")
    if user["role"] == "agent" and visit["agent_id"] != user["id"]:
        raise ApiError(403, "No permission")


def _assert_entity_write_access(user, entity_type, entity_id):
    """Per-tenant write guard for photo upload/delete: an agent may only touch
    their own visits' (and reports') attachments; clients only their own."""
    if entity_type in ("visit", "report"):
        vid = entity_id
        if entity_type == "report":
            rep = db.query("SELECT visit_id FROM reports WHERE id=?", (entity_id,), one=True)
            if not rep:
                raise ApiError(404, "Not found")
            vid = rep["visit_id"]
        v = db.query("SELECT client_id, agent_id FROM visits WHERE id=?", (vid,), one=True)
        if not v:
            raise ApiError(404, "Not found")
        _assert_visit_access(user, v)
    elif entity_type == "client":
        _assert_client_access(user, entity_id)


def _assert_photo_access(user, entity_type, entity_id):
    """Authorize reading photos/attachments for an entity, enforcing per-company
    (client) and per-agent isolation the same way the parent records do."""
    if entity_type == "client":
        if user["role"] != "client":
            require_perm(user, "clients.view")
        _assert_client_access(user, entity_id)
    elif entity_type in ("visit", "report"):
        if entity_type == "report":
            rep = db.query("SELECT visit_id FROM reports WHERE id=?", (entity_id,), one=True)
            if not rep:
                raise ApiError(404, "Not found")
            entity_id = rep["visit_id"]
        v = db.query("SELECT client_id, agent_id FROM visits WHERE id=?", (entity_id,), one=True)
        if not v:
            raise ApiError(404, "Not found")
        if user["role"] != "client":
            require_perm(user, "visits.view")
        _assert_visit_access(user, v)
    elif entity_type == "chemical":
        # not company data; clients have no chemicals.view so this 403s for them.
        require_perm(user, "chemicals.view")
    else:
        raise ApiError(400, "Invalid entity_type")


def authorize_upload_file(user, filename):
    """Per-file read authorization for /uploads/<filename>. Closes the last
    cross-tenant leak: a logged-in user guessing another tenant's file UUID.
    Returns True if `user` may read this specific file.

    Shared, non-tenant assets (the company logo + chemical SDS/label docs) are
    readable by any authenticated user; everything else is scoped to the owning
    client/visit the same way the API enforces it. Admins/managers see all."""
    if user["role"] in ("admin", "manager"):
        return True
    if filename and filename == get_settings().get("logo"):
        return True  # branding, shown on client-facing certificates/docs
    # attachments tracked in the photos table
    ph = db.query("SELECT entity_type, entity_id FROM photos WHERE filename=?", (filename,), one=True)
    if ph:
        if ph["entity_type"] == "chemical":
            return True  # product safety docs (SDS/labels) — shared catalog
        try:
            _assert_photo_access(user, ph["entity_type"], ph["entity_id"])
            return True
        except ApiError:
            return False
    # uploaded site-map picture -> scope to the owning client
    site = db.query("SELECT client_id FROM sites WHERE map_image=?", (filename,), one=True)
    if site:
        try:
            _assert_client_access(user, site["client_id"]); return True
        except ApiError:
            return False
    # captured e-signatures live on the report -> scope to the visit
    rep = db.query("SELECT visit_id FROM reports WHERE customer_signature=? OR "
                   "technician_signature=?", (filename, filename), one=True)
    if rep:
        v = db.query("SELECT client_id, agent_id FROM visits WHERE id=?", (rep["visit_id"],), one=True)
        if v:
            try:
                _assert_visit_access(user, v); return True
            except ApiError:
                return False
    # Unknown / orphaned file: deny for restricted roles (fail closed).
    return False


def _client_outstanding(cid):
    total = db.query("SELECT COALESCE(SUM(total),0) v FROM invoices WHERE client_id=? "
                     "AND status IN('sent','overdue','paid')", (cid,), one=True)["v"]
    paid = db.query("SELECT COALESCE(SUM(p.amount),0) v FROM payments p "
                    "JOIN invoices i ON i.id=p.invoice_id WHERE i.client_id=?", (cid,), one=True)["v"]
    return round(total - paid, 2)


def _site_filter(site_id, col):
    """Build an AND-clause + params for an optional location filter.

    site_id: None/"" -> all locations (no filter); "none"/"0" -> unassigned
    (col IS NULL); a numeric id -> that single location."""
    if site_id in (None, "", "all"):
        return "", []
    if str(site_id) in ("none", "0"):
        return f" AND {col} IS NULL", []
    try:
        return f" AND {col}=?", [int(site_id)]
    except (TypeError, ValueError):
        return "", []


def _active_contracts_for_site(cid, site_id):
    """Count a client's active contracts, scoped to the selected location.

    Contracts link to a location via contracts.site_id and/or per-site rows in
    contract_sites, so a contract counts for a location if either matches.
    'none'/'0' = contracts with no location at all; None/all = client total."""
    base = ("SELECT COUNT(DISTINCT ct.id) c FROM contracts ct "
            "WHERE ct.client_id=? AND ct.status='active'")
    if site_id in (None, "", "all"):
        return db.query(base, (cid,), one=True)["c"]
    if str(site_id) in ("none", "0"):
        return db.query(base + " AND ct.site_id IS NULL AND NOT EXISTS "
                        "(SELECT 1 FROM contract_sites cs WHERE cs.contract_id=ct.id)",
                        (cid,), one=True)["c"]
    try:
        sid = int(site_id)
    except (TypeError, ValueError):
        return db.query(base, (cid,), one=True)["c"]
    return db.query(base + " AND (ct.site_id=? OR EXISTS "
                    "(SELECT 1 FROM contract_sites cs WHERE cs.contract_id=ct.id AND cs.site_id=?))",
                    (cid, sid, sid), one=True)["c"]


def _finance_summary(cid, site_id=None):
    clause, params = _site_filter(site_id, "i.site_id")
    invoices = db.query(
        "SELECT i.*, COALESCE((SELECT SUM(amount) FROM payments p WHERE p.invoice_id=i.id),0) paid "
        "FROM invoices i WHERE i.client_id=?" + clause + " ORDER BY i.issue_date DESC",
        (cid, *params))
    total = sum(i["total"] for i in invoices)
    paid = sum(i["paid"] for i in invoices)
    return {
        "invoices": invoices,
        "total_invoiced": round(total, 2),
        "total_paid": round(paid, 2),
        "outstanding": round(total - paid, 2),
    }


def _next_invoice_number(doc_type="invoice"):
    # Monotonic counter persisted in settings so a number is NEVER reused — not
    # after deletions (COUNT/MAX both reuse the tail) nor concurrently (the +1
    # UPDATE is atomic under the write lock). Numbers look like INV-00042.
    prefix = "QUO" if doc_type == "quote" else "INV"
    key = f"seq_{doc_type}"
    with db.transaction() as cx:
        row = cx.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        if row is None:
            # Seed once from any existing numbers so we don't restart at 1.
            cur = 0
            for r in cx.execute("SELECT number FROM invoices WHERE doc_type=? AND number LIKE ?",
                                (doc_type, prefix + "-%")).fetchall():
                try:
                    cur = max(cur, int(str(r["number"]).rsplit("-", 1)[1]))
                except (ValueError, IndexError):
                    pass
            cx.execute("INSERT INTO settings(key,value) VALUES(?,?)", (key, str(cur)))
        cx.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key=?", (key,))
        nxt = int(cx.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()["value"])
    return f"{prefix}-{nxt:05d}"


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "PestCRM/1.0"

    def log_message(self, fmt, *args):
        pass  # quiet

    # dispatch -------------------------------------------------------------
    def _handle(self, method):
        # Reset per-request state (handler instances are reused on keep-alive).
        self._auth_cookie_token = None
        parsed = urlparse(self.path)
        path = parsed.path
        # /api/public/* is called cross-origin by the marketing website.
        self._cors = path.startswith("/api/public/")
        # static + uploads
        if method == "GET" and not path.startswith("/api/"):
            return self._serve_static(path)

        length = int(self.headers.get("Content-Length", 0) or 0)
        # Reject oversized bodies before reading them into memory (multipart
        # overhead allowed on top of the per-file image cap).
        if length > MAX_UPLOAD_BYTES + 1024 * 1024:
            return self._json(413, {"error": "Request body too large"})
        raw_body = self.rfile.read(length) if length else b""
        content_type = self.headers.get("Content-Type", "")
        body = {}
        if raw_body and content_type.startswith("application/json"):
            try:
                body = json.loads(raw_body.decode())
            except Exception:
                return self._json(400, {"error": "Invalid JSON body"})

        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        for m, regex, fn, auth_req in ROUTES:
            if m != method:
                continue
            match = regex.match(path)
            if not match:
                continue
            user = None
            if auth_req:
                user = self._current_user()
                if not user:
                    return self._json(401, {"error": "Authentication required"})
            client_ip = self.client_address[0] if self.client_address else None
            ctx = Ctx(user, list(match.groups()), query, body, raw_body, content_type, ip=client_ip)
            try:
                result = fn(ctx)
                # Fresh login issues a token -> seed the /uploads access cookie.
                if isinstance(result, dict) and result.get("token"):
                    self._auth_cookie_token = result["token"]
                if isinstance(result, dict) and "_csv" in result:
                    return self._csv(result["_csv"], result.get("_filename", "export.csv"))
                return self._json(200, result)
            except ApiError as e:
                return self._json(e.status, {"error": e.message})
            except Exception:
                # Log the full traceback server-side; return only a reference id
                # to the client so internals (SQL, paths) are never exposed.
                err_id = uuid.uuid4().hex[:8]
                print(f"[error {err_id}] {method} {path}", file=sys.stderr)
                traceback.print_exc()
                return self._json(500, {"error": "Internal server error", "error_id": err_id})
        return self._json(404, {"error": "Not found"})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_PUT(self):
        self._handle("PUT")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        # CORS preflight — only the public endpoints are callable cross-origin.
        if self.path.split("?")[0].startswith("/api/public/"):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.send_header("Access-Control-Max-Age", "86400")
            self.end_headers()
        else:
            self.send_response(404)
            self.end_headers()

    # helpers --------------------------------------------------------------
    def _user_from_token(self, token):
        """Validate a session token string -> active user row (or None)."""
        payload = auth.verify_token(token or "")
        if not payload:
            return None
        user = db.query("SELECT * FROM users WHERE id=? AND active=1", (payload["uid"],), one=True)
        if not user:
            return None
        # Reject tokens issued before the user's token_version was bumped.
        if (user["token_version"] or 0) != payload.get("tv", 0):
            return None
        return user

    def _current_user(self):
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Bearer "):
            return None
        user = self._user_from_token(hdr[7:])
        if user:
            # Refresh the /uploads access cookie so <img> tags (which can't send
            # the Bearer header) stay authorised for the life of the session.
            self._auth_cookie_token = hdr[7:]
        return user

    def _upload_user(self):
        """Resolve the session behind an /uploads request -> user row or None.
        Accepts a Bearer header (direct fetch) or the scoped pc_upl cookie that
        <img>/<a> tags send automatically."""
        hdr = self.headers.get("Authorization", "")
        if hdr.startswith("Bearer "):
            u = self._user_from_token(hdr[7:])
            if u:
                return u
        raw = self.headers.get("Cookie", "")
        if raw:
            try:
                jar = SimpleCookie()
                jar.load(raw)
                if "pc_upl" in jar:
                    return self._user_from_token(jar["pc_upl"].value)
            except Exception:
                pass
        return None

    def _json(self, status, payload):
        data = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        if getattr(self, "_cors", False):
            self.send_header("Access-Control-Allow-Origin", "*")
        self._send_upload_cookie()
        self.end_headers()
        self.wfile.write(data)

    def _send_upload_cookie(self):
        # Scoped, HttpOnly, expiring access cookie for /uploads/. Sent on login
        # and refreshed on every authenticated API call. (Add `Secure` once the
        # site is served over HTTPS.)
        tok = getattr(self, "_auth_cookie_token", None)
        if tok:
            # Mark Secure when the request arrived over HTTPS (directly or via a
            # TLS-terminating proxy) so the cookie isn't sent in the clear.
            https = (self.headers.get("X-Forwarded-Proto", "").split(",")[0].strip() == "https")
            secure = "; Secure" if https else ""
            self.send_header(
                "Set-Cookie",
                "pc_upl=%s; HttpOnly; SameSite=Lax; Path=/uploads; Max-Age=%d%s"
                % (tok, auth.TOKEN_TTL, secure),
            )

    def _csv(self, rows, filename):
        import csv, io
        buf = io.StringIO()
        if rows:
            w = csv.DictWriter(buf, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        data = ("﻿" + buf.getvalue()).encode("utf-8")  # BOM for Excel/Arabic
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_static(self, path):
        if path == "/" or path == "":
            path = "/index.html"
        if path.startswith("/uploads/"):
            # Uploaded files (photos, signatures, maps, logos, attachments) are
            # private and per-file authorized: a logged-in session AND permission
            # to read that specific file (random UUID filenames are not authz).
            user = self._upload_user()
            if not user:
                return self._json(403, {"error": "Forbidden"})
            rel = path[len("/uploads/"):]
            if not authorize_upload_file(user, os.path.basename(rel)):
                return self._json(403, {"error": "Forbidden"})
            base = UPLOAD_DIR
        else:
            base, rel = STATIC_DIR, path.lstrip("/")
        full = os.path.normpath(os.path.join(base, rel))
        if not full.startswith(base) or not os.path.isfile(full):
            # SPA fallback
            full = os.path.join(STATIC_DIR, "index.html")
        try:
            stat = os.stat(full)
        except OSError:
            return self._json(404, {"error": "Not found"})
        is_upload = path.startswith("/uploads/")
        cache = "public, max-age=86400" if is_upload else "no-cache"
        # Validator from file mtime+size: lets the browser revalidate and get a
        # tiny 304 instead of re-downloading unchanged assets, while app code
        # stays fresh (no-cache forces revalidation every load).
        etag = 'W/"%x-%x"' % (int(stat.st_mtime), stat.st_size)
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.send_header("Cache-Control", cache)
            self.end_headers()
            return
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            return self._json(404, {"error": "Not found"})
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        # Compress text assets (JS/CSS/HTML/JSON/SVG) when the client accepts it.
        encoding = None
        if ("gzip" in self.headers.get("Accept-Encoding", "") and len(data) > 512
                and ctype.split(";")[0] in _COMPRESSIBLE):
            data = gzip.compress(data, 6)
            encoding = "gzip"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        if encoding:
            self.send_header("Content-Encoding", encoding)
            self.send_header("Vary", "Accept-Encoding")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("ETag", etag)
        self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(data)


def main():
    db.init_db()
    # auto-seed on first run
    if db.query("SELECT COUNT(*) c FROM users", one=True)["c"] == 0:
        import seed
        seed.run()
    # generate any due recurring visits + invoices + reminders at startup
    try:
        _generate_due_visits()
        _generate_due_invoices()
        _generate_reminders()
    except Exception as e:
        print("startup generation:", e)
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Pest Control CRM running on http://localhost:{port}")
    print("Default login: admin@pestcrm.com / admin123")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")


if __name__ == "__main__":
    main()
