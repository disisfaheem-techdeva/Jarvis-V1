#!/usr/bin/env python3
"""Daily model-call counts per WhatsApp agent, read from each agent's own log.

Run on the host by ai-keys-sync.sh; prints one JSON line per (day, agent,
model) for the last 8 days, which `python -m app.ai_keys usage` stores in
ai_agent_usage. Counts only: no message text is read out or stored.

  ok            a model answered        "conversation_loop: API call #N: model=M"
  failed        a model call failed     "conversation_loop: API call failed"
  rate_limited  the provider said 429   "Gemini HTTP 429" / RESOURCE_EXHAUSTED
  overloaded    the provider said 503   "Gemini HTTP 503" / UNAVAILABLE

429/503 lines are counted once per failed request ("Streaming failed before
delivery", "Title generation failed"); a request the log does not tie to a
model is counted under model "".

Usage: ai_usage_collect.py <agent_key> [<agent_key> ...]
"""
import collections
import datetime
import glob
import json
import re
import sys

OK = re.compile(r"^(\d{4}-\d\d-\d\d) .*conversation_loop: API call #\d+: model=(\S+)")
FAILED = re.compile(r"^(\d{4}-\d\d-\d\d) .*conversation_loop: API call failed")
FAILED_MODEL = re.compile(r"\bmodel=([\w.\-/]+)")
HTTP = re.compile(r"^(\d{4}-\d\d-\d\d) .*(?:Streaming failed before delivery|Title generation failed|Non-retryable).*?HTTP (\d{3})")


def last4(agent):
    try:
        for line in open("/docker/hermes-%s/agent.env" % agent, encoding="utf-8"):
            if line.startswith("GEMINI_API_KEY="):
                v = line.split("=", 1)[1].strip().strip("'\"")
                return v[-4:] if v else None
    except OSError:
        return None
    return None


def collect(agent, since):
    counts = collections.defaultdict(lambda: {"ok": 0, "rate_limited": 0, "overloaded": 0, "failed": 0})
    for path in sorted(glob.glob("/var/lib/docker/volumes/hermes-%s-data/_data/logs/agent.log*" % agent)):
        try:
            fh = open(path, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if line[:10] < since:
                    continue
                m = OK.match(line)
                if m:
                    counts[(m.group(1), m.group(2))]["ok"] += 1
                    continue
                m = FAILED.match(line)
                if m:
                    mm = FAILED_MODEL.search(line)
                    counts[(m.group(1), mm.group(1) if mm else "")]["failed"] += 1
                    continue
                m = HTTP.match(line)
                if m:
                    code = m.group(2)
                    if code == "429":
                        counts[(m.group(1), "")]["rate_limited"] += 1
                    elif code == "503":
                        counts[(m.group(1), "")]["overloaded"] += 1
    k4 = last4(agent)
    for (day, model), c in sorted(counts.items()):
        print(json.dumps(dict(day=day, agent_key=agent, model=model, key_last4=k4, **c)))


if __name__ == "__main__":
    since = (datetime.date.today() - datetime.timedelta(days=7)).isoformat()
    for a in sys.argv[1:]:
        collect(a, since)
