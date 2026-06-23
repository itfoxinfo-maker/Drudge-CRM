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
        call("POST", "/visits/2/usage", admin, {"chemical_id": 1, "quantity": 3})
        _, chems2 = call("GET", "/chemicals", admin)
        after = next(c for c in chems2 if c["id"] == 1)["quantity_in_stock"]
        check("chemical usage decrements stock", abs((before - after) - 3) < 0.001)
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

        print("NOTIFICATIONS / ANALYTICS / SETTINGS")
        _, notif = call("GET", "/notifications", admin)
        check("notifications generated", notif["unread"] >= 1)
        _, an = call("GET", "/analytics", admin)
        check("analytics returns sections", all(k in an for k in ("months", "ar_aging", "agents", "totals")))
        _, s = call("PUT", "/settings", admin, {"company_name_en": "Test Co"})
        check("settings persisted", s["company_name_en"] == "Test Co")

        print("SIGNATURES / SEARCH / CSV")
        png = "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+M8AAAMCAQGNuM5UAAAAAElFTkSuQmCC"
        _, sig = call("POST", "/visits/1/signature", admin, {"which": "customer", "data": png, "customer_name": "Ali"})
        check("signature saved", bool(sig["customer_signature"]) and sig["customer_name"] == "Ali")
        _, sr = call("GET", "/search?q=Noor", admin)
        check("search finds client", len(sr["clients"]) >= 1)
        st, csv = call("GET", "/export/clients.csv", admin, raw=True)
        check("csv export works", st == 200 and b"name_en" in csv)
        st, _ = call("GET", "/export/clients.csv", agent)
        check("csv export blocked for agent (403)", st == 403)

    finally:
        proc.terminate()
        try: proc.wait(timeout=5)
        except Exception: proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n{'='*40}\n  {passed} passed, {failed} failed\n{'='*40}")
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
