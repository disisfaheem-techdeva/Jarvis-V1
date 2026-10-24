"""Jarvis makes a post from the owner's pictures, designs it, keeps it as a
draft, and posts it only when the owner says yes / "post it".

The AI and Meta are fakes; the database, the media store, the loopback call
into the platform's own publish endpoint and the sealed confirmation are real.
"""
import base64
import os
import uuid

import pytest
from psycopg.types.json import Json
from starlette.testclient import TestClient

if "@db:" in os.environ.get("DATABASE_URL", ""):
    pytest.skip("refusing to run against the compose (production) database", allow_module_level=True)

from app import console, crypto, jarvis_actions as ja, social
from app.db import tenant_session
from app.main import app

CLINIC = "11111111-1111-1111-1111-111111111111"
OWNER = {"X-Tenant-Id": CLINIC, "X-Role": "owner"}
JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"\x00" * 64).decode()


@pytest.fixture(scope="module")
def client():
    return TestClient(app)


@pytest.fixture()
def env(monkeypatch, client):
    said, published = {}, []

    def generate_json(system, prompt, required_keys=(), _retry=True):
        return dict(said["out"])

    def loopback(method, url, json=None, timeout=None, headers=None):
        return client.request(method, url.replace(ja.SELF_URL, ""), json=json, headers=headers)

    def fb(page, caption, images):
        published.append(("facebook", caption, list(images)))
        return {"ok": True, "id": "fb_1"}

    def ig(page, caption, images):
        published.append(("instagram", caption, list(images)))
        return {"ok": False, "error": "Instagram is busy"} if "IGFAIL" in caption else {"ok": True, "id": "ig_1"}

    def no_menu(*a, **k):
        raise AssertionError("pictures for a post were read as a menu")

    monkeypatch.setattr(console, "generate_json", generate_json)
    monkeypatch.setattr(ja.requests, "request", loopback)
    monkeypatch.setattr(social, "_publish_facebook", fb)
    monkeypatch.setattr(social, "_publish_instagram", ig)
    monkeypatch.setattr(console, "_read_menu_photos", no_menu)
    creds = {"access_token": "u", "pages": [{"id": "1001001", "name": "Clinic Page", "access_token": "page-tok",
                                               "ig_id": "2002002", "ig_username": "clinic"}]}
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("""INSERT INTO integrations (tenant_id, provider, status, credentials, connected_at)
                       VALUES (current_tenant(), 'meta', 'connected', %s, now())
                       ON CONFLICT (tenant_id, provider) DO UPDATE SET status = 'connected',
                         credentials = EXCLUDED.credentials, connected_at = now()""",
                    (Json(crypto.encrypt_json(creds, CLINIC, "meta")),))
    return {"said": said, "published": published}


def _picture(client):
    r = client.post("/social/media", json={"image": JPEG}, headers=OWNER)
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _status(pid):
    with tenant_session(CLINIC, "owner") as cur:
        cur.execute("SELECT status, image_urls FROM social_posts WHERE id = %s", (pid,))
        return cur.fetchone()


def _post_out(tag, head="BBQ Box Rs 2500", sub="Fresh off the coals"):
    return {"kind": "post", "caption": "BBQ night %s\n\nSmoky and fresh.\n\nOrder on WhatsApp.\n\n#bbq #lahore" % tag,
            "platforms": ["facebook", "instagram"], "headline": head, "subline": sub, "reply": "Drafted!"}


def test_pictures_go_to_a_post_unless_it_is_about_a_menu():
    pic = ["https://x/media/a.jpg"]
    assert not console._wants_menu_read("is ka design bana do", pic)
    assert not console._wants_menu_read("BBQ box 2500 ki post", pic)
    assert not console._wants_menu_read("", pic)
    assert not console._wants_menu_read("price 2500 wala deal daal do", pic)
    assert console._wants_menu_read("ye menu parh lo", pic)
    assert console._wants_menu_read("read my price list", pic)
    assert not console._wants_menu_read("ye menu parh lo", [])


def test_design_words_never_invent_a_number():
    assert console._design_text({"headline": "BBQ Box Rs 2500", "subline": "Fresh"}, "box 2500 ka") == \
        {"head": "BBQ Box Rs 2500", "sub": "Fresh"}
    assert console._design_text({"headline": "50% OFF today", "subline": "Fresh"}, "naya deal") == \
        {"head": "", "sub": "Fresh"}
    assert console._design_text({"headline": "", "subline": ""}, "x") is None


def test_pictures_become_a_designed_draft_and_yes_posts_it(client, env):
    tag = uuid.uuid4().hex[:8]
    url = _picture(client)
    env["said"]["out"] = _post_out(tag)
    r = client.post("/console", json={"message": "is ka design bana do, BBQ box 2500", "image_urls": [url]},
                    headers=OWNER)
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "post" and d["design"]["head"] == "BBQ Box Rs 2500"
    assert "Shall I post it now?" in d["reply"] and d["confirm"]
    assert d["post"]["image_urls"] == [url]
    pid = d["post"]["id"]
    assert _status(pid)["status"] == "draft" and not env["published"]

    # the dashboard swaps in the designed picture, then the owner says yes
    designed = _picture(client)
    assert client.patch("/social/posts/" + pid, json={"image_urls": [designed]}, headers=OWNER).status_code == 200
    r = client.post("/console", json={"confirm": d["confirm"]}, headers=OWNER)
    assert r.status_code == 200, r.text
    # the reply says only what the platforms confirmed (both answered with a post id)
    assert r.json()["ok"] is True and r.json()["reply"] == "Posted on Facebook and Instagram; confirmed by the platform."
    assert _status(pid)["status"] == "published"
    assert [p[0] for p in env["published"]] == ["facebook", "instagram"]
    assert env["published"][0][2] == [designed]

    # the same yes can never post twice
    r = client.post("/console", json={"confirm": d["confirm"]}, headers=OWNER)
    assert r.status_code >= 400 or r.json().get("ok") is False
    assert len(env["published"]) == 2


def test_post_it_later_asks_then_posts_the_latest_draft(client, env):
    tag = uuid.uuid4().hex[:8]
    env["said"]["out"] = _post_out(tag + " IGFAIL", head="", sub="")
    d = client.post("/console", json={"message": "ek post banao BBQ night ki", "image_urls": [_picture(client)]},
                    headers=OWNER).json()
    assert d["kind"] == "post" and d["design"] is None
    pid = d["post"]["id"]

    env["said"]["out"] = {"kind": "action", "action": "publish_post", "args": {}, "reply": "Post it?"}
    q = client.post("/console", json={"message": "post kar do"}, headers=OWNER).json()
    assert q["kind"] == "confirm" and tag in q["summary"] and not env["published"]
    a = client.post("/console", json={"confirm": q["confirm"]}, headers=OWNER).json()
    assert a["ok"] is True and "did not go out on Instagram" in a["reply"]
    assert _status(pid)["status"] == "partial"


def test_nothing_to_post(monkeypatch):
    monkeypatch.setattr(ja, "_draft", lambda cur, pid: None)
    with pytest.raises(ja.ActionError, match="no draft"):
        ja.describe(CLINIC, "owner", "publish_post", {})


def test_only_the_owner_posts():
    with pytest.raises(ja.ActionError, match="client's own decision"):
        ja.describe(CLINIC, "owner", "publish_post", {}, founder=True)
    with pytest.raises(ja.ActionError, match="only the business owner"):
        ja.describe(CLINIC, "operator", "publish_post", {})
