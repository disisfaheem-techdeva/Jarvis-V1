"""Trial data retention (Faheem's rule, 30 Sep 2026): only paying clients'
data is kept.

A business whose free trial ended unpaid is locked (trial.py). The owner is
warned by email (and on the lock screen) three days before its deletion day.
Only an email that was actually sent counts as the warning (Faheem, 16 Oct
2026): until it goes out the business is not "warned", is tried again every
run, and is never deleted. The deletion day is then the later of 15 days
after the trial ended and three full days after that email. On that day
everything the business has on the platform is deleted at once: every row in
every table (they all cascade from the tenant), its sign-up record, and, via
the server's provisioning job, its WhatsApp agent containers and session.
There is no safety copy: Faheem chose that.

Never deleted, whatever the dates say:
  * a business that has EVER paid: an invoice marked paid, a confirmed
    payment, or "Mark paying" in the founder console (all three leave a
    record), or one with a payment claim still being checked;
  * a business that is not on trial or paused (active = paying);
  * test businesses, and TechDeva's own business;
  * a business the founder put on "Keep".
Each deletion re-checks all of this inside the same transaction, so a payment
that lands a second before is never lost.

The founder console also shows how many businesses are paying and says when
it is time to buy a second server (SERVER_ALERT_AT, default 8).
"""
import datetime as _dt
import json
import re
import time

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

GRACE_DAYS = 15
WARN_DAYS = 3
SERVER_ALERT_KEY = "server_alert_at"
SERVER_ALERT_DEFAULT = 8
RUN_EVERY = 3600            # seconds between rounds in the worker
_LAST = {"t": 0.0}

PAID_EVER = """(
      EXISTS (SELECT 1 FROM client_invoices ci WHERE ci.tenant_id = t.id AND ci.status = 'paid')
   OR EXISTS (SELECT 1 FROM payment_claims pc WHERE pc.tenant_id = t.id AND pc.status IN ('confirmed', 'pending'))
   OR EXISTS (SELECT 1 FROM executions e WHERE e.tenant_id = t.id
                AND e.action IN ('techdeva.client.activate', 'billing.claim_confirmed', 'techdeva.invoice.paid')))"""

# The owner always gets the full WARN_DAYS: the deletion day is 15 days after
# the trial ended, or WARN_DAYS after the warning if that is later (an old
# unpaid trial found on the first run is warned first, never wiped at once).
# A warning given before the trial was extended does not count.
CANDIDATES = """
SELECT t.id, t.name, t.status::text AS status, t.trial_ends_at,
       CASE WHEN w.valid THEN GREATEST(t.trial_ends_at + interval '%(g)d days', r.warned_at + interval '%(w)d days')
            ELSE GREATEST(t.trial_ends_at + interval '%(g)d days', now() + interval '%(w)d days') END AS deletes_on,
       coalesce(r.hold, false) AS hold, r.hold_reason,
       CASE WHEN w.valid THEN r.warned_at END AS warned_at, coalesce(r.emailed, false) AS emailed
  FROM tenants t
  LEFT JOIN data_retention r ON r.tenant_id = t.id
  CROSS JOIN LATERAL (SELECT coalesce(r.warned_at >= t.trial_ends_at + interval '%(v)d days' AND r.emailed, false) AS valid) w
 WHERE t.status::text IN ('trial', 'paused')
   AND NOT t.is_test
   AND t.trial_ends_at IS NOT NULL
   AND t.trial_ends_at < now()
   AND t.id::text <> coalesce((SELECT value FROM platform_settings WHERE key = 'home_tenant_id'), '')
   AND NOT PAID_EVER_SQL
""".replace("%(g)d", str(GRACE_DAYS)).replace("%(w)d", str(WARN_DAYS)).replace(
    "%(v)d", str(GRACE_DAYS - WARN_DAYS - 1)).replace("PAID_EVER_SQL", PAID_EVER)


def _admin(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if role != "techdeva_admin":
        raise HTTPException(403, "Founder only.")
    return uid


def deletes_on(cur, tenant_id):
    """The deletion day for one business, or None when it will not be deleted
    (used by its own Plan & Billing page and lock screen)."""
    cur.execute(CANDIDATES + " AND t.id = %s", (str(tenant_id),))
    r = cur.fetchone()
    if not r or r["hold"]:
        return None
    return r["deletes_on"]


# ------------------------------------------------------------------ deleting
def _counts(cur, tid):
    cur.execute("""SELECT c.table_name FROM information_schema.columns c
                     JOIN information_schema.tables t ON t.table_name = c.table_name
                      AND t.table_schema = 'public' AND t.table_type = 'BASE TABLE'
                    WHERE c.table_schema = 'public' AND c.column_name = 'tenant_id'
                    ORDER BY 1""")
    out = {}
    for r in cur.fetchall():
        cur.execute('SELECT count(*) AS n FROM "%s" WHERE tenant_id = %%s' % r["table_name"].replace('"', ''), (tid,))
        n = cur.fetchone()["n"]
        if n:
            out[r["table_name"]] = n
    return out


def delete_business(tid, by="retention"):
    """Delete one business completely, if it is still eligible. Returns the
    deletion record, or None when it is no longer eligible (paid, held...)."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id FROM tenants WHERE id = %s FOR UPDATE", (str(tid),))
        if not cur.fetchone():
            return None
        due = (" AND t.id = %%s AND w.valid AND r.warned_at <= now() - interval '%d days'"
               " AND t.trial_ends_at + interval '%d days' <= now()" % (WARN_DAYS, GRACE_DAYS))
        cur.execute(CANDIDATES + due, (str(tid),))
        c = cur.fetchone()
        if not c or c["hold"]:
            return None
        cur.execute("SELECT DISTINCT agent_key FROM whatsapp_connections WHERE tenant_id = %s AND agent_key IS NOT NULL "
                    "UNION SELECT DISTINCT agent_key FROM agent_requests WHERE tenant_id = %s AND agent_key IS NOT NULL",
                    (str(tid), str(tid)))
        keys = sorted({r["agent_key"] for r in cur.fetchall() if r["agent_key"]})
        counts = _counts(cur, str(tid))
        cur.execute("DELETE FROM signups WHERE tenant_id = %s", (str(tid),))
        if cur.rowcount:
            counts["signups"] = cur.rowcount
        cur.execute("DELETE FROM tenants WHERE id = %s", (str(tid),))
        cur.execute("""INSERT INTO tenant_deletions (tenant_id, business_name, trial_ended_at, rows_deleted, agent_keys,
                                                     agents_removed_at)
                       VALUES (%s, %s, %s, %s::jsonb, %s, CASE WHEN %s THEN now() END) RETURNING *""",
                    (str(tid), c["name"], c["trial_ends_at"], json.dumps(counts), keys, not keys))
        row = dict(cur.fetchone())
        cur.execute("SELECT value FROM platform_settings WHERE key = 'home_tenant_id'")
        home = cur.fetchone()
        if home and home["value"]:
            record(cur, action="retention.deleted", resource="tenants", resource_id=str(tid), actor=by,
                   actor_type="system", status="ok", detail={"business": c["name"], "rows": sum(counts.values()),
                                                           "agents": keys}, error=None, tenant_id=home["value"])
    print("[retention] deleted %s (%s rows, agents %s)" % (tid, sum(counts.values()), keys), flush=True)
    return row


FOUNDER_KEEP_AGENTS = re.compile(r"^(irfan$|clinic$|salon$|hermes-workspace|workspace|.*ofsd|instant-grill)")


def founder_delete(tid, confirm, by):
    """The founder's Delete in the console (Rafay, 26 Oct 2026): delete one
    business now, whatever its trial dates, the same way the retention run
    does (every row, its sign-up, then its WhatsApp agents through the
    provisioning job). Refused for a paying (active) business, TechDeva's own,
    a hand-built or workspace agent's business and Instant Grill; `confirm`
    must be the business name. Returns the deletion record."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id, name, status::text AS status, trial_ends_at FROM tenants WHERE id::text = %s FOR UPDATE",
                    (str(tid),))
        t = cur.fetchone()
        if not t:
            raise HTTPException(404, "That business is not there.")
        name = t["name"] or ""
        if (confirm or "").strip().lower() != name.strip().lower():
            raise HTTPException(400, "Type the business name exactly to delete it.")
        cur.execute("SELECT value FROM platform_settings WHERE key = 'home_tenant_id'")
        home = cur.fetchone()
        home = home["value"] if home else None
        if str(t["id"]) == home:
            raise HTTPException(403, "This is TechDeva's own business; it cannot be deleted.")
        if t["status"] == "active":
            raise HTTPException(409, "%s is a paying client (active). A paying client is not deleted from here." % name)
        cur.execute("SELECT DISTINCT agent_key FROM whatsapp_connections WHERE tenant_id = %s AND agent_key IS NOT NULL "
                    "UNION SELECT DISTINCT agent_key FROM agent_requests WHERE tenant_id = %s AND agent_key IS NOT NULL",
                    (str(tid), str(tid)))
        keys = sorted({r["agent_key"] for r in cur.fetchall() if r["agent_key"]})
        if re.search(r"instant\s*-?\s*grill", name, re.I) or any(FOUNDER_KEEP_AGENTS.match(k) for k in keys):
            raise HTTPException(403, "%s has an agent that is managed by hand; it is not deleted from here." % name)
        counts = _counts(cur, str(tid))
        cur.execute("DELETE FROM signups WHERE tenant_id = %s", (str(tid),))
        if cur.rowcount:
            counts["signups"] = cur.rowcount
        cur.execute("DELETE FROM tenants WHERE id = %s", (str(tid),))
        if keys:
            cur.execute("DELETE FROM ai_key_assignments WHERE agent_key = ANY(%s)", (keys,))
            cur.execute("DELETE FROM ai_agent_live WHERE agent_key = ANY(%s)", (keys,))
        cur.execute("""INSERT INTO tenant_deletions (tenant_id, business_name, trial_ended_at, rows_deleted, agent_keys,
                                                     agents_removed_at)
                       VALUES (%s, %s, %s, %s::jsonb, %s, CASE WHEN %s THEN now() END) RETURNING *""",
                    (str(tid), name, t["trial_ends_at"], json.dumps(counts), keys, not keys))
        row = dict(cur.fetchone())
        if home:
            record(cur, action="founder.client.deleted", resource="tenants", resource_id=str(tid), actor=str(by or "founder"),
                   actor_type="human", status="ok", detail={"business": name, "status": t["status"],
                                                            "rows": sum(counts.values()), "agents": keys},
                   error=None, tenant_id=home)
    print("[retention] founder deleted %s (%s rows, agents %s)" % (tid, sum(counts.values()), keys), flush=True)
    return row


def _warn(cur, c):
    """Email the owner that the data will be deleted. Returns True only when the
    email was sent; only then is the business marked warned (warned_at), which
    starts the WARN_DAYS before deletion. A failure, no email server or no owner
    address leaves it unwarned, so it is tried again on the next run."""
    emailed = False
    try:
        from .account import mail_configured, send_mail
        if mail_configured():
            cur.execute("SELECT email FROM users WHERE tenant_id = %s AND role = 'owner' "
                        "AND email NOT LIKE '%%@pin.invalid' ORDER BY created_at LIMIT 1", (str(c["id"]),))
            u = cur.fetchone()
            if u and u["email"]:
                day = c["deletes_on"].astimezone(_dt.timezone(_dt.timedelta(hours=5))).strftime("%d %B %Y")
                send_mail(u["email"], "Your TechDeva data will be deleted on %s" % day,
                          "Hello,\n\nThe free trial for %s ended and has not been paid. On %s all of its data on "
                          "TechDeva (customers, bookings, invoices, WhatsApp agent) will be deleted and cannot be "
                          "brought back.\n\nTo keep it, pay for your plan before then: open your dashboard and go to "
                          "Plan & Billing.\n\nTechDeva" % (c["name"], day))
                emailed = True
    except Exception as e:                     # not sent: not warned, tried again next run
        print("[retention] warning email for %s failed: %s" % (c["id"], type(e).__name__), flush=True)
    if emailed:
        cur.execute("""INSERT INTO data_retention (tenant_id, warned_at, emailed) VALUES (%s, now(), true)
                       ON CONFLICT (tenant_id) DO UPDATE SET warned_at = now(), emailed = true,
                                                             updated_at = now()""", (str(c["id"]),))
    else:
        # an older warning (before a trial extension) stops counting too: emailed = false
        cur.execute("""INSERT INTO data_retention (tenant_id, emailed) VALUES (%s, false)
                       ON CONFLICT (tenant_id) DO UPDATE SET emailed = false, updated_at = now()""",
                    (str(c["id"]),))
        print("[retention] %s not warned yet: the warning email was not sent" % c["id"], flush=True)
    return emailed


def run_due(force=False):
    """Scheduler hook, at most once an hour: warn, then delete what is due."""
    if not force and time.time() - _LAST["t"] < RUN_EVERY:
        return 0
    _LAST["t"] = time.time()
    done = 0
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute(CANDIDATES)
        rows = [dict(r) for r in cur.fetchall()]
        now = _dt.datetime.now(_dt.timezone.utc)
        for c in rows:
            if c["hold"] or c["warned_at"]:
                continue
            if c["trial_ends_at"] + _dt.timedelta(days=GRACE_DAYS - WARN_DAYS) <= now:
                if _warn(cur, c):
                    done += 1
    for c in rows:
        if not c["hold"] and c["warned_at"] and c["deletes_on"] <= now:
            if delete_business(c["id"]):
                done += 1
    return done


# ------------------------------------------------------------------ server alert
def paying_count(cur):
    cur.execute("""SELECT count(*) AS n FROM tenants
                    WHERE status::text = 'active' AND NOT is_test
                      AND id::text <> coalesce((SELECT value FROM platform_settings WHERE key = 'home_tenant_id'), '')""")
    return cur.fetchone()["n"]


def alert_at(cur):
    cur.execute("SELECT value FROM platform_settings WHERE key = %s", (SERVER_ALERT_KEY,))
    r = cur.fetchone()
    try:
        return int(r["value"]) if r else SERVER_ALERT_DEFAULT
    except (TypeError, ValueError):
        return SERVER_ALERT_DEFAULT


# ------------------------------------------------------------------ routes
def make_retention_router():
    router = APIRouter()

    @router.get("/techdeva/retention")
    def overview(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute(CANDIDATES + " ORDER BY deletes_on")
            upcoming = [dict(r) for r in cur.fetchall()]
            cur.execute("SELECT id, tenant_id, business_name, trial_ended_at, deleted_at, rows_deleted, agent_keys, "
                        "agents_removed_at, agent_error FROM tenant_deletions ORDER BY deleted_at DESC LIMIT 50")
            deleted = [dict(r) for r in cur.fetchall()]
            n, at = paying_count(cur), alert_at(cur)
        now = _dt.datetime.now(_dt.timezone.utc)
        for u in upcoming:
            u["days_left"] = max(0, (u["deletes_on"] - now).days)
        return {"grace_days": GRACE_DAYS, "warn_days": WARN_DAYS, "upcoming": upcoming, "deleted": deleted,
                "paying": n, "server_alert_at": at, "server_alert": n >= at}

    @router.put("/techdeva/retention/{tenant_id}")
    def hold(tenant_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
             x_role: str | None = Header(None), authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Keep (or stop keeping) a business's data past the deletion day."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        keep = bool(body.get("hold"))
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT name FROM tenants WHERE id::text = %s", (tenant_id,))
            t = cur.fetchone()
            if not t:
                raise HTTPException(404, "That business is not there.")
            cur.execute("""INSERT INTO data_retention (tenant_id, hold, hold_reason, held_by) VALUES (%s, %s, %s, %s)
                           ON CONFLICT (tenant_id) DO UPDATE SET hold = EXCLUDED.hold, hold_reason = EXCLUDED.hold_reason,
                                 held_by = EXCLUDED.held_by, updated_at = now()""",
                        (tenant_id, keep, str(body.get("reason") or "")[:200], str(uid or "founder")))
            record(cur, action="retention.hold" if keep else "retention.release", resource="tenants",
                   resource_id=tenant_id, actor=str(uid or "founder"), actor_type="human", status="ok",
                   detail={"business": t["name"]}, error=None, tenant_id=tenant_id)
        return {"ok": True, "hold": keep}

    @router.put("/techdeva/retention-settings")
    def settings(body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        try:
            at = int(body.get("server_alert_at"))
        except (TypeError, ValueError):
            raise HTTPException(400, "Give a number of paying clients.")
        if not 1 <= at <= 1000:
            raise HTTPException(400, "Between 1 and 1000.")
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("INSERT INTO platform_settings (key, value) VALUES (%s, %s) "
                        "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (SERVER_ALERT_KEY, str(at)))
            record(cur, action="techdeva.setting", resource="platform_settings", resource_id=SERVER_ALERT_KEY,
                   actor=str(uid or "founder"), actor_type="human", status="ok",
                   detail={"server_alert_at": at}, error=None)
        return {"ok": True, "server_alert_at": at}

    @router.delete("/techdeva/clients/{tenant_id}")
    def founder_delete_route(tenant_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                             x_role: str | None = Header(None), authorization: str | None = Header(None),
                             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Delete a business now (founder console > Clients > Delete). Cannot be undone."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        row = founder_delete(tenant_id, str(body.get("confirm") or ""), uid)
        return {"ok": True, "business": row["business_name"], "rows": sum((row["rows_deleted"] or {}).values()),
                "agents": row["agent_keys"]}

    # ---- the server's provisioning job removes deleted businesses' agents
    @router.get("/techdeva/retention/agent-removals")
    def removals(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                 authorization: str | None = Header(None),
                 td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT id, agent_keys FROM tenant_deletions WHERE agents_removed_at IS NULL "
                        "AND cardinality(agent_keys) > 0 ORDER BY deleted_at LIMIT 5")
            return {"removals": [{"id": str(r["id"]), "agent_keys": r["agent_keys"]} for r in cur.fetchall()]}

    @router.post("/techdeva/retention/agent-removals/{deletion_id}")
    def removed(deletion_id: str, body: dict = Body(...), x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None), authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            if body.get("removed"):
                cur.execute("UPDATE tenant_deletions SET agents_removed_at = now(), agent_error = NULL WHERE id::text = %s",
                            (deletion_id,))
            else:
                cur.execute("UPDATE tenant_deletions SET agent_error = %s WHERE id::text = %s",
                            (str(body.get("error") or "failed")[:500], deletion_id))
        return {"ok": True}

    return router
