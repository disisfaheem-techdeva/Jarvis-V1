"""Karyana stores, a business type of their own (db/93, 8 Nov 2026).

A karyana sells products like a shop, by kg, litre and piece, and people name
its items in Urdu or Roman Urdu. Checked here: the type exists and behaves as a
product business everywhere a shop does; products carry a unit and other names;
a till sale made by an other name ("cheeni") is the right product, priced from
the list and taken out of stock once; an other name two products share matches
neither; businesses that set no other names match exactly as before.

Runs against a SCRATCH database only. Makes its own businesses and deletes them.
"""
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import categories, dashcustom  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402
from app.orders import match_item  # noqa: E402

K, B = str(uuid.uuid4()), str(uuid.uuid4())       # a karyana, and another business
U = {k: str(uuid.uuid4()) for k in ("owner", "staff", "b_owner")}
REG = "Counter"


def _bearer(tid, uid, role):
    return {"Authorization": "Bearer " + issue_token(tenant_id=tid, user_id=uid, role=role)}


OWNER, STAFF, B_OWNER = _bearer(K, U["owner"], "owner"), _bearer(K, U["staff"], "operator"), _bearer(B, U["b_owner"], "owner")


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test, business_type) VALUES "
                    "(%s, 'Karyana Test Store', 'grocery', true, 'karyana'), (%s, 'Other Test Shop', 'retail', true, 'shop')",
                    (K, B))
        cur.execute("INSERT INTO pos_settings (tenant_id, enabled) VALUES (%s, true)", (K,))
        for k, tid, role in (("owner", K, "owner"), ("staff", K, "operator"), ("b_owner", B, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (U[k], tid, k.title(), "%s@karyana-test.local" % U[k][:8], role))
        cur.execute("""INSERT INTO products (tenant_id, name, price, track_stock, unit, aliases) VALUES
                       (%s, 'Sugar', 180, true, 'kg', '{cheeni,chini,شکر}'),
                       (%s, 'Flour', 160, true, 'kg', '{aata,atta}'),
                       (%s, 'Cooking Oil 1L', 550, false, 'litre', '{tel,ghee}'),
                       (%s, 'Desi Ghee 1kg', 1900, false, 'pack', '{ghee}'),
                       (%s, 'Soap', 100, false, 'piece', '{}') RETURNING id, name""", (K, K, K, K, K))
        ids = {r["name"]: r["id"] for r in cur.fetchall()}
        for name in ("Sugar", "Flour"):
            cur.execute("INSERT INTO stock_movements (tenant_id, product_id, kind, quantity_delta) VALUES (%s, %s, 'opening', 100)",
                        (K, ids[name]))
    c = TestClient(app)
    r = c.post("/pos/shift/open", headers=OWNER, json={"register": REG, "opening_float": 0})
    assert r.status_code == 200, r.text
    yield c
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (K, B):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
        cur.execute("SELECT count(*) AS n FROM tenants WHERE id IN (%s, %s)", (K, B))
        assert cur.fetchone()["n"] == 0


def _cat(client):
    r = client.get("/pos/catalogue", headers=OWNER)
    assert r.status_code == 200, r.text
    return {i["name"]: i for i in r.json()["items"]}


def _sale(client, items, pay):
    return client.post("/pos/sale", headers=OWNER, json={"client_ref": uuid.uuid4().hex, "register": REG, "items": items,
                                                        "payments": [{"method": "cash", "amount": pay}]})


# ---------------------------------------------------------------- the type
def test_karyana_is_a_business_type_people_can_pick(client):
    r = client.get("/categories", headers=OWNER)
    assert r.status_code == 200, r.text
    k = {c["slug"]: c for c in r.json()["categories"]}["karyana"]
    assert k["labels"]["services"] == "Products" and "Udhaar" in " ".join(k["intake_fields"])
    assert "never guess" in k["agent_rules"] and "appointment" in k["agent_rules"]


def test_karyana_counts_as_a_product_business_everywhere_a_shop_does():
    assert "karyana" in categories.PRODUCT_TYPES and "karyana" in categories.ORDER_TYPES
    hidden = set(dashcustom.preset_hidden("karyana"))
    assert {"appointments", "calendar", "services", "packages", "hr"} <= hidden
    assert not ({"orders", "products", "inventory", "attendance", "console"} & hidden)
    assert dashcustom.preset_hidden("shop") == sorted(set(dashcustom.preset_hidden("shop")))   # shop unchanged in kind
    assert "attendance" in dashcustom.preset_hidden("shop")


def test_the_whatsapp_agent_gets_units_and_other_names(client):
    with tenant_session(K, "owner") as cur:
        text = categories.knowledge_block(categories.facts(cur, K))
    assert "karyana (grocery) store" in text
    assert "- Sugar: Rs 180 per kg (customers may call it: cheeni, chini, شکر)" in text
    assert "- Soap: Rs 100 per piece" in text and "Soap: Rs 100 per piece (" not in text
    assert "## Taking an order" in text


def test_a_product_without_unit_or_names_reads_exactly_as_before():
    f = {"business": {"name": "X", "business_type": "shop"}, "category": None, "services": [], "hours": [],
         "knowledge": [], "website": None,
         "products": [{"name": "Shampoo", "price": 1000, "currency": "PKR", "unit": None, "aliases": []}]}
    assert "- Shampoo: Rs 1,000\n" in categories.knowledge_block(f) + "\n"


# ---------------------------------------------------------------- products
def test_owner_sets_unit_and_other_names_and_bad_ones_are_refused(client):
    r = client.post("/business/products", headers=OWNER, json={"name": "Rice Basmati", "price": 320, "unit": "kg",
                                                               "aliases": ["chawal", "chaawal"]})
    assert r.status_code in (200, 201), r.text
    pid = r.json()["item"]["id"]
    items = client.get("/business/products", headers=OWNER).json()["items"]
    item = [p for p in items if str(p["id"]) == str(pid)][0]
    assert item["unit"] == "kg" and item["aliases"] == ["chawal", "chaawal"]
    bad = client.patch("/business/products/" + str(pid), headers=OWNER, json={"unit": "bori"})
    assert bad.status_code in (400, 409, 422), bad.text
    bad = client.patch("/business/products/" + str(pid), headers=OWNER, json={"aliases": ["a%d" % i for i in range(21)]})
    assert bad.status_code in (400, 409, 422), bad.text
    inv = client.get("/operations/inventory", headers=OWNER).json()["products"]
    assert [p for p in inv if p["name"] == "Rice Basmati"][0]["aliases"] == ["chawal", "chaawal"]
    # another business cannot see or change it
    assert client.patch("/business/products/" + str(pid), headers=B_OWNER, json={"unit": "g"}).status_code in (403, 404)


def test_the_till_shows_unit_and_other_names(client):
    s = _cat(client)["Sugar"]
    assert s["unit"] == "kg" and "cheeni" in s["aliases"] and s["on_hand"] == 100


# ---------------------------------------------------------------- matching
def test_matching_name_first_then_other_name_and_never_a_guess():
    cat = [{"id": "1", "name": "Sugar", "kind": "product", "aliases": ["cheeni"]},
           {"id": "2", "name": "Cooking Oil 1L", "kind": "product", "aliases": ["tel", "ghee"]},
           {"id": "3", "name": "Desi Ghee 1kg", "kind": "product", "aliases": ["ghee"]},
           {"id": "4", "name": "Cheeni Brown", "kind": "product", "aliases": []}]
    assert match_item(cat, "  CHEENI ")["id"] == "1"        # other name, exact, beats a near name match
    assert match_item(cat, "tel")["id"] == "2"
    assert match_item(cat, "ghee") is None                  # two products share it: neither
    assert match_item(cat, "sugar")["id"] == "1"             # the name itself still wins
    assert match_item(cat, "brown")["id"] == "4"             # near match on names, as before
    old = [{"id": "9", "name": "Chicken Karahi", "kind": "product"}]   # catalogue rows from before db/93
    assert match_item(old, "karahi")["id"] == "9" and match_item(old, "naan") is None


def test_a_till_sale_by_urdu_name_is_priced_from_the_list_and_takes_stock_once(client):
    r = _sale(client, [{"name": "cheeni", "quantity": 2.5}, {"name": "aata", "quantity": 2}], 800)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["total"] == 770 and s["recognised"]                    # 2.5 x 180 + 2 x 160
    names = sorted(i.get("description") or i.get("name") for i in s["items"])
    assert names == ["Flour", "Sugar"]
    c = _cat(client)
    assert c["Sugar"]["on_hand"] == 97.5 and c["Flour"]["on_hand"] == 98


def test_a_shared_other_name_is_refused_at_the_till_and_nothing_is_sold(client):
    before = _cat(client)
    r = _sale(client, [{"name": "ghee", "quantity": 1}], 2000)
    assert r.status_code == 400 and "ghee" in r.json()["detail"]
    after = _cat(client)
    assert after["Sugar"]["on_hand"] == before["Sugar"]["on_hand"]
