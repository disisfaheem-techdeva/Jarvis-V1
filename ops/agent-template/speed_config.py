#!/usr/bin/env python3
"""T17 speed settings for one agent (run as root by add-td-speed.sh).

  speed_config.py <config.yaml> [<ai-key-gemini-model.json>]

  * plugins.enabled gets td_speed, agent.reasoning_effort becomes "minimal"
    (td_speed sends Gemini 3 Flash Preview no thinking; other models: low);
  * the agent's saved Gemini chain (the API Manager's ai-key-gemini-model.json,
    which becomes the fallback behind the key's chosen model) is set to
    gemini-3.5-flash-lite only: gemini-2.5-flash-lite is gone for new keys
    (404) and gemini-3.8-flash answered 400/503 on 28 Oct 2026;
  * the running config's fallback_providers get the same, when the agent
    already runs gemini-3-flash-preview first.

Exit 0 when something changed, 3 when all of it was already so. Owner and
mode kept; atomic; read back.
"""
import json
import os
import sys

import yaml

PLUGIN = "td_speed"
FAST = "gemini-3-flash-preview"
FALLBACK = [{"provider": "gemini", "model": "gemini-3.5-flash-lite"}]


def _write_yaml(path, cfg, st):
    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    back = yaml.safe_load(open(tmp, encoding="utf-8"))
    if back != cfg:
        os.unlink(tmp)
        sys.exit("speed_config: read-back mismatch, config not changed")
    os.replace(tmp, path)


def main(path, saved=None):
    changed = False
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    st = os.stat(path)
    before = json.dumps(cfg, sort_keys=True, default=str)
    plugins = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    if PLUGIN not in enabled:
        plugins["enabled"] = enabled + [PLUGIN]
    cfg["plugins"] = plugins
    cfg.setdefault("agent", {})["reasoning_effort"] = "minimal"
    if (cfg.get("model") or {}).get("default") == FAST:
        cfg["fallback_providers"] = list(FALLBACK)
    if json.dumps(cfg, sort_keys=True, default=str) != before:
        _write_yaml(path, cfg, st)
        changed = True
    if saved and os.path.exists(saved):
        keep = json.load(open(saved, encoding="utf-8"))
        want = dict(keep, model={"provider": "gemini", "default": FALLBACK[0]["model"]}, fallback_providers=[])
        if want != keep:
            sst = os.stat(saved)
            tmp = saved + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(want, f)
            os.chown(tmp, sst.st_uid, sst.st_gid)
            os.chmod(tmp, sst.st_mode & 0o7777)
            if json.load(open(tmp, encoding="utf-8")) != want:
                os.unlink(tmp)
                sys.exit("speed_config: saved chain read-back mismatch")
            os.replace(tmp, saved)
            changed = True
    print("speed_config: %s" % ("td_speed on, reasoning minimal, fallback 3.5-flash-lite" if changed else "unchanged"))
    return 0 if changed else 3


if __name__ == "__main__":
    if len(sys.argv) not in (2, 3):
        sys.exit("usage: speed_config.py <config.yaml> [<ai-key-gemini-model.json>]")
    sys.exit(main(*sys.argv[1:]))
