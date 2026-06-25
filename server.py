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
from urllib.parse import urlparse, parse_qs
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
    {"module": "visits",       "actions": ["view", "create", "edit", "delete"]},
    {"module": "calendar",     "actions": ["view"]},
    {"module": "chemicals",    "actions": ["view", "create", "edit", "delete"]},
    {"module": "issues",       "actions": ["view", "create", "delete"]},
    {"module": "invoices",     "actions": ["view", "create", "edit", "delete"]},
    {"module": "payments",     "actions": ["view", "create", "delete"]},
    {"module": "contracts",    "actions": ["view", "create", "edit", "delete"]},
    {"module": "analytics",    "actions": ["view"]},
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
        "dashboard": True, "clients": True, "visits": True, "calendar": True,
        "chemicals": True, "issues": True, "invoices": True, "payments": True,
        "contracts": True, "analytics": True, "certificates": True, "maps": True,
        "users": True, "settings": True, "permissions": False,
    }),
    "agent": _expand({
        "dashboard": ["view"], "clients": ["view"], "visits": ["view", "edit"],
        "calendar": ["view"], "chemicals": ["view"], "certificates": ["view"],
        "issues": ["view", "create"], "maps": ["view", "create", "edit"],
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
                              "client_id", "specialization", "lang")}


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
    return stats


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
    if db.query("SELECT id FROM users WHERE lower(email)=?", (b["email"].lower(),), one=True):
        raise ApiError(409, "Email already in use")
    uid = db.execute(
        "INSERT INTO users(full_name,email,password_hash,role,phone,client_id,specialization,hire_date,lang) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (b["full_name"], b["email"], auth.hash_password(b["password"]), b["role"],
         b.get("phone"), b.get("client_id"), b.get("specialization"), b.get("hire_date"),
         b.get("lang", "en")))
    audit(ctx, "user.create", "user", uid, f"{b['role']} {b['email']}")
    return _public_user(db.query("SELECT * FROM users WHERE id=?", (uid,), one=True))


@route("PUT", r"/api/users/(\d+)")
def update_user(ctx):
    require_perm(ctx.user, "users.edit")
    uid = int(ctx.params[0])
    b = ctx.body
    fields, vals = [], []
    for col in ("full_name", "phone", "role", "client_id", "specialization", "hire_date", "lang", "active"):
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
    client["finance"] = _finance_summary(cid)
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


@route("POST", r"/api/clients/(\d+)/sites")
def add_site(ctx):
    require_perm(ctx.user, "clients.edit")
    cid = int(ctx.params[0])
    b = ctx.body
    if not b.get("name"):
        raise ApiError(400, "Site name is required")
    sid = db.execute("INSERT INTO sites(client_id,name,address,area) VALUES(?,?,?,?)",
                     (cid, b["name"], b.get("address"), b.get("area")))
    return db.query("SELECT * FROM sites WHERE id=?", (sid,), one=True)


@route("DELETE", r"/api/sites/(\d+)")
def delete_site(ctx):
    require_perm(ctx.user, "clients.edit")
    db.execute("DELETE FROM sites WHERE id=?", (int(ctx.params[0]),))
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
           "s.name_en service_en, s.name_ar service_ar, u.full_name agent_name, "
           "EXISTS(SELECT 1 FROM reports r WHERE r.visit_id=v.id AND r.status='complete') has_report "
           "FROM visits v JOIN clients c ON c.id=v.client_id "
           "LEFT JOIN service_types s ON s.id=v.service_type_id "
           "LEFT JOIN users u ON u.id=v.agent_id")
    if where:
        sql += " WHERE " + " AND ".join(where)
    return _paginate(ctx, sql, params, "ORDER BY v.scheduled_start DESC")


@route("GET", r"/api/visits/(\d+)")
def get_visit(ctx):
    vid = int(ctx.params[0])
    v = db.query(
        "SELECT v.*, c.name_en client_en, c.name_ar client_ar, c.id client_id, "
        "s.name_en service_en, s.name_ar service_ar, u.full_name agent_name, st.name site_name "
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
    return v


@route("POST", r"/api/visits")
def create_visit(ctx):
    require_perm(ctx.user, "visits.create")
    b = ctx.body
    if not b.get("client_id") or not b.get("scheduled_start"):
        raise ApiError(400, "Client and scheduled date are required")
    # If the client has locations defined, a visit must be assigned to one so its
    # report rolls up to the right location.
    if not b.get("site_id"):
        has_sites = db.query("SELECT 1 FROM sites WHERE client_id=? LIMIT 1", (b["client_id"],), one=True)
        if has_sites:
            raise ApiError(400, "Please choose a location for this visit")
    vid = db.execute(
        "INSERT INTO visits(client_id,site_id,agent_id,service_type_id,scheduled_start,"
        "scheduled_end,status,location,notes) VALUES(?,?,?,?,?,?,?,?,?)",
        (b["client_id"], b.get("site_id"), b.get("agent_id"), b.get("service_type_id"),
         b["scheduled_start"], b.get("scheduled_end"), b.get("status", "scheduled"),
         b.get("location"), b.get("notes")))
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
        allowed = {"status", "notes", "completed_at"}
        b = {k: val for k, val in b.items() if k in allowed}
    cols = ("client_id", "site_id", "agent_id", "service_type_id", "scheduled_start",
            "scheduled_end", "status", "location", "notes", "completed_at")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if b.get("status") == "completed" and "completed_at" not in b:
        fields.append("completed_at=datetime('now')")
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(vid)
    db.execute(f"UPDATE visits SET {','.join(fields)} WHERE id=?", vals)
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
                 "next_visit_due", "spare_parts_changed", "branch_issue")
    # Engineer service log — quantities of parts/materials used during the visit.
    num_cols = ("lamps_used", "cables_used", "transformers_used", "light_sheets_used",
                "fipronil_ml", "imidacloprid_gm", "baits_count", "glo_pieces", "flybase_bags")

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
    elif rep["status"] != "complete":
        # keep it a draft (clear any stale completed_at)
        db.execute("UPDATE reports SET status='draft', completed_at=NULL WHERE visit_id=?", (vid,))
    return db.query("SELECT * FROM reports WHERE visit_id=?", (vid,), one=True)


# Fields (and signatures) required before a report can be marked complete.
REPORT_REQUIRED_FIELDS = ("summary", "findings", "pests_found", "severity")


def _report_incomplete_fields(rep):
    """Return the list of required-but-missing field keys for a report row."""
    missing = [f for f in REPORT_REQUIRED_FIELDS if not (rep.get(f) or "").strip()]
    if not rep.get("customer_signature"):
        missing.append("customer_signature")
    if not rep.get("technician_signature"):
        missing.append("technician_signature")
    return missing


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
        if inv["client_id"] != ctx.user["client_id"]:
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
    return db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)


@route("DELETE", r"/api/invoices/(\d+)")
def delete_invoice(ctx):
    require_perm(ctx.user, "invoices.delete")
    iid = int(ctx.params[0])
    db.execute("DELETE FROM invoices WHERE id=?", (iid,))
    audit(ctx, "invoice.delete", "invoice", iid)
    return {"ok": True}


@route("POST", r"/api/invoices/(\d+)/payments")
def add_payment(ctx):
    require_perm(ctx.user, "payments.create")
    iid = int(ctx.params[0])
    b = ctx.body
    amount = float(b.get("amount", 0))
    if amount <= 0:
        raise ApiError(400, "Payment amount must be positive")
    with db.transaction() as cx:
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
    return _finance_summary(cid)


@route("POST", r"/api/invoices/(\d+)/convert")
def convert_quote(ctx):
    """Convert an accepted quote into a draft invoice (copies line items)."""
    require_perm(ctx.user, "invoices.edit")
    qid = int(ctx.params[0])
    q = db.query("SELECT * FROM invoices WHERE id=?", (qid,), one=True)
    if not q or q["doc_type"] != "quote":
        raise ApiError(400, "Not a quote")
    items = db.query("SELECT * FROM invoice_items WHERE invoice_id=?", (qid,))
    with db.transaction() as cx:
        iid = cx.execute(
            "INSERT INTO invoices(client_id,visit_id,contract_id,doc_type,number,issue_date,due_date,"
            "amount,tax,total,status,notes) VALUES(?,?,?,?,?,date('now'),date('now','+15 days'),?,?,?,?,?)",
            (q["client_id"], q["visit_id"], q["contract_id"], "invoice", _next_invoice_number("invoice"),
             q["amount"], q["tax"], q["total"], "draft", q["notes"])).lastrowid
        for it in items:
            cx.execute("INSERT INTO invoice_items(invoice_id,description,quantity,unit_price,amount) "
                       "VALUES(?,?,?,?,?)", (iid, it["description"], it["quantity"], it["unit_price"], it["amount"]))
        cx.execute("UPDATE invoices SET status='accepted' WHERE id=?", (qid,))
    return db.query("SELECT * FROM invoices WHERE id=?", (iid,), one=True)


# --------------------------------------------------------------------------
# SETTINGS (company profile / branding)
# --------------------------------------------------------------------------
DEFAULT_SETTINGS = {
    "company_name_en": "PestCare Pest Control Co.", "company_name_ar": "شركة بيست كير لمكافحة الآفات",
    "address_en": "Riyadh, Saudi Arabia", "address_ar": "الرياض، المملكة العربية السعودية",
    "phone": "+966 11 000 0000", "email": "billing@pestcare.com", "vat_no": "300000000000003",
    "currency": "EGP", "tax_rate": "14", "logo": "",
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


@route("GET", r"/api/settings")
def read_settings(ctx):
    return get_settings()


@route("PUT", r"/api/settings")
def write_settings(ctx):
    require_perm(ctx.user, "settings.edit")
    for k, v in ctx.body.items():
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
    return db.query(sql, params)


@route("POST", r"/api/contracts")
def create_contract(ctx):
    require_perm(ctx.user, "contracts.create")
    b = ctx.body
    if not b.get("client_id") or not b.get("start_date") or not b.get("frequency"):
        raise ApiError(400, "Client, start date and frequency are required")
    cid = db.execute(
        "INSERT INTO contracts(client_id,site_id,service_type_id,agent_id,frequency,start_date,"
        "end_date,next_run_date,price,status,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (b["client_id"], b.get("site_id"), b.get("service_type_id"), b.get("agent_id"),
         b["frequency"], b["start_date"], b.get("end_date"), b["start_date"],
         float(b.get("price", 0)), b.get("status", "active"), b.get("notes")))
    return db.query("SELECT * FROM contracts WHERE id=?", (cid,), one=True)


@route("PUT", r"/api/contracts/(\d+)")
def update_contract(ctx):
    require_perm(ctx.user, "contracts.edit")
    cid = int(ctx.params[0])
    b = ctx.body
    cols = ("site_id", "service_type_id", "agent_id", "frequency", "start_date",
            "end_date", "next_run_date", "price", "status", "notes")
    fields = [f"{c}=?" for c in cols if c in b]
    vals = [b[c] for c in cols if c in b]
    if not fields:
        raise ApiError(400, "Nothing to update")
    vals.append(cid)
    db.execute(f"UPDATE contracts SET {','.join(fields)} WHERE id=?", vals)
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


# --------------------------------------------------------------------------
# NOTIFICATIONS / REMINDERS
# --------------------------------------------------------------------------
@route("GET", r"/api/notifications")
def list_notifications(ctx):
    if ctx.user["role"] in ("admin", "manager", "agent"):
        _maybe_generate_reminders()  # keep the bell live without a manual trigger
    rows = db.query("SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
                    (ctx.user["id"],))
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
REMINDER_MIN_INTERVAL = 300  # seconds


def _maybe_generate_reminders():
    global _reminder_last
    now = time.time()
    with _reminder_lock:
        if now - _reminder_last < REMINDER_MIN_INTERVAL:
            return
        _reminder_last = now
    try:
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
    return created


# --------------------------------------------------------------------------
# ANALYTICS
# --------------------------------------------------------------------------
@route("GET", r"/api/analytics")
def analytics(ctx):
    require_perm(ctx.user, "analytics.view")
    months = db.query(
        "SELECT strftime('%Y-%m', issue_date) m, SUM(total) total, "
        "SUM((SELECT COALESCE(SUM(amount),0) FROM payments p WHERE p.invoice_id=i.id)) paid "
        "FROM invoices i WHERE doc_type='invoice' GROUP BY m ORDER BY m DESC LIMIT 12")
    ar_aging = db.query(
        "SELECT CASE WHEN due_date IS NULL OR date(due_date)>=date('now') THEN 'current' "
        "WHEN date(due_date)>=date('now','-30 days') THEN '1-30' "
        "WHEN date(due_date)>=date('now','-60 days') THEN '31-60' ELSE '60+' END bucket, "
        "SUM(total - COALESCE((SELECT SUM(amount) FROM payments p WHERE p.invoice_id=i.id),0)) due "
        "FROM invoices i WHERE doc_type='invoice' AND status NOT IN('paid','cancelled') GROUP BY bucket")
    agents = db.query(
        "SELECT u.full_name, COUNT(v.id) total, "
        "SUM(CASE WHEN v.status='completed' THEN 1 ELSE 0 END) completed "
        "FROM users u LEFT JOIN visits v ON v.agent_id=u.id WHERE u.role='agent' GROUP BY u.id ORDER BY completed DESC")
    chemicals = db.query(
        "SELECT ch.name_en, ch.name_ar, ch.unit, COALESCE(SUM(cu.quantity),0) used "
        "FROM chemicals ch LEFT JOIN chemical_usage cu ON cu.chemical_id=ch.id "
        "GROUP BY ch.id HAVING used > 0 ORDER BY used DESC LIMIT 10")
    services = db.query(
        "SELECT s.name_en, s.name_ar, COUNT(v.id) cnt FROM service_types s "
        "LEFT JOIN visits v ON v.service_type_id=s.id GROUP BY s.id HAVING cnt>0 ORDER BY cnt DESC")
    totals = {
        "revenue": db.query("SELECT COALESCE(SUM(amount),0) v FROM payments", one=True)["v"],
        "invoiced": db.query("SELECT COALESCE(SUM(total),0) v FROM invoices WHERE doc_type='invoice'", one=True)["v"],
        "visits_completed": db.query("SELECT COUNT(*) c FROM visits WHERE status='completed'", one=True)["c"],
        "active_contracts": db.query("SELECT COUNT(*) c FROM contracts WHERE status='active'", one=True)["c"],
    }
    return {"months": months, "ar_aging": ar_aging, "agents": agents,
            "chemicals": chemicals, "services": services, "totals": totals}


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
        "contracts": db.query("SELECT COUNT(*) c FROM contracts WHERE client_id=? AND status='active'", (cid,), one=True)["c"],
    }
    sites = db.query("SELECT id, name FROM sites WHERE client_id=? ORDER BY name", (cid,))
    return {"months": months, "status": status, "services": services,
            "severity": severity, "chemicals": chemicals, "materials": materials,
            "totals": totals, "sites": sites, "site_id": site_id or ""}


@route("GET", r"/api/clients/(\d+)/pest-trends")
def client_pest_trends(ctx):
    """Device-monitoring trends for one client: monthly pest-activity
    detections, breakdown by device type, and activity hotspots. Used for
    audit/compliance reporting (e.g. HACCP)."""
    cid = int(ctx.params[0])
    _assert_client_access(ctx.user, cid)
    if ctx.user["role"] != "client":
        require_perm(ctx.user, "analytics.view")
    site_id = ctx.query.get("site_id")
    # marker_events carry no site_id; scope via their map's location.
    ef, ep = _site_filter(site_id, "(SELECT site_id FROM maps WHERE id=marker_events.map_id)")
    eef, eep = _site_filter(site_id, "(SELECT site_id FROM maps WHERE id=e.map_id)")
    mf, mp_ = _site_filter(site_id, "m.site_id")
    labels = _month_labels(12)
    insp_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',recorded_at) m, COUNT(*) v FROM marker_events "
        "WHERE client_id=?" + ef + " GROUP BY m", (cid, *ep))}
    act_by = {r["m"]: r["v"] for r in db.query(
        "SELECT strftime('%Y-%m',recorded_at) m, COUNT(*) v FROM marker_events "
        "WHERE client_id=? AND status='activity'" + ef + " GROUP BY m", (cid, *ep))}
    months = [{"m": k, "inspections": insp_by.get(k, 0) or 0,
               "detections": act_by.get(k, 0) or 0} for k in labels]
    by_type = db.query(
        "SELECT type, COUNT(*) detections FROM marker_events "
        "WHERE client_id=? AND status='activity'" + ef + " GROUP BY type ORDER BY detections DESC", (cid, *ep))
    hotspots = db.query(
        "SELECT k.id, k.label, k.type, k.status, mp.name map_name, "
        "COUNT(e.id) detections, MAX(e.recorded_at) last_seen "
        "FROM marker_events e JOIN map_markers k ON k.id=e.marker_id "
        "JOIN maps mp ON mp.id=e.map_id "
        "WHERE e.client_id=? AND e.status='activity'" + eef +
        " GROUP BY e.marker_id ORDER BY detections DESC, last_seen DESC LIMIT 8", (cid, *eep))
    totals = {
        "inspections": db.query(
            "SELECT COUNT(*) c FROM marker_events WHERE client_id=?" + ef, (cid, *ep), one=True)["c"],
        "detections": db.query(
            "SELECT COUNT(*) c FROM marker_events WHERE client_id=? AND status='activity'" + ef,
            (cid, *ep), one=True)["c"],
        "devices": db.query(
            "SELECT COUNT(DISTINCT k.id) c FROM map_markers k JOIN maps m ON m.id=k.map_id "
            "WHERE m.client_id=?" + mf, (cid, *mp_), one=True)["c"],
        "active_now": db.query(
            "SELECT COUNT(*) c FROM map_markers k JOIN maps m ON m.id=k.map_id "
            "WHERE m.client_id=?" + mf + " AND k.status='activity'", (cid, *mp_), one=True)["c"],
    }
    last = db.query(
        "SELECT MAX(recorded_at) v FROM marker_events WHERE client_id=?" + ef, (cid, *ep), one=True)
    return {"months": months, "by_type": by_type, "hotspots": hotspots,
            "totals": totals, "last_inspection": last["v"] if last else None}


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
    f = files[0]
    ext = _validate_upload(f)
    fname = f"{uuid.uuid4().hex}{ext}"
    with open(os.path.join(UPLOAD_DIR, fname), "wb") as out:
        out.write(f["data"])
    pid = db.execute(
        "INSERT INTO photos(entity_type,entity_id,filename,original_name,caption,uploaded_by) "
        "VALUES(?,?,?,?,?,?)",
        (et, int(eid), fname, f["filename"], fields.get("caption"), ctx.user["id"]))
    return db.query("SELECT * FROM photos WHERE id=?", (pid,), one=True)


@route("DELETE", r"/api/photos/(\d+)")
def delete_photo(ctx):
    pid = int(ctx.params[0])
    row = db.query("SELECT * FROM photos WHERE id=?", (pid,), one=True)
    if row:
        require_perm(ctx.user, _PHOTO_ENTITY_PERM.get(row["entity_type"], "clients.edit"))
        try:
            os.remove(os.path.join(UPLOAD_DIR, row["filename"]))
        except OSError:
            pass
        db.execute("DELETE FROM photos WHERE id=?", (pid,))
    return {"ok": True}


# --------------------------------------------------------------------------
# SITE MAPS + DEVICE MARKERS (traps, bait stations, monitors …)
# --------------------------------------------------------------------------
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


def _log_marker_event(marker_id, map_id, status, note, user_id):
    """Append a monitoring record for a device (feeds pest-trend analytics)."""
    client_id = db.query("SELECT client_id FROM maps WHERE id=?", (map_id,), one=True)
    mk = db.query("SELECT type FROM map_markers WHERE id=?", (marker_id,), one=True)
    db.execute(
        "INSERT INTO marker_events(marker_id,map_id,client_id,type,status,note,recorded_by) "
        "VALUES(?,?,?,?,?,?,?)",
        (marker_id, map_id, client_id["client_id"] if client_id else None,
         mk["type"] if mk else None, status, note, user_id))


@route("POST", r"/api/maps/(\d+)/markers")
def add_marker(ctx):
    require_perm(ctx.user, "maps.create")
    mid = int(ctx.params[0])
    if not db.query("SELECT id FROM maps WHERE id=?", (mid,), one=True):
        raise ApiError(404, "Map not found")
    b = ctx.body
    status = b.get("status", "ok")
    nid = db.execute(
        "INSERT INTO map_markers(map_id,type,label,x,y,status,notes) VALUES(?,?,?,?,?,?,?)",
        (mid, b.get("type", "other"), b.get("label"), float(b.get("x", 0)),
         float(b.get("y", 0)), status, b.get("notes")))
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
    prefix = "QUO" if doc_type == "quote" else "INV"
    n = db.query("SELECT COUNT(*) c FROM invoices WHERE doc_type=?", (doc_type,), one=True)["c"] + 1
    return f"{prefix}-{n:05d}"


# --------------------------------------------------------------------------
# HTTP plumbing
# --------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "PestCRM/1.0"

    def log_message(self, fmt, *args):
        pass  # quiet

    # dispatch -------------------------------------------------------------
    def _handle(self, method):
        parsed = urlparse(self.path)
        path = parsed.path
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

    # helpers --------------------------------------------------------------
    def _current_user(self):
        hdr = self.headers.get("Authorization", "")
        if not hdr.startswith("Bearer "):
            return None
        payload = auth.verify_token(hdr[7:])
        if not payload:
            return None
        user = db.query("SELECT * FROM users WHERE id=? AND active=1", (payload["uid"],), one=True)
        if not user:
            return None
        # Reject tokens issued before the user's token_version was bumped.
        if (user["token_version"] or 0) != payload.get("tv", 0):
            return None
        return user

    def _json(self, status, payload):
        data = json.dumps(payload, default=str).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

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
            base, rel = UPLOAD_DIR, path[len("/uploads/"):]
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
    # generate any due recurring visits + reminders at startup
    try:
        _generate_due_visits()
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
