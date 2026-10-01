"""Read-only: for each business with Facebook connected, how many Facebook
Pages (and linked Instagram accounts) the connection holds. Posting needs at
least one Page. Prints names and counts only, never a token.

    docker compose exec -T api python - < ops/verify/meta_pages.py"""
from app.crypto import decrypt_json
from app.db import tenant_session

with tenant_session(None, "techdeva_admin") as cur:
    cur.execute("SELECT i.tenant_id, t.name, i.credentials FROM integrations i "
                "JOIN tenants t ON t.id = i.tenant_id "
                "WHERE i.provider = 'meta' AND i.status = 'connected' ORDER BY t.name")
    rows = cur.fetchall()
if not rows:
    print("No business has Facebook connected.")
for r in rows:
    try:
        creds = decrypt_json(r["credentials"], str(r["tenant_id"]), "meta")
    except Exception as e:  # noqa: BLE001 -- report, never crash the listing
        print("%-30s could not read the connection (%s)" % (r["name"], type(e).__name__))
        continue
    pages = [p for p in (creds.get("pages") or []) if p.get("id") and p.get("access_token")]
    names = ", ".join("%s%s" % (p.get("name") or "?", " + IG" if p.get("ig_id") else "") for p in pages)
    print("%-30s pages=%d  %s" % (r["name"], len(pages), names or "<- NO PAGE: posts can only be drafts"))
