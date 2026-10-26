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
SLIM_OFF = ("clarify", "memory", "session_search", "skills", "tts", "todo", "vision")


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
    # T15 slim input (26 Oct 2026): the model was sent ~10,700 tokens a message, ~12% of it
    # this business. Off: tools a WhatsApp sales agent never uses (71 coding/design skills,
    # memory writing, other chats' search, todo, clarify, speech, photos) and Hermes's
    # coding guidance. The business's knowledge and lessons live in SOUL.md and stay; the
    # memory files are not touched. Measured: ~2,100 tokens, same replies; fits Groq free.
    agent = cfg["agent"]
    dis = list(agent.get("disabled_toolsets") or [])
    for t in SLIM_OFF:
        if t not in dis:
            dis.append(t)
    agent["disabled_toolsets"] = dis
    agent["task_completion_guidance"] = False
    agent["parallel_tool_call_guidance"] = False
    agent["tool_use_enforcement"] = False
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
            or back["agent"]["reasoning_effort"] != "low"
            or not set(SLIM_OFF) <= set(back["agent"]["disabled_toolsets"])
            or back["agent"]["tool_use_enforcement"] is not False):
        os.unlink(tmp)
        sys.exit("guard_config: read-back mismatch, config not changed")
    os.replace(tmp, path)
    print("guard_config: td_guard on, review notes off, background review off")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: guard_config.py <config.yaml>")
    sys.exit(main(sys.argv[1]))
