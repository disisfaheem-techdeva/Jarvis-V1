"""TechDeva POS as a separate product (5 Nov 2026): its own address, and on
only for the businesses TechDeva switches it on for. Needs db/90 and db/91."""
import uuid

import pytest
from starlette.testclient import TestClient

from app.auth import issue_token
from app.db import tenant_session
from app.main import app

T = str(uuid.uuid4())
OWNER_ID, FOUNDER_ID = str(uuid.uuid4()), str(uuid.uuid4())
OWNER = {"Authorization": "Bearer " + issue_token(tenant_id=T, user_id=OWNER_ID, role="owner")}
FOUNDER = {"Authorization": "Bearer " + issue_token(tenant_id=None, user_id=FOUNDER_ID, role="techdeva_admin")}
POS_HOST = {"host": "pos.techdeva.net"}


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test) VALUES (%s, 'POS Off Shop', 'retail', false)", (T,))
        cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, 'Owner', %s, 'owner')",
                    (OWNER_ID, T, "%s@posoff.local" % OWNER_ID[:8]))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM executions WHERE tenant_id = %s", (T,))
        cur.execute("DELETE FROM tenants WHERE id = %s", (T,))


def test_off_by_default_and_refused(client):
    s = client.get("/pos/status", headers=OWNER).json()
    assert s["enabled"] is False and s["url"].startswith("https://")
    r = client.get("/pos/catalogue", headers=OWNER)
    assert r.status_code == 403 and "not on" in r.json()["detail"]
    r = client.post("/pos/sale", headers=OWNER, json={"client_ref": uuid.uuid4().hex, "items": []})
    assert r.status_code == 403


def test_only_the_founder_switches_it(client):
    assert client.put("/techdeva/pos/" + T, headers=OWNER, json={"enabled": True}).status_code == 403
    r = client.put("/techdeva/pos/" + T, headers=FOUNDER, json={"enabled": True})
    assert r.status_code == 200 and r.json()["enabled"] is True
    assert client.get("/pos/status", headers=OWNER).json()["enabled"] is True
    assert client.get("/pos/catalogue", headers=OWNER).status_code == 200
    listed = client.get("/techdeva/pos", headers=FOUNDER).json()["businesses"]
    assert any(b["id"] == T and b["enabled"] for b in listed)
    assert client.get("/techdeva/pos", headers=OWNER).status_code == 403
    client.put("/techdeva/pos/" + T, headers=FOUNDER, json={"enabled": False})
    assert client.get("/pos/catalogue", headers=OWNER).status_code == 403


def test_owner_settings_cannot_switch_it_on(client):
    client.put("/pos/settings", headers=OWNER, json={"enabled": True})
    assert client.get("/pos/status", headers=OWNER).json()["enabled"] is False


def test_its_own_address(client):
    r = client.get("/", headers=POS_HOST)
    assert r.status_code == 200 and "TechDeva POS" in r.text and "pos-till.js" in r.text
    r = client.get("/login", headers=POS_HOST)
    assert r.status_code == 200 and "Staff PIN" in r.text
    r = client.get("/pos", follow_redirects=False)                     # old address on api.techdeva.net
    assert r.status_code == 302 and r.headers["location"].endswith("/")
    assert "pos.techdeva.net" in r.headers["location"]
    assert "Staff login" in client.get("/login").text or client.get("/login").status_code == 200


def test_pos_is_not_a_business_name():
    from app.subdomains import RESERVED
    assert "pos" in RESERVED
