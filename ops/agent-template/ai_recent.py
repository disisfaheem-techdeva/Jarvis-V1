#!/usr/bin/env python3
"""The last 15 minutes of each agent's model calls, for the auto-switcher.
Host side, stdlib only; run by ai-keys-sync.sh every 5 minutes.

    ai_recent.py <agent-key> ...   -> one JSON line per agent:
        {"agent_key", "ok": answered calls, "limited": calls refused for a limit
         (HTTP 429 / RESOURCE_EXHAUSTED / 413 too large for the key's per-minute
         allowance), "live_last4": the key the agent runs on}

Counts only; never a message.
"""
import datetime
import json
import os
import re
import sys

WINDOW = datetime.timedelta(minutes=15)
STAMP = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)")
OK = re.compile(r"conversation_loop: API call #\d+: model=")
LIMITED = re.compile(r"API call failed.*?(?:HTTP 429|HTTP 413|RESOURCE_EXHAUSTED)")


def live_last4(agent):
    var = "TD_AI_KEY=" if os.path.exists("/docker/hermes-%s/ai-key-gemini-model.json" % agent) else "GEMINI_API_KEY="
    try:
        for line in open("/docker/hermes-%s/agent.env" % agent, encoding="utf-8"):
            if line.startswith(var):
                v = line.split("=", 1)[1].strip().strip("'\"")
                return v[-4:] if v else None
    except OSError:
        return None


def main(agents):
    since = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - WINDOW
    for agent in agents:
        path = "/var/lib/docker/volumes/hermes-%s-data/_data/logs/agent.log" % agent
        ok = limited = 0
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                f.seek(max(0, os.path.getsize(path) - 4_000_000))
                for line in f:
                    m = STAMP.match(line)
                    if not m or datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S") < since:
                        continue
                    if OK.search(line):
                        ok += 1
                    elif LIMITED.search(line):
                        limited += 1
        except OSError:
            continue
        print(json.dumps({"agent_key": agent, "ok": ok, "limited": limited, "live_last4": live_last4(agent)}))


if __name__ == "__main__":
    main(sys.argv[1:])
