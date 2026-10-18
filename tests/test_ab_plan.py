"""The planner's A/B test of creatives: how many ads run for a budget, and when
a winner is clear enough to keep it and pause the rest."""
from app.adpilot import AB_MAX_ADS, ab_plan, ab_verdict

IMG = lambda n: {"kind": "image", "token": "%040d" % n}
VID = {"kind": "video", "token": "v" * 40}


def plan(budget, creatives):
    return ab_plan({"provider": "meta", "daily_budget": budget, "creatives": creatives})


def test_budget_decides_how_many_ads_and_says_so():
    p = plan(600, [IMG(1), IMG(2), IMG(3)])
    assert p["ads"] == 1 and not p["test"] and "raise the daily budget" in p["text"]
    p = plan(1500, [IMG(1), IMG(2), IMG(3), VID])
    assert p["ads"] == 3 and p["test"] and p["left_out"] == 1 and "3 ads will run" in p["text"]
    assert p["picked"][0] == VID                      # videos first
    assert plan(100000, [IMG(n) for n in range(8)])["ads"] == AB_MAX_ADS


def test_no_creative_and_google():
    assert plan(1500, [])["ads"] == 0
    assert ab_plan({"provider": "google", "daily_budget": 1500})["ads"] == 1


def row(ad, results, spend=2000, imps=20000):
    return {"ad_id": ad, "impressions": imps, "results": results, "spend": spend}


def test_clear_winner_needs_95_percent_and_lowest_cost():
    win, why = ab_verdict([row("a", 80), row("b", 260), row("c", 80)])
    assert win["ad_id"] == "b" and "99%" in why


def test_too_close_or_too_few_results_is_no_winner():
    assert ab_verdict([row("a", 100), row("b", 102)])[0] is None
    assert ab_verdict([row("a", 3), row("b", 9)])[0] is None          # under 20 results between them


def test_more_results_but_dearer_is_no_winner():
    win, why = ab_verdict([row("a", 260, spend=20000), row("b", 80, spend=1000)])
    assert win is None and "cheapest" in why
