"""Dashboard 2.0 (24 Oct 2026): /dashboard/overview and /dashboard/performance
give the figures the Overview shows, from the real tables, with the same rules
as the pages they link to, limited to what the person's role may see.

Runs against a SCRATCH database only. Every business it makes is deleted at the end.
"""
import datetime as dt
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from psycopg.types.json import Json  # noqa: E402

from app import crypto, roles  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def world():
    """Business A (a shop with orders, money, leads, approvals); business B, empty."""
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    people = {k: str(uuid.uuid4()) for k in ("owner", "shop", "market", "b_owner")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test, business_type) VALUES "
                    "(%s, 'Dash A', 'x', 'active', true, 'shop'), (%s, 'Dash B', 'x', 'active', true, 'shop')", (a, b))
        for k, tid, role, sr in (("owner", a, "owner", None), ("shop", a, "operator", "shopkeeper"),
                                 ("market", a, "owner", "marketing"), ("b_owner", b, "owner", None)):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role, staff_role) VALUES (%s, %s, %s, %s, %s, %s)",
                        (people[k], tid, k, "%s-%s@dash-test.local" % (k, people[k][:8]), role, sr))
        # orders: two new, one confirmed, one delivered and paid (a sale), one delivered and unpaid
        inv_paid, inv_unpaid = str(uuid.uuid4()), str(uuid.uuid4())
        cur.execute("INSERT INTO invoices (id, tenant_id, number, status, issued_on, subtotal, total) VALUES "
                    "(%s, %s, 'D-1', 'paid', CURRENT_DATE, 3000, 3000), (%s, %s, 'D-2', 'issued', CURRENT_DATE - 40, 2000, 2000)",
                    (inv_paid, a, inv_unpaid, a))
        cur.execute("UPDATE invoices SET due_on = CURRENT_DATE - 10 WHERE id = %s", (inv_unpaid,))
        cur.execute("INSERT INTO payments (tenant_id, invoice_id, amount, method, paid_at) VALUES (%s, %s, 3000, 'cash', now())",
                    (a, inv_paid))
        for n, st, extra in (("O-1", "new", {}), ("O-2", "new", {}), ("O-3", "confirmed", {"confirmed_at": "now()"}),
                             ("O-4", "delivered", {"invoice_id": inv_paid}), ("O-5", "delivered", {"invoice_id": inv_unpaid})):
            cur.execute("INSERT INTO orders (tenant_id, number, status, subtotal, total, invoice_id, confirmed_at, delivered_at, recognised_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        (a, n, st, 3000 if n == "O-4" else 2000, 3000 if n == "O-4" else 2000, extra.get("invoice_id"),
                         dt.datetime.now(dt.timezone.utc) if st != "new" else None,
                         dt.datetime.now(dt.timezone.utc) if st == "delivered" else None,
                         dt.datetime.now(dt.timezone.utc) if n == "O-4" else None))
        cur.execute("INSERT INTO customers (tenant_id, name) VALUES (%s, 'Ali'), (%s, 'Sara')", (a, a))
        cur.execute("INSERT INTO leads (tenant_id, source, name, followup_at) VALUES (%s, 'manual', 'Due', now() - interval '1 hour'), "
                    "(%s, 'manual', 'Later', now() + interval '3 days')", (a, a))
        cur.execute("INSERT INTO deliverables (tenant_id, agent, kind, title, status) VALUES (%s, 'copy', 'post', 'A draft', 'awaiting_approval')", (a,))
        cur.execute("INSERT INTO social_posts (tenant_id, platforms, caption, status) VALUES (%s, ARRAY['facebook'], 'hi', 'draft')", (a,))
        for prov in ("google", "meta"):
            cur.execute("INSERT INTO integrations (tenant_id, provider, status, credentials, connected_at) VALUES (%s, %s, 'connected', %s, now())",
                        (a, prov, Json(crypto.encrypt_json({"access_token": "t"}, a, prov))))
        cur.execute("UPDATE integrations SET expires_at = now() - interval '1 day' WHERE tenant_id = %s AND provider = 'meta'", (a,))
    roles.forget()
    tok = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=people[k],
                                                          role=("operator" if k == "shop" else "owner"))} for k in people}
    yield {"a": a, "b": b, "h": tok}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_owner_sees_every_block_from_the_real_rows(client, world):
    r = client.get("/dashboard/overview", headers=world["h"]["owner"])
    assert r.status_code == 200, r.text
    d = r.json()
    c = d["orders"]["counts"]
    assert (c["new"], c["confirmed"], c["waiting_delivery"]) == (2, 1, 1)
    m = d["orders"]["money"]
    assert m["sales_today"] == 3000 and m["collected_today"] == 3000          # only the delivered AND paid order is a sale
    assert m["outstanding"] == 2000 and m["outstanding_orders"] == 1
    assert d["month"]["collected"] == 3000 and d["month"]["outstanding_all_time"] == 2000
    ap = d["ai"]["approvals"]
    assert (ap["deliverables"], ap["posts"], ap["total"]) == (1, 1, 2)       # what the Approval Queue lists
    ig = d["integrations"]
    assert ig["healthy"] == 1 and [x["provider"] for x in ig["attention"]] == ["meta"]   # expired login is not "working"
    assert d["people"]["leads"]["followups_due"] == 1 and d["people"]["customers"]["total"] == 2
    keys = [f["key"] for f in d["focus"]]
    for k in ("orders_new", "orders_delivery", "approvals", "followups", "invoices_overdue", "integrations"):
        assert k in keys, keys
    assert next(f for f in d["focus"] if f["key"] == "orders_new")["text"] == "2 orders waiting for you to confirm"
    assert {x["key"] for x in d["chart_metrics"]} == {"sales", "collected", "orders", "customers", "leads"}
    assert d["setup"]["total"] == 11 and d["setup"]["next"]["key"] == "brand"


def test_another_business_sees_nothing_of_it(client, world):
    d = client.get("/dashboard/overview", headers=world["h"]["b_owner"]).json()
    assert d["orders"]["counts"]["new"] == 0 and d["month"]["collected"] == 0 and d["focus"] == []
    assert {x["key"] for x in d["chart_metrics"]} == {"collected", "customers", "leads"}    # no orders yet: no orders chart


def test_shopkeeper_gets_no_money_and_no_ai(client, world):
    d = client.get("/dashboard/overview", headers=world["h"]["shop"]).json()
    assert d["money_visible"] is False and "money" not in d["orders"] and "hidden" in d["month"]
    assert "Jarvis" in d["ai"]["hidden"] and "Administration" in d["integrations"]["hidden"]
    assert "invoices_overdue" not in [f["key"] for f in d["focus"]]
    assert {x["key"] for x in d["chart_metrics"]} == {"orders", "customers", "leads"}
    r = client.get("/dashboard/performance?metric=sales&days=30", headers=world["h"]["shop"])
    assert r.status_code == 403


def test_marketing_role_is_limited_to_its_sections(client, world):
    d = client.get("/dashboard/overview", headers=world["h"]["market"]).json()
    assert "CRM" in d["people"]["hidden"] and "Operations" in d["orders"]["hidden"] and "hidden" in d["month"]
    assert "approvals" in d["ai"]


def test_performance_series_and_comparison(client, world):
    h = world["h"]["owner"]
    p = client.get("/dashboard/performance?metric=orders&days=7", headers=h).json()
    assert p["days"] == 7 and len(p["series"]) == 7 and len(p["previous"]["series"]) == 7
    assert p["total"] == 5 and p["series"][-1]["value"] == 5 and p["previous"]["total"] == 0 and p["change_pct"] is None
    s = client.get("/dashboard/performance?metric=sales&days=30", headers=h).json()
    c = client.get("/dashboard/performance?metric=collected&days=30", headers=h).json()
    assert s["total"] == 3000 and c["total"] == 3000 and s["definition"] != c["definition"]
    today = p["end"]
    q = client.get("/dashboard/performance?metric=customers&start=%s&end=%s" % (today, today), headers=h).json()
    assert q["days"] == 1 and q["total"] == 2


@pytest.mark.parametrize("query,status", [
    ("metric=nope", 400), ("metric=orders&days=12", 400), ("metric=orders&start=2026-01-05&end=2026-01-01", 400),
    ("metric=orders&start=bad&end=2026-01-01", 400), ("metric=orders&start=2024-01-01&end=2026-01-01", 400),
    ("metric=orders&start=2026-01-01&end=2999-01-01", 400),
])
def test_performance_refuses_bad_input(client, world, query, status):
    assert client.get("/dashboard/performance?" + query, headers=world["h"]["owner"]).status_code == status


def test_needs_a_login(client):
    assert client.get("/dashboard/overview").status_code in (400, 401, 403)
