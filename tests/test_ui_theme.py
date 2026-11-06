"""Each login keeps its own dashboard theme (6 Nov 2026). Needs db/92."""
import re
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from app.auth import issue_token
from app.db import tenant_session
from app.main import app
from app.ui_theme import THEMES

T = str(uuid.uuid4())
A, B = str(uuid.uuid4()), str(uuid.uuid4())
HA = {"Authorization": "Bearer " + issue_token(tenant_id=T, user_id=A, role="owner")}
HB = {"Authorization": "Bearer " + issue_token(tenant_id=T, user_id=B, role="operator")}


@pytest.fixture(scope="module")
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, is_test) VALUES (%s, 'Theme Shop', 'retail', true)", (T,))
        for uid, role in ((A, "owner"), (B, "operator")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, 'P', %s, %s)",
                        (uid, T, "%s@theme.local" % uid[:8], role))
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM executions WHERE tenant_id = %s", (T,))
        cur.execute("DELETE FROM tenants WHERE id = %s", (T,))


def test_default_is_business(client):
    r = client.get("/me/theme", headers=HA)
    assert r.status_code == 200 and r.json()["theme"] == "business"


def test_each_login_keeps_its_own(client):
    assert client.put("/me/theme", headers=HA, json={"theme": "glow"}).status_code == 200
    assert client.put("/me/theme", headers=HB, json={"theme": "lagoon"}).status_code == 200
    assert client.get("/me/theme", headers=HA).json()["theme"] == "glow"
    assert client.get("/me/theme", headers=HB).json()["theme"] == "lagoon"


def test_unknown_theme_and_signed_out(client):
    assert client.put("/me/theme", headers=HA, json={"theme": "<script>"}).status_code == 400
    assert client.get("/me/theme").status_code == 401


def test_dashboard_and_server_list_the_same_themes():
    html = Path(__file__).resolve().parent.parent.joinpath("web/dashboard.html").read_text(encoding="utf-8")
    block = html[html.index("var THEMES = ["):html.index("var THEME = null")]
    assert tuple(re.findall(r"id: '([a-z-]+)'", block)) == THEMES
