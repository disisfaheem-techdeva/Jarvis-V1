"""The support agent: audits the platform and every live business, fixes what is safe, explains the rest.

Every 15 minutes (scheduler.tick -> run_due) it works in this order:

  1. Audit (app/support_checks.py): nothing is changed; each problem becomes a finding.
  2. Fix on its own only what cannot reach anyone: an alert nobody has re-raised for
     days is closed. Each fix is written in support_log.
  3. Everything else stays open in support_findings until it goes away by itself or a
     person presses its one fix ("Run them again"), or does what the finding says.

It never messages a customer, never publishes, never touches money, prices or logins,
and never changes code. Owners see their own business's findings (audience 'owner');
crashes and container problems are TechDeva's (audience 'techdeva', founder only).

ask() answers questions from today's real figures and changes nothing.
"""
from __future__ import annotations

import json
import re
import time

from psycopg.types.json import Json

from . import support_checks as checks
from .db import tenant_session

EVERY_S = 900
KEEP_LOG = 50
AGENT = "Support agent"
_last = 0.0


# ------------------------------------------------------------------ helpers the tests replace
def _pairctl_up(url):
    """True when the agent's pairctl answers. Its /pair/status is the PAIRING state
    ('idle' = no pairing running), not whether WhatsApp is linked, so only
    'answers or not' is used; the bridge itself is checked by the host monitor."""
    from .whatsapp import _pairctl
    try:
        _pairctl(url, "/health", timeout=8)
        return True
    except Exception:
        return False


def _ai_health():
    try:
        from .ai import health
        return health()
    except Exception as e:  # noqa: BLE001 - an AI layer that cannot even report is itself the finding
        return {"status": "unreachable", "usable": 0, "chain": [{"state": "resting", "model": "-", "reason": str(e)[:120]}]}


def _push_tasks(rows):
    from .persistence import TASKS_Q, Persistence
    r = Persistence().r
    for tid, task_id in rows:
        r.rpush(TASKS_Q, json.dumps({"tenant_id": str(tid), "task_id": str(task_id)}))


# ------------------------------------------------------------------ storing findings
def _log(cur, tid, what, by, key=None, audience="owner"):
    cur.execute("INSERT INTO support_log (tenant_id, audience, what, by_whom, finding_key) VALUES (%s, %s, %s, %s, %s)",
                (tid, audience, what, by, key))


def _store(cur, tid, found):
    """Upsert this pass's open findings for one scope; findings not seen again are 'gone'."""
    keys = [f["key"] for f in found]
    for f in found:
        cur.execute("""UPDATE support_findings SET severity = %s, audience = %s, title = %s, detail = %s, fix = %s,
                              fix_label = %s, args = %s, last_seen_at = now()
                        WHERE status = 'open' AND tenant_id IS NOT DISTINCT FROM %s AND key = %s""",
                    (f["severity"], f["audience"], f["title"], f["detail"], f["fix"], f["fix_label"], Json(f["args"]),
                     tid, f["key"]))
        if not cur.rowcount:
            cur.execute("""INSERT INTO support_findings (tenant_id, key, audience, severity, title, detail, fix, fix_label, args)
                           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                        (tid, f["key"], f["audience"], f["severity"], f["title"], f["detail"], f["fix"], f["fix_label"],
                         Json(f["args"])))
    cur.execute("""UPDATE support_findings SET status = 'gone', closed_at = now(), closed_by = %s
                    WHERE status = 'open' AND tenant_id IS NOT DISTINCT FROM %s AND NOT (key = ANY(%s))""",
                (AGENT, tid, keys))


# ------------------------------------------------------------------ fixes
def _apply(cur, tid, f, by):
    """Do one fix. Returns the sentence for the log, and a list of tasks to push after commit."""
    args, fix = f.get("args") or {}, f.get("fix")
    if fix == "close_incidents":
        cur.execute("""UPDATE incidents SET status = 'resolved', resolved_at = now()
                        WHERE status = 'open' AND id = ANY(%s::uuid[])""", (args.get("ids") or [],))
        return "Closed %s old alert(s) that had not come back" % cur.rowcount, []
    if fix == "retry_tasks":
        cur.execute("""UPDATE tasks SET status = 'queued', attempts = 0, run_after = now(), updated_at = now(),
                              result = COALESCE(result, '{}'::jsonb) || %s::jsonb
                        WHERE tenant_id = %s AND id = ANY(%s::uuid[]) AND status IN ('failed', 'dead')
                    RETURNING id""",
                    (Json({"retried_by": by}), tid, args.get("task_ids") or []))
        ids = [r["id"] for r in cur.fetchall()]
        return "Sent %s failed AI task(s) back to the queue" % len(ids), [(tid, i) for i in ids]
    raise ValueError("unknown fix %r" % fix)


def _audit_scope(tid):
    if tid is None:
        with tenant_session(None, "techdeva_admin") as cur:
            return checks.platform(cur, _ai_health())
    with tenant_session(None, "techdeva_admin") as cur:
        return checks.business(cur, tid, _pairctl_up)


def run_scope(tid):
    """Audit one scope (a business, or None = the platform), store it, and do its safe fixes."""
    found = _audit_scope(tid)
    push = []
    with tenant_session(None, "techdeva_admin") as cur:
        _store(cur, tid, [f for f in found if not f["auto"]])
        for f in [f for f in found if f["auto"]]:
            what, more = _apply(cur, tid, f, AGENT)
            _log(cur, tid, what, AGENT, f["key"], f["audience"])
            push += more
    if push:
        _push_tasks(push)
    return {"found": len(found), "fixed": sum(1 for f in found if f["auto"])}


def run_all():
    """The platform, then every business on trial or active. One failing scope never stops the rest."""
    total = {"found": 0, "fixed": 0, "failed": 0}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT id FROM tenants WHERE status IN ('trial', 'active') AND NOT is_test ORDER BY created_at")
        tenants = [str(r["id"]) for r in cur.fetchall()]
    for tid in [None] + tenants:
        try:
            out = run_scope(tid)
            total["found"] += out["found"]
            total["fixed"] += out["fixed"]
        except Exception as e:  # noqa: BLE001
            total["failed"] += 1
            print("[support] audit of %s failed: %s" % (tid or "platform", str(e)[:200]), flush=True)
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""INSERT INTO platform_settings (key, value, updated_at) VALUES ('support_last_run', %s, now())
                       ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()""",
                    (json.dumps(total),))
    return total


def run_due():
    """Called from scheduler.tick; audits every EVERY_S seconds. Returns 0 (it emits no events)."""
    global _last
    if time.time() - _last < EVERY_S:
        return 0
    _last = time.time()
    out = run_all()
    if out["fixed"] or out["failed"]:
        print("[support] %s" % out, flush=True)
    return 0


def fix(tid, key, by, founder=False):
    """A person pressed a finding's fix. Owners reach only their own 'owner' findings."""
    push = []
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT key, audience, fix, args, title FROM support_findings
                        WHERE status = 'open' AND tenant_id IS NOT DISTINCT FROM %s AND key = %s""", (tid, key))
        f = cur.fetchone()
        if not f or (f["audience"] != "owner" and not founder):
            raise KeyError("This problem is no longer open.")
        if not f["fix"]:
            raise ValueError("This one needs a person to do it; the finding says what.")
        what, push = _apply(cur, tid, dict(f), by)
        cur.execute("""UPDATE support_findings SET status = 'fixed', closed_at = now(), closed_by = %s
                        WHERE status = 'open' AND tenant_id IS NOT DISTINCT FROM %s AND key = %s""", (by, tid, key))
        _log(cur, tid, what, by, key, f["audience"])
    if push:
        _push_tasks(push)
    return what


# ------------------------------------------------------------------ reading
def overview(tid, founder=False):
    """Open findings and recent log for one scope. A business owner never sees internal rows (RLS + filter)."""
    role = "techdeva_admin" if founder else "owner"
    scope = None if founder else tid
    with tenant_session(scope, role) as cur:
        aud = ("owner", "techdeva") if founder else ("owner",)
        cur.execute("""SELECT key, severity, audience, title, detail, fix, fix_label, first_seen_at, last_seen_at
                         FROM support_findings WHERE status = 'open' AND tenant_id IS NOT DISTINCT FROM %s
                          AND audience = ANY(%s)
                        ORDER BY CASE severity WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, first_seen_at""",
                    (tid, list(aud)))
        findings = [dict(r) for r in cur.fetchall()]
        cur.execute("""SELECT at, what, by_whom FROM support_log WHERE tenant_id IS NOT DISTINCT FROM %s
                          AND audience = ANY(%s) ORDER BY at DESC LIMIT %s""", (tid, list(aud), KEEP_LOG))
        log = [dict(r) for r in cur.fetchall()]
    return {"findings": findings, "log": log}


def founder_summary():
    """Every business with open findings, worst first, plus the platform's own."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT t.id, t.name, count(*) AS open,
                              count(*) FILTER (WHERE f.severity = 'high') AS high
                         FROM support_findings f JOIN tenants t ON t.id = f.tenant_id
                        WHERE f.status = 'open' GROUP BY t.id, t.name ORDER BY 4 DESC, 3 DESC""")
        businesses = [dict(r, id=str(r["id"])) for r in cur.fetchall()]
        cur.execute("SELECT value, updated_at FROM platform_settings WHERE key = 'support_last_run'")
        last = cur.fetchone()
    out = overview(None, founder=True)
    out["businesses"] = businesses
    out["last_run"] = {"at": last["updated_at"], **json.loads(last["value"])} if last else None
    return out


# ------------------------------------------------------------------ the chat: reads and explains, changes nothing
HOW_IT_WORKS = """How TechDeva works, for answering questions:
- Each business has a dashboard: Dashboard, Jarvis & AI, Chats, CRM, Marketing, Business, Team & HR, Finance, Operations, Administration.
- Its WhatsApp agent answers customers. If WhatsApp is logged out, the owner scans the QR code in Chats, WhatsApp.
- Facebook, Instagram and Google are connected in Administration, Integrations. Posts are made in Marketing, Publisher.
- AI work (content plans, ads, designs) waits for the owner's approval in Jarvis & AI.
- The support agent checks everything every 15 minutes. It fixes by itself only what reaches nobody; every other problem
  is listed with what to press. It never messages customers, publishes, or changes prices, money, logins or code."""


def _facts(tid, founder):
    data = founder_summary() if founder and tid is None else overview(tid, founder=founder)
    lines = ["Open problems: " + ("; ".join("[%s] %s: %s" % (f["severity"], f["title"], f["detail"])
                                           for f in data["findings"][:20]) or "none")]
    lines.append("Recently fixed: " + ("; ".join("%s (%s)" % (l["what"], l["by_whom"]) for l in data["log"][:8]) or "nothing"))
    if data.get("businesses"):
        lines.append("Businesses with problems: " + ", ".join("%s (%s open, %s high)" % (b["name"], b["open"], b["high"])
                                                              for b in data["businesses"][:20]))
    if tid:
        with tenant_session(None, "techdeva_admin") as cur:
            cur.execute("SELECT name, status::text AS status, plan::text AS plan, trial_ends_at FROM tenants WHERE id = %s", (tid,))
            t = cur.fetchone()
            cur.execute("SELECT status::text AS s, count(*) AS n FROM tasks WHERE tenant_id = %s GROUP BY 1", (tid,))
            work = {r["s"]: r["n"] for r in cur.fetchall()}
        if t:
            lines.insert(0, "Business: %s, status %s, plan %s, trial ends %s. AI tasks by state: %s" %
                         (t["name"], t["status"], t["plan"], t["trial_ends_at"] or "-", work))
    return data, "\n".join(lines)


def ask(tid, question, history=None, founder=False):
    """Answers from the real findings. Never changes anything; fixes are pressed separately."""
    from . import ai
    data, known = _facts(tid, founder)
    who = "TechDeva's founder team" if founder else "the owner of this business"
    system = ("You are Support, the helper inside TechDeva for %s. Answer in the language of the question: Roman Urdu "
              "if they write Roman Urdu. Use short, simple words and at most six lines. Say what to press and where. "
              "Answer only from the facts below; if they do not say, tell them so. Never make up a number. You cannot "
              "change anything yourself: a problem with a fix button is fixed from the Support list.\n\n%s\n\nFacts now:\n%s"
              % (who, HOW_IT_WORKS, known))
    talk = "".join("%s: %s\n" % ("Support" if h.get("role") == "assistant" else "User", str(h.get("text") or "")[:500])
                   for h in (history or [])[-6:])
    try:
        from .usage import meter
        with meter(tid, "support", "support_chat"):
            reply = ai.generate(system, talk + "User: " + str(question)[:1000])
    except Exception as e:  # noqa: BLE001 - no AI is not an error for the person asking
        print("[support] chat without AI: %s" % str(e)[:160], flush=True)
        reply = None
    if reply and reply.strip():
        return re.sub(r"<think>.*?</think>", "", reply, flags=re.S).replace("**", "").strip()[:1500]
    rows = data["findings"]
    if not rows:
        return "AI abhi jawab nahi de rahi, lekin aakhri check mein koi masla nahi mila."
    return "AI abhi jawab nahi de rahi. Abhi ke masle:\n" + "\n".join("- " + f["title"] for f in rows[:6])
