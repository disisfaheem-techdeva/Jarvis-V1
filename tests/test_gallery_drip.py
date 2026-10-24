"""The drip schedule engine (app/marketing/drip.py): pure, so every rule is
checked with fixed dates. 2 Nov 2026 is a Monday."""
import datetime as dt
from zoneinfo import ZoneInfo

import pytest

from app.marketing.drip import DripError, build_schedule, describe

UTC = dt.timezone.utc
KHI = ZoneInfo("Asia/Karachi")


def at(y, mo, d, h=0, mi=0, tz=KHI):
    return dt.datetime(y, mo, d, h, mi, tzinfo=tz)


def local(slots, tz=KHI):
    return [s.astimezone(tz) for s in slots]


def weeks(slots):
    out = {}
    for s in local(slots):
        k = s.isocalendar()[:2]
        out[k] = out.get(k, 0) + 1
    return list(out.values())


EARLY = at(2026, 11, 1, 9)                   # Sunday morning, before anything


def test_ten_items_three_a_week_span_four_calendar_weeks():
    rule = {"days": ["Mon", "Wed", "Fri"], "times": ["12:00"], "start_date": "2026-11-02"}
    s = build_schedule(10, rule, "Asia/Karachi", EARLY)
    assert weeks(s) == [3, 3, 3, 1]
    assert local(s)[0] == at(2026, 11, 2, 12) and s[0] == dt.datetime(2026, 11, 2, 7, 0, tzinfo=UTC)   # stored UTC
    assert all(x.tzinfo is not None and x.utcoffset() == dt.timedelta(0) for x in s)


def test_fifteen_items_tue_thu_sat_take_five_weeks():
    rule = {"days": ["Tue", "Thu", "Sat"], "times": ["12:00"], "start_date": "2026-11-02"}
    s = build_schedule(15, rule, "Asia/Karachi", EARLY)
    assert weeks(s) == [3, 3, 3, 3, 3]
    assert {x.strftime("%a") for x in local(s)} == {"Tue", "Thu", "Sat"}
    assert local(s)[-1] == at(2026, 12, 5, 12)
    assert describe(15, rule, s, "Asia/Karachi") == "15 posts · Tue/Thu/Sat 12:00 · ends Sat 5 Dec"


def test_a_mid_week_start_begins_at_the_next_chosen_day():
    rule = {"days": ["Mon", "Wed", "Fri"], "times": ["12:00"], "start_date": "2026-11-05"}      # a Thursday
    assert local(build_schedule(2, rule, "Asia/Karachi", EARLY)) == [at(2026, 11, 6, 12), at(2026, 11, 9, 12)]


def test_today_is_skipped_once_its_time_has_passed():
    rule = {"days": ["Mon", "Wed"], "times": ["12:00"], "start_date": "2026-11-02"}
    assert local(build_schedule(1, rule, "Asia/Karachi", at(2026, 11, 2, 13)))[0] == at(2026, 11, 4, 12)
    assert local(build_schedule(1, rule, "Asia/Karachi", at(2026, 11, 2, 11, 59)))[0] == at(2026, 11, 2, 12)
    # a start date in the past starts today, never in the past
    old = dict(rule, start_date="2026-01-01")
    assert local(build_schedule(1, old, "Asia/Karachi", at(2026, 11, 2, 13)))[0] == at(2026, 11, 4, 12)


def test_slots_another_campaign_holds_are_left_alone():
    rule = {"days": ["Mon", "Wed", "Fri"], "times": ["12:00"], "start_date": "2026-11-02"}
    taken = [at(2026, 11, 2, 12).astimezone(UTC), at(2026, 11, 6, 12, 0).astimezone(UTC)]
    assert local(build_schedule(3, rule, "Asia/Karachi", EARLY, taken)) == [at(2026, 11, 4, 12), at(2026, 11, 9, 12), at(2026, 11, 11, 12)]


def test_several_times_a_day_and_the_daily_cap():
    rule = {"days": ["Sat"], "times": ["18:00", "10:00"], "start_date": "2026-11-02"}
    assert local(build_schedule(3, rule, "Asia/Karachi", EARLY)) == [at(2026, 11, 7, 10), at(2026, 11, 7, 18), at(2026, 11, 14, 10)]
    one = dict(rule, max_per_day=1)
    assert local(build_schedule(2, one, "Asia/Karachi", EARLY)) == [at(2026, 11, 7, 10), at(2026, 11, 14, 10)]
    # a taken first slot does not use up the day: the later time is still used
    taken = [at(2026, 11, 7, 10).astimezone(UTC)]
    assert local(build_schedule(1, one, "Asia/Karachi", EARLY, taken)) == [at(2026, 11, 7, 18)]


@pytest.mark.parametrize("rule, why", [
    ({"days": [], "times": ["12:00"]}, "at least one day"),
    ({"days": ["Mon"], "times": []}, "at least one time"),
    ({"days": ["Funday"], "times": ["12:00"]}, "Days are"),
    ({"days": ["Mon"], "times": ["25:00"]}, "Times look like"),
    ({"days": ["Mon"], "times": ["12:00"], "max_per_day": 0}, "at least 1"),
    ({"days": ["Mon"], "times": ["12:00"], "start_date": "next week"}, "start date"),
])
def test_bad_rules_are_refused_with_a_reason(rule, why):
    with pytest.raises(DripError, match=why):
        build_schedule(3, rule, "Asia/Karachi", EARLY)
    with pytest.raises(DripError, match="time zone"):
        build_schedule(1, {"days": ["Mon"], "times": ["12:00"]}, "Mars/Olympus", EARLY)


def test_daylight_saving_is_real_wall_clock_time():
    lon = ZoneInfo("Europe/London")
    rule = {"days": ["Sat", "Sun"], "times": ["12:00"], "start_date": "2026-10-24"}
    s = build_schedule(3, rule, "Europe/London", dt.datetime(2026, 10, 24, 0, 0, tzinfo=UTC))
    # 12:00 London is 11:00 UTC in summer time, 12:00 UTC once the clocks go back on 25 Oct
    assert [x.strftime("%a %H:%M") for x in s] == ["Sat 11:00", "Sun 12:00", "Sat 12:00"]
    assert all(x.astimezone(lon).strftime("%H:%M") == "12:00" for x in s)
    # 01:30 does not exist on 28 Mar 2027 in London (clocks jump 01:00 -> 02:00): skipped, not moved
    spring = {"days": ["Sun"], "times": ["01:30"], "start_date": "2027-03-28"}
    first = build_schedule(1, spring, "Europe/London", dt.datetime(2027, 3, 27, tzinfo=UTC))[0]
    assert first.astimezone(lon).date() == dt.date(2027, 4, 4) and first.astimezone(lon).strftime("%H:%M") == "01:30"


def test_the_same_inputs_give_the_same_schedule_and_never_leave_the_chosen_slots():
    rule = {"days": ["Tue", "Fri"], "times": ["09:30", "19:15"], "start_date": "2026-11-02", "max_per_day": 2}
    a = build_schedule(40, rule, "Asia/Karachi", EARLY)
    assert a == build_schedule(40, rule, "Asia/Karachi", EARLY) and len(set(a)) == 40 and a == sorted(a)
    assert all(x.strftime("%a") in ("Tue", "Fri") and x.strftime("%H:%M") in ("09:30", "19:15") for x in local(a))
    assert build_schedule(0, rule, "Asia/Karachi", EARLY) == []
