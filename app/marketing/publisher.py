"""Publishing approved gallery posts at their time. Driven by the existing
worker: the scheduler tick emits marketing.posts_due, a rule queues one
publishing task per post, and the publishing agent calls publish_one().

Exactly once, by construction:
  * the claim is one UPDATE ... WHERE status='approved' AND scheduled_at <= now()
    on a row locked FOR UPDATE SKIP LOCKED, so two workers can never both win it;
  * a post that already has an external_post_id is never sent again;
  * a post without approved_by is never claimed (and the table refuses an
    approved post without one), so nothing reaches Meta that a person did not approve;
  * Meta is called outside any transaction; if the answer is lost (worker
    restart), the post is marked failed after 15 minutes and NOT re-sent,
    because it may already be live.
"""
from __future__ import annotations


from ..audit import record
from ..db import tenant_session
from .. import meta_publish_client as meta
from . import guardrails

MAX_ATTEMPTS = 3
BACKOFF_MIN = (2, 10)                    # minutes before the 2nd and 3rd attempts
STUCK_MINUTES = 15
STUCK_MSG = ("TechDeva did not get Meta's answer for this post, so it may or may not have been posted. "
             "Check the Page; if it is not there, move it to a new time and approve it again.")


def due_by_tenant(limit=200) -> dict[str, list[str]]:
    """{tenant_id: [post ids]} of approved posts whose time has come, in active campaigns."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""SELECT sp.tenant_id::text AS t, sp.id::text AS id FROM scheduled_posts sp
                         JOIN marketing_campaigns c ON c.id = sp.campaign_id AND c.status = 'active'
                        WHERE sp.status = 'approved' AND sp.scheduled_at <= now()
                          AND (sp.retry_after IS NULL OR sp.retry_after <= now())
                        ORDER BY sp.scheduled_at LIMIT %s""", (limit,))
        out: dict[str, list[str]] = {}
        for r in cur.fetchall():
            out.setdefault(r["t"], []).append(r["id"])
        return out


def sweep_stuck() -> int:
    """Posts left 'publishing' with no saved answer become failed, honestly, never re-sent."""
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("""UPDATE scheduled_posts SET status = 'failed', last_error = %s, updated_at = now()
                        WHERE status = 'publishing' AND updated_at < now() - make_interval(mins => %s)
                    RETURNING id::text, tenant_id::text""", (STUCK_MSG, STUCK_MINUTES))
        rows = cur.fetchall()
        for r in rows:
            record(cur, action="marketing.publish", resource="scheduled_posts", resource_id=r["id"], actor="scheduler",
                   actor_type="system", status="error", detail={"stuck": True}, error="no answer from Meta was saved",
                   tenant_id=r["tenant_id"])
        return len(rows)


def tick(emit) -> int:
    """Scheduler hook: one marketing.posts_due event per business with due posts."""
    n = sweep_stuck()
    for tid, ids in due_by_tenant().items():
        emit(tid, "marketing.posts_due", {"post_ids": ids}, "scheduler")
        n += 1
    return n


def _claim(tid, post_id):
    with tenant_session(tid, "owner") as cur:
        cur.execute("""UPDATE scheduled_posts sp SET status = 'publishing', attempts = attempts + 1, updated_at = now()
                        WHERE sp.id = (SELECT p.id FROM scheduled_posts p
                                         JOIN marketing_campaigns c ON c.id = p.campaign_id AND c.status = 'active'
                                        WHERE p.id::text = %s AND p.status = 'approved' AND p.scheduled_at <= now()
                                          AND p.approved_by IS NOT NULL AND p.external_post_id IS NULL
                                          AND (p.retry_after IS NULL OR p.retry_after <= now())
                                        FOR UPDATE OF p SKIP LOCKED)
                    RETURNING sp.*""", (str(post_id),))
        post = cur.fetchone()
        if not post:
            return None, None, None
        cur.execute("SELECT public_url, kind FROM media_assets WHERE id = %s", (post["media_asset_id"],))
        media = cur.fetchone()
        cur.execute("SELECT page_id, name FROM marketing_campaigns WHERE id = %s", (post["campaign_id"],))
        camp = cur.fetchone()
        page = None
        if post["platform"] in ("facebook", "instagram") and meta.mode() == "live":
            from ..social import _meta_pages
            pages = _meta_pages(cur, tid)
            page = next((p for p in pages if str(p["id"]) == str(camp["page_id"])), None) if camp["page_id"] \
                else (pages[0] if len(pages) == 1 else None)
        return dict(post), {"url": media["public_url"], "kind": media["kind"]}, {"page": page, "name": camp["name"]}


def page_for_campaign(tid, cid):
    """The Page a campaign publishes to (live mode), for re-checking the connection on resume."""
    if meta.mode() != "live":
        return None
    with tenant_session(tid, "owner") as cur:
        from ..social import _meta_pages
        pages = _meta_pages(cur, tid)
        cur.execute("SELECT page_id FROM marketing_campaigns WHERE id::text = %s", (str(cid),))
        row = cur.fetchone()
    pid = row and row["page_id"]
    return next((p for p in pages if str(p["id"]) == str(pid)), None) if pid else (pages[0] if len(pages) == 1 else None)


def publish_one(tid, post_id) -> dict:
    """Claim, publish, record. Returns {status, events} for the publishing agent."""
    post, media, camp = _claim(tid, post_id)
    if not post:
        return {"status": "skipped", "reason": "not due, not approved, already taken or already published", "events": []}
    if post["platform"] == "instagram" and meta.mode() == "live":
        from ..social import ig_over_limit
        wait = ig_over_limit(tid)
        if wait:
            with tenant_session(tid, "owner") as cur:
                cur.execute("""UPDATE scheduled_posts SET status = 'approved', attempts = GREATEST(attempts - 1, 0),
                                      last_error = %s, retry_after = now() + interval '3 hours', updated_at = now()
                                WHERE id = %s""", (wait, post["id"]))
            return {"status": "waiting", "error": wait, "events": []}
    if post["platform"] in ("facebook", "instagram") and meta.mode() == "live" and not camp["page"]:
        outcome = meta.Outcome(False, kind="auth", message="No connected Facebook Page for this campaign.")
    else:
        outcome = meta.publish(post["platform"], camp["page"], media, guardrails.full_text(post["caption"], post["hashtags"] or []))
    events = []
    with tenant_session(tid, "owner") as cur:
        if outcome.ok:
            cur.execute("""UPDATE scheduled_posts SET status = 'published', external_post_id = %s, published_at = now(),
                                  last_error = NULL, retry_after = NULL, updated_at = now() WHERE id = %s""",
                        (outcome.external_id, post["id"]))
            events.append({"type": "marketing.post_published", "payload": {
                "post_id": str(post["id"]), "campaign_id": str(post["campaign_id"]), "platform": post["platform"],
                "external_post_id": outcome.external_id, "service_tag": post["service_tag"],
                "test_mode": outcome.external_id.startswith("stub:")}})
            status = "published"
        elif outcome.kind == "transient" and post["attempts"] < MAX_ATTEMPTS:
            wait = BACKOFF_MIN[min(post["attempts"], len(BACKOFF_MIN)) - 1]
            cur.execute("""UPDATE scheduled_posts SET status = 'approved', last_error = %s,
                                  retry_after = now() + make_interval(mins => %s), updated_at = now() WHERE id = %s""",
                        (outcome.message, wait, post["id"]))
            status = "retrying"
        else:
            cur.execute("UPDATE scheduled_posts SET status = 'failed', last_error = %s, updated_at = now() WHERE id = %s",
                        (outcome.message, post["id"]))
            status = "failed"
            if outcome.kind == "auth":
                # one lost connection must not fail the next ten posts: stop the campaign, ask for a reconnect
                cur.execute("""UPDATE marketing_campaigns SET status = 'paused', paused_reason = 'meta_auth', updated_at = now()
                                WHERE id = %s AND status = 'active'""", (post["campaign_id"],))
                events.append({"type": "ops.alert", "payload": {
                    "provider": "meta", "message": "Autopilot campaign %r paused: %s" % (camp["name"], outcome.message)}})
                events.append({"type": "marketing.reconnect_needed", "payload": {
                    "campaign_id": str(post["campaign_id"]), "campaign": camp["name"], "message": outcome.message}})
        if status in ("published", "failed"):
            cur.execute("""UPDATE marketing_campaigns c SET status = 'completed', updated_at = now()
                            WHERE c.id = %s AND c.status = 'active'
                              AND NOT EXISTS (SELECT 1 FROM scheduled_posts WHERE campaign_id = c.id
                                               AND status IN ('draft', 'approved', 'publishing'))""", (post["campaign_id"],))
        record(cur, action="marketing.publish", resource="scheduled_posts", resource_id=str(post["id"]),
               actor="publishing", actor_type="agent", status="ok" if outcome.ok else "error",
               detail={"platform": post["platform"], "attempt": post["attempts"], "kind": outcome.kind,
                       "external_post_id": outcome.external_id, "result": status},
               error=None if outcome.ok else outcome.message[:300])
    return {"status": status, "external_post_id": outcome.external_id, "error": outcome.message if not outcome.ok else None,
            "events": events}
