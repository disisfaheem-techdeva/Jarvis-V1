"""db/67_founder_audit.sql: founder actions are audited with no business attached,
and a business can neither see nor write such a row.

Runs against a SCRATCH database only. Creates one throwaway business and deletes it,
and removes the platform-wide audit rows it made itself.
"""
import os
import uuid

import psycopg
import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app.audit import record  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

FOUNDER = {"X-Role": "techdeva_admin"}
TAG = "founder-audit-test-" + uuid.uuid4().hex[:8]

_TENANT_ROWS = ("SELECT count(*) AS n, md5(coalesce(string_agg(id::text || tenant_id::text || action || "
                "detail::text, ',' ORDER BY id), '')) AS h FROM executions WHERE tenant_id IS NOT NULL")


@pytest.fixture(scope="module")
def business():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (name, industry) VALUES (%s, 'test') RETURNING id", (TAG,))
        tid = str(cur.fetchone()["id"])
        cur.execute(_TENANT_ROWS)
        before = cur.fetchone()
    yield {"tid": tid, "before": before}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM tenants WHERE id = %s", (tid,))
        cur.execute("DELETE FROM executions WHERE tenant_id IS NULL AND detail->>'test' = %s", (TAG,))


@pytest.fixture
def payment_instructions():
    """Put billing's setting back the way it was."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT value FROM platform_settings WHERE key = 'payment_instructions'")
        row = cur.fetchone()
    yield
    with tenant_session(None, "techdeva_admin") as cur:
        if row:
            cur.execute("UPDATE platform_settings SET value = %s WHERE key = 'payment_instructions'", (row["value"],))
        else:
            cur.execute("DELETE FROM platform_settings WHERE key = 'payment_instructions'")
        cur.execute("DELETE FROM executions WHERE tenant_id IS NULL AND action = 'techdeva.setting' "
                    "AND resource_id = 'payment_instructions' AND at > now() - interval '5 minutes'")


def test_billing_setting_is_audited_with_no_business(business, payment_instructions):
    client = TestClient(app)
    res = client.put("/techdeva/settings/payment_instructions", headers=FOUNDER,
                     json={"value": "Bank: test " + TAG})
    assert res.status_code == 200, res.text
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT tenant_id, detail FROM executions WHERE action = 'techdeva.setting' "
                    "AND resource_id = 'payment_instructions' AND at > now() - interval '1 minute'")
        rows = cur.fetchall()
    assert len(rows) == 1 and rows[0]["tenant_id"] is None
    assert rows[0]["detail"] == {"length": len("Bank: test " + TAG)}


def test_founder_record_with_no_business_is_stored(business):
    with tenant_session(None, "techdeva_admin") as cur:
        record(cur, action="test.founder", resource="platform", actor="founder", detail={"test": TAG})
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(*) n FROM executions WHERE tenant_id IS NULL AND detail->>'test' = %s", (TAG,))
        assert cur.fetchone()["n"] == 1


def test_a_business_cannot_see_platform_rows(business):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(*) n FROM executions WHERE tenant_id IS NULL")
        assert cur.fetchone()["n"] >= 1          # the founder sees them
    for role in ("owner", "operator"):
        with tenant_session(business["tid"], role) as cur:
            cur.execute("SELECT count(*) n FROM executions WHERE tenant_id IS NULL")
            assert cur.fetchone()["n"] == 0
    # nor through the business router's read-only executions view
    res = TestClient(app).get("/business/executions",
                              headers={"X-Tenant-Id": business["tid"], "X-Role": "owner"})
    assert res.status_code == 200
    assert all(x["action"] != "test.founder" for x in res.json()["items"])


def test_a_business_cannot_write_a_platform_row(business):
    for role in ("owner", "operator"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with tenant_session(business["tid"], role) as cur:
                cur.execute("INSERT INTO executions (tenant_id, actor, actor_type, action, resource, detail) "
                            "VALUES (NULL, 'x', 'human', 'test.forged', 'platform', %s)",
                            ('{"test": "%s"}' % TAG,))
    # and record() from a business session always names that business
    with tenant_session(business["tid"], "owner") as cur:
        record(cur, action="test.owner", resource="platform", actor="x", detail={"test": TAG})
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT tenant_id::text t FROM executions WHERE action = 'test.owner' AND detail->>'test' = %s",
                    (TAG,))
        assert [r["t"] for r in cur.fetchall()] == [business["tid"]]
        cur.execute("SELECT count(*) n FROM executions WHERE action = 'test.forged'")
        assert cur.fetchone()["n"] == 0


def test_an_ai_agent_never_acts_without_a_business(business):
    with pytest.raises(psycopg.errors.CheckViolation):
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("INSERT INTO executions (tenant_id, actor, actor_type, action, resource) "
                        "VALUES (NULL, 'hermes', 'agent', 'test.agent', 'platform')")


def test_existing_business_audit_rows_unchanged(business):
    """Runs last in this module: every business row from before is still there, untouched
    (this module's only business row belongs to its own throwaway business)."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute(_TENANT_ROWS.replace("WHERE tenant_id IS NOT NULL",
                                         "WHERE tenant_id IS NOT NULL AND tenant_id <> %s"), (business["tid"],))
        after = cur.fetchone()
    assert (after["n"], after["h"]) == (business["before"]["n"], business["before"]["h"])
