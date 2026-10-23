"""The business's AI agents: what each one does, switching them on and off, and
keeping each agent's memory and data to itself (Faheem, 23 Oct 2026).

Switches. An owner can switch an agent off (table agent_settings, db/74; no row
= on). Its queued tasks are then skipped, recorded as skipped so a pipeline
carries on (content -> design -> publishing), and no new work starts for it.
Quality checks cannot be switched off: they stop invented prices reaching
customers. Switching never touches the WhatsApp agent's own containers (Hermes).

Memory. Every agent used to receive the whole business memory, including the
decrypted Facebook token that only the Analysis agent needs. Now each agent gets
only the keys listed for it in MEMORY (plus the shared, non-secret ones in
COMMON); the lessons the owner taught (all for the WhatsApp side) go only to
Follow-ups. What an agent writes is still tagged with its name (context_store.
updated_by_agent), and its AI use is counted under its own name (ai_usage.agent).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

CATALOG = {
    "analysis":       ("Analysis", "Reads your website, pages and competitors' ads and writes your growth report."),
    "growth_planner": ("Growth planner", "Turns the report into a plan of what to post and promote."),
    "content":        ("Content writer", "Writes posts and captions for the plan, from your own prices and services."),
    "design":         ("Designer", "Makes the pictures for planned posts."),
    "publishing":     ("Publisher", "Schedules posts you have approved."),
    "ads":            ("Ads agent", "Prepares ad campaigns. Never spends money: you press Launch."),
    "optimizer":      ("Campaign optimizer", "Reads your ad results and suggests budget changes for your approval."),
    "reporting":      ("Reporting", "Adds up results and where your customers came from."),
    "crm":            ("CRM agent", "Keeps your leads' details and stages up to date."),
    "hermes":         ("Follow-ups", "Follow-up tasks for WhatsApp leads. Your WhatsApp agent itself keeps answering."),
    "quality":        ("Quality check", "Checks every piece of AI work for invented facts before you see it."),
}
ALWAYS_ON = {"quality"}
# shared, not secret: connection status, what humans corrected, nothing else
COMMON = ("integrations", "learning")
MEMORY = {
    "analysis":       ("meta_credentials", "meta_credentials_error", "latest_analysis"),
    "growth_planner": ("latest_analysis", "current_growth_plan"),
    "content":        ("latest_analysis", "current_growth_plan", "current_content_plan"),
    "design":         ("latest_analysis", "current_content_plan"),
    "publishing":     ("current_content_plan",),
    "ads":            ("current_content_plan", "current_growth_plan"),
    "optimizer":      ("campaign_performance", "last_optimization"),
    "reporting":      ("attribution_rows", "campaign_performance", "last_report"),
    "crm":            (),
    "hermes":         ("last_service_discussed", "lessons"),
    "quality":        ("latest_analysis",),
}
_ON: dict = {}
TTL = 30


def memory_for(agent, store):
    """The part of the business memory this agent may see."""
    keys = set(COMMON) | set(MEMORY.get(agent, ()))
    return {k: v for k, v in (store or {}).items() if k in keys}


def enabled(tenant_id, agent):
    """Is this agent on for this business? (cached 30 s; no row = on)"""
    if agent in ALWAYS_ON:
        return True
    key = (str(tenant_id), agent)
    hit = _ON.get(key)
    if hit and hit[0] > time.time():
        return hit[1]
    try:
        with tenant_session(str(tenant_id), "owner") as cur:
            cur.execute("SELECT enabled FROM agent_settings WHERE agent = %s", (agent,))
            r = cur.fetchone()
        value = r["enabled"] if r else True
    except Exception as e:  # noqa: BLE001 -- a failed read must never stop a business's agents
        print("[agents] switch read skipped: %s" % str(e)[:200], flush=True)
        value = True
    _ON[key] = (time.time() + TTL, value)
    return value


def make_agent_admin_router():
    router = APIRouter()

    def _who(x_tenant_id, x_role, authorization, session):
        try:
            tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        if not tid:
            raise HTTPException(400, "tenant required")
        return tid, role, uid

    @router.get("/ai/agents")
    def agents(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Each agent: on or off, its work and AI use this month, when it last ran, what it may remember."""
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        from .usage import month_start
        since = month_start()
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT agent, enabled, updated_at FROM agent_settings")
            sw = {r["agent"]: dict(r) for r in cur.fetchall()}
            cur.execute("""SELECT agent, count(*) FILTER (WHERE status = 'done' AND NOT (result ? 'skipped')) AS done,
                                  count(*) FILTER (WHERE status IN ('queued','waiting','running')) AS open,
                                  count(*) FILTER (WHERE status = 'needs_human') AS needs_you,
                                  count(*) FILTER (WHERE status IN ('failed','dead')) AS failed,
                                  count(*) FILTER (WHERE result ? 'skipped') AS skipped,
                                  max(updated_at) AS last_run
                             FROM tasks WHERE created_at >= %s GROUP BY agent""", (since,))
            work = {r["agent"]: dict(r) for r in cur.fetchall()}
            cur.execute("SELECT agent, COALESCE(sum(credits), 0) AS credits FROM ai_usage WHERE at >= %s GROUP BY agent", (since,))
            credits = {r["agent"]: int(r["credits"]) for r in cur.fetchall()}
            cur.execute("SELECT key, updated_by_agent, updated_at FROM context_store")
            written = {}
            for r in cur.fetchall():
                written.setdefault(r["updated_by_agent"] or "", []).append(r["key"])
        out = []
        for key, (name, about) in CATALOG.items():
            w = work.get(key, {})
            out.append({"agent": key, "name": name, "about": about, "enabled": sw.get(key, {}).get("enabled", True),
                        "can_switch": key not in ALWAYS_ON, "switched_at": sw.get(key, {}).get("updated_at"),
                        "done": w.get("done", 0), "open": w.get("open", 0), "needs_you": w.get("needs_you", 0),
                        "failed": w.get("failed", 0), "skipped": w.get("skipped", 0), "last_run": w.get("last_run"),
                        "credits": credits.get(key, 0), "remembers": sorted(written.get(key, [])),
                        "reads": sorted(set(COMMON) | set(MEMORY.get(key, ())))})
        return {"agents": out, "since": since.date().isoformat(), "can_change": role in ("owner", "techdeva_admin")}

    @router.put("/ai/agents/{agent}")
    def switch(agent: str, body: dict = Body(...), x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _who(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the owner switches agents on and off.")
        if agent not in CATALOG:
            raise HTTPException(404, "No such agent.")
        if agent in ALWAYS_ON:
            raise HTTPException(400, "Quality checks stay on: they stop invented prices and claims reaching your customers.")
        on = bool(body.get("enabled"))
        with tenant_session(tid, role) as cur:
            cur.execute("""INSERT INTO agent_settings (tenant_id, agent, enabled, updated_by) VALUES (current_tenant(), %s, %s, %s)
                           ON CONFLICT (tenant_id, agent) DO UPDATE SET enabled = EXCLUDED.enabled,
                             updated_by = EXCLUDED.updated_by, updated_at = now()""",
                        (agent, on, str(uid) if uid and len(str(uid)) == 36 else None))
            record(cur, action="agents.switch", resource="agent_settings", resource_id=agent, actor=str(uid) if uid else None,
                   status="ok", detail={"agent": agent, "enabled": on})
        _ON.pop((str(tid), agent), None)
        return {"ok": True, "agent": agent, "enabled": on}

    return router
