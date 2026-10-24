"""Dashboard redesign (24 Oct 2026): the KPI row (/dashboard/summary), grouped
series (/dashboard/performance?group=), and highlights (/dashboard/insights),
each checked against rows made here. Scratch database only."""
import datetime as dt
import os
import uuid
from zoneinfo import ZoneInfo

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

KHI = ZoneInfo("Asia/Karachi")
TODAY = dt.datetime.now(KHI).date()


def month_start(back):
    d = TODAY.replace(day=1)
    for _ in range(back):
        d = (d - dt.timedelta(days=1)).replace(day=1)
    return d


def at(d, hour=12):
    return dt.datetime(d.year, d.month, d.day, hour, tzinfo=KHI)


@pytest.fixture(scope="module")
def world():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    p = {k: str(uuid.uuid4()) for k in ("owner", "staff", "b_owner")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test, business_type) VALUES "
                    "(%s, 'Dash3 A', 'x', 'active', true, 'salon'), (%s, 'Dash3 B', 'x', 'active', true, 'salon')", (a, b))
        for k, tid, role in (("owner", a, "owner"), ("staff", a, "operator"), ("b_owner", b, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (p[k], tid, k, "%s-%s@d3-test.local" % (k, p[k][:8]), role))
        # leads: 4 in each of the last 3 complete months except 2 two months ago, 3 in the last 7 days, 1 in the 7 days before
        plan = {1: 4, 2: 2, 3: 6}
        for back, n in plan.items():
            for i in range(n):
                cur.execute("INSERT INTO leads (tenant_id, name, source, status, created_at) VALUES (%s, %s, 'manual', 'new', %s)",
                            (a, "m%d-%d" % (back, i), at(month_start(back) + dt.timedelta(days=i))))
        for i in range(3):
            cur.execute("INSERT INTO leads (tenant_id, name, source, status, created_at) VALUES (%s, %s, 'hermes', 'new', %s)",
                        (a, "week-%d" % i, at(TODAY - dt.timedelta(days=i))))
        cur.execute("INSERT INTO leads (tenant_id, name, source, status, created_at) VALUES (%s, 'prev week', 'manual', 'new', %s)",
                    (a, at(TODAY - dt.timedelta(days=9))))
        cur.execute("INSERT INTO leads (tenant_id, name, source, status) VALUES (%s, 'B lead', 'manual', 'new')", (b,))
        # customers: Ali comes twice, Sara once; an inactive one
        ids = {}
        for name, st in (("Ali", "active"), ("Sara", "active"), ("Gone", "inactive")):
            cur.execute("INSERT INTO customers (tenant_id, name, status, created_at) VALUES (%s, %s, %s, %s) RETURNING id",
                        (a, name, st, at(TODAY)))
            ids[name] = str(cur.fetchone()["id"])
        cur.execute("INSERT INTO services (tenant_id, name, price) VALUES (%s, 'Facial', 3000), (%s, 'Hair Cut', 1500) RETURNING id, name", (a, a))
        sv = {r["name"]: str(r["id"]) for r in cur.fetchall()}
        for who, svc, day in (("Ali", "Facial", 1), ("Ali", "Hair Cut", 2), ("Sara", "Facial", 3)):
            cur.execute("""INSERT INTO appointments (tenant_id, customer_id, service_id, service, starts_at, status, created_at)
                           VALUES (%s, %s, %s, %s, %s, 'completed', %s)""",
                        (a, ids[who], sv[svc], svc, at(TODAY - dt.timedelta(days=day)), at(TODAY - dt.timedelta(days=day))))
        cur.execute("INSERT INTO appointments (tenant_id, customer_id, service, starts_at, status, created_at) VALUES (%s, %s, 'Facial', %s, 'cancelled', %s)",
                    (a, ids["Sara"], at(TODAY - dt.timedelta(days=1)), at(TODAY - dt.timedelta(days=1))))
        # bills: two facials and a haircut this week; a voided bill
        for num, svc, total, void in (("A1", "Facial", 6000, False), ("A2", "Hair Cut", 1500, False), ("A3", "Facial", 9999, True)):
            cur.execute("""INSERT INTO invoices (tenant_id, number, status, issued_on, subtotal, total, voided_at, void_reason)
                           VALUES (%s, %s, 'issued', %s, %s, %s, %s, %s) RETURNING id""",
                        (a, num, TODAY, total, total, at(TODAY) if void else None, "test" if void else None))
            iid = cur.fetchone()["id"]
            cur.execute("""INSERT INTO invoice_items (tenant_id, invoice_id, service_id, description, quantity, unit_price, line_total)
                           VALUES (%s, %s, %s, %s, %s, %s, %s)""", (a, iid, sv[svc], svc, 2 if num == "A1" else 1, 3000 if num == "A1" else total, total))
        cur.execute("INSERT INTO payments (tenant_id, amount, method, paid_at) VALUES (%s, 4000, 'cash', %s), (%s, 1000, 'cash', %s)",
                    (a, at(TODAY), a, at(TODAY - dt.timedelta(days=10))))
    h = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=p[k],
                                                        role=("operator" if k == "staff" else "owner"))} for k in p}
    yield {"a": a, "h": h}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def k(d, key):
    return next(x for x in d["kpis"] if x["key"] == key)


def test_kpi_row_compares_with_the_period_before(client, world):
    d = client.get("/dashboard/summary?days=7", headers=world["h"]["owner"]).json()
    assert [x["key"] for x in d["kpis"]] == ["leads", "customers", "appointments", "collected", "connections"]
    leads = k(d, "leads")
    assert (leads["value"], leads["previous"], leads["change_pct"]) == (3, 1, 200.0) and len(leads["spark"]) == 7
    cust = k(d, "customers")
    assert cust["value"] == 2 and cust["change_pct"] is None and "3 new in the period, 0 in the one before" == cust["note"]
    ap = k(d, "appointments")
    assert ap["value"] == 4 and ap["previous"] == 0 and ap["change_pct"] is None                # nothing before: no %
    col = k(d, "collected")
    assert (col["value"], col["previous"], col["money"]) == (4000, 1000, True) and col["change_pct"] == 300.0
    assert k(d, "connections")["value"] == 0 and k(d, "connections")["spark"] is None


def test_staff_get_no_money_and_follow_ups_instead(client, world):
    d = client.get("/dashboard/summary?days=7", headers=world["h"]["staff"]).json()
    keys = [x["key"] for x in d["kpis"]]
    assert "collected" not in keys and "followups" in keys


def test_grouping_keeps_the_totals(client, world):
    h = world["h"]["owner"]
    day = client.get("/dashboard/performance?metric=leads&days=90&group=day", headers=h).json()
    wk = client.get("/dashboard/performance?metric=leads&days=90&group=week", headers=h).json()
    mo = client.get("/dashboard/performance?metric=leads&days=90&group=month", headers=h).json()
    auto = client.get("/dashboard/performance?metric=leads&days=90", headers=h).json()
    total = sum(x["value"] for x in day["series"])
    assert total == sum(x["value"] for x in wk["series"]) == sum(x["value"] for x in mo["series"]) == day["total"]
    assert auto["group"] == "week" and len(day["series"]) == 90
    assert all(dt.date.fromisoformat(x["date"]).weekday() == 0 for x in wk["series"][1:])
    assert sum(x["days"] for x in mo["series"]) == 90
    for g in (wk, mo):                                                   # the period before: the same stretches, point for point
        assert [x["days"] for x in g["previous"]["series"]] == [x["days"] for x in g["series"]]
        assert sum(x["value"] for x in g["previous"]["series"]) == g["previous"]["total"]
    assert client.get("/dashboard/performance?metric=leads&days=30&group=year", headers=h).status_code == 400
    assert client.get("/dashboard/performance?metric=appointments&days=7", headers=h).json()["total"] == 4


def test_insights_are_worked_out_from_the_records(client, world):
    d = client.get("/dashboard/insights?metric=leads&days=7", headers=world["h"]["owner"]).json()
    assert d["best_month"]["date"] == str(month_start(3)) and d["best_month"]["value"] == 6
    assert d["slowest_month"]["date"] == str(month_start(2)) and d["slowest_month"]["value"] == 2
    assert d["last_month_change"]["change_pct"] == 100.0                                        # 4 after 2
    assert len(d["months"]) == 12 and d["months"][-1]["complete"] is False
    top = d["top"]
    assert top["by"] == "billed" and [(i["name"], i["value"]) for i in top["items"]] == [("Facial", 6000.0), ("Hair Cut", 1500.0)]
    assert top["total"] == 7500                                                                 # the voided bill left out
    assert d["customers"] == {"returning": 1, "served": 2, "rate": None,
                              "definition": "Customers with two or more visits or bills, out of everyone served."}
    src = {s["source"]: s for s in d["sources"]}
    assert src["hermes"]["leads"] == 3 and src["hermes"]["rate"] is None                        # fewer than 5
    staff = client.get("/dashboard/insights?metric=leads&days=7", headers=world["h"]["staff"]).json()
    assert staff["top"]["by"] == "bookings" and [(i["name"], i["value"]) for i in staff["top"]["items"]] == [("Facial", 2.0), ("Hair Cut", 1.0)]
    assert client.get("/dashboard/insights?metric=collected&days=7", headers=world["h"]["staff"]).status_code == 403


def test_a_new_business_gets_honest_gaps(client, world):
    d = client.get("/dashboard/insights?metric=leads&days=30", headers=world["h"]["b_owner"]).json()
    assert d["best_month"] is None and "3 complete months" in d["months_note"] and d["top"]["items"] == []
    s = client.get("/dashboard/summary?days=30", headers=world["h"]["b_owner"]).json()
    assert k(s, "leads")["value"] == 1 and k(s, "collected")["value"] == 0                    # only its own lead
