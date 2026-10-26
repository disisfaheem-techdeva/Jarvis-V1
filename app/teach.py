"""Lessons the owner sends from WhatsApp (/sikho, then /haan) reach the platform.

The agent's td_teach plugin (ops/agent-template/td_teach) appends each
confirmed lesson to its own $HERMES_HOME/td_teach/inbox.jsonl; only the owner
can run it (the gateway's admin list). ops/agent-template/teach-sync.sh, run by
cron as root, hands those lines to this CLI inside the api container:

    python -m app.teach ingest  < JSON lines {"agent_key", "id", "text", "at"}

Each becomes an agent_lessons row like a lesson typed on the dashboard
(said_as "WhatsApp /sikho [<id>]", so a line handed over twice is saved once),
and the business's agents get their lessons re-written (whatsapp.sync_lessons).
Prints "ok <id>" for every line that is saved or was already saved.
"""
import json
import re
import sys

from .audit import record
from .db import tenant_session

PROTECTED = re.compile(r"^(irfan$|clinic$|salon$|hermes-workspace|workspace|.*ofsd)")


def ingest(lines):
    done, tenants = [], set()
    with tenant_session(None, "techdeva_admin") as cur:
        for line in lines:
            try:
                e = json.loads(line)
                agent, lid, text = str(e["agent_key"]), str(e["id"]), " ".join(str(e["text"]).split())
            except (ValueError, KeyError, TypeError):
                continue
            if PROTECTED.match(agent) or not re.fullmatch(r"[0-9a-f]{32}", lid) or not 3 <= len(text) <= 600:
                continue
            cur.execute("SELECT tenant_id FROM whatsapp_connections WHERE agent_key = %s LIMIT 1", (agent,))
            w = cur.fetchone()
            if not w:
                continue
            tid, said = str(w["tenant_id"]), "WhatsApp /sikho [%s]" % lid
            cur.execute("SELECT 1 FROM agent_lessons WHERE tenant_id = %s AND said_as = %s", (tid, said))
            if not cur.fetchone():
                cur.execute("SELECT id FROM users WHERE tenant_id = %s AND role = 'owner' ORDER BY created_at LIMIT 1", (tid,))
                owner = cur.fetchone()
                cur.execute("""INSERT INTO agent_lessons (tenant_id, agent, lesson, said_as, created_by)
                               VALUES (%s, 'whatsapp', %s, %s, %s) RETURNING id""",
                            (tid, text, said, owner["id"] if owner else None))
                row = cur.fetchone()
                record(cur, action="whatsapp.teach", resource="agent_lessons", resource_id=str(row["id"]),
                       actor="owner (WhatsApp)", actor_type="human", status="ok",
                       detail={"lesson": text[:200], "agent": agent}, error=None, tenant_id=tid)
                tenants.add(tid)
            done.append(lid)
    from .whatsapp import sync_lessons
    for tid in tenants:
        print("sync %s %s" % (tid[:8], sync_lessons(tid, "owner")), file=sys.stderr)
    return done


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "ingest":
        for i in ingest([l for l in sys.stdin if l.strip()]):
            print("ok %s" % i)
        sys.exit(0)
    sys.stderr.write("usage: python -m app.teach ingest < lines\n")
    sys.exit(2)
