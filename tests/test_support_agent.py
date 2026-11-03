"""The support agent (db/89; app/support_checks.py, support_agent.py, support_routes.py).

Proved here against a SCRATCH database (everything it makes is deleted):

  AUDIT     each kind of problem becomes one finding, once, and goes away when fixed
  SAFE      it fixes by itself only what reaches nobody (old alerts nobody re-raised)
  CLICK     everything else waits; a person's fix re-queues failed AI work
  WALLS     an owner sees only their own business's owner findings, never internal ones,
            never another business; only the owner or the founder can press a fix
  CHAT      without AI it still answers from the real findings
"""
import os
import uuid

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import support_agent as sa  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

WA = {}          # pairctl url -> whether the fake pairctl answers
PUSHED = []


@pytest.fixture(autouse=True)
def fakes(monkeypatch):
    monkeypatch.setattr(sa, "_pairctl_up", lambda url: WA.get(url, True))
    monkeypatch.setattr(sa, "_ai_health", lambda: {"status": "ok", "usable": 1, "chain": []})
    monkeypatch.setattr(sa, "_push_tasks", lambda rows: PUSHED.extend(rows))
    PUSHED.clear()


@pytest.fixture(scope="module")
def world():
    ids = {k: str(uuid.uuid4()) for k in ("a", "b", "owner_a", "owner_b", "op_a", "founder", "wa1", "wa2")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test, trial_ends_at) VALUES "
                    "(%s,'Support A Clinic','clinic','trial',false, now() + interval '1 day'),"
                    "(%s,'Support B Salon','salon','active',false, NULL)", (ids["a"], ids["b"]))
        for key, tid, role in (("owner_a", ids["a"], "owner"), ("owner_b", ids["b"], "owner"), ("op_a", ids["a"], "operator")):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role) VALUES (%s,%s,'Person',%s,%s)",
                        (ids[key], tid, "%s@stest.local" % ids[key][:8], role))
        cur.execute("INSERT INTO whatsapp_connections (id, tenant_id, agent_key, pairctl_url, status, phone_number, connected_at) "
                    "VALUES (%s,%s,'stest-a','http://pairctl-a','connected','923000000001', now()),"
                    "       (%s,%s,'stest-b','http://pairctl-b','connected','923000000002', now())",
                    (ids["wa1"], ids["a"], ids["wa2"], ids["b"]))
    h = {k: {"Authorization": "Bearer " + issue_token(tenant_id=ids[t], user_id=ids[k], role=r)}
         for k, t, r in (("owner_a", "a", "owner"), ("owner_b", "b", "owner"), ("op_a", "a", "operator"))}
    h["founder"] = {"Authorization": "Bearer " + issue_token(tenant_id=None, user_id=ids["founder"], role="techdeva_admin")}
    yield {"ids": ids, "h": h}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM incidents WHERE kind LIKE 'stest.%%'")
        for t in (ids["a"], ids["b"]):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _open(tid):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT key, audience, status FROM support_findings WHERE tenant_id IS NOT DISTINCT FROM %s AND status = 'open'",
                    (tid,))
        return {r["key"]: r["audience"] for r in cur.fetchall()}


# ----------------------------------------------------- WhatsApp: told to the owner, never changed by the agent
def test_logged_out_whatsapp_is_told_once_and_nothing_is_changed(world):
    a, wa1 = world["ids"]["a"], world["ids"]["wa1"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("UPDATE whatsapp_connections SET status = 'disconnected' WHERE id = %s", (wa1,))
    assert sa.run_scope(a)["fixed"] == 0
    sa.run_scope(a)
    assert list(_open(a)).count("wa-logged-out-" + wa1) == 1      # two passes, one finding
    assert _open(a)["wa-logged-out-" + wa1] == "owner"
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT status FROM whatsapp_connections WHERE id = %s", (wa1,))
        assert cur.fetchone()["status"] == "disconnected"
        cur.execute("UPDATE whatsapp_connections SET status = 'connected' WHERE id = %s", (wa1,))
    sa.run_scope(a)
    assert "wa-logged-out-" + wa1 not in _open(a)                  # linked again: the finding is gone


def test_unreachable_agent_is_internal(world, client):
    b = world["ids"]["b"]
    WA["http://pairctl-b"] = False
    sa.run_scope(b)
    assert _open(b).get("agent-unreachable-stest-b") == "techdeva"
    mine = client.get("/support", headers=world["h"]["owner_b"]).json()
    assert not [f for f in mine["findings"] if f["key"].startswith("agent-unreachable")]
    theirs = client.get("/techdeva/support/" + b, headers=world["h"]["founder"]).json()
    assert [f for f in theirs["findings"] if f["key"] == "agent-unreachable-stest-b"]
    # it comes back: the finding is closed as gone, not left behind
    WA["http://pairctl-b"] = True
    sa.run_scope(b)
    assert "agent-unreachable-stest-b" not in _open(b)


def test_integration_not_checked_yet_is_not_a_problem(world):
    a = world["ids"]["a"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO integrations (tenant_id, provider, status, health, external_account, credentials, connected_at)
                       VALUES (%s,'google','connected','unknown','g-stest','{"token":"x"}', now())""", (a,))
    sa.run_scope(a)
    assert "reconnect-google" not in _open(a)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""UPDATE integrations SET health = 'failing', last_error = 'token revoked'
                        WHERE tenant_id = %s AND provider = 'google'""", (a,))
    sa.run_scope(a)
    assert _open(a).get("reconnect-google") == "owner"


# ----------------------------------------------------- CLICK: failed AI work waits for a person
def test_failed_tasks_wait_for_a_click_and_go_back_to_the_queue(world, client):
    a = world["ids"]["a"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO tasks (tenant_id, agent, type, payload, status, attempts, max_attempts, result) VALUES
                       (%s,'content','plan_content','{}','dead',3,3,'{"dead":"exception: boom"}'),
                       (%s,'ads','build_campaign','{}','failed',1,3,'{"closed_by_owner":"x"}') RETURNING id, status""",
                    (a, a))
        dead_id = [r["id"] for r in cur.fetchall() if r["status"] == "dead"][0]
    sa.run_scope(a)
    assert _open(a).get("tasks-failed") == "techdeva"
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT args, detail FROM support_findings WHERE tenant_id = %s AND key = 'tasks-failed' AND status = 'open'", (a,))
        f = cur.fetchone()
    assert f["args"]["task_ids"] == [str(dead_id)]                 # the one the owner closed is left alone
    assert "boom" in f["detail"]

    # an owner cannot reach an internal finding; the founder can
    assert client.post("/support/fix", json={"key": "tasks-failed"}, headers=world["h"]["owner_a"]).status_code == 404
    r = client.post("/techdeva/support/fix", json={"tenant_id": a, "key": "tasks-failed"}, headers=world["h"]["founder"])
    assert r.status_code == 200, r.text
    assert "1 failed AI task" in r.json()["done"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT status::text AS s, attempts FROM tasks WHERE id = %s", (dead_id,))
        t = cur.fetchone()
    assert (t["s"], t["attempts"]) == ("queued", 0)
    assert PUSHED == [(a, dead_id)]
    assert "tasks-failed" not in _open(a)
    # pressing it twice is a clear "no longer open", not a second retry
    assert client.post("/techdeva/support/fix", json={"tenant_id": a, "key": "tasks-failed"},
                       headers=world["h"]["founder"]).status_code == 404


def test_owner_findings_with_no_button_say_what_to_do(world, client):
    a = world["ids"]["a"]
    sa.run_scope(a)
    mine = client.get("/support", headers=world["h"]["owner_a"]).json()
    keys = {f["key"]: f for f in mine["findings"]}
    assert "trial-ending" in keys and keys["trial-ending"]["severity"] == "high"
    assert all(f["audience"] == "owner" for f in mine["findings"])
    r = client.post("/support/fix", json={"key": "trial-ending"}, headers=world["h"]["owner_a"])
    assert r.status_code == 409 and "needs a person" in r.json()["detail"]


# ----------------------------------------------------- WALLS
def test_one_business_never_sees_another(world, client):
    a, b = world["ids"]["a"], world["ids"]["b"]
    sa.run_scope(a)
    theirs = client.get("/support", headers=world["h"]["owner_b"]).json()
    assert not [f for f in theirs["findings"] if f["key"] == "trial-ending"]       # A's trial, not B's
    with tenant_session(b, "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM support_findings WHERE tenant_id = %s", (a,))
        assert cur.fetchone()["n"] == 0
        cur.execute("SELECT count(*) AS n FROM support_log WHERE tenant_id = %s", (a,))
        assert cur.fetchone()["n"] == 0
    with tenant_session(a, "owner") as cur:                       # RLS, not just the query, hides internal rows
        cur.execute("SELECT count(*) AS n FROM support_findings WHERE audience = 'techdeva'")
        assert cur.fetchone()["n"] == 0


def test_only_owner_or_founder_can_run_or_fix(world, client):
    assert client.post("/support/run", headers=world["h"]["op_a"]).status_code == 403
    assert client.post("/support/fix", json={"key": "x"}, headers=world["h"]["op_a"]).status_code == 403
    assert client.get("/techdeva/support", headers=world["h"]["owner_a"]).status_code == 403
    assert client.post("/techdeva/support/run", json={}, headers=world["h"]["owner_a"]).status_code == 403
    r = client.post("/support/run", headers=world["h"]["owner_a"])
    assert r.status_code == 200 and "findings" in r.json()


# ----------------------------------------------------- the platform
def test_old_alerts_are_closed_but_server_alerts_stay(world):
    a = world["ids"]["a"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO incidents (tenant_id, kind, severity, title, last_seen_at) VALUES
                       (%s,'stest.old','warn','Old one', now() - interval '4 days'),
                       (%s,'stest.fresh','warn','Fresh one', now())
                       RETURNING id, kind""", (a, a))
        made = {r["kind"]: r["id"] for r in cur.fetchall()}
    sa.run_scope(None)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT kind, status FROM incidents WHERE id = ANY(%s)", (list(made.values()),))
        state = {r["kind"]: r["status"] for r in cur.fetchall()}
    assert state == {"stest.old": "resolved", "stest.fresh": "open"}
    assert "stale-incidents" not in _open(None)                   # done by itself, logged, not left open


def test_queue_that_is_not_moving_is_a_platform_finding(world):
    a = world["ids"]["a"]
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO tasks (tenant_id, agent, type, payload, status, run_after)
                       VALUES (%s,'content','plan_content','{}','queued', now() - interval '2 hours') RETURNING id""", (a,))
        tid = cur.fetchone()["id"]
    sa.run_scope(None)
    assert _open(None).get("queue-late") == "techdeva"
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM tasks WHERE id = %s", (tid,))
    sa.run_scope(None)
    assert "queue-late" not in _open(None)


def test_founder_summary_lists_businesses_worst_first(world, client):
    WA["http://pairctl-b"] = False
    out = sa.run_all()
    assert out["failed"] == 0
    r = client.get("/techdeva/support", headers=world["h"]["founder"]).json()
    names = [b["name"] for b in r["businesses"]]
    assert "Support A Clinic" in names and "Support B Salon" in names
    assert r["last_run"]["found"] >= 2


# ----------------------------------------------------- CHAT
def test_ask_without_ai_answers_from_the_findings(world, client):
    r = client.post("/support/ask", json={"question": "kya masla hai?"}, headers=world["h"]["owner_a"])
    assert r.status_code == 200
    assert "trial" in r.json()["answer"].lower()
    assert client.post("/support/ask", json={"question": " "}, headers=world["h"]["owner_a"]).status_code == 400


def test_run_due_is_throttled(monkeypatch):
    calls = []
    monkeypatch.setattr(sa, "run_all", lambda: calls.append(1) or {"found": 0, "fixed": 0, "failed": 0})
    monkeypatch.setattr(sa, "_last", 0.0)
    sa.run_due()
    sa.run_due()
    assert calls == [1]
