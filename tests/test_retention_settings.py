"""PUT /techdeva/retention-settings is an audited write, like every other founder setting.

Runs against a SCRATCH database only. Puts the setting back the way it found it.
"""
import os

import pytest
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import retention  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402

FOUNDER = {"X-Role": "techdeva_admin"}
URL = "/techdeva/retention-settings"


@pytest.fixture
def client():
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT value FROM platform_settings WHERE key = %s", (retention.SERVER_ALERT_KEY,))
        row = cur.fetchone()
    yield TestClient(app)
    with tenant_session(None, "techdeva_admin") as cur:
        if row:
            cur.execute("UPDATE platform_settings SET value = %s WHERE key = %s",
                        (row["value"], retention.SERVER_ALERT_KEY))
        else:
            cur.execute("DELETE FROM platform_settings WHERE key = %s", (retention.SERVER_ALERT_KEY,))
        cur.execute("DELETE FROM executions WHERE action = 'techdeva.setting' "
                    "AND resource_id = %s AND detail->>'server_alert_at' = '737'",
                    (retention.SERVER_ALERT_KEY,))


def test_saving_the_setting_records_who_changed_it(client, monkeypatch):
    calls = []
    real = retention.record
    monkeypatch.setattr(retention, "record", lambda cur, **kw: (calls.append(kw), real(cur, **kw)))
    res = client.put(URL, headers=FOUNDER, json={"server_alert_at": 737})
    assert res.status_code == 200, res.text
    assert res.json() == {"ok": True, "server_alert_at": 737}
    assert len(calls) == 1
    kw = calls[0]
    assert (kw["action"], kw["resource"], kw["resource_id"]) == \
        ("techdeva.setting", "platform_settings", retention.SERVER_ALERT_KEY)
    assert kw["detail"] == {"server_alert_at": 737} and kw["status"] == "ok"


def test_a_refused_value_saves_and_records_nothing(client, monkeypatch):
    calls = []
    monkeypatch.setattr(retention, "record", lambda cur, **kw: calls.append(kw))
    for bad in ({"server_alert_at": 0}, {"server_alert_at": "lots"}, {}):
        assert client.put(URL, headers=FOUNDER, json=bad).status_code == 400
    assert calls == []


def test_only_the_founder_can_change_it(client):
    res = client.put(URL, headers={"X-Tenant-Id": "11111111-1111-1111-1111-111111111111", "X-Role": "owner"},
                     json={"server_alert_at": 5})
    assert res.status_code == 403


def test_the_audit_row_is_stored_with_no_business(client):
    """db/67: a founder action is stored platform-wide, tenant_id NULL."""
    assert client.put(URL, headers=FOUNDER, json={"server_alert_at": 737}).status_code == 200
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT tenant_id, actor_type, status FROM executions WHERE action = 'techdeva.setting' "
                    "AND resource_id = %s AND detail->>'server_alert_at' = '737'",
                    (retention.SERVER_ALERT_KEY,))
        rows = cur.fetchall()
    assert len(rows) == 1
    assert rows[0]["tenant_id"] is None and rows[0]["actor_type"] == "human" and rows[0]["status"] == "ok"
