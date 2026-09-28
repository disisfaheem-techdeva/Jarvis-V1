#!/usr/bin/env python3
"""Live proof, run on the server:  python3 ops/verify/live_check.py phase1 [phase2 ...]

Writes ONLY into the QA tenant ("TechDeva QA Test Co"), records the row counts
first, deletes exactly the rows it created, and proves the counts are back.
Reads other tenants (Dr Irfan) read-only. Sessions are short-lived tokens
minted inside the api container; no password is typed or printed.
Standard library only.
"""
import json, os, subprocess, sys, urllib.request, urllib.error, uuid

API = os.environ.get("API", "https://api.techdeva.net")
PORTAL = os.environ.get("PORTAL", "https://portal.techdeva.net")
QA_NAME = "TechDeva QA Test Co"
IRFAN = "605e1cf0-9ce5-4943-9b65-4a2b85605cdb"
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
RESULTS, CREATED = [], {}
COUNT_TABLES = ["customers", "appointments", "invoices", "invoice_items", "payments", "products",
                "stock_movements", "services", "packages", "package_items", "expenses", "staff"]


def sh(args, stdin=None):
    return subprocess.run(args, cwd=ROOT, input=stdin, capture_output=True, text=True, check=True).stdout


# TD_LOCAL_PSQL / TD_LOCAL_PY: run the same checks against a local copy of the stack.
LOCAL_PSQL = os.environ.get("TD_LOCAL_PSQL")
LOCAL_PY = os.environ.get("TD_LOCAL_PY")


def sql(q):
    """Superuser psql inside the db container. Used for setup lookups, counts and cleanup only."""
    base = LOCAL_PSQL.split() if LOCAL_PSQL else ["docker", "compose", "exec", "-T", "db", "psql", "-U", "postgres", "-d", "techdeva"]
    out = sh(base + ["-tA", "-F", "\t", "-c", q])
    return [l.split("\t") for l in out.strip().splitlines() if l]


def token(tid, role, uid):
    code = ("from app.auth import issue_token;print(issue_token(tenant_id=%r,user_id=%r,role=%r,ttl=900))"
            % (tid, uid, role))
    base = [LOCAL_PY] if LOCAL_PY else ["docker", "compose", "exec", "-T", "api", "python"]
    return sh(base + ["-c", code]).strip()


def call(tok, method, path, body=None, base=API):
    data = json.dumps(body).encode() if body is not None else None
    h = {"Content-Type": "application/json"}
    if tok:
        h["Authorization"] = "Bearer " + tok
    req = urllib.request.Request(base + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw and raw.strip()[:1] in "{[" else raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def check(item, name, ok, evidence):
    RESULTS.append((item, name, "PASS" if ok else "FAIL", evidence))
    print(("PASS " if ok else "FAIL ") + "[%s] %s -- %s" % (item, name, evidence), flush=True)


def track(table, rid):
    if rid:
        CREATED.setdefault(table, []).append(str(rid))


def counts(tid):
    return {t: int(sql("SELECT count(*) FROM %s WHERE tenant_id = '%s'" % (t, tid))[0][0]) for t in COUNT_TABLES}


def cleanup(tid):
    # children first; only ids this run created, only in the QA tenant
    order = ["payments", "invoice_items", "stock_movements", "package_items", "appointments",
             "invoices", "expenses", "packages", "products", "services", "customers", "staff"]
    for t in order:
        ids = CREATED.get(t)
        if t == "payments":
            inv = CREATED.get("invoices", [])
            if inv:
                sql("DELETE FROM payments WHERE tenant_id='%s' AND invoice_id IN (%s)" % (tid, ",".join("'%s'" % i for i in inv)))
        if t == "invoice_items":
            inv = CREATED.get("invoices", [])
            if inv:
                sql("DELETE FROM invoice_items WHERE tenant_id='%s' AND invoice_id IN (%s)" % (tid, ",".join("'%s'" % i for i in inv)))
            continue
        if t == "stock_movements":
            pr = CREATED.get("products", [])
            if pr:
                sql("DELETE FROM stock_movements WHERE tenant_id='%s' AND product_id IN (%s)" % (tid, ",".join("'%s'" % i for i in pr)))
            continue
        if t == "package_items":
            pk = CREATED.get("packages", [])
            if pk:
                sql("DELETE FROM package_items WHERE tenant_id='%s' AND package_id IN (%s)" % (tid, ",".join("'%s'" % i for i in pk)))
            continue
        if ids and t != "payments":
            sql("DELETE FROM %s WHERE tenant_id='%s' AND id IN (%s)" % (t, tid, ",".join("'%s'" % i for i in ids)))


# --------------------------------------------------------------------------- phases
def phase1(ctx):
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    tag = uuid.uuid4().hex[:6]
    # A1 portal links
    if not os.environ.get("TD_SKIP_PORTAL"):
        a1_portal()
    staff_and_rest(ctx, O, OP, tid, tag)


def a1_portal():
    req = urllib.request.Request(PORTAL + "/auth/login", method="GET")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k): return None
    op = urllib.request.build_opener(NoRedirect)
    try:
        op.open(req, timeout=15); loc, code = "", 200
    except urllib.error.HTTPError as e:
        code, loc = e.code, e.headers.get("Location", "")
    check("A1", "portal /auth/login redirects to /login", code in (301, 302, 307, 308) and loc.endswith("/login"), "%s -> %s" % (code, loc))
    s, home = call(None, "GET", "/", base=PORTAL)
    check("A1", "homepage Log in links to /login", isinstance(home, str) and 'href="/login"' in home and 'href="/auth/login"' not in home, "status %s" % s)


def staff_and_rest(ctx, O, OP, tid, tag):
    # #1 appointment 20000 - 12000
    staff = sql("INSERT INTO staff (tenant_id,name,role) VALUES ('%s','QA Doc %s','Doctor') RETURNING id" % (tid, tag))[0][0]; track("staff", staff)
    s, b = call(O, "POST", "/appointments/book", {"name": "QA Patient " + tag, "phone": "0300" + tag[:3].encode().hex()[:7], "source": "instagram",
        "date": "2026-09-30", "time": "14:00", "end_time": "15:30", "staff_id": staff, "currency": "PKR",
        "services": [{"name": "QA PRP", "price": 20000, "qty": 1}], "received": 12000, "payment_method": "cash",
        "total_sessions": 3, "next_appointment": "2026-10-30", "status": "booked", "notes": "QA note " + tag})
    ok = s == 200 and b.get("money", {}).get("balance") == 8000
    check("#1", "book 20,000 with 12,000 received", ok, "HTTP %s balance=%s" % (s, (b.get("money") or {}).get("balance") if isinstance(b, dict) else b))
    if s != 200:
        return
    appt, cust = b["appointment_id"], b["customer_id"]
    track("appointments", appt); track("customers", cust)
    inv = sql("SELECT invoice_id FROM appointments WHERE id='%s'" % appt)[0][0]; track("invoices", inv)
    s, L = call(O, "GET", "/appointments/list?start=2026-09-30&end=2026-09-30&q=" + tag)
    a = next((x for x in L.get("appointments", []) if x["id"] == appt), {}) if s == 200 else {}
    fields = {k: a.get(k) for k in ("customer_name", "time", "end_time", "staff_name", "currency", "total", "paid", "balance", "status", "total_sessions", "next_appointment", "notes")}
    check("#1", "all fields read back from the database", a.get("balance") == 8000 and a.get("end_time") == "15:30" and a.get("staff_name") and a.get("notes") == "QA note " + tag, json.dumps(fields))
    s, _ = call(O, "PATCH", "/appointments/" + appt, {"date": "2026-09-30", "time": "16:00", "end_time": "17:00", "status": "completed", "notes": "edited " + tag})
    s2, L = call(O, "GET", "/appointments/list?start=2026-09-30&end=2026-09-30&q=" + tag)
    a = next((x for x in L.get("appointments", []) if x["id"] == appt), {})
    check("#1", "edit persists (reopen)", s == 200 and a.get("time") == "16:00" and a.get("status") == "completed" and a.get("balance") == 8000, "time=%s status=%s balance=%s" % (a.get("time"), a.get("status"), a.get("balance")))
    # #3 history
    s, h = call(O, "GET", "/customers/%s/history" % cust)
    check("#3", "customer history from real rows", s == 200 and h["totals"] == {"visits": 1, "billed": 20000.0, "paid": 12000.0, "balance": 8000.0}, "HTTP %s totals=%s" % (s, h.get("totals") if isinstance(h, dict) else h))
    # #4 source
    check("#4", "source saved on the customer", s == 200 and h["customer"]["source"] == "instagram", "source=%s" % (h.get("customer", {}).get("source") if isinstance(h, dict) else None))
    s, c2 = call(O, "POST", "/business/customers", {"name": "QA Walkin " + tag, "source": "walk-in"})
    track("customers", (c2.get("item") or {}).get("id") if isinstance(c2, dict) else None)
    s3, cl = call(O, "GET", "/business/customers")
    got = [c for c in cl.get("items", []) if c["name"] == "QA Walkin " + tag]
    check("#4", "Add customer with Source survives a re-read", s == 200 and got and got[0]["source"] == "walk-in", "HTTP %s source=%s" % (s, got[0]["source"] if got else None))
    # money / delete protection
    s, d = call(OP, "DELETE", "/appointments/" + appt)
    check("#11", "operator cannot delete an appointment", s == 403, "HTTP %s %s" % (s, d))
    s, d = call(O, "DELETE", "/appointments/" + appt)
    check("#1", "owner cannot delete a paid appointment", s == 409, "HTTP %s" % s)
    s, d = call(O, "DELETE", "/business/appointments/" + appt)
    check("#1", "generic route cannot bypass that", s == 405, "HTTP %s" % s)
    s, d = call(O, "DELETE", "/business/customers/" + cust)
    check("#3", "customer with history cannot be deleted", s == 409, "HTTP %s %s" % (s, d.get("detail") if isinstance(d, dict) else d))
    # #2/#9 daily entry = completed booking for today
    s, v = call(O, "POST", "/appointments/book", {"name": "QA Visit " + tag, "date": "2026-09-30", "time": "10:00", "status": "completed",
        "services": [{"name": "QA Facial", "price": 5000}], "received": 5000, "payment_method": "card"})
    if s == 200:
        track("appointments", v["appointment_id"]); track("customers", v["customer_id"])
        track("invoices", sql("SELECT invoice_id FROM appointments WHERE id='%s'" % v["appointment_id"])[0][0])
    s2, L = call(O, "GET", "/appointments/list?start=2026-09-30&end=2026-09-30&include_unlinked=true")
    ids = [x["id"] for x in L.get("appointments", [])]
    check("#2/#9", "Daily Entry visit saved and listed for the day", s == 200 and v["appointment_id"] in ids and "unlinked_bills" in L, "HTTP %s, %d visit(s) on the day" % (s, len(ids)))
    s, d = call(O, "PATCH", "/appointments/" + v["appointment_id"], {"notes": "edited visit"})
    check("#2/#9", "Daily Entry edit", s == 200, "HTTP %s" % s)
    # an unpaid one can be deleted and its bill is voided, not erased
    s, u = call(O, "POST", "/appointments/book", {"name": "QA Del " + tag, "date": "2026-09-30", "time": "09:00", "services": [{"name": "X", "price": 100}]})
    if s == 200:
        track("customers", u["customer_id"]); uinv = sql("SELECT invoice_id FROM appointments WHERE id='%s'" % u["appointment_id"])[0][0]; track("invoices", uinv)
        s, d = call(O, "DELETE", "/appointments/" + u["appointment_id"])
        st = sql("SELECT status, void_reason FROM invoices WHERE id='%s'" % uinv)[0]
        check("#2/#9", "Daily Entry delete (unpaid) voids the bill", s == 200 and st[0] == "void", "HTTP %s bill=%s" % (s, st))
    # #12 inventory
    s, p = call(O, "POST", "/business/products", {"name": "QA Serum " + tag, "sku": "QA-" + tag, "price": 3500, "track_stock": True, "low_stock_threshold": 3})
    pid = (p.get("item") or {}).get("id") if isinstance(p, dict) else None; track("products", pid)
    s2, m = call(O, "POST", "/business/stock_movements", {"product_id": pid, "kind": "opening", "quantity_delta": 12, "reason": "Opening stock"})
    s3, m2 = call(O, "POST", "/business/stock_movements", {"product_id": pid, "kind": "sale", "quantity_delta": -2})
    s4, inv_ = call(O, "GET", "/operations/inventory")
    row = next((x for x in inv_.get("products", []) if x["id"] == pid), {})
    check("#12", "add product + opening 12 - sale 2 = 10 on hand", s == 200 and s2 == 200 and s3 == 200 and float(row.get("on_hand", -1)) == 10, "HTTP %s/%s/%s on_hand=%s" % (s, s2, s3, row.get("on_hand")))
    s, e = call(O, "POST", "/business/products", {"name": "QA Serum " + tag})
    check("#12", "duplicate product name refused readably", s == 409, "HTTP %s %s" % (s, e.get("detail") if isinstance(e, dict) else e))
    s, e = call(O, "POST", "/business/products", {"name": "", "price": 1})
    check("#12", "empty name refused", s == 400, "HTTP %s" % s)
    s, e = call(ctx["other"], "GET", "/customers/%s/history" % cust) if ctx.get("other") else (None, None)
    if s is not None:
        check("RLS", "another tenant cannot read this customer", s == 404, "HTTP %s" % s)


def a4_readonly():
    rows = sql("SELECT i.number, i.issued_on, i.total, i.status, coalesce(i.notes,''), i.created_at::timestamp(0), "
               "coalesce(c.name,'(no customer)') FROM invoices i LEFT JOIN customers c ON c.id=i.customer_id "
               "WHERE i.tenant_id='%s' AND NOT EXISTS (SELECT 1 FROM appointments a WHERE a.invoice_id=i.id)" % IRFAN)
    appts = sql("SELECT count(*) FROM appointments WHERE tenant_id='%s'" % IRFAN)[0][0]
    print("\nA4 (read-only, Dr Irfan): appointments=%s; bills with no appointment:" % appts)
    for r in rows:
        print("   ", " | ".join(r))
    audit = sql("SELECT at::timestamp(0), action, resource, coalesce(actor,'') FROM executions WHERE tenant_id='%s' "
                "AND resource IN ('invoices','appointments','customers') ORDER BY at LIMIT 20" % IRFAN)
    print("  audit trail for those tables:")
    for r in audit:
        print("   ", " | ".join(r))


def main():
    phases = sys.argv[1:] or ["phase1"]
    qa = sql("SELECT id FROM tenants WHERE name = '%s'" % QA_NAME)
    if not qa:
        sys.exit("QA tenant '%s' not found" % QA_NAME)
    qa = qa[0][0]
    u = sql("SELECT id FROM users WHERE tenant_id='%s' ORDER BY (role='owner') DESC LIMIT 1" % qa)
    uid = u[0][0] if u else str(uuid.uuid4())
    other = sql("SELECT id FROM tenants WHERE is_test AND id <> '%s' LIMIT 1" % qa)
    ctx = {"qa": qa, "owner": token(qa, "owner", uid), "operator": token(qa, "operator", uid),
           "other": token(other[0][0], "owner", str(uuid.uuid4())) if other else None}
    before = counts(qa)
    try:
        for ph in phases:
            if ph == "a4":
                a4_readonly(); continue
            globals()[ph](ctx)
    finally:
        cleanup(qa)
        after = counts(qa)
        same = before == after
        check("DATA", "QA tenant row counts back to where they started", same,
              "before=%s after=%s" % (before, after) if not same else json.dumps(after))
    fails = [r for r in RESULTS if r[2] == "FAIL"]
    print("\n%d checks, %d failed" % (len(RESULTS), len(fails)))


if __name__ == "__main__":
    main()
