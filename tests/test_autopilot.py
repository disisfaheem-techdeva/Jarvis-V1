"""Content Autopilot: library -> month plan (drafts) -> approve a week -> results.

The AI and Meta are replaced by fakes; everything else (RLS, the Posts tables,
the fact checker, the scheduler's table) is the real thing. Needs the database
with db/64_content_autopilot.sql applied, like the other API tests.
"""
import datetime as dt
import json
import re
import secrets
import uuid

import pytest
from psycopg.types.json import Json
from starlette.testclient import TestClient

from app import ai, autopilot, crypto
from app.db import tenant_session
from app.main import app

CLINIC = "11111111-1111-1111-1111-111111111111"
OWNER = {"X-Tenant-Id": CLINIC, "X-Role": "owner"}
TZ = dt.timezone(dt.timedelta(hours=5))
JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def _photo(label=""):
    token = secrets.token_hex(20)
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("INSERT INTO social_media (tenant_id, token, mime, bytes, byte_size) "
                    "VALUES (current_tenant(), %s, 'image/jpeg', %s, %s) RETURNING id::text", (token, JPEG, len(JPEG)))
        return cur.fetchone()["id"], token


def _connect_meta():
    creds = {"access_token": "user-tok", "pages": [{"id": "pg1", "name": "Clinic Page", "access_token": "page-tok",
                                                     "ig_id": "ig1", "ig_username": "clinic"}]}
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("""INSERT INTO integrations (tenant_id, provider, status, credentials, connected_at)
                       VALUES (current_tenant(), 'meta', 'connected', %s, now())
                       ON CONFLICT (tenant_id, provider) DO UPDATE SET status = 'connected',
                         credentials = EXCLUDED.credentials""",
                    (Json(crypto.encrypt_json(creds, CLINIC, "meta")),))


def _disconnect_meta():
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("DELETE FROM integrations WHERE provider = 'meta'")


def _fake_ai(monkeypatch, invent_price=False):
    """A stand-in model: two posts per requested week, on the dates it was given."""
    calls = []

    def generate_json(system, prompt, required_keys=(), _retry=True):
        days = re.search(r"across these dates: ([\d\-, ]+)\.", prompt).group(1).split(", ")
        calls.append(prompt)
        posts = []
        for i, d in enumerate(days[:2]):
            cap = "Glow week %d at the clinic! Book your visit on WhatsApp." % (len(calls) * 10 + i)
            if invent_price and len(calls) == 1 and i == 0:
                cap = "Facials now only Rs 777! Book today."
            posts.append({"date": d, "time": "20:30" if i else "13:00", "theme": "offer" if i else "how-to",
                          "caption": cap, "hashtags": ["Lahore", "#glow"], "media": [0] if i == 0 else [],
                          "platforms": ["facebook", "instagram"]})
        return {"posts": posts}

    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "generate_json", generate_json)
    return calls


def test_library_name_tag_and_list(client):
    pid, token = _photo()
    res = client.patch(f"/autopilot/media/photo/{pid}", headers=OWNER,
                       json={"in_library": True, "label": "Hydrafacial room", "tags": "facial, Room ,facial"})
    assert res.status_code == 200, res.text
    items = client.get("/autopilot/media", headers=OWNER).json()["items"]
    me = next(i for i in items if i["id"] == pid)
    assert me["label"] == "Hydrafacial room" and me["tags"] == ["facial", "room"] and me["kind"] == "photo"
    assert me["url"].endswith(token + ".jpg") and "token" not in me
    # another business's photo cannot be touched
    other = client.patch(f"/autopilot/media/photo/{pid}",
                         headers={"X-Tenant-Id": "22222222-2222-2222-2222-222222222222", "X-Role": "owner"},
                         json={"label": "x"})
    assert other.status_code == 404


def test_plan_writes_drafts_from_the_library_and_flags_invented_prices(client, monkeypatch):
    pid, token = _photo()
    client.patch(f"/autopilot/media/photo/{pid}", headers=OWNER, json={"in_library": True, "label": "Before after"})
    calls = _fake_ai(monkeypatch, invent_price=True)
    start = dt.datetime.now(TZ).date() + dt.timedelta(days=2)
    res = client.post("/autopilot/plan", headers=OWNER, json={
        "starts_on": start.isoformat(), "days": 14, "posts_per_week": 2, "goal": "More facial bookings",
        "offers": "", "language": "mix"})
    assert res.status_code == 200, res.text
    plan_id = res.json()["plan_id"]
    plan = next(p for p in client.get("/autopilot/plans", headers=OWNER).json()["plans"] if p["id"] == plan_id)
    assert plan["status"] == "ready" and plan["drafted"] == 4, plan       # two weeks x two posts
    assert len(calls) == 2
    # the model was given the business's own facts and the library
    assert "More facial bookings" in calls[0] and "Before after" in calls[0] and "VERIFIED CONFIG" in calls[0]
    assert "Roman Urdu" in calls[0]

    cal = client.get(f"/autopilot/calendar?start={start.isoformat()}&days=14", headers=OWNER).json()["posts"]
    mine = [p for p in cal if p["plan_id"] == plan_id]
    assert len(mine) == 4 and all(p["status"] == "draft" and p["planned_at"] for p in mine)
    first = min(mine, key=lambda p: p["planned_at"])
    assert first["planned_at"].startswith(start.isoformat()) and "T13:00:00+05:00" in first["planned_at"]
    assert all(p["image_urls"] for p in mine), "every post got a picture from the library"
    assert all("#Lahore" in p["caption"] and "#glow" in p["caption"] for p in mine)
    flagged = [p for p in mine if p["check"]]
    assert len(flagged) == 1 and "Rs 777" in flagged[0]["caption"]
    assert any("price not in config" in c for c in flagged[0]["check"])


def test_approve_week_schedules_drafts_but_holds_invented_facts(client, monkeypatch):
    _fake_ai(monkeypatch, invent_price=True)
    pid, _ = _photo()
    client.patch(f"/autopilot/media/photo/{pid}", headers=OWNER, json={"in_library": True})
    start = dt.datetime.now(TZ).date() + dt.timedelta(days=20)
    with tenant_session(CLINIC, "owner") as cur:     # an earlier run's drafts for this week
        cur.execute("UPDATE social_posts SET status = 'discarded' WHERE status IN ('draft','scheduled') "
                    "AND planned_at >= %s AND planned_at < %s",
                    (dt.datetime(start.year, start.month, start.day, tzinfo=TZ),
                     dt.datetime(start.year, start.month, start.day, tzinfo=TZ) + dt.timedelta(days=7)))
    plan_id = client.post("/autopilot/plan", headers=OWNER, json={
        "starts_on": start.isoformat(), "days": 7, "posts_per_week": 2}).json()["plan_id"]

    _disconnect_meta()
    assert client.post("/autopilot/approve-week", headers=OWNER,
                       json={"week_start": start.isoformat()}).status_code == 409
    _connect_meta()
    # only the client approves: the founder acting for the business cannot
    founder = client.post(f"/autopilot/approve-week?tenant={CLINIC}", headers={"X-Role": "techdeva_admin"},
                          json={"week_start": start.isoformat()})
    assert founder.status_code in (400, 403)

    out = client.post("/autopilot/approve-week", headers=OWNER, json={"week_start": start.isoformat()}).json()
    assert out["scheduled"] == 1 and len(out["held"]) == 1 and out["held"][0]["claims"]
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT status, scheduled_at, planned_at, page_id, caption FROM social_posts WHERE plan_id = %s",
                    (plan_id,))
        rows = cur.fetchall()
    sched = [r for r in rows if r["status"] == "scheduled"]
    held = [r for r in rows if r["status"] == "draft"]
    assert len(sched) == 1 and sched[0]["scheduled_at"] == sched[0]["planned_at"] and sched[0]["page_id"] == "pg1"
    assert len(held) == 1 and "Rs 777" in held[0]["caption"]


def test_move_a_draft(client, monkeypatch):
    _fake_ai(monkeypatch)
    start = dt.datetime.now(TZ).date() + dt.timedelta(days=40)
    plan_id = client.post("/autopilot/plan", headers=OWNER, json={
        "starts_on": start.isoformat(), "days": 3, "posts_per_week": 7}).json()["plan_id"]
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT id::text AS id FROM social_posts WHERE plan_id = %s LIMIT 1", (plan_id,))
        pid = cur.fetchone()["id"]
    new_day = (start + dt.timedelta(days=1)).isoformat()
    res = client.patch(f"/autopilot/posts/{pid}/time", headers=OWNER, json={"date": new_day, "time": "21:15"})
    assert res.status_code == 200 and res.json()["planned_at"] == new_day + "T21:15:00+05:00"
    past = client.patch(f"/autopilot/posts/{pid}/time", headers=OWNER, json={"date": "2020-01-01", "time": "10:00"})
    assert past.status_code == 400


def test_results_are_read_back_from_meta(client, monkeypatch):
    _connect_meta()
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("DELETE FROM social_posts WHERE caption = 'Our Hydrafacial, step by step'")   # earlier runs
        cur.execute("""INSERT INTO social_posts (tenant_id, platforms, caption, status, results, page_id,
                                                 published_at, theme)
                       VALUES (current_tenant(), '{facebook,instagram}', 'Our Hydrafacial, step by step', 'published',
                               %s, 'pg1', now() - interval '1 day', 'how-to') RETURNING id::text""",
                    (json.dumps({"facebook": {"ok": True, "id": "pg1_555"},
                                 "instagram": {"ok": True, "id": "ig_777"}}),))
        post_id = cur.fetchone()["id"]

    def fake_get(path, params):
        if path == "/pg1_555":
            return {"shares": {"count": 3}, "comments": {"summary": {"total_count": 4}},
                    "reactions": {"summary": {"total_count": 21}}}
        if path == "/pg1_555/insights":
            return {"data": [{"name": "post_impressions_unique", "values": [{"value": 640}]}]}
        if path == "/ig_777":
            return {"like_count": 50, "comments_count": 6}
        if path == "/ig_777/insights":
            return {"data": [{"name": "reach", "values": [{"value": 900}]},
                             {"name": "saved", "values": [{"value": 12}]},
                             {"name": "shares", "values": [{"value": 5}]}]}
        if path == "/pg1":
            return {"followers_count": 1200}
        if path == "/ig1":
            return {"followers_count": 3400}
        return None

    monkeypatch.setattr(autopilot, "_graph_get", fake_get)
    assert autopilot.refresh_insights(CLINIC) >= 2
    assert autopilot.refresh_insights(CLINIC) >= 2          # a second pass updates, never duplicates
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT platform, reach, likes, comments, shares, saves FROM post_insights WHERE post_id = %s "
                    "ORDER BY platform", (post_id,))
        rows = {r["platform"]: dict(r) for r in cur.fetchall()}
    assert rows["facebook"] == {"platform": "facebook", "reach": 640, "likes": 21, "comments": 4, "shares": 3, "saves": 0}
    assert rows["instagram"]["reach"] == 900 and rows["instagram"]["saves"] == 12 and rows["instagram"]["likes"] == 50

    res = client.get("/autopilot/results?days=30", headers=OWNER).json()
    assert res["by_platform"]["instagram"]["reach"] >= 900
    assert res["followers"]["facebook"]["now"] == 1200 and res["followers"]["instagram"]["now"] == 3400
    assert any(t["id"] == post_id for t in res["top"])
    assert any(t["theme"] == "how-to" for t in res["themes"])
    # the next plan is told what worked
    with tenant_session(CLINIC, "owner") as cur:
        assert "Hydrafacial, step by step" in autopilot._what_worked(cur)


def test_plan_refuses_without_ai_and_bad_input(client, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: False)
    assert client.post("/autopilot/plan", headers=OWNER, json={"days": 7}).status_code == 503
    monkeypatch.setattr(ai, "available", lambda: True)
    assert client.post("/autopilot/plan", headers=OWNER, json={"days": 60}).status_code == 400
    assert client.post("/autopilot/plan", headers=OWNER, json={"starts_on": "2020-01-01"}).status_code == 400
    staff = client.post("/autopilot/plan", headers={"X-Tenant-Id": CLINIC, "X-Role": "operator"}, json={})
    assert staff.status_code == 403
