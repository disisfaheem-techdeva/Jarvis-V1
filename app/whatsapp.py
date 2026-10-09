"""WhatsApp self-service: the client pairs their own number.

Until Part 4 a number was paired by a person with shell access to this server,
watching a QR code drawn in a terminal. That does not scale past one client and
it is not something to sell.

Three rules shape this module:

* The pairing control URL is read from the tenant's own row and never accepted
  from the caller. Which agent a tenant lives on is a TechDeva decision; a
  request body that could name it would be a way into another tenant's number.
* Nothing here reports a connection it cannot prove. A number counts as
  connected when the bridge hands back the number it actually paired.
* The QR is passed through, never cached. WhatsApp rotates it about every
  twenty seconds, and a stale code is worse than none: it fails silently in the
  client's hand and they blame the product.
"""

import json
import os
import re
import urllib.parse
import urllib.error
import urllib.request

import io

import qrcode
import qrcode.image.svg

from fastapi import APIRouter, Cookie, Header, HTTPException

from .audit import record
from .auth import AuthError, SESSION_COOKIE, resolve_scope
from .db import tenant_session

# Read at call time, not import time, so rotating the key means restarting the
# API rather than rebuilding it.
def _key():
    return os.environ.get("HERMES_INTERNAL_KEY", "")

PAIRCTL_TIMEOUT = 8


def _svg(data):
    """Draw the pairing code as an SVG.

    Server side on purpose. The browser gets a picture it can show and nothing
    it has to interpret, and the dashboard needs no third-party script loaded
    over the client's shoulder to display their own WhatsApp code.
    """
    if not data:
        return None
    q = qrcode.QRCode(box_size=8, border=2)
    q.add_data(data)
    q.make(fit=True)
    img = q.make_image(image_factory=qrcode.image.svg.SvgPathImage)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


def _identity(x_tenant_id, x_role, authorization, session):
    try:
        tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, session)
    except AuthError as e:
        raise HTTPException(e.status, e.message)
    if not tid:
        raise HTTPException(400, "tenant required")
    return tid, role, uid


ROW_COLS = """id, agent_key, pairctl_url, status, phone_number, display_name,
               connected_at, disconnected_at, last_error, created_at"""


def _rows(cur, tenant_id):
    """Every WhatsApp number this business has, first one first."""
    cur.execute("SELECT " + ROW_COLS + " FROM whatsapp_connections WHERE tenant_id = %s "
                "ORDER BY created_at, id", (tenant_id,))
    return cur.fetchall()


def _row(cur, tenant_id, conn=None):
    """One number: the one named by `conn` (its id), else the business's first.
    A business with a single number behaves exactly as before."""
    if conn:
        if not re.fullmatch(r"[0-9a-f-]{36}", conn):
            raise HTTPException(400, "That is not a WhatsApp number id.")
        cur.execute("SELECT " + ROW_COLS + " FROM whatsapp_connections WHERE tenant_id = %s AND id = %s",
                    (tenant_id, conn))
        r = cur.fetchone()
        if not r:
            raise HTTPException(404, "That WhatsApp number is not part of this business.")
        return r
    rows = _rows(cur, tenant_id)
    if not rows:
        raise HTTPException(409, "No WhatsApp agent has been assigned to this business yet. "
                                 "TechDeva assigns one during onboarding.")
    return rows[0]


def _public(r):
    """What the client is allowed to see. The pairctl URL is not in this."""
    return {
        "id": str(r["id"]),
        "status": r["status"],
        "connected": r["status"] == "connected",
        "phone_number": r.get("phone_number"),
        "display_name": r.get("display_name"),
        "connected_at": r.get("connected_at"),
        "disconnected_at": r.get("disconnected_at"),
        "last_error": r.get("last_error"),
    }


def _pairctl(url, path, method="GET", timeout=PAIRCTL_TIMEOUT):
    key = _key()
    if not key:
        raise HTTPException(503, "Pairing is not configured on this server.")
    req = urllib.request.Request(
        url.rstrip("/") + path,
        method=method,
        headers={"x-internal-key": key},
        data=b"" if method == "POST" else None,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            # pairctl refuses a new pairing while the number is connected: a
            # second login on the same number makes WhatsApp drop the device.
            raise HTTPException(409, "This WhatsApp number is already connected and answering "
                                     "customers. There is nothing to connect.")
        raise HTTPException(502, "The pairing service refused the request (%s)." % e.code)
    except Exception:
        # The agent may simply be down. Say so plainly rather than 500.
        raise HTTPException(503, "The pairing service for this business is not reachable.")


def _pairctl_json(url, path, method="GET", payload=None):
    """Like _pairctl, but carries a JSON body and keeps a 404 a 404."""
    key = _key()
    if not key:
        raise HTTPException(503, "WhatsApp is not configured on this server.")
    data = json.dumps(payload).encode() if payload is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url.rstrip("/") + path, method=method, data=data,
                                 headers={"x-internal-key": key,
                                          "content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=PAIRCTL_TIMEOUT) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise HTTPException(404, "That conversation is not there.")
        raise HTTPException(502, "The WhatsApp service refused the request (%s)." % e.code)
    except Exception:
        raise HTTPException(503, "The WhatsApp service for this business is not reachable right now.")


def sync_lessons(tid, role):
    """Write this business's active lessons into its own WhatsApp agent's SOUL.md.

    Never raises: teaching must not fail because the agent is down. Returns
    'no_agent', 'failed' or 'synced:<n>'."""
    try:
        with tenant_session(tid, role) as cur:
            conns = _rows(cur, tid)
            if not conns:
                return "no_agent"
            cur.execute("""SELECT lesson FROM agent_lessons
                            WHERE tenant_id = %s AND active AND agent = 'whatsapp'
                            ORDER BY created_at ASC LIMIT 200""", (tid,))
            lessons = [x["lesson"] for x in cur.fetchall()]
        count = None
        for r in conns:          # every number's agent learns the same lessons
            out = _pairctl_json(r["pairctl_url"], "/soul/lessons", "POST", {"lessons": lessons})
            count = out.get("count", len(lessons))
        return "synced:%s" % count
    except Exception as e:
        print("[whatsapp] lesson sync failed: %s" % getattr(e, "detail", e), flush=True)
        return "failed"


def make_whatsapp_router():
    router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

    @router.get("")
    def status(conn: str | None = None, x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        from .plans import plan_for
        with tenant_session(tid, role) as cur:
            out = _public(_row(cur, tid, conn))
            out["numbers"] = [_public(x) for x in _rows(cur, tid)]
        p = plan_for(tid)
        out["plan"] = {"name": p["name"], "max_numbers": p["max_numbers"]}
        return out

    @router.post("/connect")
    def connect(conn: str | None = None, x_tenant_id: str | None = Header(None),
                x_role: str | None = Header(None),
                authorization: str | None = Header(None),
                td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Open a pairing window. Owners only - this puts an AI in front of the
        business's customers, which is not an operator's decision to make."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can connect a WhatsApp number.")
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid, conn)
            _pairctl(r["pairctl_url"], "/pair/start", "POST", timeout=35)   # the guard may wait up to 20 s
            cur.execute("""UPDATE whatsapp_connections
                              SET status = 'pairing', last_error = NULL, updated_at = now()
                            WHERE id = %s""", (r["id"],))
            record(cur, action="whatsapp.connect", resource="whatsapp_connections",
                   resource_id=str(r["id"]), actor=str(uid), actor_type="human",
                   status="ok", detail={"agent": r["agent_key"]}, error=None)
        return {"status": "pairing"}

    @router.get("/qr")
    def qr(conn: str | None = None, x_tenant_id: str | None = Header(None),
           x_role: str | None = Header(None),
           authorization: str | None = Header(None),
           td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Poll while the client has their phone out. Returns the live code."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid, conn)
            s = _pairctl(r["pairctl_url"], "/pair/status")

            state = s.get("state")
            if state == "connected" and s.get("number"):
                cur.execute("""UPDATE whatsapp_connections
                                  SET status = 'connected', phone_number = %s,
                                      display_name = %s, connected_at = now(),
                                      disconnected_at = NULL, last_error = NULL,
                                      updated_at = now()
                                WHERE id = %s""",
                            (s.get("number"), s.get("name"), r["id"]))
                record(cur, action="whatsapp.paired", resource="whatsapp_connections",
                       resource_id=str(r["id"]), actor=str(uid), actor_type="human",
                       status="ok", detail={"number": s.get("number")}, error=None)
                return {"state": "connected", "qr": None, "phone_number": s.get("number")}

            if s.get("qr"):
                cur.execute("""UPDATE whatsapp_connections
                                  SET last_qr_at = now(), updated_at = now()
                                WHERE id = %s""", (r["id"],))

        return {
            "state": state or "starting",
            "qr_svg": _svg(s.get("qr")),
            "qr_age_seconds": s.get("qr_age_seconds"),
            "qr_stale": s.get("qr_stale"),
        }

    @router.post("/disconnect")
    def disconnect(conn: str | None = None, x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can disconnect the WhatsApp number.")
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid, conn)
            _pairctl(r["pairctl_url"], "/pair/stop", "POST")
            cur.execute("""UPDATE whatsapp_connections
                              SET status = 'disconnected', disconnected_at = now(),
                                  updated_at = now()
                            WHERE id = %s""", (r["id"],))
            record(cur, action="whatsapp.disconnect", resource="whatsapp_connections",
                   resource_id=str(r["id"]), actor=str(uid), actor_type="human",
                   status="ok", detail={"agent": r["agent_key"]}, error=None)
        return {"status": "disconnected"}

    @router.get("/chats")
    def chats(x_tenant_id: str | None = Header(None),
              x_role: str | None = Header(None),
              authorization: str | None = Header(None),
              td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """The business's WhatsApp conversations across all its numbers, newest
        first. Each chat says which number (`conn`) it belongs to."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            _row(cur, tid)                      # 409 when there is no number at all
            conns = _rows(cur, tid)
        chats, problems = [], []
        for r in conns:
            try:
                data = _pairctl_json(r["pairctl_url"], "/chats")
            except HTTPException as e:
                if len(conns) == 1:
                    raise
                problems.append({"number": r["phone_number"], "error": e.detail})
                continue
            for c in data.get("chats", []):
                c["conn"] = str(r["id"])
                c["number"] = r["phone_number"]
                chats.append(c)
        chats.sort(key=lambda c: c.get("last_at") or c.get("started_at") or "", reverse=True)
        return {"connected": any(r["status"] == "connected" for r in conns), "chats": chats,
                "numbers": len(conns), "unreachable": problems}

    @router.get("/chats/{session_id}")
    def chat(session_id: str, conn: str | None = None,
             x_tenant_id: str | None = Header(None),
             x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if not re.fullmatch(r"[\w.:@+\-]{1,200}", session_id or ""):
            raise HTTPException(400, "That is not a conversation id.")
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid, conn)
        data = _pairctl_json(r["pairctl_url"], "/chats/" + urllib.parse.quote(session_id, safe=""))
        msgs = data.get("messages", [])
        # The agent stores its own retry nudges ("[System: Your previous
        # response ...]") as role 'user'. They quote the agent's earlier reply,
        # which is why agent text appeared under "Customer" (A7). They were
        # never sent to or by the customer.
        for m in msgs:
            if m.get("role") == "user" and str(m.get("content") or "").lstrip().lower().startswith(("[system:", "[system note")):
                m["role"] = "system"
        # Replies sent from the dashboard are not in the agent's own history, so
        # they come from the audit log (one row per attempt, sent or not).
        with tenant_session(tid, role) as cur:
            cur.execute("""SELECT at, status, detail, error FROM executions
                            WHERE action = 'whatsapp.send' AND resource_id = %s
                            ORDER BY at LIMIT 200""", (session_id,))
            for x in cur.fetchall():
                d = x["detail"] if isinstance(x["detail"], dict) else {}
                msgs.append({"role": "owner", "content": d.get("text") or "", "at": x["at"].isoformat() if x["at"] else None,
                             "status": "sent" if x["status"] == "ok" else "failed", "error": x["error"]})
        msgs.sort(key=lambda m: m.get("at") or "")
        return {"messages": msgs}

    @router.post("/chats/{session_id}/send")
    def send_reply(session_id: str, body: dict, conn: str | None = None,
                   x_tenant_id: str | None = Header(None),
                   x_role: str | None = Header(None),
                   authorization: str | None = Header(None),
                   td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Send a person's reply into a WhatsApp conversation.

        It goes through this tenant's own pairctl (the pairctl URL comes from
        the tenant's row, never the request) with the internal key, and on to
        the agent's bridge. Whatever the chain answers is what the dashboard
        shows: a refusal is reported with its reason, never as "sent"."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if not re.fullmatch(r"[\w.:@+\-]{1,200}", session_id or ""):
            raise HTTPException(400, "That is not a conversation id.")
        text = str((body or {}).get("text") or "").strip()
        if not text:
            raise HTTPException(400, "Type a message first.")
        if len(text) > 4000:
            raise HTTPException(400, "That message is too long (4000 characters at most).")
        with tenant_session(tid, role) as cur:
            r = _row(cur, tid, conn)
        if r["status"] != "connected":
            raise HTTPException(409, "This WhatsApp number is not connected, so nothing can be sent. "
                                     "Open WhatsApp in the menu and connect it.")
        key = _key()
        if not key:
            raise HTTPException(503, "WhatsApp is not configured on this server.")
        req = urllib.request.Request(
            r["pairctl_url"].rstrip("/") + "/send", method="POST",
            data=json.dumps({"session_id": session_id, "text": text}).encode(),
            headers={"x-internal-key": key, "content-type": "application/json"})
        status, reason, out = None, None, {}
        try:
            with urllib.request.urlopen(req, timeout=40) as resp:
                out = json.loads(resp.read().decode() or "{}")
                status = resp.status
        except urllib.error.HTTPError as e:
            status = e.code
            try:
                reason = (json.loads(e.read().decode() or "{}") or {}).get("error")
            except ValueError:
                reason = None
            if e.code == 404 and not reason:
                reason = "this agent's WhatsApp service has no send route yet (older pairctl)"
            elif e.code == 404:
                reason = {"not found": "this agent's WhatsApp service has no send route yet (older pairctl)"}.get(reason, reason)
        except Exception as e:
            status, reason = 0, "the WhatsApp service for this business is not reachable (%s)" % type(e).__name__
        ok = status == 200 and out.get("sent") is True
        with tenant_session(tid, role) as cur:
            record(cur, action="whatsapp.send", resource="whatsapp_messages", resource_id=session_id,
                   actor=str(uid), actor_type="human", status="ok" if ok else "error",
                   detail={"text": text, "message_id": out.get("message_id"), "at": out.get("at")},
                   error=None if ok else ("HTTP %s: %s" % (status, reason)))
        if not ok:
            if status == 404:
                # the audit row above keeps the technical reason; people get a plain one
                raise HTTPException(502, "Not sent: sending from the dashboard is not switched on for this "
                                         "WhatsApp number yet. Use Reply in WhatsApp instead.")
            raise HTTPException(502, "Not sent. %s" % (reason or ("the WhatsApp service answered HTTP %s" % status)))
        return {"sent": True, "message_id": out.get("message_id"), "at": out.get("at")}

    @router.post("/sync-lessons")
    def sync(x_tenant_id: str | None = Header(None),
             x_role: str | None = Header(None),
             authorization: str | None = Header(None),
             td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """Re-send everything the owner has taught to their WhatsApp agent."""
        tid, role, uid = _identity(x_tenant_id, x_role, authorization, td_session)
        if role not in ("owner", "techdeva_admin"):
            raise HTTPException(403, "Only the business owner can change what the agent knows.")
        s = sync_lessons(tid, role)
        if s == "no_agent":
            raise HTTPException(409, "No WhatsApp agent has been assigned to this business yet.")
        if s == "failed":
            raise HTTPException(503, "Could not reach your WhatsApp agent. Try again in a minute.")
        return {"ok": True, "status": s}

    @router.post("/assign")
    def assign(body: dict,
               x_tenant_id: str | None = Header(None),
               x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """TechDeva assigns a tenant to an agent. Admin only, by design: this is
        the mapping the rest of the module refuses to take from a client."""
        # Not _identity(): that insists on a current tenant, and the founder
        # has none -- so the one person allowed to assign never could.
        try:
            tid, role, uid = resolve_scope(x_tenant_id, x_role, authorization, td_session)
        except AuthError as e:
            raise HTTPException(e.status, e.message)
        if role != "techdeva_admin":
            raise HTTPException(403, "Assigning an agent is a TechDeva action.")
        target = body.get("tenant_id") or tid
        if not target:
            raise HTTPException(400, "tenant_id is required")
        agent_key = (body.get("agent_key") or "").strip()
        pairctl_url = (body.get("pairctl_url") or "").strip()
        if not agent_key or not pairctl_url:
            raise HTTPException(400, "agent_key and pairctl_url are both required")
        with tenant_session(tid, role) as cur:
            cur.execute("""INSERT INTO whatsapp_connections (tenant_id, agent_key, pairctl_url)
                           VALUES (%s, %s, %s)
                           ON CONFLICT (tenant_id, agent_key) DO UPDATE
                             SET agent_key = EXCLUDED.agent_key,
                                 pairctl_url = EXCLUDED.pairctl_url,
                                 updated_at = now()
                           RETURNING id""", (target, agent_key, pairctl_url))
            new_id = cur.fetchone()["id"]
            record(cur, action="whatsapp.assign", resource="whatsapp_connections",
                   resource_id=str(new_id), actor=str(uid), actor_type="human",
                   status="ok", detail={"agent": agent_key}, error=None, tenant_id=target)
        return {"ok": True, "id": str(new_id), "tenant_id": str(target), "agent_key": agent_key}

    return router
