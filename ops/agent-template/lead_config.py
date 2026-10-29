#!/usr/bin/env python3
"""T19 lead capture settings for one agent (run as root by add-td-lead.sh).

  lead_config.py <config.yaml>

  * plugins.enabled gets td_lead;
  * platform_toolsets.whatsapp gets the td_lead toolset (its save_lead tool),
    next to whatever it already has.

Exit 0 when something changed, 3 when all of it was already so. Owner and
mode kept; atomic; read back.
"""
import json
import os
import sys

import yaml

PLUGIN = "td_lead"


def main(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    st = os.stat(path)
    before = json.dumps(cfg, sort_keys=True, default=str)
    plugins = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    if PLUGIN not in enabled:
        plugins["enabled"] = enabled + [PLUGIN]
    cfg["plugins"] = plugins
    pt = cfg.get("platform_toolsets") if isinstance(cfg.get("platform_toolsets"), dict) else {}
    wa = pt.get("whatsapp") if isinstance(pt.get("whatsapp"), list) else ["hermes-whatsapp"]
    if PLUGIN not in wa:
        pt["whatsapp"] = wa + [PLUGIN]
    cfg["platform_toolsets"] = pt
    if json.dumps(cfg, sort_keys=True, default=str) == before:
        print("lead_config: unchanged")
        return 3
    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    if yaml.safe_load(open(tmp, encoding="utf-8")) != cfg:
        os.unlink(tmp)
        sys.exit("lead_config: read-back mismatch, config not changed")
    os.replace(tmp, path)
    print("lead_config: td_lead on, save_lead tool on WhatsApp")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: lead_config.py <config.yaml>")
    sys.exit(main(sys.argv[1]))
