#!/usr/bin/env python3
"""T13 settings in one agent's config.yaml (run as root by add-td-guard.sh).

  guard_config.py <config.yaml>

  * plugins.enabled gets td_guard (a WhatsApp photo is not answered);
  * display.memory_notifications: off -- the "💾 Self-improvement review: User
    profile updated" line reached a customer on 26 Oct 2026;
  * memory.nudge_interval: 0 and skills.creation_nudge_interval: 0 -- no
    background review at all: no extra model call every few turns, and no
    customer's words written into the agent's one shared memory or skills.

Exit 0 when the file changed (re-create the agent), 3 when it already had all
of this. Owner and mode kept; atomic; read back.
"""
import json
import os
import sys

import yaml

PLUGIN = "td_guard"


def main(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    st = os.stat(path)
    before = json.dumps(cfg, sort_keys=True, default=str)
    plugins = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    if PLUGIN not in enabled:
        enabled = enabled + [PLUGIN]
    plugins["enabled"] = enabled
    cfg["plugins"] = plugins
    cfg.setdefault("display", {})["memory_notifications"] = "off"
    cfg.setdefault("memory", {})["nudge_interval"] = 0
    cfg.setdefault("skills", {})["creation_nudge_interval"] = 0
    # T14: a sales reply needs little thinking; at "medium" Gemini 3.x spent most of
    # the shared 800-token output cap thinking and the reply was cut and continued
    cfg.setdefault("agent", {})["reasoning_effort"] = "low"
    if json.dumps(cfg, sort_keys=True, default=str) == before:
        print("guard_config: unchanged")
        return 3
    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    back = yaml.safe_load(open(tmp, encoding="utf-8"))
    if (PLUGIN not in back["plugins"]["enabled"] or back["display"]["memory_notifications"] != "off"
            or back["memory"]["nudge_interval"] != 0 or back["skills"]["creation_nudge_interval"] != 0
            or back["agent"]["reasoning_effort"] != "low"):
        os.unlink(tmp)
        sys.exit("guard_config: read-back mismatch, config not changed")
    os.replace(tmp, path)
    print("guard_config: td_guard on, review notes off, background review off")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: guard_config.py <config.yaml>")
    sys.exit(main(sys.argv[1]))
