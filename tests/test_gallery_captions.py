"""Autopilot Gallery captions: the guardrails (pure), the strict reply shape
with its one corrective retry (a fake AI), and the vision -> service mapping."""
import pytest

from app.marketing import captioner, guardrails, vision_client

CFG = {"business_name": "Glow Studio", "currency": "PKR", "language": "en",
       "services": [{"name": "Hair Cut", "price": 1500}, {"name": "HydraFacial", "price": 6000},
                    {"name": "Laser Hair Removal", "price": None}],
       "price_list": {"Bridal makeup": "25,000"},
       "marketing": {"banned_phrases": ["guaranteed results", "before and after", "no side effects"]}}


def test_only_prices_the_business_wrote_down_survive():
    text, removed = guardrails.strip_unknown_prices("HydraFacial now PKR 6,000. Bridal Rs. 25,000. Peel just Rs 999!", CFG)
    assert removed and "6,000" in text and "25,000" in text and "999" not in text
    text, removed = guardrails.strip_unknown_prices("Book a Hair Cut for 1500/- today", CFG)
    assert not removed and "1500/-" in text
    assert guardrails.strip_unknown_prices("Open 7 days, 10 to 8", CFG) == ("Open 7 days, 10 to 8", False)   # not prices


def test_the_checks_flag_and_never_crash():
    out = guardrails.check("Guaranteed results with our laser! Only PKR 2,500", ["#glow", "glow", "#Laser Hair"], "Book now",
                           "instagram", CFG)
    assert "price_removed" in out["flags"] and "banned_phrase:guaranteed results" in out["flags"]
    assert "2,500" not in out["caption"] and out["caption"].endswith("Book now")
    assert out["hashtags"] == ["#glow", "#LaserHair"]
    urdu = guardrails.check("ہیئر کٹ آج", [], "", "facebook", CFG)
    assert "wrong_script" in urdu["flags"]
    assert "wrong_script" not in guardrails.check("ہیئر کٹ آج", [], "", "facebook", dict(CFG, language="ur"))["flags"]


def test_too_similar_and_platform_limits():
    a = "Fresh cuts all week at Glow Studio, walk in and leave looking sharp"
    assert guardrails.too_similar(a + " today", [a])
    assert not guardrails.too_similar("Our HydraFacial leaves skin calm and bright", [a])
    out = guardrails.check("x" * 2190, ["#a%d" % i for i in range(40)], "", "instagram", CFG)
    assert len(guardrails.full_text(out["caption"], out["hashtags"])) <= 2200 and len(out["hashtags"]) <= 30
    assert "too_long" in guardrails.check("x" * 2300, [], "", "instagram", CFG)["flags"]
    assert guardrails.platform_limit_error("ok", ["#a"] * 31, "instagram")


def test_the_picture_decides_the_service_never_the_ai():
    shampoo = {"subject": "bottle of shampoo on a shelf", "visible_service_guess": "hair care product"}
    haircut = {"subject": "barber giving a hair cut", "visible_service_guess": "haircut"}
    svc = [{"name": n} for n in ("Hair Cut", "HydraFacial", "Laser Hair Removal")]
    assert vision_client.match_service(haircut, svc) == "Hair Cut"
    assert vision_client.match_service(shampoo, svc) is None              # no configured service shown: no tag
    assert vision_client.match_service({"visible_service_guess": "hydrafacial treatment"}, svc) == "HydraFacial"
    assert vision_client.match_service({"visible_service_guess": "haircut"}, svc) == "Hair Cut"
    assert vision_client.match_service({"subject": "laser hair removal on legs"}, svc) == "Laser Hair Removal"
    assert vision_client.describe({"original_name": "hair-cut_02.jpg"}, None)["summary"]["subject"] == "hair cut"


ITEMS = [{"asset_id": "a1", "kind": "image", "service_tag": "Hair Cut", "vision_summary": {}, "when": "x", "when_text": "Sat"},
         {"asset_id": "a2", "kind": "image", "service_tag": "HydraFacial", "vision_summary": {}, "when": "y", "when_text": "Tue"}]


def test_a_reply_of_the_wrong_shape_is_retried_once_with_the_reason(monkeypatch):
    monkeypatch.setenv("CAPTION_MODE", "live")
    calls = []
    replies = [{"captions": [{"asset_id": "a1", "caption": "Sharp cuts", "hashtags": [], "cta": "Book"}]},
               {"captions": [{"asset_id": "a1", "caption": "Sharp cuts", "hashtags": [], "cta": "Book"},
                             {"asset_id": "a2", "caption": "Calm skin", "hashtags": ["#glow"], "cta": "Book"}]}]

    def fake(system, prompt, required_keys=(), _retry=True):
        calls.append(prompt)
        return replies[len(calls) - 1]
    from app import ai
    monkeypatch.setattr(ai, "generate_json", fake)
    monkeypatch.setattr(captioner, "_business_block", lambda cfg, tid: "Business: Glow Studio")
    got = captioner._write(CFG, "t", ITEMS, [], ["facebook"])
    assert set(got) == {"a1", "a2"} and len(calls) == 2
    assert "missing captions for a2" in calls[1] and "exactly 2 entries" in calls[1]


def test_after_two_bad_replies_the_missing_pictures_get_an_empty_flagged_caption(monkeypatch):
    monkeypatch.setenv("CAPTION_MODE", "live")
    from app import ai
    monkeypatch.setattr(ai, "generate_json", lambda *a, **k: {"captions": [{"asset_id": "a1", "caption": "Only one", "hashtags": [], "cta": ""}]})
    monkeypatch.setattr(captioner, "_business_block", lambda cfg, tid: "Business")
    got = captioner._write(CFG, "t", ITEMS, [], ["facebook"])
    assert set(got) == {"a1"}
    done = captioner._finish(CFG, ITEMS[1], got.get("a2"), ["facebook", "instagram"], [])
    assert done["facebook"] == {"caption": "", "hashtags": [], "flags": ["caption_failed"]} and done["instagram"]["flags"] == ["caption_failed"]


@pytest.mark.parametrize("data, why", [
    ({}, 'no "captions" list'),
    ({"captions": [{"asset_id": "zz", "caption": "x"}]}, "unknown or missing asset_id"),
    ({"captions": [{"asset_id": "a1", "caption": "  "}, {"asset_id": "a2", "caption": "y", "hashtags": "#a"}]}, "has no caption"),
])
def test_the_reply_shape_is_checked(data, why):
    good, problem = captioner._valid(data, ["a1", "a2"])
    assert why in problem
