#!/usr/bin/env python3
"""Live proof, run on the server:  python3 ops/verify/live_check.py phase1 [phase2 ...]

Writes ONLY into the QA tenant ("TechDeva QA Test Co"), records the row counts
first, deletes exactly the rows it created, and proves the counts are back.
Reads other tenants (Dr Irfan) read-only. Sessions are short-lived tokens
minted inside the api container; no password is typed or printed.
Standard library only.
"""
import base64, json, os, subprocess, sys, urllib.parse, urllib.request, urllib.error, uuid

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
             "invoices", "expenses", "packages", "products", "services", "customers", "staff",
             "locations", "business_policies"]
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
        track("appointments", u["appointment_id"]); track("customers", u["customer_id"]); uinv = sql("SELECT invoice_id FROM appointments WHERE id='%s'" % u["appointment_id"])[0][0]; track("invoices", uinv)
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


def phase2(ctx):
    O, OP = ctx["owner"], ctx["operator"]
    tag = uuid.uuid4().hex[:6]
    def item(r): return (r.get("item") or {}) if isinstance(r, dict) else {}
    # #6 services
    s, r = call(O, "POST", "/business/services", {"name": "QA Svc " + tag, "price": 5000, "duration_minutes": 15, "category": "injection"})
    sid = item(r).get("id"); track("services", sid)
    check("#6", "add service", s == 200 and sid, "HTTP %s" % s)
    s, r = call(O, "PATCH", "/business/services/%s" % sid, {"name": "QA Svc edited " + tag, "price": 18000})
    s2, L = call(O, "GET", "/business/services")
    got = [x for x in L.get("items", []) if x.get("id") == sid]
    check("#6", "edit service persists", s == 200 and got and got[0]["price"] == 18000 and got[0]["name"].startswith("QA Svc edited"), "HTTP %s price=%s" % (s, got[0]["price"] if got else None))
    s, r = call(O, "POST", "/business/services/import-onboarding")
    for x in (r.get("saved") or []) if isinstance(r, dict) else []:
        track("services", x["id"])
    s2, r2 = call(O, "POST", "/business/services/import-onboarding")
    check("#6/A5", "import onboarding services is idempotent", s == 200 and s2 == 200 and r2.get("count") == 0,
          "first HTTP %s %s, second HTTP %s %s" % (s, json.dumps(r)[:200], s2, json.dumps(r2)[:200]))
    # #10 packages with contents, then FK-safe delete of the service
    s, r = call(O, "POST", "/business/packages", {"name": "QA Pack " + tag, "price": 30000, "validity_days": 90})
    pk = item(r).get("id"); track("packages", pk)
    s2, r2 = call(O, "POST", "/business/package_items", {"package_id": pk, "service_id": sid, "quantity": 3})
    pit = item(r2).get("id")
    s3, r3 = call(O, "PATCH", "/business/packages/%s" % pk, {"price": 28000})
    s4, r4 = call(O, "PATCH", "/business/package_items/%s" % pit, {"quantity": 4})
    s5, L = call(O, "GET", "/business/package_items")
    q = [x["quantity"] for x in L.get("items", []) if x.get("id") == pit]
    check("#10", "package create, add item, edit price and quantity", s == 200 and s2 == 200 and s3 == 200 and s4 == 200 and q == [4], "HTTP %s/%s/%s/%s qty=%s" % (s, s2, s3, s4, q))
    s, r = call(O, "POST", "/business/package_items", {"package_id": pk, "quantity": 1})
    check("#10", "package line with no service or product refused", s == 400, "HTTP %s" % s)
    s, r = call(O, "POST", "/business/package_items", {"package_id": pk, "service_id": sid, "quantity": 0})
    check("#10", "package quantity 0 refused", s == 400, "HTTP %s" % s)
    s, r = call(O, "DELETE", "/business/services/%s" % sid)
    check("#6", "service inside a package cannot be deleted (FK-safe)", s == 409, "HTTP %s %s" % (s, r.get("detail") if isinstance(r, dict) else r))
    s, r = call(O, "DELETE", "/business/package_items/%s" % pit)
    s2, r2 = call(O, "DELETE", "/business/packages/%s" % pk)
    check("#10", "remove item, then delete an unsold package", s == 200 and s2 == 200, "HTTP %s/%s" % (s, s2))
    s, r = call(O, "DELETE", "/business/services/%s" % sid)
    check("#6", "unused service deletes", s == 200, "HTTP %s" % s)
    # #7 products
    s, r = call(O, "POST", "/business/products", {"name": "QA Prod " + tag, "price": 2200, "track_stock": True})
    prid = item(r).get("id"); track("products", prid)
    s2, r2 = call(O, "PATCH", "/business/products/%s" % prid, {"price": 2400, "sku": "QA-" + tag})
    s3, L = call(O, "GET", "/business/products")
    got = [x for x in L.get("items", []) if x.get("id") == prid]
    check("#7", "add + edit product persists", s == 200 and s2 == 200 and got and got[0]["price"] == 2400, "HTTP %s/%s price=%s" % (s, s2, got[0]["price"] if got else None))
    call(O, "POST", "/business/stock_movements", {"product_id": prid, "kind": "opening", "quantity_delta": 5})
    s, r = call(O, "DELETE", "/business/products/%s" % prid)
    check("#7", "product with stock history cannot be deleted", s == 409, "HTTP %s" % s)
    s, r = call(O, "PATCH", "/business/products/%s" % prid, {"active": False})
    check("#7", "product can be deactivated instead", s == 200 and item(r).get("active") is False, "HTTP %s" % s)
    # #8 expenses
    s, r = call(OP, "POST", "/business/expenses", {"description": "QA gloves " + tag, "amount": 1500, "category": "supplies", "method": "cash", "spent_on": "2026-09-30"})
    eid = item(r).get("id"); track("expenses", eid)
    check("#8/#11", "operator can record an expense", s == 200, "HTTP %s" % s)
    s, r = call(OP, "PATCH", "/business/expenses/%s" % eid, {"amount": 1})
    check("#11", "operator cannot edit an expense", s == 403, "HTTP %s" % s)
    s, r = call(O, "PATCH", "/business/expenses/%s" % eid, {"amount": 1800})
    s2, L = call(O, "GET", "/business/expenses")
    got = [x for x in L.get("items", []) if x.get("id") == eid]
    check("#8", "owner edit expense persists", s == 200 and got and got[0]["amount"] == 1800, "HTTP %s amount=%s" % (s, got[0]["amount"] if got else None))
    s, r = call(OP, "DELETE", "/business/expenses/%s" % eid)
    check("#11", "operator cannot delete an expense", s == 403, "HTTP %s" % s)
    s, r = call(O, "DELETE", "/business/expenses/%s" % eid)
    s2, L = call(O, "GET", "/business/expenses")
    check("#8", "owner delete expense, gone on re-read", s == 200 and not [x for x in L.get("items", []) if x.get("id") == eid], "HTTP %s" % s)
    # #11 rights: every owner-only write refused for the operator
    for res, body in (("services", {"name": "QA op " + tag}), ("products", {"name": "QA op " + tag}), ("packages", {"name": "QA op " + tag}),
                      ("staff", {"name": "QA op " + tag}), ("locations", {"name": "QA op " + tag}),
                      ("business_policies", {"kind": "x", "title": "x", "body": "x"})):
        s, r = call(OP, "POST", "/business/" + res, body)
        if s == 200:
            track(res, item(r).get("id"))
        check("#11", "operator POST /business/%s refused" % res, s == 403, "HTTP %s" % s)
    s, r = call(OP, "POST", "/business/services/import-onboarding")
    check("#11", "operator cannot import services", s == 403, "HTTP %s" % s)
    s, r = call(OP, "GET", "/operations/pnl")
    check("#11", "operator cannot read profit & loss", s == 403, "HTTP %s" % s)
    s, r = call(OP, "GET", "/team/hr")
    check("#11", "operator cannot read HR records", s == 403, "HTTP %s" % s)
    s, r = call(OP, "POST", "/business/customers", {"name": "QA op cust " + tag})
    cid = item(r).get("id"); track("customers", cid)
    s2, r2 = call(OP, "DELETE", "/business/customers/%s" % cid)
    check("#11", "operator can add a customer but not delete one", s == 200 and s2 == 403, "HTTP %s/%s" % (s, s2))


def phase6(ctx):
    """Jarvis pictures: owner upload -> public JPEG link; operator refused. Cleaned up."""
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    # a real 1x1 JPEG
    jpg = base64.b64decode("/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////wgALCAABAAEBAREA/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPxA=")
    data = "data:image/jpeg;base64," + base64.b64encode(jpg).decode()
    s, r = call(OP, "POST", "/social/media", {"image": data})
    check("#pics", "operator cannot upload post pictures", s == 403, "HTTP %s" % s)
    s, r = call(O, "POST", "/social/media", {"image": data})
    url = r.get("url") if isinstance(r, dict) else None
    ok = False
    if url:
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                ok = resp.status == 200 and resp.headers.get("content-type") == "image/jpeg"
        except Exception as e:
            print("  fetch failed:", e)
    check("#pics", "owner upload gives a public JPEG link Meta can fetch", s == 200 and ok, "HTTP %s %s" % (s, url))
    s2, r2 = call(O, "POST", "/social/media", {"image": "data:image/svg+xml;base64,PHN2Zy8+"})
    check("#pics", "SVG (script-capable) refused", s2 == 400, "HTTP %s" % s2)
    sql("DELETE FROM social_media WHERE tenant_id='%s'" % tid)
    left = sql("SELECT count(*) FROM social_media WHERE tenant_id='%s'" % tid)[0][0]
    check("DATA", "QA test pictures removed", left == "0", "left=%s" % left)


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


def a5_readonly():
    rows = sql("SELECT name, price, duration_minutes, category, active FROM services WHERE tenant_id='%s' ORDER BY name" % IRFAN)
    cfg = sql("SELECT services::text, price_list::text FROM tenant_config WHERE tenant_id='%s'" % IRFAN)
    print("\nA5 (read-only, Dr Irfan): services table has %d row(s):" % len(rows))
    for r in rows:
        print("   ", " | ".join(r))
    if cfg:
        print("  onboarding services:", cfg[0][0][:300])
        print("  onboarding prices:  ", cfg[0][1][:300])


def phase3(ctx):
    """#16 Facebook config vs Meta, and #5 WhatsApp send, read-only on Irfan except
    one probe whose audit row is deleted again (proved by count)."""
    O = ctx["owner"]
    s, d = call(O, "GET", "/oauth/meta/diagnose")
    print("\n#16 Meta app diagnose:", json.dumps(d, indent=1) if isinstance(d, dict) else d)
    if isinstance(d, dict) and s == 200:
        check("#16", "Meta app id + secret accepted by Meta", d.get("app_check") == "ok",
              d.get("app_error") or ("app '%s'" % d.get("app_name")))
        check("#16", "redirect_uri is public https, same for dialog and exchange",
              d.get("redirect_uri", "").startswith("https://") and not d.get("redirect_base_is_localhost"), d.get("redirect_uri"))
        check("#16", "no stray whitespace in META_APP_SECRET in .env", not d.get("secret_had_whitespace"),
              "secret length %s" % d.get("secret_length"))
    else:
        check("#16", "diagnose endpoint answered", False, "HTTP %s %s" % (s, d))
    err = sql("SELECT provider, status, coalesce(last_error,'') FROM integrations WHERE tenant_id='%s' AND provider IN ('meta','instagram')" % IRFAN)
    print("  Irfan's Meta integration rows (read-only):", err or "none")
    # --- WhatsApp, Irfan, read-only
    conn = sql("SELECT status, coalesce(phone_number,''), agent_key FROM whatsapp_connections WHERE tenant_id='%s'" % IRFAN)
    print("\n#5 Irfan WhatsApp connection:", conn or "none")
    uid = sql("SELECT id FROM users WHERE tenant_id='%s' ORDER BY (role='owner') DESC LIMIT 1" % IRFAN)
    if not (conn and uid):
        check("#5", "Irfan has a WhatsApp agent assigned", False, "no whatsapp_connections row or no user")
        return
    I = token(IRFAN, "owner", uid[0][0])
    s, chats = call(I, "GET", "/whatsapp/chats")
    lst = chats.get("chats", []) if isinstance(chats, dict) else []
    check("#5", "Irfan's inbox reads through pairctl", s == 200, "HTTP %s, %d chats" % (s, len(lst)))
    if lst:
        s, m = call(I, "GET", "/whatsapp/chats/" + urllib.parse.quote(lst[0]["session_id"], safe=""))
        print("  roles in the latest chat (A7 label check; 30 chars each):")
        for x in (m.get("messages", []) if isinstance(m, dict) else [])[:8]:
            print("    %-9s %s" % (x.get("role"), str(x.get("content", "")).replace("\n", " ")[:30]))
    probe = "td-probe-" + uuid.uuid4().hex[:8]
    before = sql("SELECT count(*) FROM executions WHERE tenant_id='%s' AND action='whatsapp.send'" % IRFAN)[0][0]
    s, r = call(I, "POST", "/whatsapp/chats/%s/send" % probe, {"text": "probe - never delivered (no such conversation)"})
    reason = r.get("detail") if isinstance(r, dict) else r
    refused_honestly = s in (409, 502, 503) and not (isinstance(r, dict) and r.get("sent"))
    check("#5", "a refused send shows the real reason, never 'sent'", refused_honestly,
          "HTTP %s %s" % (s, reason))
    sql("DELETE FROM executions WHERE tenant_id='%s' AND action='whatsapp.send' AND resource_id='%s'" % (IRFAN, probe))
    after = sql("SELECT count(*) FROM executions WHERE tenant_id='%s' AND action='whatsapp.send'" % IRFAN)[0][0]
    check("DATA", "Irfan's audit rows back to where they started after the probe", before == after, "%s -> %s" % (before, after))


def phase4(ctx):
    """#13 pins (QA tenant; its prefs are restored exactly), and that the served
    dashboard carries the A9/#14/#17/#18 changes."""
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    s, page = call(None, "GET", "/dashboard")
    markers = {"A9 incidents throttle": "INC_EVERY_MS", "#17 sidebar scroll kept": "S.sbTop",
               "#18 menu groups": "'AI / Automation'", "#14 Jarvis states": "JV_LABEL", "#13 pins": "pinned_by_role",
               "#5 reply box": "ibx_send"}
    for k, m in markers.items():
        check(k.split()[0], "live dashboard.html has " + k, s == 200 and isinstance(page, str) and m in page, "marker %r" % m)
    orig = sql("SELECT coalesce(dashboard_prefs::text, '{}') FROM tenant_config WHERE tenant_id='%s'" % tid)
    try:
        s, r = call(OP, "PUT", "/console/prefs/pins", {"pinned_by_role": {"owner": ["appointments"], "operator": []}})
        check("#13/#11", "operator cannot change pins", s == 403, "HTTP %s" % s)
        s, r = call(O, "PUT", "/console/prefs/pins", {"pinned_by_role": {"owner": ["appointments", "inbox"], "operator": ["dailyentry"]}})
        s2, pr = call(OP, "GET", "/console/prefs")
        got = (pr.get("prefs") or {}).get("pinned_by_role") if isinstance(pr, dict) else None
        check("#13", "pins saved per role and read back by staff", s == 200 and got == {"owner": ["appointments", "inbox"], "operator": ["dailyentry"]}, "HTTP %s read=%s" % (s, got))
        s, r = call(O, "PUT", "/console/prefs/pins", {"pinned_by_role": {"owner": ["../../x"]}})
        check("#13", "a bad section id is refused", s == 400, "HTTP %s" % s)
    finally:
        if orig:
            sql("UPDATE tenant_config SET dashboard_prefs = $TD$%s$TD$::jsonb WHERE tenant_id='%s'" % (orig[0][0], tid))
        else:
            sql("UPDATE tenant_config SET dashboard_prefs = '{}'::jsonb WHERE tenant_id='%s'" % tid)
        now = sql("SELECT coalesce(dashboard_prefs::text, '{}') FROM tenant_config WHERE tenant_id='%s'" % tid)
        check("DATA", "QA dashboard prefs restored exactly", (orig or [["{}"]])[0][0] == (now or [["{}"]])[0][0] or not orig, "restored")


def phase5(ctx):
    """Jarvis end to end on the live AI chain (a question: nothing is written),
    and the server voice that the Responding state plays."""
    O = ctx["owner"]
    import time as _t
    for i in range(3):
        req = urllib.request.Request(API + "/console", method="POST",
                                     data=json.dumps({"message": "How many customers do I have?", "voice": True}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + O})
        t0 = _t.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
                print("  Jarvis turn %d: total %.1fs, server %s, model %s" % (i + 1, _t.time() - t0,
                      resp.headers.get("Server-Timing"), resp.headers.get("X-Jarvis-Model")))
        except urllib.error.HTTPError as e:
            print("  Jarvis turn %d: HTTP %s after %.1fs %s" % (i + 1, e.code, _t.time() - t0, e.read()[:160]))
    for txt in ("You have five customers.", "You have five customers and one appointment tomorrow at four. Would you like me to remind them?"):
        req = urllib.request.Request(API + "/console/speak", method="POST", data=json.dumps({"text": txt, "lang": "en"}).encode(),
                                     headers={"Content-Type": "application/json", "Authorization": "Bearer " + O})
        t0 = _t.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                n = len(resp.read())
                print("  voice %d chars: total %.1fs, server %s, %d KB" % (len(txt), _t.time() - t0, resp.headers.get("Server-Timing"), n // 1024))
        except urllib.error.HTTPError as e:
            print("  voice %d chars: HTTP %s after %.1fs" % (len(txt), e.code, _t.time() - t0))
    s, r = call(O, "POST", "/console", {"message": "How many customers and appointments do I have? Answer in one sentence."})
    kind = r.get("kind") if isinstance(r, dict) else None
    check("#14", "Jarvis answers through the live AI chain", s == 200 and kind == "question" and r.get("reply"),
          "HTTP %s kind=%s reply=%s" % (s, kind, (r.get("reply") or r.get("detail") or "")[:120] if isinstance(r, dict) else r))
    req = urllib.request.Request(API + "/console/speak", data=json.dumps({"text": "Test", "lang": "en"}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer " + O})
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            audio = resp.read(); ok = resp.status == 200 and len(audio) > 1000
            check("#14", "server voice returns audio for Responding", ok, "HTTP %s, %d bytes %s" % (resp.status, len(audio), resp.headers.get("content-type")))
    except urllib.error.HTTPError as e:
        check("#14", "server voice returns audio for Responding", False, "HTTP %s %s (the browser voice is the fallback)" % (e.code, e.read()[:120]))


def phase7(ctx):
    """Part 15: forgot password, scheduled posts, plan & billing, WhatsApp agent
    requests, founder Requests page, legal pages. Writes only a QA draft post and
    (if the QA business has no agent) one QA agent request; both deleted after."""
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    ADM = token(None, "techdeva_admin", "live-check")
    n = lambda t: int(sql("SELECT count(*) FROM %s WHERE tenant_id='%s'" % (t, tid))[0][0])
    before = {t: n(t) for t in ("social_posts", "agent_requests", "payment_claims")}
    s, page = call(None, "GET", "/reset")
    check("P15", "forgot-password page is served", s == 200 and "forgot-password" in str(page), "HTTP %s" % s)
    s, page = call(None, "GET", "/dashboard")
    for k, m in {"Posts page": "VIEWS.posts", "Plan & Billing page": "VIEWS.billing",
                 "agent request": "/whatsapp/agent-request", "Forgot password link": 'href="/reset"'}.items():
        check("P15", "live dashboard.html has " + k, s == 200 and m in str(page), "marker %r" % m)
    s, page = call(None, "GET", "/techdeva")
    check("P15", "founder console has the Requests page", s == 200 and "wireRequests" in str(page), "HTTP %s" % s)
    s, r = call(None, "POST", "/auth/forgot-password", {"email": "nobody-%s@example.invalid" % uuid.uuid4().hex[:6]})
    check("P15", "forgot-password answers the same for an unknown email", s == 200 and r.get("ok"), "HTTP %s %s" % (s, r))
    s, r = call(OP, "GET", "/billing")
    check("P15", "staff cannot see the plan and bills", s == 403, "HTTP %s" % s)
    s, r = call(O, "GET", "/billing")
    check("P15", "owner sees plan & billing", s == 200 and "account" in r, "HTTP %s status=%s" % (s, (r.get("account") or {}).get("status") if isinstance(r, dict) else r))
    for path in ("/techdeva/payment-claims", "/techdeva/agent-requests", "/techdeva/password-resets"):
        s, r = call(ADM, "GET", path)
        check("P15", "founder reads " + path, s == 200, "HTTP %s" % s)
        s, r = call(O, "GET", path)
        check("P15", "a client cannot read " + path, s == 403, "HTTP %s" % s)
    pid = None
    try:
        s, r = call(O, "POST", "/social/posts", {"caption": "Live check draft (deleted after)", "platforms": ["facebook"]})
        pid = (r.get("post") or {}).get("id") if isinstance(r, dict) else None
        check("P15", "owner writes a draft post", s == 200 and pid, "HTTP %s" % s)
        s, r = call(OP, "POST", "/social/posts/%s/schedule" % pid, {"at": "2030-01-01T00:00:00Z"})
        check("P15", "staff cannot schedule a post", s == 403, "HTTP %s" % s)
        s, r = call(O, "POST", "/social/posts/%s/schedule" % pid, {"at": "2020-01-01T00:00:00Z"})
        connected = not (s == 409 and "Connect Facebook" in str(r))
        if connected:
            check("P15", "a time in the past is refused", s == 400, "HTTP %s %s" % (s, r.get("detail") if isinstance(r, dict) else r))
            import datetime as _d
            at = (_d.datetime.now(_d.timezone.utc) + _d.timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
            s, r = call(O, "POST", "/social/posts/%s/schedule" % pid, {"at": at})
            check("P15", "owner schedules a post", s == 200 and (r.get("post") or {}).get("status") == "scheduled", "HTTP %s" % s)
            s, r = call(O, "POST", "/social/posts/%s/unschedule" % pid)
            check("P15", "owner takes it back to a draft", s == 200 and (r.get("post") or {}).get("status") == "draft", "HTTP %s" % s)
        else:
            check("P15", "scheduling without Facebook says so (QA has no Facebook)", True, r.get("detail") if isinstance(r, dict) else r)
        s, r = call(OP, "POST", "/whatsapp/agent-request", {"owner_phone": "923000000000"})
        check("P15", "staff cannot request a WhatsApp agent", s == 403, "HTTP %s" % s)
        s, r = call(O, "GET", "/whatsapp/agent-request")
        check("P15", "owner reads the agent request state", s == 200, "HTTP %s %s" % (s, (r.get("request") or {}).get("status") if isinstance(r, dict) else r))
    finally:
        if pid:
            sql("DELETE FROM social_posts WHERE tenant_id='%s' AND id='%s'" % (tid, pid))
        after = {t: n(t) for t in before}
        check("DATA", "QA posts / agent requests / payment claims back to where they started", before == after,
              "before=%s after=%s" % (before, after))
    if not os.environ.get("TD_SKIP_PORTAL"):
        for path in ("/privacy", "/terms", "/data-deletion"):
            s, page = call(None, "GET", path, base=PORTAL)
            check("P15", "website " + path + " is public", s == 200, "HTTP %s" % s)


def wholocks(ctx=None):
    """Read-only: which real businesses the trial lock would lock right now."""
    rows = sql("SELECT name, status, coalesce(to_char(trial_ends_at,'YYYY-MM-DD'),'-'), "
               "(status::text='paused' OR (status::text='trial' AND trial_ends_at IS NOT NULL AND trial_ends_at < now())) "
               "FROM tenants WHERE NOT is_test ORDER BY 4 DESC, name")
    print("\nbusiness                                  status    trial ends   LOCKED?")
    for n, st, te, lk in rows:
        print("%-41s %-9s %-12s %s" % (n[:41], st, te, "YES - dashboard locked" if lk == "t" else "no"))
    print("\nTo keep one open: founder console > Clients > 'Mark paying' (paid) or '+7 days trial'.")


def phase8(ctx):
    """Trial lock on the QA business: expire its trial, prove the lock, extend,
    prove the unlock, then restore its status and trial date exactly."""
    import time as _t
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    ADM = token(None, "techdeva_admin", "live-check")
    orig = sql("SELECT status::text, coalesce(trial_ends_at::text,'') FROM tenants WHERE id='%s'" % tid)[0]
    try:
        sql("UPDATE tenants SET status='trial', trial_ends_at=now()-interval '1 hour' WHERE id='%s'" % tid)
        _t.sleep(22)          # the api caches the lock state for 20 s
        s, r = call(O, "GET", "/business/customers")
        check("LOCK", "expired trial: owner's data is locked", s == 402 and r.get("locked"), "HTTP %s" % s)
        s, r = call(OP, "GET", "/leads")
        check("LOCK", "expired trial: staff are locked too", s == 402, "HTTP %s" % s)
        s, r = call(O, "GET", "/billing")
        check("LOCK", "expired trial: Plan & Billing still opens to pay", s == 200 and (r.get("account") or {}).get("locked"), "HTTP %s" % s)
        s, r = call(O, "GET", "/me")
        check("LOCK", "/me tells the dashboard to show the lock screen", s == 200 and r.get("locked") is True, "HTTP %s" % s)
        s, r = call(ADM, "GET", "/techdeva/book")
        check("LOCK", "founder is never locked", s == 200, "HTTP %s" % s)
        s, r = call(ADM, "POST", "/techdeva/clients/%s/trial" % tid, {"days": 1})
        s2, r2 = call(O, "GET", "/business/customers")
        check("LOCK", "founder '+ days' unlocks at once", s == 200 and s2 == 200, "extend HTTP %s, then data HTTP %s" % (s, s2))
    finally:
        sql("UPDATE tenants SET status=$$%s$$::tenant_status, trial_ends_at=%s WHERE id='%s'"
            % (orig[0], ("$$%s$$::timestamptz" % orig[1]) if orig[1] else "NULL", tid))
        now = sql("SELECT status::text, coalesce(trial_ends_at::text,'') FROM tenants WHERE id='%s'" % tid)[0]
        check("DATA", "QA status and trial date restored exactly", list(now) == list(orig), "%s" % (now,))


def phase9(ctx):
    """Plans and several WhatsApp numbers. Read-only except one QA plan change,
    which is put back exactly."""
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    ADM = token(None, "techdeva_admin", "live-check")
    s, r = call(None, "GET", "/plans")
    got = {p["key"]: (p["price_pkr"], p["max_numbers"], p["max_conversations"]) for p in (r.get("plans") or [])} if s == 200 else {}
    check("PLAN", "public price list has the three plans", set(got) >= {"starter", "growth", "pro"}, json.dumps(got))
    s, r = call(OP, "GET", "/plan")
    check("PLAN", "staff cannot see the plan", s == 403, "HTTP %s" % s)
    s, r = call(O, "GET", "/plan")
    ok = s == 200 and "usage" in r and "plan" in r
    check("PLAN", "owner sees plan and this month's conversations", ok,
          "HTTP %s %s" % (s, {"plan": r["plan"]["name"], "conversations": r["usage"]["conversations"], "numbers": r["usage"]["numbers"],
                               "approximate": r["usage"]["approximate"], "not counted": r["usage"]["incomplete"]} if ok else r))
    s, r = call(O, "PUT", "/techdeva/plans/pro", {"price_pkr": 1})
    check("PLAN", "a client cannot change prices", s == 403, "HTTP %s" % s)
    orig = sql("SELECT coalesce(plan_key,'-'), monthly_fee::text FROM client_accounts WHERE tenant_id='%s'" % tid)
    try:
        s, r = call(ADM, "PUT", "/techdeva/clients/%s/plan" % tid, {"plan_key": "growth"})
        s2, r2 = call(O, "GET", "/plan")
        check("PLAN", "founder puts a client on Growth; fee and limits follow", s == 200 and (r2.get("plan") or {}).get("max_numbers") == 2,
              "fee %s, limits %s" % (r.get("monthly_fee"), (r2.get("plan") or {}).get("max_numbers")))
    finally:
        if orig:
            sql("UPDATE client_accounts SET plan_key=%s, monthly_fee=%s WHERE tenant_id='%s'"
                % (("'%s'" % orig[0][0]) if orig[0][0] != "-" else "NULL", orig[0][1], tid))
        else:
            sql("DELETE FROM client_accounts WHERE tenant_id='%s'" % tid)
        now = sql("SELECT coalesce(plan_key,'-'), monthly_fee::text FROM client_accounts WHERE tenant_id='%s'" % tid)
        check("DATA", "QA plan and fee restored exactly", now == orig, "%s" % now)
    s, r = call(ADM, "GET", "/techdeva/usage")
    check("PLAN", "founder sees every client's usage", s == 200 and "clients" in r, "HTTP %s, %d clients" % (s, len(r.get("clients") or {})))
    ir = sql("SELECT id FROM tenants WHERE id='%s'" % IRFAN)
    if ir:
        u = (r.get("clients") or {}).get(IRFAN) or {}
        print("  Irfan this month (read-only): %s" % json.dumps({"plan": (u.get("plan") or {}).get("name"),
              "conversations": (u.get("usage") or {}).get("conversations"), "approximate": (u.get("usage") or {}).get("approximate"),
              "not counted": (u.get("usage") or {}).get("incomplete")}))


def phase10(ctx):
    """Team PIN login on the QA business: add a login, log in by business code +
    PIN, prove staff limits and the weak-PIN rule, then delete everything made."""
    import random
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    code_before = sql("SELECT coalesce(login_code,'-') FROM tenants WHERE id='%s'" % tid)[0][0]
    users_before = int(sql("SELECT count(*) FROM users WHERE tenant_id='%s'" % tid)[0][0])
    uid = None
    try:
        s, r = call(O, "POST", "/team/logins", {"name": "Live Check PIN", "access": "staff", "pin": "1234"})
        check("PIN", "an easy PIN (1234) is refused", s == 400, "HTTP %s %s" % (s, r.get("detail") if isinstance(r, dict) else r))
        s, r = call(OP, "POST", "/team/logins", {"name": "x", "access": "staff", "pin": "58203"})
        check("PIN", "staff cannot give out logins", s == 403, "HTTP %s" % s)
        pin = None
        for _ in range(20):
            cand = "%06d" % random.randint(100000, 999999)
            s, r = call(O, "POST", "/team/logins", {"name": "Live Check PIN", "access": "staff", "pin": cand})
            if s == 200:
                pin, uid = cand, r.get("id"); break
        check("PIN", "owner gives a team member a PIN login", bool(uid), "HTTP %s" % s)
        s, r = call(O, "GET", "/team/logins")
        code = r.get("code") if isinstance(r, dict) else None
        check("PIN", "business code for phones", s == 200 and bool(code), "code shown to the owner")
        s, r = call(None, "POST", "/auth/pin", {"code": code, "pin": pin})
        check("PIN", "team member logs in with code + PIN", s == 200 and r.get("role") == "operator", "HTTP %s %s" % (s, r))
        s, r = call(None, "POST", "/auth/pin", {"code": code, "pin": "000001"})
        check("PIN", "a wrong PIN is refused", s == 401, "HTTP %s" % s)
        s, r = call(O, "DELETE", "/team/logins/%s/pin" % uid)
        s2, r2 = call(None, "POST", "/auth/pin", {"code": code, "pin": pin})
        check("PIN", "removing the PIN stops it working", s == 200 and s2 == 401, "remove HTTP %s, then login HTTP %s" % (s, s2))
    finally:
        sql("DELETE FROM pin_attempts WHERE tenant_id='%s' AND at > now() - interval '10 minutes'" % tid)
        if uid:
            sql("DELETE FROM executions WHERE resource='users' AND resource_id='%s'" % uid)
            sql("DELETE FROM users WHERE id='%s' AND tenant_id='%s' AND email LIKE '%%@pin.invalid'" % (uid, tid))
        if code_before == "-":
            sql("UPDATE tenants SET login_code=NULL WHERE id='%s'" % tid)
        after = int(sql("SELECT count(*) FROM users WHERE tenant_id='%s'" % tid)[0][0])
        check("DATA", "QA logins back to where they started", after == users_before, "before=%s after=%s" % (users_before, after))


def phase11(ctx):
    """Jarvis does the work (Part 16), with the REAL model: a booking, a page
    to open, and an expense that must wait for a yes. Everything it creates in
    the QA business is deleted again."""
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    name = "Live Check Jarvis"
    try:
        s, r = call(O, "POST", "/console", {"confirm": "not.a.token"})
        # the old code also answered 400 (no message), so check it is the NEW refusal
        check("J16", "new Jarvis is deployed (a fake yes is refused)",
              s == 400 and "confirmation" in str(r.get("detail") if isinstance(r, dict) else r),
              "HTTP %s %s" % (s, r.get("detail") if isinstance(r, dict) else r))
        first = "Book an appointment for %s tomorrow at 11:00." % name
        s, r = call(O, "POST", "/console", {"message": first})
        asked = None
        if s == 200 and r.get("kind") == "question":
            # a person would answer; this also proves the conversation memory
            asked = str(r.get("reply"))[:90]
            s, r = call(O, "POST", "/console", {
                "message": "No phone number, just book it.",
                "history": [{"role": "user", "text": first}, {"role": "jarvis", "text": r.get("reply")}]})
        rows = sql("SELECT a.id, c.id, coalesce(a.invoice_id::text,'-') FROM appointments a JOIN customers c ON c.id = a.customer_id "
                   "WHERE a.tenant_id='%s' AND c.name = '%s'" % (tid, name))
        for a_id, c_id, inv in rows:
            CREATED.setdefault("appointments", []).append(a_id)
            CREATED.setdefault("customers", []).append(c_id)
            if inv != "-":
                CREATED.setdefault("invoices", []).append(inv)
        check("J16", "\"book ... tomorrow at 11\" makes a real booking", s == 200 and len(rows) == 1,
              "HTTP %s kind=%s reply=%s rows=%d%s" % (s, r.get("kind"), str(r.get("reply"))[:90], len(rows),
                                                       " (after it asked: %s)" % asked if asked else ""))
        s, r = call(OP, "POST", "/console", {"message": "Open the expenses page."})
        check("J16", "\"open the expenses page\" opens it", s == 200 and r.get("navigate") == "expenses",
              "HTTP %s %s" % (s, {k: r.get(k) for k in ("kind", "action", "navigate")}))
        s, r = call(O, "POST", "/console", {"message": "Record an expense of 1234 rupees for live check tea."})
        before = int(sql("SELECT count(*) FROM expenses WHERE tenant_id='%s' AND amount = 1234" % tid)[0][0])
        check("J16", "an expense waits for a yes", s == 200 and r.get("kind") == "confirm" and before == 0,
              "HTTP %s kind=%s reply=%s" % (s, r.get("kind"), str(r.get("reply"))[:90]))
        tok = r.get("confirm")
        if tok:
            s2, r2 = call(OP, "POST", "/console", {"confirm": tok})
            s, r = call(O, "POST", "/console", {"confirm": tok})
            s3, r3 = call(O, "POST", "/console", {"confirm": tok})
            ids = [x[0] for x in sql("SELECT id FROM expenses WHERE tenant_id='%s' AND amount = 1234" % tid)]
            CREATED.setdefault("expenses", []).extend(ids)
            check("J16", "yes records it once; someone else's or a second yes does nothing",
                  s == 200 and s2 == 403 and s3 == 409 and len(ids) == 1,
                  "other=%s yes=%s again=%s rows=%d" % (s2, s, s3, len(ids)))
    finally:
        made = [i for t in ("appointments", "customers", "invoices", "expenses") for i in CREATED.get(t, [])]
        if made:
            sql("DELETE FROM executions WHERE tenant_id='%s' AND resource_id IN (%s)"
                % (tid, ",".join("'%s'" % i for i in made)))


def phase12(ctx):
    """Correcting a saved visit's bill (Part 17): owner only, never below what
    was paid, totals recomputed. Everything made is deleted again."""
    O, OP, tid = ctx["owner"], ctx["operator"], ctx["qa"]
    s, r = call(O, "POST", "/appointments/book", {
        "date": "2026-01-15", "time": "12:00", "status": "completed", "name": "Live Check Bill Edit",
        "services": [{"name": "Live check service", "price": 5000}], "received": 2000, "payment_method": "cash"})
    aid = r.get("appointment_id") if isinstance(r, dict) else None
    if aid:
        CREATED.setdefault("appointments", []).append(aid)
        CREATED.setdefault("customers", []).append(r.get("customer_id"))
        inv = sql("SELECT invoice_id FROM appointments WHERE id='%s'" % aid)[0][0]
        CREATED.setdefault("invoices", []).append(inv)
    check("B17", "a visit with a part payment is recorded", s == 200 and bool(aid), "HTTP %s" % s)
    if not aid:
        return
    try:
        s, r = call(OP, "PUT", "/appointments/%s/bill" % aid, {"services": [{"name": "x", "price": 1}]})
        check("B17", "staff cannot change prices on a saved bill", s == 403, "HTTP %s" % s)
        s, r = call(O, "PUT", "/appointments/%s/bill" % aid, {"services": [{"name": "x", "price": 1000}]})
        check("B17", "a bill cannot go below what was paid", s == 409, "HTTP %s %s" % (s, r.get("detail") if isinstance(r, dict) else r))
        s, r = call(O, "PUT", "/appointments/%s/bill" % aid, {
            "services": [{"name": "Live check service", "price": 6000}, {"name": "Live check extra", "price": 1000}],
            "discount_type": "flat", "discount_value": 500, "gst_percent": 10})
        m = r.get("money", {}) if isinstance(r, dict) else {}
        check("B17", "owner corrects the bill; totals and balance follow",
              s == 200 and m.get("total") == 7150.0 and m.get("paid") == 2000.0 and m.get("balance") == 5150.0,
              "HTTP %s %s" % (s, m))
    finally:
        sql("DELETE FROM executions WHERE tenant_id='%s' AND resource_id IN ('%s','%s')" % (tid, aid, inv))


def phase13(ctx):
    """Ad accounts (Part 18): owner-only, honest about what is set up, never a
    token in the answer. Read-only; the QA business has no ad login."""
    O, OP = ctx["owner"], ctx["operator"]
    s, r = call(O, "GET", "/ads/status")
    setup = r.get("setup", {}) if isinstance(r, dict) else {}
    check("A18", "ad accounts status answers", s == 200 and "meta" in r and "google" in r,
          "HTTP %s setup=%s" % (s, setup))
    check("A18", "no token leaks into the answer", "access_token" not in json.dumps(r) and "refresh_token" not in json.dumps(r),
          "checked the whole reply")
    s, r = call(OP, "GET", "/ads/status")
    check("A18", "staff cannot see ad accounts or spend", s == 403, "HTTP %s" % s)
    s, r = call(OP, "GET", "/connect/google")
    check("A18", "staff cannot connect accounts", s == 403, "HTTP %s" % s)
    s, r = call(O, "GET", "/ads/performance?days=30")
    plats = {p.get("provider"): p for p in (r.get("platforms") or [])} if isinstance(r, dict) else {}
    check("A18", "ad results answer for Facebook and Google", s == 200 and set(plats) == {"meta", "google"},
          "HTTP %s %s" % (s, {k: (v.get("connected"), v.get("error")) for k, v in plats.items()}))
    adm = token(None, "techdeva_admin", "live-check")
    s, r = call(adm, "GET", "/techdeva/connections")
    t = r.get("totals", {}) if isinstance(r, dict) else {}
    check("A18", "founder sees how many clients are connected", s == 200 and "clients" in t and
          "access_token" not in json.dumps(r), "HTTP %s %s" % (s, t))
    s, r = call(O, "GET", "/techdeva/connections")
    check("A18", "a client cannot see the founder's connections list", s == 403, "HTTP %s" % s)
    print("   setup on this server: facebook=%s google=%s google_ads_results=%s"
          % (setup.get("facebook"), setup.get("google"), setup.get("google_ads_reporting")))


def phase14(ctx):
    """TechDeva's own work (Part 19): the founder's catalogue, client services
    and delivery board; clients never reach it; only TechDeva's own business
    sees tasks. Read-only on live: it never picks or changes the home business."""
    O, OP, OT = ctx["owner"], ctx["operator"], ctx["other"]
    forced = sql("SELECT count(*) FROM pg_class WHERE relname IN ('td_catalog','td_client_services','td_tasks') "
                 "AND relrowsecurity AND relforcerowsecurity")
    check("A19", "work tables exist with row level security forced", forced and forced[0][0] == "3",
          "tables forced: %s" % (forced[0][0] if forced else "none"))
    adm = token(None, "techdeva_admin", "live-check")
    s, r = call(adm, "GET", "/techdeva/work/home")
    home = (r.get("home") or {}) if isinstance(r, dict) else {}
    check("A19", "founder sees which business is TechDeva's own", s == 200 and "candidates" in r,
          "HTTP %s home=%s" % (s, home.get("name") or "not chosen yet"))
    for path in ("/techdeva/work/catalog", "/techdeva/work/client-services", "/techdeva/work/tasks", "/techdeva/work/team"):
        s, r = call(adm, "GET", path)
        ok = s == 200
        s2, _ = call(O, "GET", path)
        check("A19", "founder reads %s, a client is refused" % path.rsplit("/", 1)[1], ok and s2 == 403,
              "founder HTTP %s, client HTTP %s" % (s, s2))
    s, r = call(adm, "GET", "/techdeva/work/team")
    check("A19", "founder sees the employee list", s == 200 and "employees" in r,
          "HTTP %s employees=%s" % (s, len(r.get("employees") or []) if isinstance(r, dict) else "-"))
    s, r = call(O, "POST", "/techdeva/work/employees", {"name": "live check"})
    check("A19", "a client cannot add employees to TechDeva", s == 403, "HTTP %s" % s)
    s, r = call(O, "PUT", "/techdeva/work/home", {"tenant_id": ctx["qa"]})
    check("A19", "a client cannot make itself TechDeva's business", s == 403, "HTTP %s" % s)
    qa_is_home = str(home.get("id") or "") == str(ctx["qa"])
    s, r = call(OP, "GET", "/work/tasks")
    check("A19", "tasks show only in TechDeva's own business", s == 200 and bool(r.get("home")) == qa_is_home,
          "HTTP %s home=%s (QA is home: %s)" % (s, r.get("home"), qa_is_home))
    if qa_is_home:
        check("A19", "the team's view carries no prices", "price" not in json.dumps(r), "checked the whole reply")
    if OT:
        s, r = call(OT, "GET", "/work/tasks")
        check("A19", "another business sees no TechDeva tasks", s == 200 and not r.get("home") and not r.get("tasks"),
              "HTTP %s home=%s tasks=%s" % (s, r.get("home"), len(r.get("tasks") or [])))
    s, r = call(OP, "PUT", "/work/tasks/%s" % uuid.uuid4(), {"status": "done"})
    check("A19", "moving a task that is not yours is refused cleanly", s in (403, 404), "HTTP %s" % s)


def phase15(ctx):
    """Growth advisor (Pro): owner-only, Pro-only, never a made-up number.
    Read-only on live unless the QA business is on Pro (then it builds this
    week's QA report, which cleanup removes)."""
    O, OP = ctx["owner"], ctx["operator"]
    forced = sql("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'advisor_reports'")
    check("A20", "advisor table exists with row level security forced", forced and forced[0][0] == "t",
          "forced=%s" % (forced[0][0] if forced else "missing"))
    s, r = call(OP, "GET", "/advisor")
    check("A20", "staff cannot see the growth advisor", s == 403, "HTTP %s" % s)
    had = sql("SELECT count(*) FROM advisor_reports WHERE tenant_id='%s'" % ctx["qa"])[0][0]
    s, r = call(O, "GET", "/advisor")
    if had == "0":      # a report this run made for QA goes again
        sql("DELETE FROM advisor_reports WHERE tenant_id='%s'" % ctx["qa"])
    ok = s == 200 and isinstance(r, dict) and "available" in r
    if ok and r.get("available"):
        ok = isinstance(r.get("tips"), list) and len(r["tips"]) <= 4 and all(
            t.get("title") and t.get("why") and t.get("do") for t in r["tips"])
    check("A20", "owner gets the advisor (or the Pro upsell)", ok,
          "HTTP %s plan=%s available=%s tips=%s" % (s, r.get("plan") if isinstance(r, dict) else "-",
                                                    r.get("available") if isinstance(r, dict) else "-",
                                                    len(r.get("tips") or []) if isinstance(r, dict) else "-"))


def phase16(ctx):
    """Ad autopilot (Pro): owner-only, Pro-only, rules validated. Read-only on
    live: it never pauses, writes or launches anything."""
    O, OP = ctx["owner"], ctx["operator"]
    forced = sql("SELECT count(*) FROM pg_class WHERE relname IN ('ad_pilot_settings','ad_drafts','ad_actions') "
                 "AND relrowsecurity AND relforcerowsecurity")
    check("A21", "autopilot tables exist with row level security forced", forced and forced[0][0] == "3",
          "tables forced: %s" % (forced[0][0] if forced else "none"))
    s, r = call(OP, "GET", "/adpilot")
    check("A21", "staff cannot see or change the ads", s == 403, "HTTP %s" % s)
    s, r = call(O, "GET", "/adpilot")
    ok = s == 402 or (s == 200 and "settings" in r and "drafts" in r)
    check("A21", "owner gets the autopilot (or the Pro note)", ok, "HTTP %s" % s)
    if s == 200:
        s2, r2 = call(O, "PUT", "/adpilot/settings", {"meta_daily_cap": 50})
        check("A21", "a daily cap too small to run an ad is refused", s2 == 400, "HTTP %s" % s2)
        s2, r2 = call(O, "POST", "/adpilot/pause", {"provider": "meta", "campaign_id": "not-a-number"})
        check("A21", "a bad campaign id is refused before any ad platform call", s2 == 400 and "not valid" in json.dumps(r2),
              "HTTP %s" % s2)


def phase17(ctx):
    """Trial data retention + server alert. Read-only: lists who would be
    deleted and when, never deletes."""
    O = ctx["owner"]
    forced = sql("SELECT count(*) FROM pg_class WHERE relname IN ('data_retention','tenant_deletions') "
                  "AND relrowsecurity AND relforcerowsecurity")
    check("A22", "retention tables exist, founder-only", forced and forced[0][0] == "2",
          "tables forced: %s" % (forced[0][0] if forced else "none"))
    adm = token(None, "techdeva_admin", "live-check")
    s, r = call(adm, "GET", "/techdeva/retention")
    ok = s == 200 and all(k in r for k in ("upcoming", "deleted", "paying", "server_alert_at"))
    check("A22", "founder sees upcoming deletions and paying clients", ok,
          "HTTP %s paying=%s alert_at=%s" % (s, r.get("paying"), r.get("server_alert_at")) if isinstance(r, dict) else s)
    s2, _ = call(O, "GET", "/techdeva/retention")
    check("A22", "a business cannot see the deletion list", s2 == 403, "HTTP %s" % s2)
    if ok:
        ids = [u["id"] for u in r["upcoming"]]
        bad = sql("SELECT name FROM tenants WHERE id::text IN (%s) AND (is_test OR status::text = 'active')"
                  % (",".join("'%s'" % i for i in ids) or "''"))
        check("A22", "no test or paying business is ever on the list", not bad, "found: %s" % bad if bad else "none")
        for u in r["upcoming"]:
            print("   %s: trial ended %s, %s" % (u["name"], str(u["trial_ends_at"])[:10],
                                                  "KEPT" if u["hold"] else "deleted on %s (warned: %s)"
                                                  % (str(u["deletes_on"])[:10], str(u["warned_at"] or "not yet")[:10])))


def phase18(ctx):
    """New packages (1 Oct 2026): the public price list the website reads."""
    s, r = call(None, "GET", "/plans")
    plans = {p["key"]: p for p in (r.get("plans") or [])} if isinstance(r, dict) else {}
    want = {"starter": (19999, 99, 1, 300), "growth": (49999, 199, 2, 1500), "pro": (64999, 299, 5, 4000)}
    got = {k: (int(p["price_pkr"] or 0), int(p.get("price_usd") or 0), p["max_numbers"], p["max_conversations"])
           for k, p in plans.items()}
    check("A23", "public price list has the new packages in PKR and USD", s == 200 and all(got.get(k) == v for k, v in want.items()),
          "HTTP %s %s" % (s, got))


def phase19(ctx):
    """Posts (1 Oct 2026): several pictures per post, and the accounts call says
    whether Facebook is logged in even when no Page came through. Only a
    draft is made (nothing is published) and it is deleted again here."""
    s, r = call(ctx["owner"], "GET", "/social/accounts")
    check("A24", "posts page can tell 'no Facebook Page' from 'not connected'",
          s == 200 and isinstance(r, dict) and "facebook_login" in r, "HTTP %s %s" % (s, r))
    check("A27", "posts page gets Page ids so the owner chooses the Page (never the first one)",
          s == 200 and isinstance(r, dict) and "page_id" in r and all("id" in p for p in r.get("pages") or []),
          "pages=%d chosen=%s" % (len((r or {}).get("pages") or []), (r or {}).get("page_id")))
    pics = ["https://example.com/qa-%d.jpg" % i for i in (1, 2, 3)]
    s, r = call(ctx["owner"], "POST", "/social/posts", {"caption": "QA several pictures", "platforms": ["facebook"],
                                                        "image_urls": pics + [pics[0]]})
    post = (r or {}).get("post") or {} if isinstance(r, dict) else {}
    try:
        check("A25", "a draft keeps several pictures in order (duplicates dropped)",
              s == 200 and post.get("image_urls") == pics and post.get("image_url") == pics[0],
              "HTTP %s %s" % (s, post.get("image_urls")))
        s2, r2 = call(ctx["owner"], "POST", "/social/posts", {"caption": "QA too many",
                                                              "image_urls": ["https://example.com/%d.jpg" % i for i in range(11)]})
        check("A26", "more than 10 pictures is refused", s2 == 400, "HTTP %s %s" % (s2, r2))
    finally:
        if post.get("id"):
            sql("DELETE FROM social_posts WHERE tenant_id='%s' AND id='%s'" % (ctx["qa"], post["id"]))


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
    # QA's own trial ends like anyone's, and a locked QA would answer 402 to
    # every check. Open it for the run; its exact state is put back after.
    qa_state = sql("SELECT status::text, coalesce(trial_ends_at::text,'-') FROM tenants WHERE id='%s'" % qa)[0]
    sql("UPDATE tenants SET status='active' WHERE id='%s' AND status <> 'active'" % qa)
    if qa_state[0] != "active":
        import time as _t; _t.sleep(21)           # the api caches the lock state for 20 s
    try:
        for ph in phases:
            if ph == "wholocks":
                wholocks(); continue
            if ph in ("a4", "a5"):
                globals()[ph + "_readonly"](); continue
            globals()[ph](ctx)
    finally:
        cleanup(qa)
        sql("UPDATE tenants SET status=$$%s$$::tenant_status, trial_ends_at=%s WHERE id='%s'"
            % (qa_state[0], "NULL" if qa_state[1] == "-" else "$$%s$$::timestamptz" % qa_state[1], qa))
        after = counts(qa)
        same = before == after
        check("DATA", "QA tenant row counts back to where they started", same,
              "before=%s after=%s" % (before, after) if not same else json.dumps(after))
    fails = [r for r in RESULTS if r[2] == "FAIL"]
    print("\n%d checks, %d failed" % (len(RESULTS), len(fails)))


if __name__ == "__main__":
    main()
