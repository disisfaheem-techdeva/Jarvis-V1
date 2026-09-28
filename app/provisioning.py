"""A business asks for its own WhatsApp agent; TechDeva approves; the server builds it.

    owner      POST /whatsapp/agent-request      -> requested
    founder    POST /techdeva/agent-requests/{id}/approve | reject
    server     ops/agent-template/provision-queue.sh (cron, on the host) picks up
               'approved', runs new-agent.sh, and reports building -> ready | failed

The API never starts containers itself: it has no Docker access, and giving it
some would hand the whole server to anyone who breaks into the API. The host
script does the building and holds the Docker rights.

Approval is a person's decision on purpose: each agent is two containers on the
same machine as the clinic, so a free-trial signup must not be able to start
them unattended.
"""
import re

from fastapi import APIRouter, Body, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

COLS = ("id, tenant_id, owner_phone, home_name, business_name, assistant, status, agent_key, "
        "error, created_at, updated_at")
HOST_STATES = ("building", "ready", "failed")


def _scope(x_tenant_id, x_role, authorization, session):
    try:
        return resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)


def _admin(*a):
    _t, role, uid = _scope(*a)
    if role != "techdeva_admin":
        raise HTTPException(403, "admin only")
    return uid


def agent_key_for(business, tenant_id):
    """Readable, unique, and inside new-agent.sh's rules: ^[a-z][a-z0-9-]{1,30}$."""
    slug = re.sub(r"[^a-z0-9]+", "-", (business or "").lower()).strip("-")[:20].strip("-")
    if not slug or not slug[0].isalpha():
        slug = "c" + slug
    return ("%s-%s" % (slug, str(tenant_id).replace("-", "")[:6])).strip("-")


def _safe_name(raw, fallback):
    """Names go into the agent's config file on the server (new-agent.sh):
    plain characters only, the same rule the script enforces."""
    v = re.sub(r"[^A-Za-z0-9 .'-]", "", str(raw or ""))
    v = re.sub(r"\s+", " ", v).strip(" .'-")[:60]
    return v if len(v) >= 2 else fallback


def _phone(raw):
    d = re.sub(r"\D", "", str(raw or ""))
    if d.startswith("00"):
        d = d[2:]
    if d.startswith("03") and len(d) == 11:          # 0300 1234567 -> 92300...
        d = "92" + d[1:]
    return d


def make_provisioning_router():
    router = APIRouter()

    # ---------------------------------------------------------------- owner
    @router.get("/whatsapp/agent-request")
    def mine(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _scope(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT " + COLS + " FROM agent_requests ORDER BY created_at DESC LIMIT 1")
            r = cur.fetchone()
        return {"request": dict(r) if r else None}

    @router.post("/whatsapp/agent-request")
    def ask(body: dict = Body(...),
            x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
            authorization: str | None = Header(None),
            td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _scope(x_tenant_id, x_role, authorization, td_session)
        if not tid:
            raise HTTPException(400, "tenant required")
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can ask for a WhatsApp agent.")
        phone = _phone(body.get("owner_phone"))
        if not 10 <= len(phone) <= 15:
            raise HTTPException(400, "Enter the owner's WhatsApp number with country code, e.g. 92300 1234567.")
        home = _safe_name(body.get("home_name"), "")
        assistant = re.sub(r"[^A-Za-z]", "", str(body.get("assistant") or "Zara"))[:20]
        assistant = assistant if len(assistant) >= 2 else "Zara"
        with tenant_session(tid, role) as cur:
            cur.execute("SELECT 1 FROM whatsapp_connections WHERE tenant_id = current_tenant()")
            if cur.fetchone():
                raise HTTPException(409, "Your business already has a WhatsApp agent. Connect it below.")
            cur.execute("SELECT name FROM tenants WHERE id = current_tenant()")
            business = _safe_name((cur.fetchone() or {}).get("name"), "Business")
            if len(home) < 2:
                home = business
            cur.execute("SELECT 1 FROM agent_requests WHERE status IN ('requested','approved','building')")
            if cur.fetchone():
                raise HTTPException(409, "Your request is already with TechDeva.")
            cur.execute("INSERT INTO agent_requests (tenant_id, owner_phone, home_name, business_name, assistant, requested_by) "
                        "VALUES (current_tenant(), %s, %s, %s, %s, %s) RETURNING " + COLS,
                        (phone, home, business, assistant, str(uid) if uid else None))
            row = dict(cur.fetchone())
            record(cur, action="whatsapp.agent_requested", resource="agent_requests",
                   resource_id=str(row["id"]), actor=str(uid), status="ok", detail={"assistant": assistant})
        return {"request": row}

    # -------------------------------------------------------------- founder
    @router.get("/techdeva/agent-requests")
    def queue(status: str | None = None,
              x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            if status:
                cur.execute("SELECT " + COLS + " FROM agent_requests WHERE status = %s ORDER BY created_at LIMIT 20",
                            (status,))
            else:
                cur.execute("SELECT " + COLS + " FROM agent_requests "
                            "ORDER BY (status IN ('requested','failed')) DESC, created_at DESC LIMIT 40")
            rows = [dict(r) for r in cur.fetchall()]
        return {"requests": rows}

    def _move(rid, uid, frm, to, **extra):
        sets = ["status = %s", "updated_at = now()"]
        vals = [to]
        for k, v in extra.items():
            sets.append("%s = %%s" % k)
            vals.append(v)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("UPDATE agent_requests SET " + ", ".join(sets) + " WHERE id = %s AND status = ANY(%s) "
                        "RETURNING " + COLS, vals + [rid, list(frm)])
            row = cur.fetchone()
            if not row:
                raise HTTPException(409, "That request is not in a state that allows this.")
            row = dict(row)
            record(cur, action="whatsapp.agent_" + to, resource="agent_requests", resource_id=rid,
                   actor=uid, actor_type="human" if uid != "provisioner" else "system",
                   status="ok" if to != "failed" else "error", detail={"agent_key": row["agent_key"]},
                   error=extra.get("error"), tenant_id=row["tenant_id"])
        return row

    @router.post("/techdeva/agent-requests/{rid}/approve")
    def approve(rid: str, x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Approve (or retry a failed build). The server builds it within a minute."""
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT tenant_id, business_name, agent_key FROM agent_requests WHERE id = %s", (rid,))
            r = cur.fetchone()
        if not r:
            raise HTTPException(404, "No such request.")
        key = r["agent_key"] or agent_key_for(r["business_name"], r["tenant_id"])
        return {"request": _move(rid, str(uid), ("requested", "failed"), "approved",
                                 agent_key=key, decided_by=str(uid), error=None)}

    @router.post("/techdeva/agent-requests/{rid}/reject")
    def reject(rid: str, body: dict = Body(default={}),
               x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        uid = _admin(x_tenant_id, x_role, authorization, td_session)
        reason = str(body.get("reason") or "").strip()[:300] or "TechDeva will contact you about your WhatsApp agent."
        return {"request": _move(rid, str(uid), ("requested", "failed"), "rejected",
                                 error=reason, decided_by=str(uid))}

    @router.post("/techdeva/agent-requests/{rid}/state")
    def host_state(rid: str, body: dict = Body(...),
                   x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Reported by provision-queue.sh on the server (admin token it mints itself)."""
        _admin(x_tenant_id, x_role, authorization, td_session)
        to = str(body.get("status") or "")
        if to not in HOST_STATES:
            raise HTTPException(400, "status must be building, ready or failed")
        frm = {"building": ("approved",), "ready": ("building",), "failed": ("approved", "building")}[to]
        err = str(body.get("error") or "")[:1000] or None
        return {"request": _move(rid, "provisioner", frm, to, error=err)}

    return router
