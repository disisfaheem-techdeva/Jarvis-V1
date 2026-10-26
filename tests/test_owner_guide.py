"""The owner's full guide for the WhatsApp agent (db/82): kept exactly as
written, split into stored parts, written into the agent's knowledge block
before the hard rule, replaced on save. Scratch database; the agent is a fake."""
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import categories  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

EXAMPLE = ('Example 1: Customer asks about delivery\n\nCustomer: "Order kitni der mein aa jayega?"\n\n'
           'Bot: "Ji 🚚 Hamara usual delivery estimate approximately 2 hours hai. Exact timing aapki location '
           'aur current availability par depend karti hai."\n\n')
GUIDE = ("You are the WhatsApp assistant for the shop. Be warm and short.\n\n" + EXAMPLE * 30).strip()


@pytest.fixture(scope="module")
def world():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    p = {k: str(uuid.uuid4()) for k in ("owner", "staff", "b_owner")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test, business_type) VALUES "
                    "(%s, 'Guide Grill', 'food', 'active', true, 'restaurant'), (%s, 'Other', 'x', 'active', true, 'shop')", (a, b))
        for k, tid, role in (("owner", a, "owner"), ("staff", a, "operator"), ("b_owner", b, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (p[k], tid, k, "%s-%s@guide-test.local" % (k, p[k][:8]), role))
        cur.execute("INSERT INTO products (tenant_id, name, price, active) VALUES (%s, 'BBQ Box', 2500, true)", (a,))
    h = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=p[k],
                                                        role=("operator" if k == "staff" else "owner"))} for k in p}
    yield {"a": a, "b": b, "h": h}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM agent_knowledge_sync WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


@pytest.fixture
def agent(monkeypatch, world):
    """A fake WhatsApp agent for business A: records what reaches its SOUL.md."""
    got = []
    monkeypatch.setattr(categories, "_rows", lambda cur, tid: [{"agent_key": "guidegrill", "pairctl_url": "http://agent"}]
                        if str(tid) == world["a"] else [])
    monkeypatch.setattr(categories, "_pairctl_json", lambda url, path, method, body: got.append((path, body["text"])) or {})
    return got


def test_splitting_keeps_every_character():
    parts = categories.split_guide(GUIDE)
    assert len(parts) > 1 and all(len(x) <= categories.GUIDE_PART for x in parts)
    assert "".join(parts) == GUIDE
    one_line = "word " * 900
    assert "".join(categories.split_guide(one_line)) == one_line


def test_the_guide_reaches_the_agent_exactly_as_written(world, agent):
    c = TestClient(app)
    r = c.put("/business-profile/guide", json={"text": GUIDE}, headers=world["h"]["owner"])
    assert r.status_code == 200, r.text
    assert r.json()["chars"] == len(GUIDE) and r.json()["parts"] >= 3 and r.json()["sync"].startswith("synced")
    g = c.get("/business-profile/guide", headers=world["h"]["owner"]).json()
    assert g["text"] == GUIDE and g["max"] == 20000
    path, text = agent[-1]
    assert path == "/soul/knowledge" and GUIDE in text                                    # line breaks and examples intact
    assert text.index("## The owner's guide") < text.index("## Greeting") < text.index("## Hard rule")
    assert "the list wins" in text and "BBQ Box: Rs 2,500" in text


def test_saving_again_replaces_and_empty_removes(world, agent):
    c, h = TestClient(app), world["h"]["owner"]
    c.put("/business-profile/guide", json={"text": GUIDE}, headers=h)
    r = c.put("/business-profile/guide", json={"text": "Only this new guide, nothing from before."}, headers=h)
    assert r.json()["parts"] == 1
    assert c.get("/business-profile/guide", headers=h).json()["text"] == "Only this new guide, nothing from before."
    assert "Example 1" not in agent[-1][1]
    assert c.put("/business-profile/guide", json={"text": ""}, headers=h).json()["parts"] == 0
    assert "## The owner's guide" not in agent[-1][1]
    with tenant_session(world["a"], "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM business_knowledge WHERE kind = 'guide' AND active")
        assert cur.fetchone()["n"] == 0


def test_limits_roles_and_other_businesses(world, agent):
    c = TestClient(app)
    assert c.put("/business-profile/guide", json={"text": "x" * 20001}, headers=world["h"]["owner"]).status_code == 400
    assert c.put("/business-profile/guide", json={"text": "too short"}, headers=world["h"]["owner"]).status_code == 400
    assert c.put("/business-profile/guide", json={"text": GUIDE}, headers=world["h"]["staff"]).status_code == 403
    c.put("/business-profile/guide", json={"text": GUIDE}, headers=world["h"]["owner"])
    assert c.get("/business-profile/guide", headers=world["h"]["b_owner"]).json()["text"] == ""   # B sees none of A's
    # the short rules box keeps its own limit; Jarvis still asks for a sentence or two there
    assert c.post("/console", json={"message": "x" * 2001}, headers=world["h"]["owner"]).status_code == 400
