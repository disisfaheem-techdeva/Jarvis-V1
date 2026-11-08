"""Countries (Phase 10, db/97, app/countries.py).

Checked: nine countries and no tax rate on any of them (even one set by hand
stays hidden until validated); choosing a country fills the currency and time
zone but keeps a language already chosen; an unknown country or a time zone
outside the country is refused; only the owner chooses, while staff can still
read the list; one business's country never reaches another; td_local_today()
follows the business's own time zone; the choice is audited.

Runs against a SCRATCH database only. Makes its own businesses and deletes them.
"""
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

K, B = str(uuid.uuid4()), str(uuid.uuid4())
U = {k: str(uuid.uuid4()) for k in ("owner", "cashier", "b_owner")}


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER, CASHIER, B_OWNER = (_bearer(K, U["owner"], "owner"), _bearer(K, U["cashier"], "operator"),
                           _bearer(B, U["b_owner"], "owner"))


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'Country Shop', 'grocery', true, 'karyana'), (%s, 'Other Shop', 'grocery', true, 'karyana')",
                    (K, B))
        for k, tid, role in (("owner", K, "owner"), ("cashier", K, "operator"), ("b_owner", B, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@country-test.local" % U[k][:8], role))
        # this business already chose Urdu: choosing a country must keep it
        cur.execute("INSERT INTO tenant_config (tenant_id, language) VALUES (%s, 'ur')", (K,))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, B):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


def test_nine_countries_no_tax(client):
    r = client.get("/countries", headers=OWNER)
    assert r.status_code == 200
    cs = r.json()["countries"]
    assert [c["code"] for c in cs] == ["PK", "AE", "SA", "QA", "OM", "GB", "CA", "US", "AU"]
    assert all(c["tax_rate"] is None and c["tax_validated"] is False for c in cs)
    pk = cs[0]
    assert pk["currency"] == "PKR" and pk["currency_symbol"] == "Rs" and pk["timezones"] == ["Asia/Karachi"]
    assert "jazzcash" in pk["payment_methods"]


def test_unvalidated_rate_is_never_shown(client):
    """Even if a rate gets into the table, the API hides it until it is validated."""
    from app.countries import _row
    row = {"code": "AE", "name": "UAE", "currency": "AED", "currency_symbol": "AED", "timezones": ["Asia/Dubai"],
           "languages": ["ar"], "date_format": "DD/MM/YYYY", "payment_methods": ["cash"], "tax_name": "VAT",
           "tax_rate": 5, "tax_validated": False}
    assert _row(row)["tax_rate"] is None
    assert _row(dict(row, tax_validated=True))["tax_rate"] == 5.0


def test_staff_read_list_but_cannot_choose(client):
    assert client.get("/countries", headers=CASHIER).status_code == 200
    assert client.put("/business-profile/country", headers=CASHIER, json={"country": "AE"}).status_code == 403


def test_no_country_yet_assumes_pakistan(client):
    r = client.get("/business-profile/country", headers=B_OWNER).json()
    assert r["country"] is None and r["assumed"] is True and r["details"]["code"] == "PK"


def test_refuses_unknown_country_and_wrong_zone(client):
    assert client.put("/business-profile/country", headers=OWNER, json={"country": "ZZ"}).status_code == 400
    r = client.put("/business-profile/country", headers=OWNER, json={"country": "AE", "timezone": "Asia/Karachi"})
    assert r.status_code == 400 and "Asia/Dubai" in r.json()["detail"]
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT country FROM tenant_config WHERE tenant_id = %s", (K,))
        assert cur.fetchone()["country"] is None


def test_choose_fills_currency_and_zone_keeps_language(client):
    r = client.put("/business-profile/country", headers=OWNER, json={"country": "ca", "timezone": "America/Vancouver"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert (d["country"], d["currency"], d["timezone"], d["language"]) == ("CA", "CAD", "America/Vancouver", "ur")
    assert d["assumed"] is False
    # no time zone given: the country's first
    d = client.put("/business-profile/country", headers=OWNER, json={"country": "AE"}).json()
    assert (d["currency"], d["timezone"], d["language"]) == ("AED", "Asia/Dubai", "ur")
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT detail FROM executions WHERE tenant_id = %s AND action = 'business.country'",
                    (K,))
        rows = [r["detail"] for r in cur.fetchall()]
    assert sorted(x["country"] for x in rows) == ["AE", "CA"]


def test_language_filled_when_none(client):
    d = client.put("/business-profile/country", headers=B_OWNER, json={"country": "GB"}).json()
    assert (d["currency"], d["timezone"], d["language"]) == ("GBP", "Europe/London", "en")


def test_businesses_stay_apart(client):
    a = client.get("/business-profile/country", headers=OWNER).json()
    b = client.get("/business-profile/country", headers=B_OWNER).json()
    assert a["country"] == "AE" and b["country"] == "GB"
    with tenant_session(B, "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM tenant_config WHERE tenant_id = %s", (K,))
        assert cur.fetchone()["n"] == 0


def test_local_today_follows_business_zone(client):
    # 25 hours apart: the two dates always differ
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("UPDATE tenant_config SET timezone = 'Pacific/Kiritimati' WHERE tenant_id = %s", (K,))
        cur.execute("UPDATE tenant_config SET timezone = 'Pacific/Pago_Pago' WHERE tenant_id = %s", (B,))
    from app.countries import local_today
    with tenant_session(K, "owner") as cur:
        cur.execute("SELECT td_local_today() AS d, (now() AT TIME ZONE 'Pacific/Kiritimati')::date AS want")
        r = cur.fetchone()
        assert r["d"] == r["want"]
        assert local_today(cur) == r["want"]
        kd = r["d"]
    with tenant_session(B, "owner") as cur:
        cur.execute("SELECT td_local_today() AS d, (now() AT TIME ZONE 'Pacific/Pago_Pago')::date AS want")
        r = cur.fetchone()
        assert r["d"] == r["want"] and r["d"] != kd
