"""The partner network (db/88; app/partner_engine.py, partner_network.py, partner_site.py).

Proved here, end to end through the real API and database:

  SET-UP      approving an agency sets it up completely and automatically;
              a failing step is recorded, retried, and never re-runs a done step
  CLIENTS     method A (partner adds a client) and method B (branded link) both
              create a real business, assigned to the partner in the same
              INSERT, with its subscription, its fee, its plan and its trial
  MONEY       55/45 by default, negotiated per partner, frozen per payment;
              a floor; refunds and chargebacks; no double split; no split of a
              payment made before the business joined; payouts that release
              their rows when they fail
  WALLS       an agency never sees another's clients, money or settings; it
              cannot write its own split, ledger or domain status; staff
              logins cannot change prices; a client sees what it pays and
              nothing about the agency's terms
  WHITE LABEL the public site's data never says TechDeva

Runs against a SCRATCH database only; everything it makes is deleted.
"""
import os
import uuid
from decimal import Decimal

import psycopg
import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import partner_engine as eng  # noqa: E402
from app.auth import hash_password, issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import P, app  # noqa: E402

PW = "agency-pass-98765"
TAG = "pnt" + uuid.uuid4().hex[:6]


def _h(token):
    return {"Authorization": "Bearer " + token}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def founder():
    return _h(issue_token(tenant_id=None, user_id=str(uuid.uuid4()), role="techdeva_admin"))


@pytest.fixture(scope="module")
def net(client, founder):
    """Two approved agencies, set up by the real approval flow."""
    made = {}
    for key, country in (("a", "AE"), ("b", "GB")):
        r = client.post("/techdeva/partners", headers=founder,
                        json={"name": "%s Agency %s" % (key.upper(), TAG), "slug": "%s-%s" % (TAG, key),
                              "country": country, "contact_name": "Owner " + key,
                              "contact_email": "%s-%s@agency.test" % (TAG, key)})
        assert r.status_code == 200, r.text
        pid = r.json()["id"]
        r = client.post("/techdeva/partners/%s/approve" % pid, headers=founder)
        assert r.status_code == 200, r.text
        made[key] = {"id": pid, "approve": r.json()}
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id FROM partner_users WHERE partner_id = %s", (pid,))
            uid = str(cur.fetchone()["id"])
            cur.execute("UPDATE partner_users SET password_hash = %s WHERE id = %s", (hash_password(PW), uid))
            made[key]["uid"] = uid
            made[key]["h"] = _h(issue_token(tenant_id=None, user_id=uid, role="partner", partner_id=pid))
    yield made
    with tenant_session(None, "techdeva_admin") as cur:
        ids = [made[k]["id"] for k in made]
        cur.execute("SELECT id FROM tenants WHERE partner_id = ANY(%s::uuid[])", (ids,))
        tids = [str(r["id"]) for r in cur.fetchall()]
        cur.execute("DELETE FROM partner_ledger WHERE partner_id = ANY(%s::uuid[])", (ids,))
        cur.execute("DELETE FROM partner_payouts WHERE partner_id = ANY(%s::uuid[])", (ids,))
        cur.execute("DELETE FROM partner_client_subscriptions WHERE partner_id = ANY(%s::uuid[])", (ids,))
        for t in tids:
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM signups WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT id FROM partners WHERE id = ANY(%s::uuid[]) OR slug LIKE %s OR contact_email LIKE %s",
                    (ids, TAG + "%", TAG + "%"))
        every = [str(r["id"]) for r in cur.fetchall()]
        for t in ("partner_ledger", "partner_payouts", "partner_client_subscriptions"):
            cur.execute("DELETE FROM %s WHERE partner_id = ANY(%%s::uuid[])" % t, (every,))
        cur.execute("DELETE FROM partners WHERE id = ANY(%s::uuid[])", (every,))


def _new_client(client, h, name, **extra):
    body = {"business_name": name, "contact_email": "%s-%s@client.test" % (TAG, uuid.uuid4().hex[:8]),
            "contact_name": "Owner", "industry": "clinic", **extra}
    r = client.post("/partner/clients", headers=h, json=body)
    assert r.status_code == 200, r.text
    return r.json(), body["contact_email"]


def _pay(tid, amount, currency="USD"):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO client_payments (tenant_id, amount, currency) VALUES (%s,%s,%s) RETURNING id",
                    (tid, amount, currency))
        return str(cur.fetchone()["id"])


# ============================================================ SET-UP
def test_approval_sets_the_agency_up_completely(net, founder, client):
    a = net["a"]["approve"]
    assert a["provisioning"] == "done", a
    assert [s["step"] for s in a["steps"]] == list(eng.STEPS)
    assert all(s["status"] == "done" for s in a["steps"]), a["steps"]
    d = client.get("/partner/dashboard", headers=net["a"]["h"]).json()
    assert d["terms"]["partner_share_pct"] == "55"                       # standard terms
    assert d["partner"]["code"].startswith("TDP-")                        # a partner ID
    assert len(d["signup_links"]) == 1 and "/join/" in d["signup_links"][0]["url"]
    books = client.get("/partner/price-books", headers=net["a"]["h"]).json()["price_books"]
    assert {b["plan_key"] for b in books} >= {"starter", "growth", "pro"}   # ready to sell
    growth = next(b for b in books if b["plan_key"] == "growth")
    assert growth["you_keep"] == "109.45" and growth["platform_share"] == "89.55"


def test_approving_twice_does_not_set_up_twice(net, founder, client):
    pid = net["a"]["id"]
    assert client.post("/techdeva/partners/%s/approve" % pid, headers=founder).status_code == 200
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(*) AS n FROM partner_signup_links WHERE partner_id = %s", (pid,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT count(*) AS n FROM partner_revenue_terms WHERE partner_id = %s", (pid,))
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT count(*) AS n FROM partner_price_books WHERE partner_id = %s", (pid,))
        assert cur.fetchone()["n"] == 3


def test_a_failing_step_is_recorded_retried_and_never_repeats_done_work(founder, client, monkeypatch):
    r = client.post("/techdeva/partners", headers=founder,
                    json={"name": "Flaky " + TAG, "slug": TAG + "-flaky", "contact_email": TAG + "-f@agency.test"})
    pid = r.json()["id"]
    real = eng._STEP_FN["website"]
    monkeypatch.setitem(eng._STEP_FN, "website", lambda cur, p: (_ for _ in ()).throw(RuntimeError("disk full")))
    out = client.post("/techdeva/partners/%s/approve" % pid, headers=founder).json()
    assert out["provisioning"] == "failed"
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["branding"]["status"] == "done" and steps["website"]["status"] == "failed"
    assert "disk full" in steps["website"]["last_error"]
    assert out["run"]["status"] == "failed"
    done_before = steps["branding"]["attempts"]

    monkeypatch.setitem(eng._STEP_FN, "website", real)
    out = client.post("/techdeva/partners/%s/provisioning/retry" % pid, headers=founder).json()
    assert out["result"] == "done"
    steps = {s["step"]: s for s in out["steps"]}
    assert steps["branding"]["attempts"] == done_before                   # not run again
    assert steps["website"]["status"] == "done" and steps["website"]["last_error"] is None


def test_an_agency_can_apply_and_waits_for_approval(client):
    r = client.post("/partner/apply", json={"company": "Applicant " + TAG, "email": TAG + "-ap@agency.test",
                                            "country": "SA"})
    assert r.status_code == 200 and r.json()["status"] == "pending"
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT status, applied_via FROM partners WHERE contact_email = %s", (TAG + "-ap@agency.test",))
        row = cur.fetchone()
        assert row["status"] == "pending" and row["applied_via"] == "applied"
        cur.execute("SELECT count(*) AS n FROM partner_provisioning_runs r JOIN partners p ON p.id = r.partner_id "
                    "WHERE p.contact_email = %s", (TAG + "-ap@agency.test",))
        assert cur.fetchone()["n"] == 0                                    # nothing set up before approval


# ============================================================ CLIENTS
def test_method_a_partner_adds_a_client_end_to_end(net, client):
    out, email = _new_client(client, net["a"]["h"], "Clinic One " + TAG)
    tid = out["tenant_id"]
    assert out["invite_emailed"] is False and out["invite_token"]       # no mail on scratch: token handed back
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT partner_id, partner_assigned_at, status::text AS s, subdomain FROM tenants WHERE id = %s", (tid,))
        t = cur.fetchone()
        assert str(t["partner_id"]) == net["a"]["id"] and t["partner_assigned_at"] and t["s"] == "trial"
        assert t["subdomain"]                                             # the platform's own provisioning ran
        cur.execute("SELECT price, currency, plan_key, status FROM partner_client_subscriptions WHERE tenant_id = %s", (tid,))
        s = cur.fetchone()
        assert s["status"] == "trial" and s["currency"] == "USD"
        cur.execute("SELECT monthly_fee, plan_key FROM client_accounts WHERE tenant_id = %s", (tid,))
        assert cur.fetchone()["plan_key"] == s["plan_key"]                # its limits follow the package
        cur.execute("SELECT password_hash FROM users WHERE lower(email) = %s", (email,))
        assert cur.fetchone()["password_hash"] is None                    # chooses it via the invite
        cur.execute("SELECT trial_ends_at > now() + interval '6 days' AS ok FROM tenants WHERE id = %s", (tid,))
        assert cur.fetchone()["ok"]


def test_the_invited_client_sets_a_password_on_its_partners_site_only(net, client):
    out, email = _new_client(client, net["a"]["h"], "Clinic Welcome " + TAG)
    tok = out["invite_token"]
    slug_b = TAG + "-b"
    assert client.post("/public/partner-site/%s/welcome" % slug_b,
                       json={"token": tok, "password": "a-good-long-pass"}).status_code == 400
    r = client.post("/public/partner-site/%s-a/welcome" % TAG, json={"token": tok, "password": "a-good-long-pass"})
    assert r.status_code == 200, r.text
    assert client.post("/public/partner-site/%s-a/welcome" % TAG,
                       json={"token": tok, "password": "another-long-pass"}).status_code == 400   # single use
    assert client.post("/auth/login", json={"email": email, "password": "a-good-long-pass"}).status_code == 200


def test_method_b_branded_link_with_a_coupon(net, client):
    h = net["a"]["h"]
    assert client.post("/partner/coupons", headers=h, json={"code": "LAUNCH20", "percent_off": 20}).status_code == 200
    link = client.get("/partner/dashboard", headers=h).json()["signup_links"][0]
    growth = next(b for b in client.get("/partner/price-books", headers=h).json()["price_books"]
                  if b["plan_key"] == "growth")
    email = "%s-self@client.test" % TAG
    r = client.post("/public/partner-site/%s-a/join/%s" % (TAG, link["token"]),
                    json={"business_name": "Self Salon " + TAG, "contact_email": email, "password": "salon-pass-123",
                          "package_id": growth["id"], "coupon_code": "launch20"})
    assert r.status_code == 200, r.text
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT s.list_price, s.discount, s.price, t.partner_id FROM partner_client_subscriptions s
                         JOIN tenants t ON t.id = s.tenant_id JOIN users u ON u.tenant_id = t.id
                        WHERE lower(u.email) = %s""", (email,))
        s = cur.fetchone()
        assert (s["list_price"], s["discount"], s["price"]) == (Decimal("199"), Decimal("39.80"), Decimal("159.20"))
        assert str(s["partner_id"]) == net["a"]["id"]
        cur.execute("SELECT uses FROM partner_coupons WHERE partner_id = %s AND code = 'LAUNCH20'", (net["a"]["id"],))
        assert cur.fetchone()["uses"] == 1
        cur.execute("SELECT uses FROM partner_signup_links WHERE token = %s", (link["token"],))
        assert cur.fetchone()["uses"] == 1
    assert r.json()["verification_required"] is True
    # like every new login it waits for the emailed code (no mail on scratch: make the code here)
    assert client.post("/auth/login", json={"email": email, "password": "salon-pass-123"}).status_code == 403
    from app import email_codes as ec
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id FROM users WHERE lower(email) = %s", (email,))
        code = ec.issue(cur, cur.fetchone()["id"])
    v = client.post("/auth/verify-email", json={"email": email, "code": code})
    assert v.status_code == 200 and "techdeva" not in v.text.lower()
    assert client.post("/auth/login", json={"email": email, "password": "salon-pass-123"}).status_code == 200
    # a link on another agency's site is not valid here
    assert client.get("/public/partner-site/%s-b/link/%s" % (TAG, link["token"])).status_code == 404
    assert client.post("/public/partner-site/%s-a/join/%s" % (TAG, "x" * 24),
                       json={"business_name": "No", "contact_email": "n@x.test", "password": "salon-pass-123"}
                       ).status_code == 404


# ============================================================ MONEY
def test_payments_are_split_once_at_the_terms_in_force(net, founder, client):
    out, _ = _new_client(client, net["a"]["h"], "Money Clinic " + TAG)
    tid = out["tenant_id"]
    pay = _pay(tid, "199")
    assert eng.sync_ledger() >= 1
    assert eng.sync_ledger() == 0                                         # never twice
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT * FROM partner_ledger WHERE source_id = %s", (pay,))
        rows = cur.fetchall()
    assert len(rows) == 1
    assert (rows[0]["partner_amount"], rows[0]["techdeva_amount"]) == (Decimal("109.45"), Decimal("89.55"))

    # renegotiated to 70% from today: the next payment splits 70/30, the old one stays 55/45
    r = client.post("/techdeva/partners/%s/terms" % net["a"]["id"], headers=founder,
                    json={"partner_share_pct": 70, "effective_from": "2000-01-01"})
    assert r.status_code == 200
    r = client.post("/techdeva/partners/%s/terms" % net["a"]["id"], headers=founder,
                    json={"partner_share_pct": 70, "min_techdeva_amount": 40, "floor_currency": "USD"})
    assert r.status_code == 200, r.text
    pay2 = _pay(tid, "100")
    eng.sync_ledger()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT partner_amount, techdeva_amount, floor_applied FROM partner_ledger WHERE source_id = %s", (pay2,))
        r2 = cur.fetchone()
        cur.execute("SELECT partner_amount FROM partner_ledger WHERE source_id = %s", (pay,))
        assert cur.fetchone()["partner_amount"] == Decimal("109.45")      # history did not move
    assert (r2["partner_amount"], r2["techdeva_amount"], r2["floor_applied"]) == (Decimal("60.00"), Decimal("40.00"), True)
    net["a"]["money_tid"], net["a"]["pay"], net["a"]["pay2"] = tid, pay, pay2


def test_a_payment_from_before_the_business_joined_is_not_split(net, founder, client):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (name, industry, status, is_test) VALUES (%s,'x','active',false) RETURNING id",
                    ("Moved Later " + TAG,))
        tid = str(cur.fetchone()["id"])
    old = _pay(tid, "500")
    r = client.post("/techdeva/partners/%s/clients" % net["b"]["id"], headers=founder, json={"tenant_id": tid})
    assert r.status_code == 200
    with tenant_session(None, "techdeva_admin") as cur:            # partners.py (db/87) sets the date too
        cur.execute("UPDATE tenants SET partner_assigned_at = COALESCE(partner_assigned_at, now()) WHERE id = %s", (tid,))
    eng.sync_ledger()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(*) AS n FROM partner_ledger WHERE source_id = %s", (old,))
        assert cur.fetchone()["n"] == 0


def test_refunds_chargebacks_and_their_limits(net, founder, client):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id FROM partner_ledger WHERE source_id = %s", (net["a"]["pay"],))
        lid = str(cur.fetchone()["id"])
    r = client.post("/techdeva/partner-ledger/%s/reverse" % lid, headers=founder,
                    json={"amount": "99.50", "kind": "refund"})
    assert r.status_code == 200, r.text
    assert (r.json()["partner_amount"], r.json()["techdeva_amount"]) == ("-54.73", "-44.77")   # at 55/45, its own terms
    r = client.post("/techdeva/partner-ledger/%s/reverse" % lid, headers=founder,
                    json={"amount": "99.50", "kind": "chargeback", "gateway_fee": "15"})
    assert r.status_code == 200 and r.json()["net"] == "-114.50"
    r = client.post("/techdeva/partner-ledger/%s/reverse" % lid, headers=founder, json={"amount": "0.01"})
    assert r.status_code == 400 and "exceed" in r.json()["detail"]          # 199 already reversed in full


def test_payouts_gather_release_on_failure_and_need_a_reference(net, founder, client):
    out, _ = _new_client(client, net["b"]["h"], "Payout Clinic " + TAG)
    _pay(out["tenant_id"], "300")
    made = client.post("/techdeva/partner-payouts/run", headers=founder, json={"partner_id": net["b"]["id"]}).json()["created"]
    assert len(made) == 1 and made[0]["amount"] == "165.00"
    oid = made[0]["id"]
    assert client.post("/techdeva/partner-payouts/%s/mark" % oid, headers=founder,
                       json={"status": "paid"}).status_code == 400                   # needs the bank reference
    assert client.post("/techdeva/partner-payouts/%s/mark" % oid, headers=founder,
                       json={"status": "failed", "reason": "bank rejected IBAN"}).status_code == 200
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(*) AS n FROM partner_ledger WHERE payout_id = %s", (oid,))
        assert cur.fetchone()["n"] == 0                                              # released, not lost
    again = client.post("/techdeva/partner-payouts/run", headers=founder, json={"partner_id": net["b"]["id"]}).json()["created"]
    assert again[0]["amount"] == "165.00"
    r = client.post("/techdeva/partner-payouts/%s/mark" % again[0]["id"], headers=founder,
                    json={"status": "paid", "reference": "WISE-123"})
    assert r.status_code == 200
    p = client.get("/partner/payouts", headers=net["b"]["h"]).json()["payouts"]
    assert {(x["status"], x["amount"]) for x in p} == {("failed", "165.00"), ("paid", "165.00")}


def test_currency_mismatch_and_missing_terms_go_to_review_not_payout(net, founder, client):
    out, _ = _new_client(client, net["a"]["h"], "PKR Clinic " + TAG)
    pkr = _pay(out["tenant_id"], "19999", "PKR")                 # partner A's floor is in USD
    eng.sync_ledger()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id, needs_review, review_reason FROM partner_ledger WHERE source_id = %s", (pkr,))
        row = cur.fetchone()
    assert row["needs_review"] and "USD" in row["review_reason"]
    made = eng.create_payouts(partner_id=net["a"]["id"])
    assert all(m["currency"] != "PKR" for m in made)                         # held until reviewed
    assert client.post("/techdeva/partner-ledger/%s/clear-review" % row["id"], headers=founder,
                       json={"note": "checked with finance"}).status_code == 200


# ============================================================ WALLS
def test_agencies_never_see_each_other(net, client):
    for me, other in (("a", "b"), ("b", "a")):
        pid_other = net[other]["id"]
        with tenant_session(None, "partner", partner_id=net[me]["id"]) as cur:
            for t in ("partner_ledger", "partner_payouts", "partner_client_subscriptions", "partner_price_books",
                      "partner_coupons", "partner_signup_links", "partner_revenue_terms", "partner_branding",
                      "partner_provisioning_runs"):
                cur.execute("SELECT count(*) AS n FROM %s WHERE partner_id = %%s" % t, (pid_other,))
                assert cur.fetchone()["n"] == 0, "%s sees %s of %s" % (me, t, other)
            cur.execute("SELECT count(*) AS n FROM tenants WHERE partner_id = %s", (pid_other,))
            assert cur.fetchone()["n"] == 0
            for t in ("leads", "users", "client_payments", "integrations"):
                cur.execute("SELECT count(*) AS n FROM %s" % t)
                assert cur.fetchone()["n"] == 0, "a partner can read %s" % t
    tid_a = net["a"]["money_tid"]
    assert client.get("/partner/clients/%s" % tid_a, headers=net["b"]["h"]).status_code == 404
    assert client.post("/partner/clients/%s/cancel" % tid_a, headers=net["b"]["h"]).status_code == 404


def test_an_agency_cannot_write_money_or_ownership(net):
    pid = net["a"]["id"]
    with tenant_session(None, "partner", partner_id=pid) as cur:
        for sql, args in (
                ("INSERT INTO partner_revenue_terms (partner_id, partner_share_pct, effective_from) VALUES (%s, 99, '2100-01-01')", (pid,)),
                ("INSERT INTO partner_ledger (partner_id, kind, gross, net, partner_share_pct, partner_amount, techdeva_amount, currency) "
                 "VALUES (%s,'adjustment',1000,1000,100,1000,0,'USD')", (pid,)),
                ("INSERT INTO partner_domains (partner_id, domain, status, token) VALUES (%s,'evil.example.com','verified','t')", (pid,)),
                ("INSERT INTO partner_payouts (partner_id, amount, currency) VALUES (%s, 1000, 'USD')", (pid,))):
            cur.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(sql, args)
            cur.execute("ROLLBACK TO SAVEPOINT s")
        for sql in ("UPDATE partner_revenue_terms SET partner_share_pct = 99",
                    "UPDATE partner_ledger SET partner_amount = partner_amount * 10",
                    "UPDATE partner_payouts SET status = 'paid'"):
            cur.execute("SAVEPOINT s")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cur.execute(sql)                                          # refused by the database itself
            cur.execute("ROLLBACK TO SAVEPOINT s")
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT max(partner_share_pct) AS m FROM partner_revenue_terms WHERE partner_id = %s", (pid,))
        assert cur.fetchone()["m"] <= 70


def test_staff_logins_cannot_change_prices_or_brand(net, client, founder):
    email = "%s-staff@agency.test" % TAG
    r = client.post("/techdeva/partners/%s/users" % net["a"]["id"], headers=founder,
                    json={"email": email, "role": "partner_staff"})
    staff = _h(issue_token(tenant_id=None, user_id=r.json()["id"], role="partner", partner_id=net["a"]["id"]))
    assert client.get("/partner/dashboard", headers=staff).status_code == 200
    assert client.post("/partner/coupons", headers=staff, json={"code": "STAFF50", "percent_off": 50}).status_code == 403
    assert client.put("/partner/branding", headers=staff, json={"brand_name": "Hijack"}).status_code == 403
    assert client.post("/partner/price-books", headers=staff,
                       json={"name": "Cheap", "plan_key": "pro", "price": 1}).status_code == 403


def test_a_client_sees_what_it_pays_and_nothing_of_the_agency(net):
    tid = net["a"]["money_tid"]
    with tenant_session(tid, "owner") as cur:
        cur.execute("SELECT price FROM partner_client_subscriptions")
        assert len(cur.fetchall()) == 1
        for t in ("partner_ledger", "partner_revenue_terms", "partner_payouts", "partner_price_books",
                  "partner_coupons", "partners"):
            cur.execute("SELECT count(*) AS n FROM %s" % t)
            assert cur.fetchone()["n"] == 0, "a client can read %s" % t


def test_a_suspended_agency_is_locked_out_but_its_clients_keep_working(net, founder, client):
    pid = net["b"]["id"]
    assert client.post("/techdeva/partners/%s/status" % pid, headers=founder, json={"status": "suspended"}).status_code == 200
    assert client.get("/partner/dashboard", headers=net["b"]["h"]).status_code == 403
    assert client.get("/public/partner-site/%s-b" % TAG).status_code == 404
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(*) AS n FROM tenants WHERE partner_id = %s AND status::text = 'paused'", (pid,))
        assert cur.fetchone()["n"] == 0
    assert client.post("/techdeva/partners/%s/approve" % pid, headers=founder).status_code == 200


# ============================================================ WHITE LABEL
def test_the_public_site_never_says_techdeva(net, client):
    h = net["a"]["h"]
    assert client.put("/partner/branding", headers=h,
                      json={"brand_name": "Gulf Growth AI", "headline": "AI that answers every lead",
                            "primary_color": "#0E7C66"}).status_code == 200
    assert client.put("/partner/branding", headers=h, json={"primary_color": "red"}).status_code == 400
    r = client.get("/public/partner-site/%s-a" % TAG)
    assert r.status_code == 200
    assert "techdeva" not in r.text.lower()
    assert r.json()["brand_name"] == "Gulf Growth AI" and r.json()["packages"]
    assert client.get("/public/partner-site/no-such-agency").status_code == 404
    assert client.put("/partner/branding", headers=h, json={"website_published": False}).status_code == 200
    assert client.get("/public/partner-site/%s-a" % TAG).status_code == 404
    client.put("/partner/branding", headers=h, json={"website_published": True})


def test_the_network_overview_adds_up_per_currency(net, founder, client):
    eng.sync_ledger()
    d = client.get("/techdeva/partner-network", headers=founder).json()
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT currency, SUM(net) AS net FROM partner_ledger GROUP BY currency")
        nets = {r["currency"]: r["net"] for r in cur.fetchall()}
    assert d["money"], "no money rows to check"
    for m in d["money"]:                                  # every cent lands with exactly one side
        assert Decimal(m["partner_earned"]) + Decimal(m["techdeva_earned"]) == nets[m["currency"]]
    assert d["partner_clients"] >= 1
    assert client.get("/techdeva/partner-network", headers=net["a"]["h"]).status_code in (401, 403)


# ============================================================ PAGES AND SIGN-OUT
def test_pages_are_served_and_sign_out_ends_the_login(net, client):
    assert client.get("/partner").status_code == 200
    assert client.get("/partner/welcome").status_code == 200
    r = client.get("/p/%s-a" % TAG)
    assert r.status_code == 200 and "techdeva" not in r.text.lower()       # the template itself, too
    login = client.post("/partner/login", json={"email": "%s-a@agency.test" % TAG, "password": PW})
    assert login.status_code == 200
    h = _h(login.json()["token"])
    assert client.get("/partner/dashboard", headers=h).status_code == 200
    assert client.post("/partner/logout", headers=h).status_code == 200
    assert client.get("/partner/dashboard", headers=h).status_code == 401  # refused on the server, not just forgotten
