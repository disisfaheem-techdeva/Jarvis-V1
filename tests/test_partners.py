"""White-label partners (db/87, app/partners.py).

What is proved here, in order of how much it would cost to get wrong:

1. An agency sees the businesses it brought and NOTHING inside them — not a
   lead, a conversation, an appointment or a connected ad account.
2. An agency never sees another agency's businesses, logins or wholesale terms.
3. A business owner is completely unaffected: still sees only itself, and cannot
   see that partners exist at all, nor what its agency pays TechDeva.
4. A direct TechDeva client (partner_id NULL — the shape every existing business
   has, including the clinic) is untouched.
5. A partner token is refused by every non-partner route.
6. A partner cannot create, rename or take over a business.
7. Suspending an agency locks its dashboard and leaves its customers running.

Runs against a SCRATCH database only. Every business and agency it makes is
deleted at the end.
"""
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app.auth import hash_password, issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

PW = "partner-pass-12345"


@pytest.fixture(scope="module")
def world():
    """Two agencies, a business each, and one direct TechDeva client."""
    ids = {k: str(uuid.uuid4()) for k in
           ("pa", "pb", "ta", "tb", "direct", "ta_hidden",
            "ua", "ub", "owner_a", "owner_direct", "founder")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO partners (id, name, slug, status, country) VALUES "
                    "(%s,'Gulf Agency','ptest-gulf','active','AE'),"
                    "(%s,'London Agency','ptest-london','active','GB')", (ids["pa"], ids["pb"]))
        # is_test=false for the three real ones: the partner list hides test
        # businesses exactly as the founder's book does, and that is also proved.
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test, partner_id) VALUES "
                    "(%s,'Partner A Clinic','clinic','active',false,%s),"
                    "(%s,'Partner B Salon','salon','active',false,%s),"
                    "(%s,'Direct Client','clinic','active',false,NULL),"
                    "(%s,'Partner A Scratch','clinic','active',true,%s)",
                    (ids["ta"], ids["pa"], ids["tb"], ids["pb"], ids["direct"],
                     ids["ta_hidden"], ids["pa"]))
        # a customer's own data, which no agency may read
        cur.execute("INSERT INTO leads (tenant_id, name, service, source) VALUES (%s,'Ayesha','HydraFacial','manual')",
                    (ids["ta"],))
        cur.execute("INSERT INTO integrations (tenant_id, provider, status, external_account, "
                    "credentials, connected_at) "
                    "VALUES (%s,'meta','connected','act_999','{\"access_token\":\"secret\"}',now())",
                    (ids["ta"],))
        # business owners
        for key, tid in (("owner_a", ids["ta"]), ("owner_direct", ids["direct"])):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s,%s,'Owner',%s,'owner')",
                        (ids[key], tid, "%s@ptest.local" % ids[key][:8]))
        # partner logins, password already set
        for key, pid in (("ua", ids["pa"]), ("ub", ids["pb"])):
            cur.execute("INSERT INTO partner_users (id, partner_id, email, name, password_hash, role) "
                        "VALUES (%s,%s,%s,'Agency Person',%s,'partner_owner')",
                        (ids[key], pid, "%s@ptest-agency.local" % ids[key][:8], hash_password(PW)))
        # wholesale terms, different for each agency on purpose
        cur.execute("INSERT INTO partner_agreements (partner_id, plan_key, wholesale_price) VALUES "
                    "(%s,'growth',90),(%s,'growth',85)", (ids["pa"], ids["pb"]))

    h = {
        "pa": {"Authorization": "Bearer " + issue_token(tenant_id=None, user_id=ids["ua"],
                                                         role="partner", partner_id=ids["pa"])},
        "pb": {"Authorization": "Bearer " + issue_token(tenant_id=None, user_id=ids["ub"],
                                                         role="partner", partner_id=ids["pb"])},
        "owner_a": {"Authorization": "Bearer " + issue_token(tenant_id=ids["ta"], user_id=ids["owner_a"],
                                                              role="owner")},
        "owner_direct": {"Authorization": "Bearer " + issue_token(tenant_id=ids["direct"],
                                                                   user_id=ids["owner_direct"], role="owner")},
        "founder": {"Authorization": "Bearer " + issue_token(tenant_id=None, user_id=ids["founder"],
                                                              role="techdeva_admin")},
    }
    yield {"ids": ids, "h": h}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (ids["ta"], ids["tb"], ids["direct"], ids["ta_hidden"]):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("DELETE FROM partners WHERE slug IN ('ptest-gulf','ptest-london')")
        cur.execute("DELETE FROM partners WHERE slug LIKE 'ptest-%'")


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


# ----------------------------------------------------- 1 + 2. what an agency sees
def test_agency_sees_only_its_own_clients(client, world):
    r = client.get("/partner/clients", headers=world["h"]["pa"]).json()
    # its own business, and not the test business that also belongs to it
    assert [c["name"] for c in r["clients"]] == ["Partner A Clinic"], r
    assert r["count"] == 1
    assert "Partner A Scratch" not in [c["name"] for c in r["clients"]]

    r = client.get("/partner/clients", headers=world["h"]["pb"]).json()
    assert [c["name"] for c in r["clients"]] == ["Partner B Salon"], r


def test_agency_sees_nothing_inside_a_customer(client, world):
    """The agency brought the business; it is not inside the business."""
    h = world["h"]["pa"]
    pid = world["ids"]["pa"]
    with tenant_session(None, "partner", partner_id=pid) as cur:
        for table in ("leads", "conversations", "messages", "appointments",
                      "integrations", "revenue_events", "customers", "invoices"):
            cur.execute("SELECT count(*) AS n FROM %s" % table)
            assert cur.fetchone()["n"] == 0, "a partner could read %s" % table
    # and no client route will serve a partner token either
    assert client.get("/crm/leads", headers=h).status_code in (401, 403)


def test_agency_sees_only_its_own_terms_and_identity(client, world):
    r = client.get("/partner/me", headers=world["h"]["pa"]).json()
    assert r["partner"]["name"] == "Gulf Agency"
    assert [float(a["wholesale_price"]) for a in r["agreements"]] == [90.0], r

    r = client.get("/partner/me", headers=world["h"]["pb"]).json()
    assert r["partner"]["name"] == "London Agency"
    assert [float(a["wholesale_price"]) for a in r["agreements"]] == [85.0], r


# -------------------------------------------- 3 + 4. the existing world is intact
def test_a_business_owner_is_unaffected(client, world):
    ids, h = world["ids"], world["h"]
    with tenant_session(ids["ta"], "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM tenants")
        assert cur.fetchone()["n"] == 1                      # still only itself
        cur.execute("SELECT count(*) AS n FROM leads")
        assert cur.fetchone()["n"] == 1                      # still has its own lead
        cur.execute("SELECT count(*) AS n FROM partners")
        assert cur.fetchone()["n"] == 0                      # partners do not exist to it
        cur.execute("SELECT count(*) AS n FROM partner_agreements")
        assert cur.fetchone()["n"] == 0                      # nor what its agency pays us
    assert client.get("/techdeva/partners", headers=h["owner_a"]).status_code == 403


def test_a_direct_techdeva_client_is_untouched(client, world):
    """Every business that exists today has partner_id NULL. This is that shape."""
    with tenant_session(world["ids"]["direct"], "owner") as cur:
        cur.execute("SELECT name, partner_id FROM tenants")
        rows = cur.fetchall()
        assert len(rows) == 1 and rows[0]["name"] == "Direct Client"
        assert rows[0]["partner_id"] is None


# --------------------------------------------------- 5. a partner token is fenced
def test_partner_token_is_refused_everywhere_else(client, world):
    h = world["h"]["pa"]
    for path in ("/crm/leads", "/techdeva/book", "/techdeva/partners", "/portal/config"):
        assert client.get(path, headers=h).status_code in (401, 403), path


def test_a_business_token_cannot_reach_the_partner_dashboard(client, world):
    for key in ("owner_a", "owner_direct", "founder"):
        r = client.get("/partner/clients", headers=world["h"][key])
        assert r.status_code == 403, (key, r.status_code)


# ------------------------------------- 6. an agency cannot write to the platform
def test_agency_cannot_create_rename_or_take_over_a_business(world):
    ids = world["ids"]
    with tenant_session(None, "partner", partner_id=ids["pa"]) as cur:
        cur.execute("UPDATE tenants SET name = 'Renamed' WHERE id = %s", (ids["ta"],))
        assert cur.rowcount == 0
        cur.execute("UPDATE tenants SET partner_id = %s WHERE id = %s", (ids["pa"], ids["tb"]))
        assert cur.rowcount == 0, "an agency took over another agency's client"
    # the names really did not change
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT name, partner_id FROM tenants WHERE id = %s", (ids["ta"],))
        assert cur.fetchone()["name"] == "Partner A Clinic"
        cur.execute("SELECT partner_id FROM tenants WHERE id = %s", (ids["tb"],))
        assert str(cur.fetchone()["partner_id"]) == ids["pb"]


# ---------------------------------------------------------- 7. suspension, login
def test_login_then_suspension_locks_the_agency_not_its_customers(client, world):
    ids = world["ids"]
    email = "%s@ptest-agency.local" % ids["ua"][:8]

    r = client.post("/partner/login", json={"email": email, "password": PW})
    assert r.status_code == 200, r.text
    assert r.json()["partner"] == "Gulf Agency"
    token = r.json()["token"]
    assert client.get("/partner/clients",
                      headers={"Authorization": "Bearer " + token}).status_code == 200

    assert client.post("/partner/login", json={"email": email, "password": "wrong"}).status_code in (401, 429)

    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("UPDATE partners SET status = 'suspended' WHERE id = %s", (ids["pa"],))

    # the dashboard closes, even on a token issued before the suspension
    assert client.get("/partner/clients",
                      headers={"Authorization": "Bearer " + token}).status_code == 403
    assert client.post("/partner/login", json={"email": email, "password": PW}).status_code == 403

    # the customer keeps working: a suspended agency does not close a business
    assert client.get("/portal/config", headers=world["h"]["owner_a"]).status_code == 200

    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("UPDATE partners SET status = 'active' WHERE id = %s", (ids["pa"],))


# ------------------------------------------------------- the founder's own side
def test_founder_creates_an_agency_sets_terms_and_assigns_a_client(client, world):
    h = world["h"]["founder"]
    r = client.post("/techdeva/partners", headers=h,
                    json={"name": "Toronto Agency", "slug": "ptest-toronto", "country": "CA"})
    assert r.status_code == 200, r.text
    pid = r.json()["id"]
    assert r.json()["status"] == "pending"                   # approval is not automatic

    assert client.post("/techdeva/partners", headers=h,
                       json={"name": "Dup", "slug": "ptest-toronto"}).status_code == 409
    assert client.post("/techdeva/partners", headers=h,
                       json={"name": "Bad", "slug": "techdeva"}).status_code == 400

    r = client.post("/techdeva/partners/%s/agreement" % pid, headers=h,
                    json={"plan_key": "growth", "wholesale_price": 95, "currency": "USD"})
    assert r.status_code == 200, r.text
    assert client.post("/techdeva/partners/%s/agreement" % pid, headers=h,
                       json={"plan_key": "nosuchplan", "wholesale_price": 1}).status_code == 400
    assert client.post("/techdeva/partners/%s/agreement" % pid, headers=h,
                       json={"plan_key": "growth", "wholesale_price": -5}).status_code == 400

    # a business already with another agency is not moved silently
    tb = world["ids"]["tb"]
    assert client.post("/techdeva/partners/%s/clients" % pid, headers=h,
                       json={"tenant_id": tb}).status_code == 409
    r = client.post("/techdeva/partners/%s/clients" % pid, headers=h,
                    json={"tenant_id": tb, "reassign": True})
    assert r.status_code == 200 and r.json()["previous_partner_id"] == world["ids"]["pb"]

    # ...and the old agency has lost it, the new one has it
    assert client.get("/partner/clients", headers=world["h"]["pb"]).json()["count"] == 0
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("UPDATE tenants SET partner_id = %s WHERE id = %s", (world["ids"]["pb"], tb))


def test_founder_creates_a_login_that_needs_a_password_set(client, world):
    h = world["h"]["founder"]
    pid = world["ids"]["pa"]
    email = "newperson-%s@ptest-agency.local" % uuid.uuid4().hex[:6]
    r = client.post("/techdeva/partners/%s/users" % pid, headers=h, json={"email": email, "name": "New"})
    assert r.status_code == 200, r.text
    token = r.json()["set_password_token"]

    # no password yet: no sign-in
    assert client.post("/partner/login", json={"email": email, "password": PW}).status_code in (401, 429)

    assert client.post("/partner/set-password", json={"token": token, "password": "short"}).status_code == 400
    assert client.post("/partner/set-password", json={"token": token, "password": PW}).status_code == 200
    # the link is single-use: setting the password changed what it was bound to
    assert client.post("/partner/set-password", json={"token": token, "password": PW}).status_code == 400

    assert client.post("/techdeva/partners/%s/users" % pid, headers=h,
                       json={"email": email}).status_code == 409
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM partner_users WHERE lower(email) = %s", (email.lower(),))
