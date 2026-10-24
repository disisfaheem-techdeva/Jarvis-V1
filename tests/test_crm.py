"""CRM upgrade (24 Oct 2026; docs/CRM-UPGRADE.md, db/77): leads searched and
filtered on the server, a lead's full record, human follow-ups kept apart from
the AI agent's follow-up, conversion that never makes a second customer,
duplicates shown for review, and every link checked inside one business.

Runs against a SCRATCH database only. Every business it makes is deleted at the end.
"""
import datetime as dt
import os
import uuid

import psycopg
import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import roles  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture(scope="module")
def world():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    p = {k: str(uuid.uuid4()) for k in ("owner", "staff", "shop", "hr", "b_owner")}
    L = {}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test) VALUES (%s, 'CRM A', 'x', 'active', true), "
                    "(%s, 'CRM B', 'x', 'active', true)", (a, b))
        for k, tid, role, sr in (("owner", a, "owner", None), ("staff", a, "operator", None), ("shop", a, "operator", "shopkeeper"),
                                 ("hr", a, "owner", "hr"), ("b_owner", b, "owner", None)):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role, staff_role) VALUES (%s, %s, %s, %s, %s, %s)",
                        (p[k], tid, k.capitalize(), "%s-%s@crm-test.local" % (k, p[k][:8]), role, sr))
        for key, tid, name, phone, email, status, est in (
                ("ayesha", a, "Ayesha Khan", "+92 300 1112233", "ayesha@x.test", "new", 0),
                ("bilal", a, "Bilal", "0300-1112233", None, "new", 0),
                ("sara", a, "Sara Malik", "03214445566", "sara@x.test", "qualified", 5000),
                ("zain", a, "Zain", None, None, "sold", 9000),
                ("b_lead", b, "Ayesha Khan", "+92 300 1112233", None, "new", 0)):
            cur.execute("INSERT INTO leads (tenant_id, name, phone, email, status, source, est_revenue, followup_at) "
                        "VALUES (%s, %s, %s, %s, %s, 'manual', %s, %s) RETURNING id",
                        (tid, name, phone, email, status, est, NOW + dt.timedelta(days=2) if key == "ayesha" else None))
            L[key] = str(cur.fetchone()["id"])
        cur.execute("INSERT INTO customers (tenant_id, name, phone) VALUES (%s, 'Ayesha K', '03001112233') RETURNING id", (a,))
        L["cust_ayesha"] = str(cur.fetchone()["id"])
        cur.execute("INSERT INTO customers (tenant_id, name, phone) VALUES (%s, 'Other B', '0399') RETURNING id", (b,))
        L["cust_b"] = str(cur.fetchone()["id"])
    roles.forget()
    h = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=p[k],
                                                        role=("operator" if k in ("staff", "shop") else "owner"))} for k in p}
    yield {"a": a, "b": b, "p": p, "L": L, "h": h}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def names(r):
    return sorted(x["name"] for x in r.json()["leads"])


def test_search_filters_sorting_and_pages_on_the_server(client, world):
    h = world["h"]["owner"]
    assert names(client.get("/crm/leads?q=ayesha", headers=h)) == ["Ayesha Khan"]                 # not business B's
    assert names(client.get("/crm/leads?q=1112233", headers=h)) == ["Ayesha Khan", "Bilal"]         # any spelling of the number
    assert names(client.get("/crm/leads?q=sara@x", headers=h)) == ["Sara Malik"]
    assert names(client.get("/crm/leads?status=qualified,sold", headers=h)) == ["Sara Malik", "Zain"]
    r = client.get("/crm/leads?sort=value&page_size=5", headers=h).json()
    assert r["total"] == 4 and [x["name"] for x in r["leads"]][:2] == ["Zain", "Sara Malik"] and r["by_status"]["new"] == 2
    r = client.get("/crm/leads?page_size=5&page=2", headers=h).json()
    assert r["leads"] == [] and r["total"] == 0
    assert client.get("/crm/leads?q=100%25", headers=h).json()["leads"] == []                       # a % is text, not a wildcard
    assert client.get("/crm/leads?sort=evil", headers=h).status_code == 400
    assert client.get("/crm/leads?status=nope", headers=h).status_code == 400
    assert names(client.get("/crm/leads?owner=none", headers=h)) == ["Ayesha Khan", "Bilal", "Sara Malik", "Zain"]


def test_lead_details_can_be_edited_with_checks(client, world):
    h, L = world["h"]["owner"], world["L"]
    r = client.post("/leads", json={"name": "Hamza", "phone": "0301 9998877", "email": "hamza@x.test", "company": "Hamza Traders"}, headers=h)
    assert r.status_code == 200
    hid = r.json()["id"]
    r = client.patch("/leads/" + hid, json={"est_revenue": 12000, "priority": 2, "company": "Hamza & Sons", "status": "qualified"}, headers=h)
    assert r.status_code == 200, r.text
    d = client.get("/crm/leads/" + hid, headers=h).json()["lead"]
    assert d["company"] == "Hamza & Sons" and d["est_revenue"] == 12000 and d["status_changed_at"]
    assert client.patch("/leads/" + hid, json={"email": "not-an-email"}, headers=h).status_code == 400
    assert client.patch("/leads/" + hid, json={"est_revenue": -5}, headers=h).status_code == 400
    assert client.patch("/leads/" + hid, json={"name": "  "}, headers=h).status_code == 400
    n = len(client.get("/crm/leads/" + hid, headers=h).json()["changes"])
    client.patch("/leads/" + hid, json={"est_revenue": 12000.0, "priority": 2}, headers=h)          # same values: nothing to record
    assert len(client.get("/crm/leads/" + hid, headers=h).json()["changes"]) == n
    world["L"]["hamza"] = hid


def test_follow_ups_views_and_overdue_stays_until_done(client, world):
    h, L = world["h"]["owner"], world["L"]
    yesterday = (NOW - dt.timedelta(days=1)).isoformat()
    r = client.post("/crm/activities", json={"lead_id": L["bilal"], "title": "Call about price", "due_at": yesterday, "priority": "high"}, headers=h)
    assert r.status_code == 201, r.text
    late = r.json()["activity"]
    assert late["assigned_to"] == world["p"]["owner"]                                             # mine unless given to someone
    later = (NOW + dt.timedelta(days=3)).isoformat()
    client.post("/crm/activities", json={"lead_id": L["sara"], "title": "Send offer", "due_at": later,
                                         "assigned_to": world["p"]["staff"]}, headers=h)
    v = lambda view: [a["title"] for a in client.get("/crm/activities?view=" + view, headers=h).json()["activities"]]  # noqa: E731
    assert v("overdue") == ["Call about price"] and "Send offer" in v("upcoming") and v("mine") == ["Call about price"]
    assert [x["name"] for x in client.get("/crm/leads?follow=overdue", headers=h).json()["leads"]] == ["Bilal"]
    assert "Ayesha Khan" in names(client.get("/crm/leads?follow=none", headers=h))                   # open lead, nothing planned
    r = client.patch("/crm/activities/" + late["id"], json={"done": True, "outcome": "Will visit Saturday"}, headers=h)
    assert r.status_code == 200 and r.json()["activity"]["done_at"]
    assert v("overdue") == [] and "Call about price" in v("done")
    # a call that happened: the lead was contacted
    client.post("/crm/activities", json={"lead_id": L["sara"], "kind": "call", "title": "Called", "done": True, "outcome": "Interested"}, headers=h)
    assert client.get("/crm/leads/" + L["sara"], headers=h).json()["lead"]["last_contacted_at"]
    n = client.post("/crm/activities", json={"lead_id": L["sara"], "kind": "note", "title": "Prefers evenings"}, headers=h).json()["activity"]
    assert n["done_at"] and "Prefers evenings" not in v("all")                                      # notes are history, not to-dos
    for bad in ({"lead_id": L["sara"], "title": ""}, {"lead_id": L["sara"], "title": "x", "kind": "fax"},
                {"title": "x"}, {"lead_id": L["sara"], "title": "x", "due_at": "tomorrow"},
                {"lead_id": L["sara"], "title": "x", "priority": "urgent"}):
        assert client.post("/crm/activities", json=bad, headers=h).status_code == 400, bad


def test_ai_follow_up_is_left_alone(client, world):
    """leads.followup_at belongs to the AI agent: CRM follow-ups never set or clear it."""
    h, L = world["h"]["owner"], world["L"]
    before = client.get("/crm/leads/" + L["ayesha"], headers=h).json()["lead"]["followup_at"]
    client.post("/crm/activities", json={"lead_id": L["ayesha"], "title": "Check in", "due_at": NOW.isoformat()}, headers=h)
    assert before and client.get("/crm/leads/" + L["ayesha"], headers=h).json()["lead"]["followup_at"] == before


def test_record_page_shows_history_and_possible_duplicates(client, world):
    h, L = world["h"]["owner"], world["L"]
    d = client.get("/crm/leads/" + L["ayesha"], headers=h).json()
    dup = {(x["type"], x["name"]): x["why"] for x in d["duplicates"]}
    assert dup == {("lead", "Bilal"): "same phone number", ("customer", "Ayesha K"): "same phone number"}   # not business B's
    assert [a["title"] for a in d["activities"]] == ["Check in"]
    hz = client.get("/crm/leads/" + L["hamza"], headers=h).json()
    assert any(c["action"] == "lead.update" for c in hz["changes"])
    g = client.get("/crm/duplicates", headers=h).json()["groups"]
    assert len(g) == 1 and {r["name"] for r in g[0]["records"]} == {"Ayesha Khan", "Bilal", "Ayesha K"}
    assert client.get("/crm/duplicates/check?phone=0300%201112233", headers=h).json()["matches"]


def test_conversion_reuses_a_matching_customer_and_is_safe_to_repeat(client, world):
    h, L = world["h"]["owner"], world["L"]
    count = lambda: client.get("/business/customers", headers=h).json()["items"].__len__()  # noqa: E731
    n0 = count()
    r = client.post("/crm/leads/%s/convert" % L["ayesha"], json={}, headers=h).json()
    assert r == {"customer_id": L["cust_ayesha"], "already": False, "created": False} and count() == n0
    assert client.post("/crm/leads/%s/convert" % L["ayesha"], json={}, headers=h).json()["already"] is True
    r = client.post("/crm/leads/%s/convert" % L["sara"], json={}, headers=h).json()
    assert r["created"] is True and count() == n0 + 1
    acts = client.get("/crm/activities?view=done&customer_id=" + r["customer_id"], headers=h).json()["activities"]
    assert {a["title"] for a in acts} >= {"Called", "Prefers evenings"}                            # the history moved with them
    with tenant_session(world["a"], "owner") as cur:
        cur.execute("INSERT INTO customers (tenant_id, name, phone) VALUES (current_tenant(), 'Bilal Senior', '+923001112233')")
    r = client.post("/crm/leads/%s/convert" % L["bilal"], json={}, headers=h)
    assert r.status_code == 409 and len(r.json()["detail"]["candidates"]) == 2                     # two match: the owner chooses
    r = client.post("/crm/leads/%s/convert" % L["bilal"], json={"customer_id": world["L"]["cust_b"]}, headers=h)
    assert r.status_code == 404                                                                    # not another business's customer


def test_links_stay_inside_one_business(client, world):
    h, L, hb = world["h"]["owner"], world["L"], world["h"]["b_owner"]
    assert client.post("/crm/activities", json={"lead_id": L["b_lead"], "title": "x"}, headers=h).status_code == 404
    assert client.post("/crm/activities", json={"customer_id": L["cust_b"], "title": "x"}, headers=h).status_code == 404
    assert client.post("/crm/activities", json={"lead_id": L["sara"], "title": "x", "assigned_to": world["p"]["b_owner"]},
                       headers=h).status_code == 400
    aid = client.get("/crm/activities?view=all", headers=h).json()["activities"][0]["id"]
    assert client.patch("/crm/activities/" + aid, json={"done": True}, headers=hb).status_code == 404
    assert client.get("/crm/leads/" + L["sara"], headers=hb).status_code == 404
    assert client.get("/crm/activities?view=all", headers=hb).json()["activities"] == []
    # the database refuses it too, even without the API
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with tenant_session(world["a"], "owner") as cur:
            cur.execute("INSERT INTO crm_activities (tenant_id, lead_id, kind, title) VALUES (current_tenant(), %s, 'task', 'x')", (L["b_lead"],))


def test_roles(client, world):
    h, L = world["h"], world["L"]
    assert client.get("/crm/leads", headers=h["hr"]).status_code == 403                            # HR role: no CRM
    assert client.get("/crm/leads", headers=h["shop"]).status_code == 200                          # Shopkeeper: CRM work
    a = client.post("/crm/activities", json={"lead_id": L["sara"], "kind": "note", "title": "Owner note"}, headers=h["owner"]).json()["activity"]
    assert client.delete("/crm/activities/" + a["id"], headers=h["staff"]).status_code == 403      # not theirs
    mine = client.post("/crm/activities", json={"lead_id": L["sara"], "kind": "note", "title": "Staff note"}, headers=h["staff"]).json()["activity"]
    assert client.delete("/crm/activities/" + mine["id"], headers=h["staff"]).status_code == 200
    assert client.delete("/crm/activities/" + a["id"], headers=h["owner"]).status_code == 200
    assert {p["name"] for p in client.get("/crm/team", headers=h["owner"]).json()["people"]} == {"Owner", "Staff", "Shop", "Hr"}
