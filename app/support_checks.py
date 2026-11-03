"""The support agent's audit: what is wrong right now. Nothing is changed here.

Each check returns findings (dicts made by finding()). A finding with auto=True
is one the agent may fix by itself because the fix reaches nobody: closing an
alert nobody has re-raised for days. Every other finding waits for a person
(app/support_agent.py applies the fixes).

audience 'owner'    the business owner sees it in their own dashboard
audience 'techdeva' internal: only the founder sees it (crashes, containers)

Every query names its tenant explicitly: the caller's cursor is the founder's
(tenant_session(None, "techdeva_admin")), which sees every business.
"""
from __future__ import annotations

import datetime as _dt

STALE_INCIDENT = _dt.timedelta(days=3)       # an open alert nobody has re-raised for this long is over
QUEUE_LATE = _dt.timedelta(minutes=30)       # queued work this late means the worker is not taking it
APPROVAL_WAIT = _dt.timedelta(days=3)
TRIAL_WARN = _dt.timedelta(days=3)
RECENT = _dt.timedelta(days=14)              # failed work older than this is history, not a problem
SIGNUP_LATE = _dt.timedelta(hours=1)
HOST_INCIDENTS = ("platform.monitor", "monitor.alerts")   # written by /opt/techdeva-monitor.sh on the host
REPLY_KINDS = {"bot_error": "with an error message", "unbacked_claim": "with a price or promise not in your records",
               "unknown_service": "offering something not on your list"}   # app/replycheck.py


def finding(key, severity, audience, title, detail="", fix=None, fix_label="", auto=False, **args):
    return {"key": key, "severity": severity, "audience": audience, "title": title, "detail": detail or "",
            "fix": fix, "fix_label": fix_label, "auto": auto, "args": args}


def _now():
    return _dt.datetime.now(_dt.timezone.utc)


def _short(text, n=160):
    text = " ".join(str(text or "").split())
    return text if len(text) <= n else text[: n - 1] + "…"


# ------------------------------------------------------------------ the platform (tenant_id NULL)
def platform(cur, ai_health=None):
    found = []
    if ai_health is not None and ai_health.get("status") != "ok":
        resting = [c for c in ai_health.get("chain") or [] if c.get("state") == "resting"]
        why = "; ".join("%s: %s" % (c.get("model"), _short(c.get("last_error") or c.get("reason"), 80)) for c in resting[:3])
        found.append(finding("ai-degraded", "high", "techdeva", "The AI workforce cannot think",
                             "Status %s, %s usable model(s). %s" % (ai_health.get("status"), ai_health.get("usable", 0), why)))
    cur.execute("""SELECT count(*) AS n, min(COALESCE(run_after, created_at)) AS oldest FROM tasks
                    WHERE status = 'queued' AND COALESCE(run_after, created_at) < now() - %s""", (QUEUE_LATE,))
    r = cur.fetchone()
    if r["n"]:
        found.append(finding("queue-late", "high", "techdeva", "%s task(s) are waiting in the queue too long" % r["n"],
                             "Oldest due since %s. The worker may be down or stuck." % r["oldest"].strftime("%d %b %H:%M UTC")))
    cur.execute("""SELECT id, kind, title, detail FROM incidents
                    WHERE status = 'open' AND kind = ANY(%s)""", (list(HOST_INCIDENTS),))
    for i in cur.fetchall():
        found.append(finding("host-" + i["kind"], "high", "techdeva", "Server monitor: " + _short(i["title"], 80),
                             _short(i["detail"], 400)))
    cur.execute("""SELECT id, tenant_id, kind, title FROM incidents
                    WHERE status = 'open' AND last_seen_at < now() - %s AND NOT (kind = ANY(%s))""",
                (STALE_INCIDENT, list(HOST_INCIDENTS)))
    stale = cur.fetchall()
    if stale:
        found.append(finding("stale-incidents", "low", "techdeva", "%s old alert(s) nobody has re-raised for 3 days" % len(stale),
                             "Closed, because the problem has not come back since: " +
                             _short(", ".join(sorted({s["kind"] for s in stale})), 200),
                             "close_incidents", "Close them", auto=True, ids=[str(s["id"]) for s in stale]))
    cur.execute("""SELECT id, business_name, created_at FROM signups
                    WHERE status = 'new' AND tenant_id IS NULL AND created_at < now() - %s
                    ORDER BY created_at DESC LIMIT 20""", (SIGNUP_LATE,))
    late = cur.fetchall()
    if late:
        found.append(finding("signups-waiting", "medium", "techdeva", "%s sign-up(s) never became a business" % len(late),
                             "Waiting: " + _short(", ".join(s["business_name"] or "?" for s in late), 200)))
    cur.execute("""SELECT r.id, r.partner_id, r.last_error, p.name FROM partner_provisioning_runs r
                     JOIN partners p ON p.id = r.partner_id
                    WHERE r.status = 'failed'""")
    for r in cur.fetchall():
        found.append(finding("partner-setup-%s" % r["id"], "medium", "techdeva",
                             "Partner set-up stopped: %s" % r["name"], _short(r["last_error"], 300)))
    return found


# ------------------------------------------------------------------ one business
def business(cur, tid, pairctl_up):
    """pairctl_up(url) -> True when that WhatsApp agent's control service answers."""
    found = []
    cur.execute("SELECT name, status::text AS status, trial_ends_at FROM tenants WHERE id = %s", (tid,))
    t = cur.fetchone()
    if not t:
        return found
    found += _whatsapp(cur, tid, pairctl_up)
    found += _work(cur, tid)
    if t["status"] == "trial" and t["trial_ends_at"] and t["trial_ends_at"] - _now() < TRIAL_WARN:
        ends = t["trial_ends_at"]
        found.append(finding("trial-ending", "high", "owner", "Your free trial is ending",
                             "It ends on %s. Choose a plan to keep your agents working." % ends.strftime("%d %B %Y")
                             if ends > _now() else "It ended on %s." % ends.strftime("%d %B %Y")))
    # health is unknown | ok | degraded | failing (integrations_health_ck); 'unknown' only means "not checked yet"
    cur.execute("""SELECT provider, health, last_error, expires_at FROM integrations
                    WHERE tenant_id = %s AND status = 'connected' AND provider <> 'whatsapp'
                      AND (health IN ('degraded', 'failing') OR expires_at < now() + interval '7 days')""", (tid,))
    for i in cur.fetchall():
        name = {"meta": "Facebook", "instagram": "Instagram", "google": "Google",
                "storefront": "your online store"}.get(i["provider"], i["provider"])
        why = ("Access ends on %s." % i["expires_at"].strftime("%d %B")) if i["expires_at"] and i["expires_at"] < _now() + _dt.timedelta(days=7) \
            else _short(i["last_error"] or i["health"], 200)
        found.append(finding("reconnect-" + i["provider"], "high", "owner", "Reconnect %s" % name,
                             why + " Open Administration, Integrations and connect again."))
    cur.execute("""SELECT count(*) AS n FROM scheduled_posts
                    WHERE tenant_id = %s AND status = 'failed' AND updated_at > now() - %s""", (tid, RECENT))
    n = cur.fetchone()["n"]
    if n:
        found.append(finding("posts-failed", "medium", "owner", "%s post(s) could not be published" % n,
                             "Open Marketing, Publisher to see why and post again."))
    cur.execute("""SELECT kind, count(*) AS n, max(excerpt) AS sample FROM agent_reply_flags
                    WHERE tenant_id = %s AND status = 'open' AND created_at > now() - interval '7 days'
                    GROUP BY kind""", (tid,))
    for r in cur.fetchall():
        what = REPLY_KINDS.get(r["kind"], "doubtful")
        found.append(finding("reply-" + r["kind"], "medium", "owner",
                             "Your WhatsApp agent sent %s repl%s %s" % (r["n"], "y" if r["n"] == 1 else "ies", what),
                             "For example: " + _short(r["sample"], 140) + " Teach it the right answer in Hermes AI."))
    return found


def _whatsapp(cur, tid, pairctl_up):
    """pairctl_up(url) -> whether the agent's control service answers. Whether the
    WhatsApp bridge inside the agent is linked is checked on the host (monitor)."""
    found = []
    cur.execute("""SELECT id, agent_key, pairctl_url, status, phone_number, connected_at FROM whatsapp_connections
                    WHERE tenant_id = %s AND status IN ('connected', 'disconnected')""", (tid,))
    for c in cur.fetchall():
        number = c["phone_number"] or c["agent_key"]
        if c["status"] == "disconnected":
            if c["connected_at"]:
                found.append(finding("wa-logged-out-%s" % c["id"], "high", "owner", "WhatsApp %s is logged out" % number,
                                     "Your agent cannot read or answer customers. Open Chats, WhatsApp and scan the "
                                     "QR code with that phone."))
        elif not (c["pairctl_url"] and pairctl_up(c["pairctl_url"])):
            found.append(finding("agent-unreachable-%s" % c["agent_key"], "high", "techdeva",
                                 "WhatsApp agent %s does not answer" % c["agent_key"],
                                 "Its control service (pairctl) is not reachable; the container may be down."))
    return found


def _work(cur, tid):
    found = []
    cur.execute("""SELECT id, agent, type, status, result FROM tasks
                    WHERE tenant_id = %s AND status IN ('failed', 'dead') AND updated_at > now() - %s
                      AND NOT (COALESCE(result, '{}'::jsonb) ? 'closed_by_owner')
                      AND NOT (COALESCE(result, '{}'::jsonb) ? 'rejected_by_client')
                    ORDER BY updated_at DESC LIMIT 50""", (tid, RECENT))
    rows = cur.fetchall()
    if rows:
        kinds = sorted({"%s/%s" % (r["agent"], r["type"]) for r in rows})
        why = next((str((r["result"] or {}).get("dead") or (r["result"] or {}).get("error") or "") for r in rows
                    if (r["result"] or {}).get("dead") or (r["result"] or {}).get("error")), "")
        found.append(finding("tasks-failed", "medium", "techdeva", "%s AI task(s) failed" % len(rows),
                             "Work: %s.%s" % (", ".join(kinds)[:160], (" Last error: " + _short(why, 160)) if why else ""),
                             "retry_tasks", "Run them again", task_ids=[str(r["id"]) for r in rows]))
    cur.execute("""SELECT count(*) AS n, min(created_at) AS oldest FROM deliverables
                    WHERE tenant_id = %s AND status = 'awaiting_approval' AND created_at < now() - %s""", (tid, APPROVAL_WAIT))
    r = cur.fetchone()
    if r["n"]:
        found.append(finding("approvals-waiting", "medium", "owner", "%s item(s) are waiting for your approval" % r["n"],
                             "The oldest since %s. Open Jarvis & AI to approve or reject them." % r["oldest"].strftime("%d %B")))
    return found
