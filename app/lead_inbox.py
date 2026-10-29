"""Leads the WhatsApp agent saves (td_lead's save_lead tool) reach the business's Leads.

The agent's td_lead plugin (ops/agent-template/td_lead) appends each save to
its own $HERMES_HOME/td_lead/inbox.jsonl, with the customer's number taken
from the WhatsApp sender, never from the model. ops/agent-template/lead-sync.sh,
run by cron as root, hands those lines to this CLI inside the api container:

    python -m app.lead_inbox ingest  < JSON lines {"agent_key", "id", "phone", "key",
                                                   "name", "whatsapp_name", "email",
                                                   "interest", "callback_time", "notes", "at"}

A customer with an open lead (not sold, lost or fake) with the same number gets
that lead updated: empty fields filled, what they want replaced, a dated line
added to the notes. Anyone else gets a new lead, source "hermes", tagged
"whatsapp". Every line is recorded in the audit log ("whatsapp.lead"), so a
line handed over twice is saved once. Nothing is emitted: no automatic
outreach starts from these leads. Prints "ok <id>" for every line that is saved
or was already saved.
"""
import datetime
import json
import re
import sys

from .audit import record
from .db import tenant_session

PROTECTED = re.compile(r"^(irfan$|clinic$|salon$|hermes-workspace|workspace|.*ofsd)")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,120}\.[A-Za-z]{2,24}$")
PKT = datetime.timezone(datetime.timedelta(hours=5))


def _s(v, n):
    return " ".join(str(v or "").split())[:n]


def _note(e, when):
    parts = []
    if e["interest"]:
        parts.append("wants " + e["interest"])
    if e["callback_time"]:
        parts.append("call back: " + e["callback_time"])
    if e["email"]:
        parts.append("email " + e["email"])
    if e["notes"]:
        parts.append(e["notes"])
    if not e["phone"] and e["key"]:
        parts.append("WhatsApp id %s (number not known)" % e["key"])
    return "WhatsApp agent, %s: %s" % (when.strftime("%d %b %H:%M"), "; ".join(parts) or "talked on WhatsApp")


def save(cur, tid, e):
    """Add or update this customer's lead. Returns (lead_id, 'new' | 'updated')."""
    when = datetime.datetime.fromtimestamp(int(e["at"] or 0) or datetime.datetime.now().timestamp(), PKT)
    name = e["name"] or e["whatsapp_name"] or None
    phone = ("+" + e["phone"]) if e["phone"] else None
    note = _note(e, when)
    row = None
    if e["phone"]:
        cur.execute("""SELECT id FROM leads
                        WHERE tenant_id = %s AND status NOT IN ('sold', 'lost', 'fake')
                          AND right(regexp_replace(coalesce(phone, ''), '\\D', '', 'g'), 10) = right(%s, 10)
                        ORDER BY created_at DESC LIMIT 1""", (tid, e["phone"]))
        row = cur.fetchone()
    if row:
        cur.execute("""UPDATE leads SET name = coalesce(nullif(name, ''), %s),
                                        email = coalesce(nullif(%s, ''), email),
                                        service = coalesce(nullif(%s, ''), service),
                                        notes = left(coalesce(notes || E'\\n', '') || %s, 4000),
                                        tags = CASE WHEN 'whatsapp' = ANY(tags) THEN tags ELSE tags || ARRAY['whatsapp'] END,
                                        updated_at = now()
                        WHERE id = %s""", (name, e["email"], e["interest"], note, row["id"]))
        return str(row["id"]), "updated"
    cur.execute("""INSERT INTO leads (tenant_id, name, phone, email, service, source, tags, notes, created_by)
                   VALUES (%s, %s, %s, nullif(%s, ''), nullif(%s, ''), 'hermes', ARRAY['whatsapp'], %s, 'whatsapp_agent')
                   RETURNING id""", (tid, name, phone, e["email"], e["interest"], note))
    return str(cur.fetchone()["id"]), "new"


def parse(line):
    e = json.loads(line)
    out = {"agent_key": str(e["agent_key"]), "id": str(e["id"]), "at": int(e.get("at") or 0),
           "key": re.sub(r"\D", "", str(e.get("key") or ""))[:20],
           "phone": re.sub(r"\D", "", str(e.get("phone") or "")),
           "name": _s(e.get("name"), 80), "whatsapp_name": _s(e.get("whatsapp_name"), 60),
           "email": _s(e.get("email"), 120), "interest": _s(e.get("interest"), 160),
           "callback_time": _s(e.get("callback_time"), 80), "notes": _s(e.get("notes"), 400)}
    if not 7 <= len(out["phone"]) <= 15:
        out["phone"] = ""
    if out["email"] and not EMAIL.match(out["email"]):
        out["notes"] = _s(("email as given: %s. " % out["email"]) + out["notes"], 400)
        out["email"] = ""
    if not re.search(r"[A-Za-z؀-ۿ]", out["whatsapp_name"]):
        out["whatsapp_name"] = ""          # names like "." or "~" say nothing
    return out


def ingest(lines):
    done = []
    with tenant_session(None, "techdeva_admin") as cur:
        for line in lines:
            try:
                e = parse(line)
            except (ValueError, KeyError, TypeError):
                continue
            if PROTECTED.match(e["agent_key"]) or not re.fullmatch(r"[0-9a-f]{32}", e["id"]):
                continue
            if not (e["phone"] or e["key"]):
                continue
            cur.execute("SELECT tenant_id FROM whatsapp_connections WHERE agent_key = %s LIMIT 1", (e["agent_key"],))
            w = cur.fetchone()
            if not w:
                continue
            tid = str(w["tenant_id"])
            cur.execute("""SELECT 1 FROM executions WHERE tenant_id = %s AND action = 'whatsapp.lead'
                             AND detail->>'line' = %s LIMIT 1""", (tid, e["id"]))
            if not cur.fetchone():
                lead_id, how = save(cur, tid, e)
                record(cur, action="whatsapp.lead", resource="leads", resource_id=lead_id,
                       actor="whatsapp agent (%s)" % e["agent_key"], actor_type="agent", status="ok",
                       detail={"line": e["id"], "how": how, "agent": e["agent_key"],
                               "fields": [k for k in ("name", "email", "interest", "callback_time", "notes") if e[k]]},
                       error=None, tenant_id=tid)
            done.append(e["id"])
    return done


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        for i in ingest([l for l in sys.stdin if l.strip()]):
            print("ok %s" % i)
        sys.exit(0)
    sys.stderr.write("usage: python -m app.lead_inbox ingest < lines\n")
    sys.exit(2)
