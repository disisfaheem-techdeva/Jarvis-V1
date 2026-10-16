"""Trial data deletion waits for a warning email that was actually sent (Faheem, 16 Oct 2026).

Runs against a SCRATCH database only. Uses one throwaway unpaid-trial business and
deletes it at the end if the test did not.
"""
import os
import uuid

import pytest

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import account, retention  # noqa: E402
from app.db import tenant_session  # noqa: E402

TAG = "retention-warn-test-" + uuid.uuid4().hex[:8]


@pytest.fixture
def biz():
    """An unpaid trial that ended 13 days ago: past the warning point, before the 15-day grace."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (name, industry, status, trial_ends_at) "
                    "VALUES (%s, 'test', 'paused', now() - interval '13 days') RETURNING id::text AS id", (TAG,))
        tid = cur.fetchone()["id"]
        cur.execute("INSERT INTO users (tenant_id, name, email, role) VALUES (%s, 'Owner', %s, 'owner')",
                    (tid, TAG + "@example.org"))
    yield tid
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("DELETE FROM tenants WHERE id = %s", (tid,))
        cur.execute("DELETE FROM tenant_deletions WHERE tenant_id = %s", (tid,))


def _row(tid):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute(retention.CANDIDATES + " AND t.id = %s", (tid,))
        c = cur.fetchone()
        cur.execute("SELECT warned_at, emailed FROM data_retention WHERE tenant_id = %s", (tid,))
        return (dict(c) if c else None), cur.fetchone()


def _warn(tid):
    c, _ = _row(tid)
    with tenant_session(None, "techdeva_admin") as cur:
        return retention._warn(cur, c)


def _exists(tid):
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT 1 FROM tenants WHERE id = %s", (tid,))
        return cur.fetchone() is not None


def _mail(monkeypatch, configured=True, fail=False):
    sent = []

    def send(to, subject, text):
        if fail:
            raise ConnectionError("SMTP server disconnected")
        sent.append((to, subject))
    monkeypatch.setattr(account, "mail_configured", lambda: configured)
    monkeypatch.setattr(account, "send_mail", send)
    return sent


@pytest.mark.parametrize("configured,fail", [(False, False), (True, True)])
def test_an_email_that_was_not_sent_is_not_a_warning(biz, monkeypatch, configured, fail):
    _mail(monkeypatch, configured=configured, fail=fail)
    assert _warn(biz) is False
    c, r = _row(biz)
    assert c["warned_at"] is None and r["warned_at"] is None and r["emailed"] is False
    # the deletion day keeps moving: always at least WARN_DAYS away while unwarned
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT %s > now() + interval '2 days 23 hours' AS later", (c["deletes_on"],))
        assert cur.fetchone()["later"]


def test_never_deleted_while_unwarned_even_long_after_the_grace(biz, monkeypatch):
    _mail(monkeypatch, fail=True)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("UPDATE tenants SET trial_ends_at = now() - interval '40 days' WHERE id = %s", (biz,))
    assert _warn(biz) is False
    assert retention.delete_business(biz) is None
    assert _exists(biz)


def test_a_sent_email_starts_the_full_warning_period(biz, monkeypatch):
    sent = _mail(monkeypatch)
    assert _warn(biz) is True
    assert sent == [(TAG + "@example.org", sent[0][1])] and "will be deleted" in sent[0][1]
    c, r = _row(biz)
    assert r["emailed"] is True and c["warned_at"] is not None
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT %s = %s + make_interval(days => %s) AS exact",
                    (c["deletes_on"], c["warned_at"], retention.WARN_DAYS))
        assert cur.fetchone()["exact"]          # three full days after the email, not the 15-day mark
    assert retention.delete_business(biz) is None and _exists(biz)       # not due yet
    with tenant_session(None, "techdeva_admin") as cur:
        # time passes: the email went out WARN_DAYS + 1 minute ago, and the 15-day grace is over too
        cur.execute("UPDATE data_retention SET warned_at = now() - make_interval(days => %s, mins => 1) "
                    "WHERE tenant_id = %s", (retention.WARN_DAYS, biz))
        cur.execute("UPDATE tenants SET trial_ends_at = now() - interval '16 days' WHERE id = %s", (biz,))
    assert retention.delete_business(biz) is not None                   # due: deleted
    assert not _exists(biz)


def test_an_old_warned_row_without_email_does_not_count(biz):
    """A row written by the old code (warned, email failed) is not a warning."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO data_retention (tenant_id, warned_at, emailed) "
                    "VALUES (%s, now() - interval '10 days', false)", (biz,))
        cur.execute("UPDATE tenants SET trial_ends_at = now() - interval '30 days' WHERE id = %s", (biz,))
    c, _ = _row(biz)
    assert c["warned_at"] is None
    assert retention.delete_business(biz) is None and _exists(biz)
