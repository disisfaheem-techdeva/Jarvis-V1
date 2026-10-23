"""Alerts: things in this business that need a person (23 Oct 2026).

The Alerts page used to list only incidents (problems the platform monitor
opened), so it said "everything is fine" while a connection had expired,
stock had run low or an invoice was overdue. This adds those, read live from
the business's own records. Nothing is stored and nothing new is revealed:
each alert links to the page that already shows the same rows.
"""
from fastapi import APIRouter, Cookie, Header

from .auth import SESSION_COOKIE
from .db import tenant_session
from .operations import _identity

PROVIDER_NAMES = {"meta": "Facebook & Instagram", "facebook": "Facebook", "instagram": "Instagram",
                  "google": "Google", "whatsapp": "WhatsApp", "tiktok": "TikTok"}


def _plural(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


def business_checks(cur):
    """[{kind, severity, title, detail, link, count}] for the tenant the cursor is scoped to."""
    out = []

    cur.execute("SELECT provider, status, last_error FROM integrations WHERE status IN ('error','expired') ORDER BY provider")
    for r in cur.fetchall():
        name = PROVIDER_NAMES.get(r["provider"], r["provider"])
        out.append({"kind": "integration." + r["provider"], "severity": "critical",
                    "title": name + (" connection expired" if r["status"] == "expired" else " connection has a problem"),
                    "detail": "Reconnect it so your assistant can keep working." + (" Reason: " + r["last_error"] if r["last_error"] else ""),
                    "link": "integrations", "count": 1})

    cur.execute("SELECT count(*) AS n FROM tasks WHERE status = 'needs_human'")
    n = cur.fetchone()["n"]
    cur.execute("SELECT count(*) AS n FROM deliverables WHERE status = 'awaiting_approval'")
    n += cur.fetchone()["n"]
    if n:
        out.append({"kind": "approvals", "severity": "warn", "title": _plural(n, "item waits", "items wait") + " for your approval",
                    "detail": "Your AI team stopped and is waiting for a person to decide.", "link": "needshuman", "count": n})

    cur.execute("SELECT count(*) AS n FROM social_threads WHERE needs_owner IS NOT NULL AND needs_owner <> '' AND NOT ai_paused")
    n = cur.fetchone()["n"]
    if n:
        out.append({"kind": "social.needs_owner", "severity": "warn", "title": _plural(n, "social conversation needs", "social conversations need") + " you",
                    "detail": "The assistant left these Facebook or Instagram messages for you to answer.", "link": "socialinbox", "count": n})

    cur.execute("""SELECT p.name FROM products p JOIN stock_movements m ON m.product_id = p.id
                    WHERE p.active AND p.low_stock_threshold IS NOT NULL
                    GROUP BY p.id, p.name, p.low_stock_threshold
                   HAVING SUM(m.quantity_delta) <= p.low_stock_threshold ORDER BY p.name""")
    low = [r["name"] for r in cur.fetchall()]
    if low:
        out.append({"kind": "stock.low", "severity": "warn", "title": _plural(len(low), "product is", "products are") + " low on stock",
                    "detail": ", ".join(low[:5]) + (" and %d more" % (len(low) - 5) if len(low) > 5 else ""), "link": "inventory", "count": len(low)})

    cur.execute("""SELECT count(*) AS n, COALESCE(SUM(total), 0) AS total FROM invoices
                    WHERE status IN ('issued','partial') AND due_on IS NOT NULL AND due_on < CURRENT_DATE""")
    r = cur.fetchone()
    if r["n"]:
        out.append({"kind": "invoices.overdue", "severity": "warn", "title": _plural(r["n"], "invoice is", "invoices are") + " past the due date",
                    "detail": "Unpaid after the date you set. Follow up with the customer or record the payment.", "link": "invoices", "count": r["n"]})

    cur.execute("SELECT count(*) AS n FROM ad_drafts WHERE status = 'failed' AND created_at > now() - interval '7 days'")
    n = cur.fetchone()["n"]
    if n:
        out.append({"kind": "ads.failed", "severity": "warn", "title": _plural(n, "ad campaign", "ad campaigns") + " did not launch",
                    "detail": "Open Campaigns to see what Facebook or Google said, fix it and launch again.", "link": "marketing", "count": n})
    return out


def make_alerts_router():
    router = APIRouter()

    @router.get("/alerts/checks")
    def checks(x_tenant_id: str | None = Header(None), x_role: str | None = Header(None),
               authorization: str | None = Header(None),
               td_session: str | None = Cookie(None, alias=SESSION_COOKIE)):
        """What needs a person in this business right now (shown with the incidents on Alerts)."""
        tid, role, _uid = _identity(x_tenant_id, x_role, authorization, td_session)
        with tenant_session(tid, role) as cur:
            items = business_checks(cur)
        return {"checks": items, "count": len(items)}

    return router
