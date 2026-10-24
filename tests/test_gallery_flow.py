"""Autopilot Gallery end to end, with the real API, database (RLS), Redis
queues, orchestrator rules and worker. External services are the stubs:
vision (file name), captions (from the business's config), Meta publishing.

The clock: scheduled times come from the real 'now'. "Advancing the clock" moves
a campaign's posts earlier by the gap to a chosen slot (spacing unchanged), so
exactly the posts up to that slot are due, then the scheduler hook and the
worker run as they do in production. Scratch database only.
"""
import io
import os
import threading
import time
import uuid

import pytest
from PIL import Image
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import meta_publish_client as meta, roles  # noqa: E402
from app.auth import issue_token  # noqa: E402
from app.db import tenant_session  # noqa: E402
from app.main import app  # noqa: E402
from app.marketing import publisher  # noqa: E402
from app.persistence import EVENTS_Q, TASKS_Q  # noqa: E402
from app.storage import LocalStorage, set_storage  # noqa: E402
from app.worker import Worker  # noqa: E402

RULE = {"days": ["Tue", "Thu", "Sat"], "times": ["12:00"], "platforms": ["facebook", "instagram"]}


def jpeg(seed, size=(1080, 1080)):
    b = io.BytesIO()
    Image.new("RGB", size, ((seed * 37) % 256, (seed * 91) % 256, (seed * 53) % 256)).save(b, "JPEG")
    return b.getvalue()


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    env = {"CAPTION_MODE": "stub", "META_PUBLISH_MODE": "stub", "VISION_PROVIDER": ""}
    saved = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    set_storage(LocalStorage(root=str(tmp_path_factory.mktemp("gallery")), public_base="https://api.test"))
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    p = {k: str(uuid.uuid4()) for k in ("owner", "mkt", "b_owner")}
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("INSERT INTO tenants (id, name, industry, status, is_test) VALUES (%s, 'Glow Studio', 'salon', 'active', true), "
                    "(%s, 'Other Biz', 'x', 'active', true)", (a, b))
        for tid in (a, b):
            cur.execute("""INSERT INTO tenant_config (tenant_id, services, price_list, timezone, language)
                           VALUES (%s, '[{"name":"Hair Cut","price":1500},{"name":"HydraFacial","price":6000}]', '{}',
                                   'Asia/Karachi', 'en')""", (tid,))
        for k, tid, role, sr in (("owner", a, "owner", None), ("mkt", a, "owner", "marketing"), ("b_owner", b, "owner", None)):
            cur.execute("INSERT INTO users (id, tenant_id, name, email, role, staff_role) VALUES (%s, %s, %s, %s, %s, %s)",
                        (p[k], tid, k, "%s-%s@gallery-test.local" % (k, p[k][:8]), role, sr))
    roles.forget()
    h = {k: {"Authorization": "Bearer " + issue_token(tenant_id=(b if k == "b_owner" else a), user_id=p[k], role="owner")}
         for k in p}
    w = Worker()
    w.p.r.delete(EVENTS_Q, TASKS_Q)
    yield {"a": a, "b": b, "p": p, "h": h, "w": w}
    w.p.r.delete(EVENTS_Q, TASKS_Q)
    with tenant_session(None, "techdeva_admin") as cur:
        for t in (a, b):
            cur.execute("DELETE FROM executions WHERE tenant_id = %s", (t,))
            cur.execute("DELETE FROM tenants WHERE id = %s", (t,))
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    meta.STUB_FAILURES.clear()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


def drain(w, limit=500):
    for _ in range(limit):
        if not w.process_once(timeout=1):
            return
    raise AssertionError("the queues never emptied")


def upload(client, h, files):
    r = client.post("/marketing/media", files=[("files", (n, data, "application/octet-stream")) for n, data in files], headers=h)
    assert r.status_code == 200, r.text
    return r.json()["results"]


def campaign(client, h, cid):
    r = client.get("/marketing/campaigns/" + cid, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def advance_to(tid, cid, nth):
    """Move the campaign earlier so its nth time (1-based) is one minute ago."""
    with tenant_session(tid, "owner") as cur:
        cur.execute("SELECT DISTINCT scheduled_at FROM scheduled_posts WHERE campaign_id = %s AND status <> 'cancelled' "
                    "ORDER BY scheduled_at", (cid,))
        slots = [r["scheduled_at"] for r in cur.fetchall()]
        cur.execute("UPDATE scheduled_posts SET scheduled_at = scheduled_at - (%s - now()) - interval '1 minute' "
                    "WHERE campaign_id = %s", (slots[nth - 1], cid))


def run_scheduler(w):
    publisher.tick(w.p.emit)
    drain(w)


@pytest.fixture(scope="module")
def made(client, world):
    """15 pictures uploaded and a Tue/Thu/Sat 12:00 campaign generated and captioned."""
    h = world["h"]["mkt"]                                    # marketing staff can upload and generate
    names = [("haircut_%02d.jpg" % i if i % 2 else "hydrafacial_%02d.jpg" % i, jpeg(i)) for i in range(15)]
    res = upload(client, h, names)
    assert all(r["ok"] for r in res), res
    ids = [r["asset"]["id"] for r in res]
    r = client.post("/marketing/autopilot/generate", json={"name": "November drip", "media_asset_ids": ids,
                                                           "rule_config": RULE}, headers=h)
    assert r.status_code == 200, r.text
    cid = r.json()["campaign_id"]
    drain(world["w"])                                        # analyse -> captions, through rules and agents
    return {"cid": cid, "ids": ids}


# ------------------------------------------------------------------ upload
def test_upload_checks_type_duplicates_shape_and_keeps_keys_per_business(client, world):
    h = world["h"]["owner"]
    png = io.BytesIO()
    Image.new("RGBA", (1000, 1000), (10, 20, 30, 128)).save(png, "PNG")
    res = upload(client, h, [("wide.jpg", jpeg(99, (2000, 600))), ("logo.png", png.getvalue()),
                             ("notes.txt", b"hello there"), ("again.jpg", jpeg(99, (2000, 600)))])
    by = {r["name"]: r for r in res}
    assert by["wide.jpg"]["ok"] and [w["code"] for w in by["wide.jpg"]["asset"]["warnings"]] == ["ig_aspect"]
    assert by["logo.png"]["ok"] and by["logo.png"]["asset"]["mime_type"] == "image/jpeg"     # Instagram takes JPEG only
    assert by["notes.txt"]["status"] == 415 and by["again.jpg"]["status"] == 409             # not media; a duplicate
    key = by["wide.jpg"]["asset"]["storage_key"]
    assert key.startswith("tenants/%s/marketing/" % world["a"])
    assert client.get("/media/g/" + key).status_code == 200                                   # Meta can fetch it
    assert client.get("/media/g/tenants/../../etc/passwd").status_code == 404


# ------------------------------------------------------------------ generate -> captions -> approve -> publish
def test_fifteen_pictures_become_thirty_drafts_at_the_right_utc_times(client, world, made):
    c = campaign(client, world["h"]["owner"], made["cid"])
    assert c["status"] == "awaiting_approval" and c["captioning"] == "done"
    posts = c["posts"]
    assert len(posts) == 30 and {p["platform"] for p in posts} == {"facebook", "instagram"}
    assert all(p["status"] == "draft" and p["caption"] for p in posts)
    from zoneinfo import ZoneInfo
    import datetime as dt
    times = sorted({p["scheduled_at"] for p in posts})
    assert len(times) == 15
    for t in times:
        local = dt.datetime.fromisoformat(t).astimezone(ZoneInfo("Asia/Karachi"))
        assert local.strftime("%a %H:%M") in ("Tue 12:00", "Thu 12:00", "Sat 12:00")
        assert dt.datetime.fromisoformat(t).utcoffset() == dt.timedelta(0)                     # stored UTC
    hair = [p for p in posts if p["service_tag"] == "Hair Cut"]
    assert hair and all("Hair Cut" in p["caption"] and "1500" in p["caption"] for p in hair)  # price from config


def test_unapproved_drafts_are_never_published_even_when_due(client, world, made):
    advance_to(world["a"], made["cid"], 2)
    run_scheduler(world["w"])
    c = campaign(client, world["h"]["owner"], made["cid"])
    assert all(p["status"] == "draft" for p in c["posts"])
    with tenant_session(world["a"], "owner") as cur:          # the table itself refuses an approval with no person
        with pytest.raises(Exception, match="scheduled_posts_approval_ck"):
            cur.execute("UPDATE scheduled_posts SET status = 'approved' WHERE campaign_id = %s", (made["cid"],))


def test_only_the_owner_approves_and_late_posts_must_be_moved_first(client, world, made):
    r = client.post("/marketing/campaigns/%s/approve" % made["cid"], headers=world["h"]["mkt"])
    assert r.status_code == 403                                                                # marketing staff: no
    r = client.post("/marketing/campaigns/%s/approve" % made["cid"], headers=world["h"]["owner"])
    assert r.status_code == 409 and "past" in r.json()["detail"]["message"]                    # the 2 we made due
    with tenant_session(world["a"], "owner") as cur:                                            # back to the future
        cur.execute("UPDATE scheduled_posts SET scheduled_at = scheduled_at + interval '30 days' WHERE campaign_id = %s",
                    (made["cid"],))
    r = client.post("/marketing/campaigns/%s/approve" % made["cid"], headers=world["h"]["owner"])
    assert r.status_code == 200 and r.json()["approved"] == 30, r.text
    c = campaign(client, world["h"]["owner"], made["cid"])
    assert c["status"] == "active" and all(p["approved_by"] == world["p"]["owner"] for p in c["posts"])


def test_editing_an_approved_caption_sends_it_back_for_approval(client, world, made):
    c = campaign(client, world["h"]["owner"], made["cid"])
    post = c["posts"][-1]
    r = client.patch("/marketing/posts/" + post["id"], json={"caption": "Weekend glow, done by hand"}, headers=world["h"]["mkt"])
    assert r.status_code == 200 and r.json()["needs_approval"] is True
    p = next(x for x in campaign(client, world["h"]["owner"], made["cid"])["posts"] if x["id"] == post["id"])
    assert p["status"] == "draft" and p["approved_by"] is None and p["caption_source"] == "human_edited" and p["caption_version"] == 2
    assert client.post("/marketing/campaigns/%s/approve" % made["cid"], headers=world["h"]["owner"]).json()["approved"] == 1


def test_each_post_goes_out_once_at_its_time_and_the_campaign_completes(client, world, made, monkeypatch):
    sent = []
    real = meta.publish
    monkeypatch.setattr(meta, "publish", lambda *a, **k: sent.append(a[0]) or real(*a, **k))
    w = world["w"]
    advance_to(world["a"], made["cid"], 3)                     # the first three times have come
    run_scheduler(w)
    run_scheduler(w)                                           # a second tick finds nothing new
    c = campaign(client, world["h"]["owner"], made["cid"])
    assert c["counts"] == {"published": 6, "approved": 24} and len(sent) == 6
    assert all(p["test_mode"] and p["external_post_id"].startswith("stub:") for p in c["posts"] if p["status"] == "published")
    advance_to(world["a"], made["cid"], 15)
    run_scheduler(w)
    c = campaign(client, world["h"]["owner"], made["cid"])
    assert c["counts"] == {"published": 30} and len(sent) == 30 and c["status"] == "completed"
    with tenant_session(world["a"], "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM events WHERE type = 'marketing.post_published' AND payload->>'campaign_id' = %s",
                    (made["cid"],))
        assert cur.fetchone()["n"] == 30


# ------------------------------------------------------------------ races and failures
def _fresh(client, world, n, seed):
    res = upload(client, world["h"]["owner"], [("haircut_x%d_%d.jpg" % (seed, i), jpeg(seed * 100 + i)) for i in range(n)])
    r = client.post("/marketing/autopilot/generate", json={"name": "c%d" % seed, "media_asset_ids": [x["asset"]["id"] for x in res],
                                                           "rule_config": RULE}, headers=world["h"]["owner"])
    cid = r.json()["campaign_id"]
    drain(world["w"])
    assert client.post("/marketing/campaigns/%s/approve" % cid, headers=world["h"]["owner"]).status_code == 200
    return cid


def test_two_workers_racing_for_one_due_post_publish_it_once(client, world, monkeypatch):
    cid = _fresh(client, world, 1, 7)
    advance_to(world["a"], cid, 1)
    calls = []
    real = meta.publish

    def slow(*a, **k):
        calls.append(a[0])
        time.sleep(0.3)
        return real(*a, **k)
    monkeypatch.setattr(meta, "publish", slow)
    post = next(p for p in campaign(client, world["h"]["owner"], cid)["posts"] if p["platform"] == "facebook")
    out = []
    ts = [threading.Thread(target=lambda: out.append(publisher.publish_one(world["a"], post["id"]))) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(o["status"] for o in out) == ["published", "skipped"] and calls == ["facebook"]
    assert publisher.publish_one(world["a"], post["id"])["status"] == "skipped"                 # never twice


def test_a_lost_meta_login_pauses_the_campaign_and_tells_everyone(client, world):
    cid = _fresh(client, world, 3, 8)
    advance_to(world["a"], cid, 1)
    meta.STUB_FAILURES["instagram"] = meta.GraphError("auth", "Error validating access token (code 190)")
    meta.STUB_FAILURES["validate"] = meta.GraphError("auth", "Error validating access token")
    try:
        run_scheduler(world["w"])
        c = campaign(client, world["h"]["owner"], cid)
        assert c["status"] == "paused" and c["paused_reason"] == "meta_auth"
        ig = [p for p in c["posts"] if p["platform"] == "instagram"]
        assert ig[0]["status"] == "failed" and "190" in ig[0]["last_error"]
        assert [p["status"] for p in ig[1:]] == ["approved", "approved"]                       # the rest untouched
        with tenant_session(world["a"], "owner") as cur:
            cur.execute("SELECT type, payload FROM events WHERE at > now() - interval '5 minutes' "
                        "AND type IN ('techdeva.alert', 'notify.sent', 'ops.alert')")
            ev = cur.fetchall()
        assert any(e["type"] == "techdeva.alert" and "paused" in e["payload"]["message"] for e in ev)
        assert any(e["type"] == "notify.sent" and e["payload"]["role"] == "owner" and "reconnect" in e["payload"]["message"] for e in ev)
        r = client.post("/marketing/campaigns/%s/resume" % cid, headers=world["h"]["owner"])
        assert r.status_code == 409 and r.json()["detail"]["reconnect"] is True                 # token still bad
    finally:
        meta.STUB_FAILURES.clear()
    assert client.post("/marketing/campaigns/%s/resume" % cid, headers=world["h"]["owner"]).json()["status"] == "active"


def test_a_busy_meta_is_retried_then_failed_after_three_attempts(client, world):
    cid = _fresh(client, world, 1, 9)
    advance_to(world["a"], cid, 1)
    meta.STUB_FAILURES["facebook"] = meta.GraphError("transient", "Meta answered 503")
    try:
        for attempt in (1, 2, 3):
            with tenant_session(world["a"], "owner") as cur:
                cur.execute("UPDATE scheduled_posts SET retry_after = NULL WHERE campaign_id = %s", (cid,))
            run_scheduler(world["w"])
            fb = next(p for p in campaign(client, world["h"]["owner"], cid)["posts"] if p["platform"] == "facebook")
            assert fb["attempts"] == attempt
            assert fb["status"] == ("approved" if attempt < 3 else "failed") and "503" in fb["last_error"]
    finally:
        meta.STUB_FAILURES.clear()


def test_flagged_or_empty_captions_block_approval_unless_overridden(client, world):
    res = upload(client, world["h"]["owner"], [("haircut_flag.jpg", jpeg(4242))])
    cid = client.post("/marketing/autopilot/generate", json={"name": "flags", "media_asset_ids": [res[0]["asset"]["id"]],
                                                             "rule_config": dict(RULE, platforms=["facebook"])},
                      headers=world["h"]["owner"]).json()["campaign_id"]
    drain(world["w"])
    post = campaign(client, world["h"]["owner"], cid)["posts"][0]
    r = client.patch("/marketing/posts/" + post["id"], json={"caption": "Guaranteed results, only PKR 99"}, headers=world["h"]["owner"])
    assert r.status_code == 200
    p = campaign(client, world["h"]["owner"], cid)["posts"][0]
    assert {f["code"] for f in p["flags"]} == {"price_not_listed", "banned_phrase:guaranteed results"}
    r = client.post("/marketing/campaigns/%s/approve" % cid, headers=world["h"]["owner"])
    assert r.status_code == 409 and r.json()["detail"]["blockers"][0]["reason"] == "flagged"
    r = client.post("/marketing/campaigns/%s/approve" % cid, json={"override_post_ids": [p["id"]]}, headers=world["h"]["owner"])
    assert r.status_code == 200 and r.json()["overridden"] == [p["id"]]


def test_moving_a_post_checks_the_future_and_other_posts(client, world):
    cid = _fresh(client, world, 2, 11)
    posts = [p for p in campaign(client, world["h"]["owner"], cid)["posts"] if p["platform"] == "facebook"]
    h = world["h"]["owner"]
    assert client.patch("/marketing/posts/" + posts[0]["id"], json={"scheduled_at": "2020-01-01T12:00:00+05:00"}, headers=h).status_code == 400
    r = client.patch("/marketing/posts/" + posts[0]["id"], json={"scheduled_at": posts[1]["scheduled_at"]}, headers=h)
    assert r.status_code == 409 and "already goes out" in r.json()["detail"]
    r = client.patch("/marketing/posts/" + posts[0]["id"], json={"scheduled_at": "2030-01-05T09:15:00+05:00"}, headers=h)
    assert r.status_code == 200 and r.json()["status"] == "draft"


# ------------------------------------------------------------------ isolation
def test_another_business_sees_and_changes_nothing(client, world, made):
    hb = world["h"]["b_owner"]
    assert client.get("/marketing/campaigns/" + made["cid"], headers=hb).status_code == 404
    assert client.get("/marketing/campaigns", headers=hb).json()["campaigns"] == []
    assert client.get("/marketing/media", headers=hb).json()["media"] == []
    any_post = campaign(client, world["h"]["owner"], made["cid"])["posts"][0]["id"]
    assert client.patch("/marketing/posts/" + any_post, json={"cancel": True}, headers=hb).status_code == 404
    assert client.post("/marketing/campaigns/%s/approve" % made["cid"], headers=hb).status_code == 404
    r = client.post("/marketing/autopilot/generate", json={"name": "steal", "media_asset_ids": made["ids"][:1], "rule_config": RULE}, headers=hb)
    assert r.status_code == 404                                                                 # A's picture is not B's
    with tenant_session(world["b"], "owner") as cur:
        cur.execute("SELECT count(*) AS n FROM scheduled_posts")
        assert cur.fetchone()["n"] == 0
        with pytest.raises(Exception, match="row-level security|scheduled post must use"):
            cur.execute("INSERT INTO scheduled_posts (tenant_id, campaign_id, media_asset_id, platform, scheduled_at) "
                        "VALUES (%s, %s, %s, 'facebook', now())", (world["a"], made["cid"], made["ids"][0]))
    with tenant_session(None, "techdeva_admin") as cur:
        cur.execute("SELECT count(DISTINCT tenant_id) AS n FROM media_assets WHERE tenant_id IN (%s, %s)", (world["a"], world["b"]))
        assert cur.fetchone()["n"] == 1                                                         # admin sees A's rows
        cur.execute("SELECT bool_and(storage_key LIKE 'tenants/' || tenant_id || '/marketing/%%') AS ok FROM media_assets "
                    "WHERE tenant_id = %s", (world["a"],))
        assert cur.fetchone()["ok"] is True
