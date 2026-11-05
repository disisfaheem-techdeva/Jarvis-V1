"""A client's own address, <name>.techdeva.net, chosen automatically.

Every new business gets one at signup (Faheem, 2 Oct 2026: no manual step).
The name comes from the business name; if it is reserved or taken a number
is added (cozzy-taste, cozzy-taste-2 ...). The founder can still change it
in the console. ops/setup/sync_subdomains.sh on the server puts new names
live (https certificate and routing) within a few minutes.
"""
import re

RESERVED = {"www", "api", "portal", "crm", "mail", "admin", "app", "dashboard", "login", "pin",
            "techdeva", "founder", "status", "static", "cdn", "ftp", "smtp", "webmail", "cpanel", "pos", "sales",
            "leads", "partner"}
VALID = re.compile(r"^[a-z][a-z0-9-]{1,30}[a-z0-9]$")


def slug(name):
    s = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")
    s = re.sub(r"^[^a-z]+", "", s)[:28].strip("-")
    return s if len(s) >= 3 else (s + "-biz" if s else "client")


def pick(cur, name, tenant_id=None):
    """A free valid address for this business (cur: an admin session)."""
    base = slug(name)
    cur.execute("SELECT subdomain FROM tenants WHERE subdomain LIKE %s AND id::text <> %s",
                (base + "%", str(tenant_id or "")))
    taken = {r["subdomain"] for r in cur.fetchall()}
    cand, n = base, 1
    while cand in RESERVED or cand in taken or not VALID.match(cand):
        n += 1
        cand = "%s-%d" % (base[:28], n)
        if n > 500:
            return None
    return cand


def assign(cur, tenant_id, name):
    """Give the business an address if it has none. Returns the address."""
    cur.execute("SELECT subdomain FROM tenants WHERE id = %s", (tenant_id,))
    r = cur.fetchone()
    if r and r["subdomain"]:
        return r["subdomain"]
    sub = pick(cur, name, tenant_id)
    if sub:
        cur.execute("UPDATE tenants SET subdomain = %s WHERE id = %s AND subdomain IS NULL", (sub, tenant_id))
    return sub
