"""Backend batch 1: login has no demo bypass, PATCH /leads/{id}, offers, workspace settings.

Runs against a SCRATCH database only (a restored copy with db/65 and db/66 applied).
It creates two throwaway tenants and deletes them at the end; everything they
own goes with them (ON DELETE CASCADE).
"""
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

TAG = "batch1-test-" + uuid.uuid4().hex[:8]


def _owner(tid):
    return {"X-Tenant-Id": tid, "X-Role": "owner"}


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture(scope="module")
def world():
    """Two businesses, A and B, each with one lead and one user."""
    out = {}
    with tenant_session(None, "techdeva_admin") as cur:
        for k in ("a", "b"):
            cur.execute("INSERT INTO tenants (name, industry) VALUES (%s, 'test') RETURNING id",
                        ("%s-%s" % (TAG, k),))
            tid = str(cur.fetchone()["id"])
            cur.execute("INSERT INTO leads (tenant_id, name, phone, source) "
                        "VALUES (%s, 'Test Lead', '03000000000', 'manual') RETURNING id", (tid,))
            lead = str(cur.fetchone()["id"])
            cur.execute("INSERT INTO users (tenant_id, name, email, role) "
                        "VALUES (%s, 'Front desk', %s, 'operator') RETURNING id",
                        (tid, "%s-%s@techdeva.test" % (TAG, k)))
            out[k] = {"tid": tid, "lead": lead, "user": str(cur.fetchone()["id"])}
    yield out
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM tenants WHERE name LIKE %s", (TAG + "-%",))
        cur.execute("SELECT count(*) n FROM tenants WHERE name LIKE %s", (TAG + "-%",))
        assert cur.fetchone()["n"] == 0


# ------------------------------------------------------------------ login
def test_demo_login_is_refused(client):
    for email in ("admin@cantonment.gov.pk", "operator@cantonment.gov.pk"):
        res = client.post("/auth/login", json={"email": email, "password": "not-a-real-password"})
        assert res.status_code == 401, res.text


# ------------------------------------------------------------- PATCH /leads
def test_lead_bad_status_is_400(client, world):
    a = world["a"]
    res = client.patch("/leads/" + a["lead"], headers=_owner(a["tid"]), json={"status": "won"})
    assert res.status_code == 400
    assert "qualified" in res.json()["detail"]


def test_lead_unknown_field_is_400(client, world):
    a = world["a"]
    res = client.patch("/leads/" + a["lead"], headers=_owner(a["tid"]), json={"assigned_to": a["user"]})
    assert res.status_code == 400


def test_other_tenants_lead_is_404(client, world):
    res = client.patch("/leads/" + world["b"]["lead"], headers=_owner(world["a"]["tid"]),
                       json={"status": "qualified"})
    assert res.status_code == 404
    with tenant_session(world["b"]["tid"], "owner") as cur:
        cur.execute("SELECT status FROM leads WHERE id = %s", (world["b"]["lead"],))
        assert cur.fetchone()["status"] == "new"


def test_cannot_assign_another_businesss_user(client, world):
    a = world["a"]
    res = client.patch("/leads/" + a["lead"], headers=_owner(a["tid"]),
                       json={"assigned_operator": world["b"]["user"]})
    assert res.status_code == 400


def test_lead_update_returns_lead_and_leaves_a_trail(client, world):
    a = world["a"]
    res = client.patch("/leads/" + a["lead"], headers=_owner(a["tid"]),
                       json={"status": "qualified", "notes": "called back", "assigned_operator": a["user"]})
    assert res.status_code == 200, res.text
    lead = res.json()["lead"]
    assert set(lead) == {"id", "name", "phone", "service", "status", "source", "priority", "est_revenue"}
    assert lead["status"] == "qualified" and str(lead["id"]) == a["lead"]
    with tenant_session(a["tid"], "owner") as cur:
        cur.execute("SELECT notes, assigned_operator FROM leads WHERE id = %s", (a["lead"],))
        row = cur.fetchone()
        assert row["notes"] == "called back" and str(row["assigned_operator"]) == a["user"]
        cur.execute("SELECT payload FROM events WHERE type = 'lead.updated' "
                    "AND payload->>'lead_id' = %s", (a["lead"],))
        ev = cur.fetchone()["payload"]
        assert ev["changes"]["status"] == {"from": "new", "to": "qualified"}
        cur.execute("SELECT count(*) n FROM executions WHERE action = 'lead.update' AND resource_id = %s",
                    (a["lead"],))
        assert cur.fetchone()["n"] == 1


# ------------------------------------------------------------------ offers
def test_offers_create_list_update_delete(client, world):
    h = _owner(world["a"]["tid"])
    res = client.post("/business/offers", headers=h,
                      json={"title": "Eid 10% off", "discount_type": "percent", "discount_value": 10,
                            "starts_on": "2026-10-20", "ends_on": "2026-10-31"})
    assert res.status_code == 200, res.text
    offer = res.json()["item"]
    assert offer["applies_to"] == "all" and offer["active"] is True

    listed = client.get("/business/offers", headers=h).json()
    assert [o["id"] for o in listed["items"]] == [offer["id"]]
    # another business sees none of it
    assert client.get("/business/offers", headers=_owner(world["b"]["tid"])).json()["count"] == 0

    res = client.patch("/business/offers/" + str(offer["id"]), headers=h, json={"active": False})
    assert res.status_code == 200 and res.json()["item"]["active"] is False
    assert client.patch("/business/offers/" + str(offer["id"]), headers=_owner(world["b"]["tid"]),
                        json={"active": True}).status_code == 404

    assert client.delete("/business/offers/" + str(offer["id"]), headers=h).status_code == 200
    assert client.get("/business/offers", headers=h).json()["count"] == 0


def test_offers_refuse_bad_values(client, world):
    h = _owner(world["a"]["tid"])
    base = {"title": "x", "discount_type": "percent", "discount_value": 10}
    for bad in ({"discount_value": 0}, {"discount_value": 150}, {"discount_type": "free"},
                {"title": "  "}, {"starts_on": "2026-11-02", "ends_on": "2026-11-01"},
                {"applies_to": "service"},                                # no item named
                {"applies_to": "service", "item_id": str(uuid.uuid4())},  # not this business's
                {"item_id": str(uuid.uuid4())}):                          # 'all' with an item
        res = client.post("/business/offers", headers=h, json={**base, **bad})
        assert res.status_code == 400, (bad, res.text)
    assert client.get("/business/offers", headers=h).json()["count"] == 0


def test_offers_are_owner_only(client, world):
    res = client.post("/business/offers", headers={"X-Tenant-Id": world["a"]["tid"], "X-Role": "operator"},
                      json={"title": "x", "discount_type": "amount", "discount_value": 500})
    assert res.status_code == 403


# ------------------------------------------------------- workspace settings
def test_config_accepts_workspace_settings(client, world):
    h = _owner(world["a"]["tid"])
    res = client.put("/portal/config", headers=h,
                     json={"timezone": "Asia/Karachi", "currency": "PKR", "language": "ur"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["saved"] == ["currency", "language", "timezone"]
    assert body["config"]["language"] == "ur" and body["config"]["timezone"] == "Asia/Karachi"


@pytest.mark.parametrize("bad", [{"timezone": "Mars/Base"}, {"currency": "pkr"},
                                 {"currency": "RUPEES"}, {"language": "fr"}])
def test_config_rejects_bad_workspace_settings(client, world, bad):
    res = client.put("/portal/config", headers=_owner(world["a"]["tid"]), json=bad)
    assert res.status_code == 422, res.text


def test_config_workspace_settings_owner_only(client, world):
    res = client.put("/portal/config", headers={"X-Tenant-Id": world["a"]["tid"], "X-Role": "operator"},
                     json={"timezone": "Asia/Dubai"})
    assert res.status_code == 403
