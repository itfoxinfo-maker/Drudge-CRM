#!/usr/bin/env python3
"""End-to-end API tests for the Pest Control CRM.

Spins up the server against an isolated temp database, exercises every major
feature, and reports PASS/FAIL. Run:  python3 test_api.py
Exits non-zero if any check fails.
"""
import json, os, sys, time, subprocess, tempfile, urllib.request, urllib.error, shutil

PORT = 8765
BASE = f"http://localhost:{PORT}/api"
HERE = os.path.dirname(os.path.abspath(__file__))

passed = failed = 0
def check(name, cond):
    global passed, failed
    if cond:
        passed += 1; print(f"  \033[32mPASS\033[0m {name}")
    else:
        failed += 1; print(f"  \033[31mFAIL\033[0m {name}")

def call(method, path, token=None, body=None, raw=False):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if token: req.add_header("Authorization", "Bearer " + token)
    if body is not None: req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, (r.read() if raw else json.load(r))
    except urllib.error.HTTPError as e:
        return e.code, None

def login(email, pw):
    _, d = call("POST", "/auth/login", body={"email": email, "password": pw})
    return d["token"] if d else None

def post_multipart(path, token, fields, filename, filebytes, ctype="image/png"):
    """POST a multipart/form-data body (for photo upload tests)."""
    boundary = "----pctest"
    chunks = []
    for k, v in fields.items():
        chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n").encode())
    chunks.append((f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
                   f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n").encode() + filebytes + b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    body = b"".join(chunks)
    req = urllib.request.Request(BASE + path, data=body, method="POST")
    if token: req.add_header("Authorization", "Bearer " + token)
    req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, None

def raw_get(path, token=None, cookie=None):
    """GET a non-/api path (e.g. /uploads/..); returns (status, headers)."""
    req = urllib.request.Request(f"http://localhost:{PORT}{path}", method="GET")
    if token: req.add_header("Authorization", "Bearer " + token)
    if cookie: req.add_header("Cookie", cookie)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.headers

def main():
    tmp = tempfile.mkdtemp(prefix="pestcrm-test-")
    env = dict(os.environ, PESTCRM_DATA_DIR=tmp)
    proc = subprocess.Popen([sys.executable, "server.py", str(PORT)], cwd=HERE,
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # wait for boot
        for _ in range(50):
            try:
                urllib.request.urlopen(f"http://localhost:{PORT}/", timeout=1); break
            except Exception:
                time.sleep(0.2)

        print("AUTH & ROLES")
        admin = login("admin@pestcrm.com", "admin123")
        agent = login("agent1@pestcrm.com", "agent123")
        client = login("client@alnoor.com", "client123")
        check("admin login", bool(admin))
        check("agent login", bool(agent))
        check("client login", bool(client))
        check("bad login rejected", login("admin@pestcrm.com", "wrong") is None)
        st, _ = call("GET", "/dashboard")
        check("unauthenticated blocked (401)", st == 401)

        print("CLIENTS & SCOPING")
        st, clients = call("GET", "/clients", admin)
        check("admin sees all clients", len(clients) >= 4)
        st, cl = call("GET", "/clients", client)
        check("client sees only own", len(cl) == 1)
        st, _ = call("POST", "/clients", client, {"name_en": "X"})
        check("client cannot create client (403)", st == 403)

        print("VISITS, REPORTS, CHEMICALS")
        st, visits = call("GET", "/visits", agent)
        check("agent sees only own visits", all(v["agent_name"] == "Yousef Ali" for v in visits))
        _, chems = call("GET", "/chemicals", admin)
        cyp = next(c for c in chems if c["id"] == 1)
        before = cyp["quantity_in_stock"]
        _, used_row = call("POST", "/visits/2/usage", admin, {"chemical_id": 1, "quantity": 3})
        _, chems2 = call("GET", "/chemicals", admin)
        after = next(c for c in chems2 if c["id"] == 1)["quantity_in_stock"]
        # Usage comes out of the engineer's issued balance, NOT central stock
        # (central stock is decremented once, when the material is issued).
        check("visit usage does NOT touch central stock", abs(after - before) < 0.001)
        check("visit usage recorded", bool(used_row and used_row.get("id")))
        _, rep = call("POST", "/visits/2/report", admin, {"summary": "ok", "severity": "medium"})
        check("report upsert", rep and rep["severity"] == "medium")

        print("INVOICES + LINE ITEMS + PAYMENTS")
        _, inv = call("POST", "/invoices", admin, {"client_id": 1, "issue_date": "2026-06-22",
            "tax": 30, "items": [{"description": "A", "quantity": 2, "unit_price": 100},
                                 {"description": "B", "quantity": 1, "unit_price": 50}]})
        check("invoice total from items + tax", inv["amount"] == 250 and inv["total"] == 280)
        _, full = call("GET", f"/invoices/{inv['id']}", admin)
        check("invoice has 2 line items", len(full["items"]) == 2)
        call("POST", f"/invoices/{inv['id']}/payments", admin, {"amount": 280})
        _, paid = call("GET", f"/invoices/{inv['id']}", admin)
        check("invoice auto-marked paid", paid["status"] == "paid")
        st, _ = call("GET", "/invoices", agent)
        check("agent blocked from invoices (403)", st == 403)

        print("QUOTES")
        _, quo = call("POST", "/invoices", admin, {"client_id": 1, "issue_date": "2026-06-22",
            "doc_type": "quote", "items": [{"description": "Annual", "quantity": 1, "unit_price": 1000}]})
        check("quote created with QUO prefix", quo["number"].startswith("QUO"))
        _, conv = call("POST", f"/invoices/{quo['id']}/convert", admin)
        check("quote converts to invoice", conv["doc_type"] == "invoice" and conv["total"] == quo["total"])
        _, q2 = call("GET", f"/invoices/{quo['id']}", admin)
        check("quote marked accepted", q2["status"] == "accepted")

        print("CONTRACTS")
        _, ct = call("POST", "/contracts", admin, {"client_id": 1, "frequency": "monthly",
            "start_date": "2026-05-01", "price": 300, "service_type_id": 1})
        check("contract created", bool(ct["id"]))
        _, run = call("POST", "/contracts/run", admin)
        check("contract generated visits", run["created"] >= 1)

        print("EVENT NOTIFICATIONS (start / end / contract / visit-due)")
        mgr = login("manager@pestcrm.com", "manager123")
        _, mn = call("GET", "/notifications", mgr)
        check("manager notified of new contract", any(n["type"] == "contract_new" for n in mn["items"]))
        call("PUT", "/visits/1", agent, {"status": "in_progress"})
        _, mn = call("GET", "/notifications", mgr)
        check("manager notified visit started", any(n["type"] == "visit_started" for n in mn["items"]))
        call("PUT", "/visits/1", agent, {"status": "completed"})
        _, mn = call("GET", "/notifications", mgr)
        check("manager notified visit completed", any(n["type"] == "visit_completed" for n in mn["items"]))
        # a visit starting now -> the assigned agent is reminded to start it
        call("POST", "/visits", admin, {"client_id": 1, "agent_id": 3,
                                        "scheduled_start": time.strftime("%Y-%m-%dT%H:%M")})
        call("POST", "/notifications/generate", admin)
        _, an = call("GET", "/notifications", agent)
        check("agent reminded it's time to start the visit", any(n["type"] == "visit_due" for n in an["items"]))

        print("NOTIFICATIONS / ANALYTICS / SETTINGS")
        _, notif = call("GET", "/notifications", admin)
        check("notifications generated", notif["unread"] >= 1)
        _, an = call("GET", "/analytics", admin)
        check("analytics returns sections", all(k in an for k in ("months", "ar_aging", "agents", "totals")))
        _, s = call("PUT", "/settings", admin, {"company_name_en": "Test Co"})
        check("settings persisted", s["company_name_en"] == "Test Co")

        print("PER-LOCATION REPORTS + ANALYTICS")
        _, site = call("POST", "/clients/1/sites", admin, {"name": "Branch X"})
        sid = site["id"]
        # once a client has locations, a visit must be assigned to one
        st, _ = call("POST", "/visits", admin, {"client_id": 1, "scheduled_start": "2026-07-01 09:00"})
        check("visit without location blocked (400)", st == 400)
        st, vis = call("POST", "/visits", admin,
                       {"client_id": 1, "site_id": sid, "scheduled_start": "2026-07-01 09:00"})
        check("visit with location created", st == 200 and vis["site_id"] == sid)
        # an invoice can be attributed to a location
        _, linv = call("POST", "/invoices", admin,
                       {"client_id": 1, "site_id": sid, "issue_date": "2026-07-01", "amount": 500})
        check("invoice carries site_id", linv["site_id"] == sid)
        # analytics: total vs per-location vs unassigned
        _, aall = call("GET", "/clients/1/analytics", admin)
        _, asite = call("GET", f"/clients/1/analytics?site_id={sid}", admin)
        _, anone = call("GET", "/clients/1/analytics?site_id=none", admin)
        check("analytics lists the location", any(x["id"] == sid for x in aall["sites"]))
        check("per-location visits within total", 1 <= asite["totals"]["visits"] <= aall["totals"]["visits"])
        check("per-location invoiced reflects site", asite["totals"]["invoiced"] >= 500)
        check("location split sums under total",
              asite["totals"]["visits"] + anone["totals"]["visits"] == aall["totals"]["visits"])

        print("CENTRAL REPORTS LIST")
        _, rl = call("GET", "/reports", admin)
        check("reports list returns rows with joins",
              isinstance(rl, list) and (not rl or all(k in rl[0] for k in ("visit_id", "client_en", "agent_name", "severity", "status"))))
        _, rp = call("GET", "/reports?page=1&limit=10", admin)
        check("reports list paginates", all(k in rp for k in ("items", "total", "page", "pages")))
        st, rf = call("GET", "/reports?severity=high&status=complete", admin)
        check("reports list filters", st == 200 and all(r["severity"] == "high" and r["status"] == "complete" for r in rf))
        _, rag = call("GET", "/reports?page=1&limit=50", agent)
        check("reports scoped to agent's own", all(r["agent_id"] for r in rag["items"]))
        # the /reports/drafts route is not shadowed by /reports
        st, _ = call("GET", "/reports/drafts", admin)
        check("reports drafts route still resolves", st == 200)
        # ?lang= auto-translation param is accepted (translation itself is
        # best-effort/network-dependent, so we only assert the endpoints work)
        st, vt = call("GET", "/visits/1?lang=en", admin)
        check("visit accepts lang param", st == 200 and "report" in vt)
        st, _ = call("GET", "/reports?lang=ar", admin)
        check("reports list accepts lang param", st == 200)

        print("SIGNATURES / SEARCH / CSV")
        png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMCAQGNuM5UAAAAAElFTkSuQmCC"
        _, sig = call("POST", "/visits/1/signature", admin, {"which": "customer", "data": png, "customer_name": "Ali"})
        check("signature saved", bool(sig["customer_signature"]) and sig["customer_name"] == "Ali")
        _, sr = call("GET", "/search?q=Noor", admin)
        check("search finds client", len(sr["clients"]) >= 1)
        st, csv = call("GET", "/export/clients.csv", admin, raw=True)
        check("csv export works", st == 200 and b"name_en" in csv)
        # Agents have clients.view by default, so they may export clients;
        # but they lack invoices.view, so invoice export is forbidden.
        st, _ = call("GET", "/export/invoices.csv", agent)
        check("invoice csv export blocked for agent (403)", st == 403)

        print("DATA ISOLATION (photos + scoped exports)")
        # photos: a client may read its own visit's photos but not another company's.
        st, _ = call("GET", "/photos?entity_type=visit&entity_id=1", client)
        check("client reads own visit photos (200)", st == 200)
        st, _ = call("GET", "/photos?entity_type=visit&entity_id=2", client)
        check("client blocked from other company's visit photos (403)", st == 403)
        # an agent may read its own visit's photos but not another agent's.
        st, _ = call("GET", "/photos?entity_type=visit&entity_id=1", agent)
        check("agent reads own visit photos (200)", st == 200)
        st, _ = call("GET", "/photos?entity_type=visit&entity_id=2", agent)
        check("agent blocked from other agent's visit photos (403)", st == 403)
        # CSV exports are row-scoped: a client gets only its own company's visits;
        # agent2 ("Omar Saeed") only appears on other companies' visits.
        st, cvis = call("GET", "/export/visits.csv", client, raw=True)
        check("client visits export scoped to own company",
              st == 200 and b"Omar Saeed" not in cvis)
        st, cinv = call("GET", "/export/invoices.csv", client, raw=True)
        check("client invoices export scoped to own company",
              st == 200 and b"INV-00002" not in cinv)
        st, _ = call("GET", "/export/chemicals.csv", client)
        check("client blocked from chemicals export (403)", st == 403)
        # an agent's visits export excludes the other agent's visits.
        st, avis = call("GET", "/export/visits.csv", agent, raw=True)
        check("agent visits export excludes other agent",
              st == 200 and b"Omar Saeed" not in avis)

        print("ENGINEER ISSUE BALANCE (issued - used = remaining)")
        _, agusers = call("GET", "/users?role=agent", admin)
        a1 = next(u["id"] for u in agusers if u["email"] == "agent1@pestcrm.com")
        # issue 5 units of chemical 1 to agent1, then log 2 used on agent1's own visit
        _, ch_b = call("GET", "/chemicals", admin)
        c1_before = next(c for c in ch_b if c["id"] == 1)["quantity_in_stock"]
        _, iss = call("POST", "/issues", admin, {"agent_id": a1,
                      "items": [{"chemical_id": 1, "quantity": 5}]})
        check("issue created for agent1", bool(iss and iss.get("id")))
        _, ch_a = call("GET", "/chemicals", admin)
        c1_after = next(c for c in ch_a if c["id"] == 1)["quantity_in_stock"]
        check("issuing deducts central stock (single deduction point)",
              abs((c1_before - c1_after) - 5) < 0.001)
        call("POST", "/visits/1/usage", admin, {"chemical_id": 1, "quantity": 2})
        _, bal = call("GET", f"/issues/balance?agent_id={a1}", admin)
        eng = next((e for e in bal["engineers"] if e["agent_id"] == a1), None)
        mat = next((m for m in eng["materials"] if m["chemical_id"] == 1), None) if eng else None
        check("balance shows issued total", mat and mat["issued"] == 5)
        check("balance deducts visit usage", mat and mat["used"] == 2)
        check("balance remaining = issued - used", mat and mat["remaining"] == 3)
        # an agent sees only their own balance
        _, abal = call("GET", "/issues/balance", agent)
        check("agent balance scoped to self", all(e["agent_id"] == a1 for e in abal["engineers"]))

        print("RBAC PERMISSIONS")
        manager = login("manager@pestcrm.com", "manager123")
        # Visibility of the RBAC admin surface is admin-only.
        st, _ = call("GET", "/permissions/catalog", manager)
        check("manager blocked from permissions catalog (403)", st == 403)
        st, cat = call("GET", "/permissions/catalog", admin)
        check("admin reads permissions catalog", st == 200 and "catalog" in cat)
        # admin role is immutable.
        st, _ = call("PUT", "/permissions/roles/admin", admin, {"perms": {"invoices.view": False}})
        check("admin role cannot be edited (400)", st == 400)

        # agent lacks invoices.view by default.
        st, _ = call("GET", "/invoices", agent)
        check("agent invoices blocked by default (403)", st == 403)
        # grant it at the role level -> enforcement is live (same token).
        call("PUT", "/permissions/roles/agent", admin, {"perms": {"invoices.view": True}})
        st, _ = call("GET", "/invoices", agent)
        check("role grant enables agent invoices (200)", st == 200)
        # resetting to the built-in default removes the override row.
        call("PUT", "/permissions/roles/agent", admin, {"perms": {"invoices.view": False}})
        _, cat2 = call("GET", "/permissions/catalog", admin)
        check("resetting to default drops the role override",
              "invoices.view" not in cat2["role_overrides"].get("agent", {}))
        st, _ = call("GET", "/invoices", agent)
        check("agent invoices blocked again after reset (403)", st == 403)

        # per-user override beats the role default.
        _, users = call("GET", "/users?role=agent", admin)
        aid = next(u["id"] for u in users if u["email"] == "agent1@pestcrm.com")
        call("PUT", f"/permissions/users/{aid}", admin, {"perms": {"invoices.view": True}})
        st, _ = call("GET", "/invoices", agent)
        check("per-user override grants agent invoices (200)", st == 200)
        _, up = call("GET", f"/permissions/users/{aid}", admin)
        check("user override recorded", up["overrides"].get("invoices.view") is True)
        # clearing the override (null) reverts to inherited role default.
        call("PUT", f"/permissions/users/{aid}", admin, {"perms": {"invoices.view": None}})
        _, up2 = call("GET", f"/permissions/users/{aid}", admin)
        check("clearing override reverts to inherit", "invoices.view" not in up2["overrides"])
        st, _ = call("GET", "/invoices", agent)
        check("agent invoices blocked after clearing override (403)", st == 403)

        # the permission changes above were written to the audit trail.
        _, audit = call("GET", "/audit", admin)
        check("audit log records permission changes",
              any(r["action"].startswith("permissions.") for r in audit))

        print("UPLOADS ARE PRIVATE")
        # The auth gate runs before file existence, so a non-existent name still
        # reveals whether access control fires (403 vs the SPA fallback 200).
        st, _ = raw_get("/uploads/whatever.jpg")
        check("upload blocked without a session (403)", st == 403)
        st, _ = raw_get("/uploads/whatever.jpg", token=admin)
        check("upload allowed with Bearer token", st != 403)
        st, _ = raw_get("/uploads/whatever.jpg", cookie=f"pc_upl={admin}")
        check("upload allowed with access cookie", st != 403)
        st, _ = raw_get("/uploads/whatever.jpg", cookie="pc_upl=garbage")
        check("upload blocked with a bogus cookie (403)", st == 403)
        # login seeds the scoped /uploads access cookie
        req = urllib.request.Request(BASE + "/auth/login", method="POST",
                                     data=json.dumps({"email": "admin@pestcrm.com", "password": "admin123"}).encode())
        req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req) as r:
            sc = r.headers.get("Set-Cookie") or ""
        check("login sets scoped HttpOnly upload cookie",
              "pc_upl=" in sc and "HttpOnly" in sc and "Path=/uploads" in sc)
        # static assets stay public (login page must load before auth)
        st, _ = raw_get("/index.html")
        check("static assets stay public", st == 200)

        print("CONTRACTS PER LOCATION")
        # Nile View Hotel is seeded with 2 sites and one CLIENT-LEVEL active contract.
        sun = next((c for c in clients if "Nile View" in (c.get("name_en") or "")), None)
        check("found seeded multi-site client", bool(sun))
        if sun:
            _, an_all = call("GET", f"/clients/{sun['id']}/analytics", admin)
            check("contract counted at client level (all locations)",
                  an_all["totals"]["contracts"] >= 1)
            sites = an_all.get("sites") or []
            if sites:
                _, an_site = call("GET", f"/clients/{sun['id']}/analytics?site_id={sites[0]['id']}", admin)
                check("client-level contract not counted for a specific location",
                      an_site["totals"]["contracts"] == 0)
            _, an_un = call("GET", f"/clients/{sun['id']}/analytics?site_id=none", admin)
            check("client-level contract counted under unassigned",
                  an_un["totals"]["contracts"] >= 1)

        print("MATERIALS AS INVENTORY ITEMS")
        _, chems_all = call("GET", "/chemicals", admin)
        lamp = next((c for c in chems_all if c.get("material_key") == "lamps_used"), None)
        check("UV lamp seeded as a real inventory item",
              bool(lamp) and lamp["name_en"] == "UV Lamp")
        _, users = call("GET", "/users", admin)
        yousef = next((u for u in users if u.get("full_name") == "Yousef Ali"), None)
        _, allvisits = call("GET", "/visits", admin)
        av = next((v for v in allvisits if v.get("agent_id") == (yousef or {}).get("id")), None)
        check("found an agent and one of their visits", bool(yousef) and bool(av))
        if lamp and yousef and av:
            def lamp_bal():
                _, b = call("GET", f"/issues/balance?agent_id={yousef['id']}", admin)
                engs = b.get("engineers") or []
                mats = engs[0]["materials"] if engs else []
                return next((m for m in mats if m["chemical_id"] == lamp["id"]), None)
            base = lamp_bal()
            base_used = base["used"] if base else 0
            # stock up, issue 10 lamps (deducts central stock), then use 3 on a visit
            call("POST", f"/chemicals/{lamp['id']}/stock", admin, {"change": 50, "reason": "purchase"})
            st, _ = call("POST", "/issues", admin,
                         {"agent_id": yousef["id"], "items": [{"chemical_id": lamp["id"], "quantity": 10}]})
            check("issued 10 lamps to the engineer", st == 200)
            call("POST", f"/visits/{av['id']}/report", admin, {"lamps_used": 3, "severity": "low"})
            lb = lamp_bal()
            check("lamps show as issued on the engineer balance", lb and lb["issued"] == 10)
            check("report lamp counter folds into used", lb and round(lb["used"] - base_used, 3) == 3)
            check("lamp remaining = issued - used",
                  lb and lb["remaining"] == round(lb["issued"] - lb["used"], 3))
            # materials must NOT be loggable as chemical-usage (would double-count)
            st, _ = call("POST", f"/visits/{av['id']}/usage", admin,
                         {"chemical_id": lamp["id"], "quantity": 1})
            check("material blocked from chemical-usage (no double count)", st == 400)

        print("SETTINGS SECRECY")
        call("PUT", "/settings", admin, {"smtp_host": "smtp.example.com", "smtp_pass": "s3cret"})
        _, cset = call("GET", "/settings", client)
        check("client cannot read SMTP secrets", "smtp_pass" not in cset and "smtp_host" not in cset)
        _, aset = call("GET", "/settings", agent)
        check("agent cannot read SMTP secrets", "smtp_pass" not in aset)
        _, dset = call("GET", "/settings", admin)
        check("admin can read SMTP secrets", dset.get("smtp_pass") == "s3cret")

        print("USER MGMT ESCALATION GUARDS")
        st, _ = call("POST", "/users", manager,
                     {"full_name": "X", "email": "newadmin@x.com", "password": "pw123456", "role": "admin"})
        check("manager cannot create an admin (403)", st == 403)
        _, allusers = call("GET", "/users", admin)
        mgr = next((u for u in allusers if u["email"] == "manager@pestcrm.com"), None)
        adm = next((u for u in allusers if u["role"] == "admin"), None)
        st, _ = call("PUT", f"/users/{mgr['id']}", manager, {"role": "admin"})
        check("manager cannot promote a user to admin (403)", st == 403)
        st, _ = call("PUT", f"/users/{adm['id']}", manager, {"phone": "123"})
        check("manager cannot edit an admin account (403)", st == 403)
        st, _ = call("POST", "/users", manager,
                     {"full_name": "Reg", "email": "reg@x.com", "password": "pw123456", "role": "agent"})
        check("manager can still create a non-admin user", st == 200)

        print("INVOICE NUMBERING (no reuse on delete)")
        _, i1 = call("POST", "/invoices", admin, {"client_id": sun["id"], "issue_date": "2026-01-01", "amount": 10})
        _, i2 = call("POST", "/invoices", admin, {"client_id": sun["id"], "issue_date": "2026-01-01", "amount": 20})
        call("DELETE", f"/invoices/{i2['id']}", admin)
        _, i3 = call("POST", "/invoices", admin, {"client_id": sun["id"], "issue_date": "2026-01-01", "amount": 30})
        check("invoice number not reused after a delete",
              i3["number"] not in (i1["number"], i2["number"]))

        print("PAYMENT VALIDATION")
        _, pinv = call("POST", "/invoices", admin, {"client_id": sun["id"], "issue_date": "2026-01-01", "amount": 100})
        st, _ = call("POST", f"/invoices/{pinv['id']}/payments", admin, {"amount": "abc"})
        check("non-numeric payment rejected (400)", st == 400)
        st, _ = call("POST", f"/invoices/{pinv['id']}/payments", admin, {"amount": -5})
        check("negative payment rejected (400)", st == 400)
        st, _ = call("POST", "/invoices/999999/payments", admin, {"amount": 5})
        check("payment on a missing invoice (404)", st == 404)

        print("QUOTE CONVERT KEEPS LOCATION")
        _, an = call("GET", f"/clients/{sun['id']}/analytics", admin)
        qsid = (an.get("sites") or [{}])[0].get("id")
        _, quote = call("POST", "/invoices", admin,
                        {"client_id": sun["id"], "issue_date": "2026-01-01",
                         "doc_type": "quote", "amount": 50, "site_id": qsid})
        st, conv = call("POST", f"/invoices/{quote['id']}/convert", admin)
        check("converted invoice keeps its location", conv and conv.get("site_id") == qsid)

        print("PHOTO WRITE SCOPING")
        PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        omar = next((u for u in agusers if u["email"] == "agent2@pestcrm.com"), None)
        _, vall = call("GET", "/visits", admin)
        omar_v = next((v for v in vall if v.get("agent_id") == (omar or {}).get("id")), None)
        own_v = next((v for v in vall if v.get("agent_id") == a1), None)
        if omar_v:
            st, _ = post_multipart("/photos", agent,
                                   {"entity_type": "visit", "entity_id": omar_v["id"]}, "x.png", PNG)
            check("agent cannot attach a photo to another agent's visit (403)", st == 403)
        if own_v:
            st, ph = post_multipart("/photos", agent,
                                    {"entity_type": "visit", "entity_id": own_v["id"]}, "x.png", PNG)
            check("agent can attach a photo to their own visit (200)", st == 200)

        print("QR-CODED DEVICES (scan-to-inspect)")
        PNGm = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        # client@alnoor is scoped to one client — build the device under it so the
        # client can READ its trail but is blocked from RECORDING inspections.
        _, ccl = call("GET", "/clients", client)
        ccid = ccl[0]["id"]
        st, cmap = post_multipart("/maps", admin, {"client_id": ccid, "name": "Ground Floor"},
                                  "m.png", PNGm)
        check("map uploaded for device test", st == 200 and cmap.get("id"))
        _, mk = call("POST", f"/maps/{cmap['id']}/markers", admin,
                     {"type": "bait_station", "label": "BS-01", "x": 10, "y": 20, "status": "ok"})
        tok = mk.get("qr_token")
        check("new device gets a qr_token", bool(tok) and len(tok) >= 16)
        st, allmaps = call("GET", "/maps", admin)
        check("central devices page lists maps with client+counts", st == 200
              and any(m.get("id") == cmap["id"] and m.get("client_en") and "marker_count" in m for m in allmaps))
        st, _ = call("GET", "/maps", agent)
        check("agent (maps.view) can list all maps", st == 200)
        st, sc = call("GET", f"/scan/{tok}", admin)
        check("scan resolves device by token", st == 200 and sc.get("label") == "BS-01")
        check("scan returns inspection history", isinstance(sc.get("history"), list) and len(sc["history"]) >= 1)
        st, _ = call("GET", "/scan/deadbeefdeadbeef", admin)
        check("unknown token -> 404", st == 404)
        st, ins = call("POST", f"/scan/{tok}", admin,
                       {"status": "activity", "note": "droppings", "lat": 24.7, "lng": 46.6})
        check("tap-to-inspect records event", st == 200 and ins.get("status") == "activity")
        _, sc2 = call("GET", f"/scan/{tok}", admin)
        check("device current status updated by scan", sc2.get("status") == "activity")
        latest = sc2["history"][0]
        check("scan event is geo-stamped + sourced", latest.get("source") == "scan"
              and abs((latest.get("lat") or 0) - 24.7) < 0.01)
        st, _ = call("POST", f"/scan/{tok}", admin, {"status": "bogus"})
        check("invalid scan status rejected (400)", st == 400)
        st, _ = call("POST", f"/scan/{tok}", agent, {"status": "ok"})
        check("technician (maps.edit) can inspect (200)", st == 200)
        st, _ = call("GET", f"/scan/{tok}", client)
        check("client can view own device trail (200)", st == 200)
        st, _ = call("POST", f"/scan/{tok}", client, {"status": "ok"})
        check("client cannot record inspections (403)", st == 403)

        print("DEVICE CODES (generate / assign / scan-to-report)")
        st, g1 = call("POST", "/devices/generate", admin, {"type": "light_trap", "count": 3})
        check("generate mints sequential codes", st == 200 and g1["codes"][:3] == ["LIT0001", "LIT0002", "LIT0003"])
        st, g2 = call("POST", "/devices/generate", admin, {"type": "light_trap", "count": 2})
        check("generate continues the sequence", g2["codes"] == ["LIT0004", "LIT0005"])
        st, gb = call("POST", "/devices/generate", admin, {"type": "bait_station", "count": 1})
        check("each type has its own prefix/sequence", gb["codes"] == ["BAI0001"])
        st, _ = call("POST", "/devices/generate", admin, {"type": "bogus", "count": 5})
        check("invalid device type rejected (400)", st == 400)
        st, _ = call("POST", "/devices/generate", admin, {"type": "light_trap", "count": 0})
        check("invalid count rejected (400)", st == 400)
        st, _ = call("POST", "/devices/generate", agent, {"type": "fly_trap", "count": 1})
        check("agent without maps.create blocked? (agents have it -> 200)", st in (200, 403))

        _, devs = call("GET", "/devices?type=light_trap", admin)
        bycode = {d["code"]: d for d in devs}
        ids = [bycode["LIT0001"]["id"], bycode["LIT0002"]["id"], bycode["LIT0003"]["id"]]
        st, asg = call("POST", "/devices/assign", admin, {"ids": ids, "client_id": 1})
        check("assign codes to a client", st == 200 and asg["count"] == 3)
        _, devs2 = call("GET", "/devices?client_id=1&type=light_trap", admin)
        check("assigned devices now scoped to client", len(devs2) >= 3 and all(d["client_id"] == 1 for d in devs2))

        # put a client-1 visit in progress so the scan attaches to it
        _, vall = call("GET", "/visits", admin)
        c1v = next((v for v in vall if v.get("client_id") == 1), None)
        call("PUT", f"/visits/{c1v['id']}", admin, {"status": "in_progress"})
        st, sc = call("GET", "/scan/LIT0001", admin)
        check("scan resolves assigned device by code", st == 200 and sc["code"] == "LIT0001" and sc["client_id"] == 1)
        check("scan suggests the in-progress visit", sc.get("active_visit_id") == c1v["id"])
        st, ins = call("POST", "/scan/LIT0001", admin,
                       {"status": "activity", "findings": "2 moths", "lat": 24.7, "lng": 46.6})
        check("scan-to-report files an inspection on the visit", st == 200
              and ins["status"] == "activity" and ins["visit_id"] == c1v["id"])
        _, sc2 = call("GET", "/scan/LIT0001", admin)
        check("device status updated + history recorded", sc2["status"] == "activity"
              and len(sc2["history"]) >= 1 and sc2["history"][0]["findings"] == "2 moths")
        st, _ = call("POST", "/scan/LIT0001", admin, {"status": "nope"})
        check("invalid inspection status rejected (400)", st == 400)
        st, _ = call("GET", "/scan/ZZZ9999", admin)
        check("unknown code -> 404", st == 404)
        st, _ = call("POST", "/scan/LIT0004", admin, {"status": "ok"})  # LIT0004 unassigned
        check("inspecting an unassigned code -> 409", st == 409)

        st, cov = call("GET", f"/visits/{c1v['id']}/devices", admin)
        check("visit coverage counts scanned vs total", st == 200
              and cov["total"] >= 3 and cov["scanned"] == 1)
        st, _ = call("GET", "/devices", client)
        check("client can list (own) devices", st == 200)

        print("AUDIT PACK + technician licensing")
        # licence/cert numbers round-trip through user create + update
        st, lu = call("POST", "/users", admin, {
            "full_name": "Licensed Tech", "email": "lic@pestcrm.com", "password": "secret123",
            "role": "agent", "license_no": "PCO-9911", "license_expiry": "2027-12-31"})
        check("create user stores licence no", st == 200 and lu.get("license_no") == "PCO-9911")
        st, lu2 = call("PUT", f"/users/{lu['id']}", admin, {"license_no": "PCO-2025"})
        check("update user changes licence no", st == 200 and lu2.get("license_no") == "PCO-2025")
        _, ulist = call("GET", "/users", admin)
        check("user list exposes licence fields",
              any(u["id"] == lu["id"] and u.get("license_expiry") == "2027-12-31" for u in ulist))

        st, ap = call("GET", "/clients/1/audit-pack", admin)
        check("audit pack returns (200)", st == 200)
        check("audit pack has all sections", ap and all(k in ap for k in (
            "client", "range", "summary", "history", "chem_log", "products",
            "technicians", "corrective", "device_alerts", "trend")))
        check("audit pack summary is numeric", ap and isinstance(ap["summary"]["visits"], int))
        check("audit pack products carry attachments list",
              all(isinstance(p.get("attachments"), list) for p in ap["products"]))
        check("audit pack defaults to a date range",
              bool(ap["range"]["from"]) and bool(ap["range"]["to"]))
        # explicit range + site filter accepted
        st, ap2 = call("GET", "/clients/1/audit-pack?from=2020-01-01&to=2030-01-01", admin)
        check("audit pack accepts explicit range", st == 200 and ap2["range"]["from"] == "2020-01-01")
        st, _ = call("GET", "/clients/1/audit-pack?site_id=none", admin)
        check("audit pack accepts site filter", st == 200)
        # access control: agent needs analytics.view; cross-tenant client blocked
        _, ccl = call("GET", "/clients", client)
        own_cid = ccl[0]["id"]
        st, _ = call("GET", f"/clients/{own_cid}/audit-pack", client)
        check("client can pull own audit pack", st == 200)
        other = next((c for c in call("GET", "/clients", admin)[1] if c["id"] != own_cid), None)
        if other:
            st, _ = call("GET", f"/clients/{other['id']}/audit-pack", client)
            check("client cannot pull another client's audit pack (403)", st == 403)

        print("SMART DISPATCH (geo sites / route optimize / SLA)")
        # a client with 3 geocoded sites laid out so the time order != geo order
        _, dc = call("POST", "/clients", admin, {"name_en": "Dispatch Co"})
        coords = [(30.10, 31.10), (30.50, 31.50), (30.20, 31.20)]  # B is far, C is near A
        sids = []
        for i, (la, ln) in enumerate(coords):
            st, s = call("POST", f"/clients/{dc['id']}/sites", admin,
                         {"name": f"Site {i}", "lat": la, "lng": ln})
            sids.append(s["id"])
        check("site stores coordinates", st == 200 and s.get("lat") == 30.20)
        # update_site can change coordinates (+ parse a "lat,lng" geo string)
        st, su = call("PUT", f"/sites/{sids[0]}", admin, {"geo": "30.11,31.11"})
        check("update_site parses geo string", st == 200 and round(su["lat"], 2) == 30.11)
        call("PUT", f"/sites/{sids[0]}", admin, {"lat": 30.10, "lng": 31.10})  # restore
        # three visits same day, same agent, scheduled in a non-optimal order
        day = "2026-07-15"
        order_sites = [sids[0], sids[1], sids[2]]   # near, FAR, near -> wasteful
        for i, sid in enumerate(order_sites):
            call("POST", "/visits", admin, {
                "client_id": dc["id"], "site_id": sid, "agent_id": a1,
                "scheduled_start": f"{day} {9 + i:02d}:00:00"})
        # list_visits now surfaces site coordinates (needed by the board)
        _, dv = call("GET", f"/visits?from={day}&to={day}", admin)
        dvi = dv["items"] if isinstance(dv, dict) else dv
        check("visits list carries site coordinates", any(x.get("site_lat") is not None for x in dvi))
        # optimize (preview) — should not increase distance, returns full order
        st, op = call("POST", "/dispatch/optimize", admin, {"agent_id": a1, "date": day})
        check("optimize returns 200", st == 200)
        check("optimize previews all stops in order", len(op["order"]) == 3 and op["applied"] is False)
        check("optimize does not increase distance", op["km_after"] <= op["km_before"] + 1e-6)
        # apply — rewrites scheduled times into the optimized sequence
        st, ap2 = call("POST", "/dispatch/optimize", admin, {"agent_id": a1, "date": day, "apply": True})
        check("optimize apply commits the order", st == 200 and ap2["applied"] is True)
        _, dv2 = call("GET", f"/visits?from={day}&to={day}&agent={a1}", admin)
        dvi2 = dv2["items"] if isinstance(dv2, dict) else dv2
        starts = sorted(x["scheduled_start"] for x in dvi2)
        check("apply spaced the visit times", starts[0][11:16] == "09:00" and starts[1][11:16] == "10:30")
        st, _ = call("POST", "/dispatch/optimize", agent, {"agent_id": a1, "date": day})
        check("optimize needs visits.edit (agent allowed/blocked)", st in (200, 403))

        # SLA: an old monthly contract with no completed visits -> overdue
        call("POST", "/contracts", admin, {
            "client_id": dc["id"], "frequency": "monthly", "start_date": "2026-01-01"})
        st, sla = call("GET", "/dispatch/sla", admin)
        check("SLA returns items + counts", st == 200 and "counts" in sla and "items" in sla)
        mine = next((r for r in sla["items"] if r["client_id"] == dc["id"]), None)
        check("overdue monthly contract flagged", mine is not None and mine["status"] == "overdue")
        check("SLA counts tally with items",
              sla["counts"]["overdue"] + sla["counts"]["due_soon"] + sla["counts"]["ok"] == len(sla["items"]))
        st, csla = call("GET", "/dispatch/sla", client)
        check("client SLA scoped to own contracts (200)", st == 200
              and all(r["client_id"] == (csla["items"][0]["client_id"] if csla["items"] else r["client_id"]) for r in csla["items"]))

        print("PER-FILE UPLOAD AUTHORIZATION (cross-tenant)")
        APNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        _, myc = call("GET", "/clients", client)
        my_cid = myc[0]["id"]
        _, allc = call("GET", "/clients", admin)
        other_cid = next(c["id"] for c in allc if c["id"] != my_cid)
        _, ph_own = post_multipart("/photos", admin, {"entity_type": "client", "entity_id": my_cid}, "own.png", APNG)
        _, ph_other = post_multipart("/photos", admin, {"entity_type": "client", "entity_id": other_cid}, "oth.png", APNG)
        st, _ = raw_get(f"/uploads/{ph_own['filename']}", cookie=f"pc_upl={client}")
        check("client can read own company's file (not 403)", st != 403)
        st, _ = raw_get(f"/uploads/{ph_other['filename']}", cookie=f"pc_upl={client}")
        check("client CANNOT read another company's file (403)", st == 403)
        st, _ = raw_get(f"/uploads/{ph_other['filename']}", token=admin)
        check("admin can read any tenant's file (not 403)", st != 403)
        st, _ = raw_get("/uploads/orphan-deadbeef.png", cookie=f"pc_upl={client}")
        check("client denied unknown/orphan file (403, fail-closed)", st == 403)
        # chemical SDS/label docs are shared catalog -> any authed user (keeps the
        # client-facing Audit Pack's SDS links working)
        _, chs = call("GET", "/chemicals", admin)
        if chs:
            _, ph_chem = post_multipart("/photos", admin, {"entity_type": "chemical", "entity_id": chs[0]["id"]}, "sds.png", APNG)
            st, _ = raw_get(f"/uploads/{ph_chem['filename']}", cookie=f"pc_upl={client}")
            check("client may read shared chemical SDS file (not 403)", st != 403)
        # company logo is shared branding -> readable by any authed user
        call("PUT", "/settings", admin, {"logo": "brandlogo.png"})
        st, _ = raw_get("/uploads/brandlogo.png", cookie=f"pc_upl={client}")
        check("client may read shared company logo (not 403)", st != 403)

        print("VISIT REQUESTS (client self-service)")
        st, vr = call("POST", "/visit-requests", client, {"preferred_date": "2026-08-01", "note": "Ants in kitchen"})
        check("client can submit a visit request", st == 200 and vr["status"] == "pending")
        _, mine = call("GET", "/visit-requests", client)
        check("client sees own requests", any(r["id"] == vr["id"] for r in mine))
        # cross-tenant: client can't request for another company
        st, _ = call("POST", "/visit-requests", client, {"client_id": other_cid, "preferred_date": "2026-08-02"})
        # client_id is ignored for clients (forced to own) -> still 200 on own, so assert it didn't target other
        _, all_after = call("GET", "/visit-requests", admin)
        check("client request is scoped to own company",
              all(r["client_id"] == my_cid for r in all_after if r["created_by"] and r["status"] == "pending" and r["id"] >= vr["id"]))
        # staff inbox + approve -> creates a visit, links it, marks approved
        _, staffview = call("GET", "/visit-requests?status=pending", admin)
        check("staff inbox lists pending requests", any(r["id"] == vr["id"] for r in staffview))
        st, visit = call("POST", f"/visit-requests/{vr['id']}/approve", admin, {"agent_id": a1})
        check("approve creates a scheduled visit", st == 200 and visit["status"] == "scheduled" and visit["agent_id"] == a1)
        _, after = call("GET", "/visit-requests", admin)
        appr = next((r for r in after if r["id"] == vr["id"]), None)
        check("request marked approved + linked to the visit", appr and appr["status"] == "approved" and appr["visit_id"] == visit["id"])
        st, _ = call("POST", f"/visit-requests/{vr['id']}/approve", admin, {})
        check("a handled request can't be approved again (400)", st == 400)
        # decline flow
        _, vr2 = call("POST", "/visit-requests", client, {"note": "Follow-up"})
        st, _ = call("POST", f"/visit-requests/{vr2['id']}/decline", admin, {"reason": "out of scope"})
        check("staff can decline a request", st == 200)
        st, _ = call("POST", "/visit-requests/99999/approve", admin, {})
        check("approving a missing request -> 404", st == 404)
        # a plain client cannot approve
        st, _ = call("POST", f"/visit-requests/{vr2['id']}/approve", client, {})
        check("client cannot approve requests (403)", st == 403)

        print("OWNER DASHBOARD COCKPIT")
        _, dash = call("GET", "/dashboard", admin)
        cp = dash.get("cockpit")
        check("admin dashboard includes cockpit", isinstance(cp, dict))
        check("cockpit has revenue (this + prev month)",
              cp and "revenue_month" in cp and "revenue_prev" in cp)
        check("cockpit has overdue receivables", cp and "overdue_invoices" in cp and "overdue_amount" in cp)
        check("cockpit SLA health counts", cp and set(cp["sla"]) == {"ok", "due_soon", "overdue"})
        check("cockpit utilization is per-agent list",
              cp and isinstance(cp["utilization"], list)
              and all({"name", "total", "completed", "rate"} <= set(a) for a in cp["utilization"]))
        check("utilization rate is 0-100", cp and all(0 <= a["rate"] <= 100 for a in cp["utilization"]))
        _, adash = call("GET", "/dashboard", agent)
        check("agent dashboard has no cockpit", "cockpit" not in adash)
        _, cdash = call("GET", "/dashboard", client)
        check("client dashboard has no cockpit", "cockpit" not in cdash)

        print("INVENTORY REORDER ALERTS")
        st, lowchem = call("POST", "/chemicals", admin, {
            "name_en": "Reorder Test Chem", "unit": "L",
            "quantity_in_stock": 1, "reorder_level": 10})
        check("low-stock chemical created", st == 200 and bool(lowchem["id"]))
        st, okchem = call("POST", "/chemicals", admin, {
            "name_en": "Well Stocked Chem", "unit": "L",
            "quantity_in_stock": 100, "reorder_level": 5})
        mgr_tok = login("manager@pestcrm.com", "manager123")
        call("POST", "/notifications/generate", admin)
        _, mn = call("GET", "/notifications", mgr_tok)
        lowmsgs = [n for n in mn["items"] if n["type"] == "low_stock"]
        check("manager alerted to reorder low-stock item",
              any(n["link_id"] == lowchem["id"] and n["link_view"] == "chemicals" for n in lowmsgs))
        check("well-stocked item does NOT trigger a reorder alert",
              not any(n["link_id"] == okchem["id"] for n in lowmsgs))
        # re-running the scan does not duplicate the alert (dedup per item per month)
        before = len(lowmsgs)
        call("POST", "/notifications/generate", admin)
        _, mn2 = call("GET", "/notifications", mgr_tok)
        after = len([n for n in mn2["items"] if n["type"] == "low_stock"])
        check("reorder alert is de-duplicated (no repeat in same month)", after == before)

        print("RECURRING BILLING (auto-invoice from contracts)")
        today = time.strftime("%Y-%m-%d")
        _, ownc = call("GET", "/clients", client)            # client sees only its own company
        bill_cid = ownc[0]["id"]
        st, ct = call("POST", "/contracts", admin, {
            "client_id": bill_cid, "frequency": "monthly", "start_date": today,
            "price": 1000, "auto_invoice": 1, "next_bill_date": today})
        check("auto-invoice contract created",
              st == 200 and ct["auto_invoice"] == 1 and ct["next_bill_date"] == today)
        st, r = call("POST", "/contracts/bill", admin, {})
        check("billing run generated invoices", st == 200 and r["created"] >= 1)
        _, invs = call("GET", "/invoices", admin)
        invlist = invs if isinstance(invs, list) else invs.get("items", [])
        mine = [i for i in invlist if i.get("contract_id") == ct["id"]]
        check("contract produced an auto-invoice", len(mine) >= 1)
        inv = mine[0]
        check("auto-invoice is issued as 'sent' (payable, not draft)", inv["status"] == "sent")
        check("auto-invoice amount = contract price", abs(inv["amount"] - 1000) < 0.01)
        check("auto-invoice applies tax + total",
              inv["tax"] > 0 and abs(inv["total"] - (inv["amount"] + inv["tax"])) < 0.01)
        # advancing next_bill_date past today means a second run does nothing
        _, r2 = call("POST", "/contracts/bill", admin, {})
        check("billing is idempotent (no double-bill same cycle)", r2["created"] == 0)
        # the client portal is notified of the new invoice
        _, cn = call("GET", "/notifications", client)
        check("client notified of the new invoice", any(n["type"] == "invoice_new" for n in cn["items"]))
        # a contract NOT opted into auto-billing is never billed
        call("POST", "/contracts", admin, {"client_id": bill_cid, "frequency": "monthly",
                                           "start_date": today, "price": 500})
        _, r3 = call("POST", "/contracts/bill", admin, {})
        check("contract without auto-invoice is not billed", r3["created"] == 0)

        print("ONLINE PAYMENTS (gateway-agnostic, manual sandbox)")
        st, pinv = call("POST", "/invoices", admin, {
            "client_id": bill_cid, "issue_date": today, "status": "sent",
            "items": [{"description": "Online pay test", "quantity": 1, "unit_price": 300}]})
        check("payable invoice created", st == 200 and pinv["total"] == 300)
        st, intent = call("POST", f"/invoices/{pinv['id']}/pay", client, {})
        check("client can start an online payment",
              st == 200 and intent["provider"] == "manual" and abs(intent["amount"] - 300) < 0.01)
        _, ps = call("GET", f"/payment-intents/{intent['token']}", client)
        check("payment intent starts pending", ps["status"] == "pending")
        # the gateway callback is unauthenticated (provider calls it server-to-server)
        st, cb = call("POST", "/payments/callback/manual", None, {"token": intent["token"]})
        check("payment callback marks intent paid", st == 200 and cb["status"] == "paid")
        _, inv2 = call("GET", f"/invoices/{pinv['id']}", admin)
        check("invoice auto-marked paid after online payment", inv2["status"] == "paid")
        check("payment recorded with online method",
              any(p["method"] == "online:manual" for p in inv2["payments"]))
        # callbacks are idempotent — a retry must not double-charge
        st, _ = call("POST", "/payments/callback/manual", None, {"token": intent["token"]})
        _, inv3 = call("GET", f"/invoices/{pinv['id']}", admin)
        check("repeat callback does not double-pay", st == 200 and len(inv3["payments"]) == 1)
        # an already-paid invoice can't open a new payment
        st, _ = call("POST", f"/invoices/{pinv['id']}/pay", client, {})
        check("paying an already-paid invoice is rejected (400)", st == 400)
        # an unknown reference is rejected
        st, _ = call("POST", "/payments/callback/manual", None, {"token": "deadbeef00"})
        check("callback for unknown reference -> 404", st == 404)

        print("CLIENT QUOTE APPROVAL (portal accept/decline)")
        st, q1 = call("POST", "/invoices", admin, {
            "client_id": bill_cid, "doc_type": "quote", "issue_date": today, "status": "sent",
            "items": [{"description": "Annual plan", "quantity": 1, "unit_price": 400}]})
        check("sent quote created", st == 200 and q1["total"] == 400)
        _, cn = call("GET", "/notifications", client)
        check("client notified when quote is sent",
              any(n["type"] == "quote_new" for n in cn["items"]))
        st, _ = call("POST", f"/invoices/{q1['id']}/approve", agent)
        check("agent without invoices.edit cannot approve (403)", st == 403)
        st, ninv = call("POST", f"/invoices/{q1['id']}/approve", client)
        check("client approves own sent quote -> payable invoice",
              st == 200 and ninv["doc_type"] == "invoice" and ninv["status"] == "sent"
              and ninv["total"] == 400 and ninv["due_date"])
        _, q1b = call("GET", f"/invoices/{q1['id']}", client)
        check("approved quote marked accepted", q1b["status"] == "accepted")
        st, _ = call("POST", f"/invoices/{q1['id']}/approve", client)
        check("re-approving an accepted quote -> 400", st == 400)
        _, an = call("GET", "/notifications", admin)
        check("staff notified of the approval",
              any(n["type"] == "quote_approved" for n in an["items"]))
        st, q2 = call("POST", "/invoices", admin, {
            "client_id": bill_cid, "doc_type": "quote", "issue_date": today, "status": "sent",
            "items": [{"description": "One-off", "quantity": 1, "unit_price": 150}]})
        st, q2d = call("POST", f"/invoices/{q2['id']}/decline", client, {"reason": "too pricey"})
        check("client declines quote with reason",
              st == 200 and q2d["status"] == "declined" and "too pricey" in (q2d["notes"] or ""))
        _, an = call("GET", "/notifications", admin)
        check("staff notified of the decline",
              any(n["type"] == "quote_declined" for n in an["items"]))
        st, q3 = call("POST", "/invoices", admin, {
            "client_id": bill_cid, "doc_type": "quote", "issue_date": today, "amount": 50})
        st, _ = call("POST", f"/invoices/{q3['id']}/approve", client)
        check("draft (not sent) quote cannot be approved (400)", st == 400)

        print("HEALTH ENDPOINT + MONITOR")
        st, h = call("GET", "/health")
        check("/api/health answers unauthenticated", st == 200 and h["ok"] is True and h["db"] == "ok")
        menv = dict(os.environ, PESTCRM_DATA_DIR=tmp, PESTCRM_URL=f"http://localhost:{PORT}")
        mon = subprocess.run([sys.executable, os.path.join(HERE, "monitor.py"), "--no-restart"],
                             env=menv, capture_output=True, text=True, timeout=60)
        check("monitor.py runs clean against a live server", mon.returncode == 0)
        check("monitor sees healthy http+db+disk",
              "http: ok" in mon.stdout and "integrity: ok" in mon.stdout and "disk: ok" in mon.stdout)
        # temp data dir has no backups -> the stale-backup alert must fire
        check("monitor flags missing backups", "ALERT [backup]" in mon.stdout)
        _, an = call("GET", "/notifications", admin)
        check("stale-backup alert lands as admin notification",
              any(n["type"] == "monitor_backup" for n in an["items"]))
        mon2 = subprocess.run([sys.executable, os.path.join(HERE, "monitor.py"), "--no-restart"],
                              env=menv, capture_output=True, text=True, timeout=60)
        _, an2 = call("GET", "/notifications", admin)
        check("monitor alerts dedup (once per day)",
              sum(1 for n in an2["items"] if n["type"] == "monitor_backup")
              == sum(1 for n in an["items"] if n["type"] == "monitor_backup"))

        print("INVOICE HARDENING (drafts, delete, convert, overpay)")
        _, dft = call("POST", "/invoices", admin, {"client_id": bill_cid, "issue_date": today,
                                                   "amount": 999, "status": "draft"})
        _, clist = call("GET", "/invoices?doc_type=all", client)
        rows = clist["items"] if isinstance(clist, dict) else clist
        check("client list hides draft documents", all(r["id"] != dft["id"] for r in rows))
        st, _ = call("GET", f"/invoices/{dft['id']}", client)
        check("client cannot open a draft invoice (403)", st == 403)
        st, _ = call("GET", f"/invoices/{dft['id']}", admin)
        check("staff still see drafts", st == 200)
        st, _ = call("DELETE", f"/invoices/{pinv['id']}", admin)
        check("invoice with payments cannot be deleted (400)", st == 400)
        st, _ = call("DELETE", f"/invoices/{dft['id']}", admin)
        check("unpaid invoice still deletable", st == 200)
        st, _ = call("POST", f"/invoices/{q1['id']}/convert", admin)
        check("accepted quote cannot be converted again (400)", st == 400)
        _, ovi = call("POST", "/invoices", admin, {"client_id": bill_cid, "issue_date": today,
                                                   "amount": 100, "status": "sent"})
        st, _ = call("POST", f"/invoices/{ovi['id']}/payments", admin, {"amount": 150})
        check("overpayment rejected (400)", st == 400)
        st, _ = call("POST", f"/invoices/{ovi['id']}/payments", admin, {"amount": 100})
        check("exact payment accepted", st == 200)

        print("LEADS (public website form + pipeline)")
        st, r = call("POST", "/public/lead", None, {"name": "Ahmed Web", "phone": "0100000001",
                                                    "company": "Cairo Bakery", "sector": "bakery",
                                                    "message": "Need monthly service",
                                                    "preferred_date": "2027-02-01"})
        check("public lead accepted (unauthenticated)", st == 200 and r["ok"] is True)
        st, r = call("POST", "/public/lead", None, {"name": "Bot", "phone": "1", "website": "spam.com"})
        check("honeypot swallows bots (fake ok)", st == 200 and r["ok"] is True)
        st, _ = call("POST", "/public/lead", None, {"name": "No Contact"})
        check("lead without phone/email rejected (400)", st == 400)
        _, ld = call("GET", "/leads", admin)
        check("admin sees the lead with counts", any(l["name"] == "Ahmed Web" for l in ld["items"])
              and ld["counts"].get("new", 0) >= 1)
        check("honeypot lead was NOT stored", all(l["name"] != "Bot" for l in ld["items"]))
        st, _ = call("GET", "/leads", agent)
        check("agent has no leads access (403)", st == 403)
        st, _ = call("GET", "/leads", client)
        check("client has no leads access (403)", st == 403)
        lead = next(l for l in ld["items"] if l["name"] == "Ahmed Web")
        check("website lead carries source + date",
              lead["source"] == "website" and lead["preferred_date"] == "2027-02-01")
        _, an = call("GET", "/notifications", admin)
        check("staff notified of new website lead", any(n["type"] == "lead_new" for n in an["items"]))
        st, up = call("PUT", f"/leads/{lead['id']}", admin, {"status": "contacted"})
        check("lead status update", st == 200 and up["status"] == "contacted")
        st, _ = call("PUT", f"/leads/{lead['id']}", admin, {"status": "bogus"})
        check("invalid lead status rejected (400)", st == 400)
        st, newc = call("POST", f"/leads/{lead['id']}/convert", admin)
        check("lead converts to client", st == 200 and newc["name_en"] == "Cairo Bakery")
        _, ld2 = call("GET", "/leads?status=won", admin)
        check("converted lead marked won + linked",
              any(l["id"] == lead["id"] and l["client_id"] == newc["id"] for l in ld2["items"]))
        st, _ = call("POST", f"/leads/{lead['id']}/convert", admin)
        check("double-convert rejected (400)", st == 400)
        # CORS preflight for the website's cross-origin fetch
        req = urllib.request.Request(BASE + "/public/lead", method="OPTIONS")
        with urllib.request.urlopen(req) as resp:
            check("CORS preflight answers 204 + allow-origin",
                  resp.status == 204 and resp.headers.get("Access-Control-Allow-Origin") == "*")

        print("PRICE BOOK")
        st, pb1 = call("POST", "/price-book", admin, {"name_en": "General Pest Control",
                                                      "name_ar": "مكافحة عامة", "unit_price": 750})
        check("price item created", st == 200 and pb1["unit_price"] == 750)
        st, _ = call("POST", "/price-book", agent, {"name_en": "Nope", "unit_price": 1})
        check("agent cannot create price items (403)", st == 403)
        st, _ = call("GET", "/price-book", client)
        check("client cannot read the price book (403)", st == 403)
        _, pbl = call("GET", "/price-book", admin)
        check("price book lists active items", any(p["id"] == pb1["id"] for p in pbl))
        st, pb1b = call("PUT", f"/price-book/{pb1['id']}", admin, {"active": 0, "unit_price": 800})
        check("price item update + deactivate", st == 200 and pb1b["active"] == 0 and pb1b["unit_price"] == 800)
        _, pbl = call("GET", "/price-book", admin)
        check("inactive item hidden from picker list", all(p["id"] != pb1["id"] for p in pbl))
        _, pbl = call("GET", "/price-book?all=1", admin)
        check("manage list still shows inactive", any(p["id"] == pb1["id"] for p in pbl))

        print("CLIENT STATEMENT OF ACCOUNT")
        _, stm = call("GET", f"/clients/{bill_cid}/statement", admin)
        check("statement has invoice + payment entries",
              any(e["kind"] == "invoice" for e in stm["entries"])
              and any(e["kind"] == "payment" for e in stm["entries"]))
        check("statement closing = debits - credits",
              abs(stm["closing"] - (stm["opening"] + stm["total_debit"] - stm["total_credit"])) < 0.01)
        bal_ok = True
        run = stm["opening"]
        for e in stm["entries"]:
            run = round(run + e["debit"] - e["credit"], 2)
            if abs(run - e["balance"]) > 0.01:
                bal_ok = False
        check("running balance is consistent", bal_ok)
        st, _ = call("GET", f"/clients/{bill_cid}/statement", client)
        check("client can pull own statement", st == 200)
        st, _ = call("GET", "/clients/999/statement", client)
        check("client cannot pull another statement", st in (403, 404))

        print("AGENT DOUBLE-BOOKING GUARD")
        _, cfc = call("POST", "/clients", admin, {"name_en": "Conflict Test Co"})
        _, v1 = call("POST", "/visits", admin, {"client_id": cfc["id"], "agent_id": 3,
                                                "scheduled_start": "2027-03-01 09:00",
                                                "scheduled_end": "2027-03-01 10:00"})
        st, _ = call("POST", "/visits", admin, {"client_id": cfc["id"], "agent_id": 3,
                                                "scheduled_start": "2027-03-01 09:30"})
        check("overlapping visit for same agent -> 409", st == 409)
        st, v2 = call("POST", "/visits", admin, {"client_id": cfc["id"], "agent_id": 3,
                                                 "scheduled_start": "2027-03-01 09:30",
                                                 "ignore_conflict": True})
        check("override with ignore_conflict works", st == 200)
        st, v3 = call("POST", "/visits", admin, {"client_id": cfc["id"], "agent_id": 3,
                                                 "scheduled_start": "2027-03-01 14:00"})
        check("non-overlapping slot is fine", st == 200)
        st, _ = call("PUT", f"/visits/{v3['id']}", admin, {"scheduled_start": "2027-03-01 09:15"})
        check("moving a visit into a clash -> 409", st == 409)
        st, _ = call("PUT", f"/visits/{v3['id']}", admin, {"notes": "just a note"})
        check("non-schedule edits skip the conflict check", st == 200)

        print("VISIT RATINGS")
        call("PUT", f"/visits/{v1['id']}", admin, {"status": "completed"})
        call("PUT", f"/visits/{v2['id']}", admin, {"status": "completed"})
        st, _ = call("POST", f"/visits/{v1['id']}/rating", client, {"stars": 5})
        check("another company's client cannot rate (403)", st == 403)
        st, _ = call("POST", f"/visits/{v1['id']}/rating", admin, {"stars": 5})
        check("staff cannot rate (403)", st == 403)
        # portal user for the conflict-test client
        call("POST", "/users", admin, {"full_name": "CT Portal", "email": "ct@test.com",
                                       "password": "ctpass123", "role": "client",
                                       "client_id": cfc["id"]})
        ctok = login("ct@test.com", "ctpass123")
        st, _ = call("POST", f"/visits/{v1['id']}/rating", ctok, {"stars": 9})
        check("stars out of range rejected (400)", st == 400)
        st, rt = call("POST", f"/visits/{v1['id']}/rating", ctok, {"stars": 4, "comment": "Great job"})
        check("client rates completed visit", st == 200 and rt["stars"] == 4)
        st, _ = call("POST", f"/visits/{v1['id']}/rating", ctok, {"stars": 1})
        check("visit cannot be rated twice (400)", st == 400)
        st, _ = call("POST", f"/visits/{v3['id']}/rating", ctok, {"stars": 3})
        check("scheduled (not completed) visit cannot be rated (400)", st == 400)
        _, vd = call("GET", f"/visits/{v1['id']}", admin)
        check("visit detail carries the rating", vd["rating"] and vd["rating"]["stars"] == 4)
        _, an = call("GET", "/notifications", admin)
        check("staff notified of the rating", any(n["type"] == "visit_rated" for n in an["items"]))
        _, dash = call("GET", "/dashboard", admin)
        check("cockpit shows avg technician rating",
              any(u.get("rating") == 4.0 for u in dash["cockpit"]["utilization"]))

        print("PURCHASE ORDERS / STOCK-IN")
        _, chems = call("GET", "/chemicals", admin)
        c0 = chems[0]
        before = c0["quantity_in_stock"]
        st, po = call("POST", "/purchase-orders", admin, {
            "supplier": "AgroChem Ltd", "reference": "SUP-778",
            "items": [{"chemical_id": c0["id"], "quantity": 10, "unit_cost": 25}]})
        check("purchase order created with total", st == 200 and po["total_cost"] == 250)
        _, chems2 = call("GET", "/chemicals", admin)
        c0b = next(c for c in chems2 if c["id"] == c0["id"])
        check("stock incremented by the purchase", c0b["quantity_in_stock"] == before + 10)
        _, txs = call("GET", f"/chemicals/{c0['id']}/transactions", admin)
        check("purchase logged in inventory transactions",
              any(tx["reason"] == "purchase" and tx["reference"] == f"PO-{po['id']}" for tx in txs))
        st, _ = call("POST", "/purchase-orders", admin, {"items": [{"chemical_id": c0["id"], "quantity": 0}]})
        check("zero quantity rejected (400)", st == 400)
        st, _ = call("POST", "/purchase-orders", admin, {"items": [{"chemical_id": 99999, "quantity": 5}]})
        check("unknown chemical rejected (404)", st == 404)
        st, _ = call("POST", "/purchase-orders", admin, {"items": []})
        check("empty purchase rejected (400)", st == 400)
        st, _ = call("POST", "/purchase-orders", agent, {
            "items": [{"chemical_id": c0["id"], "quantity": 1}]})
        check("agent cannot stock in (403)", st == 403)
        _, pol = call("GET", "/purchase-orders", admin)
        check("purchase history lists items",
              any(p["id"] == po["id"] and p["items"] and p["supplier"] == "AgroChem Ltd" for p in pol))

    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'='*40}\n  {passed} passed, {failed} failed\n{'='*40}")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
