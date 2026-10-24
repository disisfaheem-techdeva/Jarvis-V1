"""CRM figures (Phase D): every number on /crm/overview checked against rows made
here, with the same definitions the page shows. Scratch database only."""
import datetime as dt
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)


@pytest.fixture(scope="module")
def world():
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    p = {k: str(uuid.uuid4()) for k in ("owner", "staff", "b_owner")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test) VALUES (%s, 'Rep A', 'x', 'active', true), "
                    "(%s, 'Rep B', 'x', 'active', true)", (a, b))
        for k, tid, role in (("owner", a, "owner"), ("staff", a, "operator"), ("b_owner", b, "owner")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s, %s, %s, %s, %s)",
                        (p[k], tid, k.capitalize(), "%s-%s@rep-test.local" % (k, p[k][:8]), role))
        # tenant A: 7 leads this week, 1 old one (60 days ago)
        rows = [  # name, source, status, value, owner, status_changed_days_ago, created_days_ago
            ("L1", "manual", "new", 0, "staff", None, 1), ("L2", "manual", "qualified", 3000, "staff", None, 2),
            ("L3", "manual", "booked", 4000, None, None, 2), ("L4", "hermes", "sold", 1000, "staff", 0, 5),
            ("L5", "hermes", "sold", 2000, "owner", 1, 6), ("L6", "manual", "lost", 500, None, 0, 3),
            ("L7", "manual", "new", 0, None, None, 0), ("OLD", "imported", "new", 700, None, None, 60)]
        ids = {}
        for name, src, st, val, own, chg, age in rows:
            cur.execute("""INSERT INTO leads (tenant_id, name, source, status, est_revenue, assigned_operator, status_changed_at, created_at)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id""",
                        (a, name, src, st, val, p[own] if own else None, NOW - dt.timedelta(days=chg) if chg is not None else None,
                         NOW - dt.timedelta(days=age)))
            ids[name] = str(cur.fetchone()["id"])
        for lead, due, done, who in (("L1", -2, None, "staff"), ("L2", 0, None, "staff"), ("L3", 3, None, "owner"),
                                     ("L2", -1, 0, "staff")):
            due_at = (NOW + dt.timedelta(days=due)) if due != 0 else NOW + dt.timedelta(minutes=2)    # today, not yet late
            cur.execute("""INSERT INTO crm_activities (tenant_id, lead_id, kind, title, due_at, done_at, assigned_to)
                           VALUES (%s, %s, 'task', 't', %s, %s, %s)""",
                        (a, ids[lead], due_at, NOW if done is not None else None, p[who]))
        cur.execute("INSERT INTO leads (tenant_id, name, source, status, est_revenue) VALUES (%s, 'B1', 'manual', 'sold', 99999)", (b,))
    h = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=p[k],
                                                        role=("operator" if k == "staff" else "owner"))} for k in p}
    yield {"a": a, "p": p, "h": h}
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def test_figures_match_the_rows(client, world):
    d = client.get("/crm/overview?days=30", headers=world["h"]["owner"]).json()
    assert d["leads"] == {"total": 8, "new": 7, "qualified": 2, "open": 5, "no_followup": 2}   # L7 and OLD have nothing planned
    assert d["pipeline"]["open_value"] == 3000 + 4000 + 700
    assert d["pipeline"]["by_stage"]["sold"] == {"count": 2, "value": 3000.0}
    assert d["won"] == {"count": 2, "value": 3000.0, "lost": 1}                                 # not business B's 99,999
    assert d["conversion"] == {"converted": 2, "of": 7, "rate": 28.6, "note": None}
    assert d["followups"] == {"overdue": 1, "today": 1, "open": 3, "done": 1}
    src = {s["source"]: s for s in d["sources"]}
    assert src["manual"] == {"source": "manual", "leads": 5, "converted": 0, "rate": 0.0}
    assert src["hermes"]["rate"] is None and src["hermes"]["converted"] == 2                   # fewer than 5: no rate
    assert "imported" not in src                                                               # created 60 days ago
    assert d["cycle_days"]["value"] is None and d["cycle_days"]["from"] == 2                   # fewer than 3: no average
    team = {t["name"]: t for t in d["team"]}
    assert (team["Staff"]["open_leads"], team["Staff"]["overdue"], team["Staff"]["done"], team["Staff"]["won"]) == (2, 1, 1, 1)
    assert d["unassigned"] == 3 and set(d["definitions"]) >= {"won", "conversion", "open_value"}


def test_period_owner_filter_and_who_sees_the_team(client, world):
    d = client.get("/crm/overview?days=7", headers=world["h"]["owner"]).json()
    assert d["leads"]["new"] == 7 and d["conversion"]["rate"] == 28.6
    mine = client.get("/crm/overview?days=30&owner=me", headers=world["h"]["staff"]).json()
    assert mine["leads"]["total"] == 3 and mine["followups"]["overdue"] == 1 and mine["won"]["count"] == 1
    assert mine["team"] is None and mine["unassigned"] is None                                  # staff do not get the team table
    assert client.get("/crm/overview?days=3", headers=world["h"]["owner"]).status_code == 422
    small = client.get("/crm/overview?days=30", headers=world["h"]["b_owner"]).json()
    assert small["conversion"]["rate"] is None and small["conversion"]["note"]
