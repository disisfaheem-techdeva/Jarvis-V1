"""The drip schedule engine: which minutes a campaign's posts go out.

Pure: no database, no clock, no I/O. Everything it needs is passed in, so the
same inputs always give the same schedule and every rule is unit-tested
(tests/test_gallery_drip.py).

    build_schedule(n_items, rule_config, tz, now_utc, existing_slots_utc) -> [datetime (UTC)]

Walks forward day by day in the business's own time zone from the later of the
start date and today, over the chosen weekdays x times, and takes a slot unless
it is already past, already taken (another campaign or a scheduled post on the
same platforms), or the day already has max_per_day of this campaign's posts.
Times are wall-clock times in that zone: 12:00 stays 12:00 across a daylight
saving change, and a time that does not exist that day (clocks jump forward)
is skipped rather than moved, so nothing is ever posted outside the chosen times.
"""
from __future__ import annotations

import datetime as dt
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TIME = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
MAX_ITEMS = 500
MAX_WALK_DAYS = 3 * 366          # a schedule that cannot fit in three years is a mistake, not a plan


class DripError(ValueError):
    """The rule cannot produce a schedule; the message is shown to the person."""


def parse_rule(rule_config: dict) -> dict:
    """Check and normalise a rule. Raises DripError with a plain reason."""
    if not isinstance(rule_config, dict):
        raise DripError("The schedule rule is missing.")
    days = rule_config.get("days") or []
    if not isinstance(days, list) or not days:
        raise DripError("Choose at least one day of the week.")
    norm = []
    for d in days:
        k = str(d).strip()[:3].capitalize()
        if k not in DAYS:
            raise DripError("Days are Mon, Tue, Wed, Thu, Fri, Sat or Sun (got %r)." % d)
        if k not in norm:
            norm.append(k)
    times = rule_config.get("times") or []
    if not isinstance(times, list) or not times:
        raise DripError("Choose at least one time of day.")
    clean_times = []
    for t in times:
        if not _TIME.match(str(t).strip()):
            raise DripError("Times look like 12:00 (24-hour), got %r." % t)
        if str(t).strip() not in clean_times:
            clean_times.append(str(t).strip())
    clean_times.sort()
    start = rule_config.get("start_date")
    try:
        start_date = dt.date.fromisoformat(str(start)) if start else None
    except ValueError:
        raise DripError("The start date looks like 2026-11-01.")
    mpd = rule_config.get("max_per_day")
    if mpd is None:
        mpd = len(clean_times)
    try:
        mpd = int(mpd)
    except (TypeError, ValueError):
        raise DripError("Posts per day must be a whole number.")
    if mpd < 1:
        raise DripError("Posts per day must be at least 1.")
    return {"days": [d for d in DAYS if d in norm], "times": clean_times,
            "start_date": start_date, "max_per_day": min(mpd, len(clean_times))}


def zone(tz: str) -> ZoneInfo:
    try:
        return ZoneInfo(str(tz))
    except (ZoneInfoNotFoundError, ValueError):
        raise DripError("Unknown time zone %r." % tz)


def local_to_utc(day: dt.date, hhmm: str, tz: ZoneInfo) -> dt.datetime | None:
    """The UTC moment of a wall-clock time, or None when that time does not exist
    that day (the hour skipped when clocks go forward)."""
    h, m = map(int, hhmm.split(":"))
    local = dt.datetime(day.year, day.month, day.day, h, m, tzinfo=tz)      # fold=0: the first of a repeated hour
    utc = local.astimezone(dt.timezone.utc)
    back = utc.astimezone(tz)
    if (back.hour, back.minute) != (h, m):
        return None
    return utc


def _minute(t: dt.datetime) -> dt.datetime:
    if t.tzinfo is None:
        raise DripError("Times passed to the schedule must carry a time zone.")
    return t.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)


def build_schedule(n_items: int, rule_config: dict, tz: str, now_utc: dt.datetime,
                   existing_slots_utc=()) -> list[dt.datetime]:
    """One UTC time per item, in order. Deterministic for the same inputs."""
    if not isinstance(n_items, int) or n_items < 0:
        raise DripError("The number of posts must be zero or more.")
    if n_items > MAX_ITEMS:
        raise DripError("At most %d posts in one campaign." % MAX_ITEMS)
    rule = parse_rule(rule_config)
    if n_items == 0:
        return []
    zi = zone(tz)
    now = _minute(now_utc)
    taken = {_minute(t) for t in existing_slots_utc}
    today_local = now.astimezone(zi).date()
    day = max(rule["start_date"] or today_local, today_local)
    out: list[dt.datetime] = []
    for _ in range(MAX_WALK_DAYS):
        if DAYS[day.weekday()] in rule["days"]:
            used = 0
            for t in rule["times"]:
                if used >= rule["max_per_day"]:
                    break
                slot = local_to_utc(day, t, zi)
                if slot is None or slot <= now or slot in taken:
                    continue
                out.append(slot)
                used += 1
                if len(out) == n_items:
                    return out
        day += dt.timedelta(days=1)
    raise DripError("These days and times cannot fit %d posts within three years." % n_items)


def describe(n_items: int, rule_config: dict, slots: list[dt.datetime], tz: str) -> str:
    """'15 posts · Tue/Thu/Sat 12:00 · ends Sat 6 Dec' for the preview line."""
    rule = parse_rule(rule_config)
    head = "%d post%s · %s %s" % (n_items, "" if n_items == 1 else "s", "/".join(rule["days"]), ", ".join(rule["times"]))
    if not slots:
        return head
    last = slots[-1].astimezone(zone(tz))
    return "%s · ends %s %d %s" % (head, DAYS[last.weekday()], last.day, last.strftime("%b"))
