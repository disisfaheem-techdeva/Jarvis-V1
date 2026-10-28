#!/usr/bin/env python3
"""T16: plugins.enabled gets td_memory in one agent's config.yaml (run as root by add-td-memory.sh).

  memory_config.py <config.yaml>

Nothing else in the file changes. Exit 0 when the file changed (re-create the
agent), 3 when td_memory was already on. Owner and mode kept; atomic; read back.
"""
import os
import sys

import yaml

PLUGIN = "td_memory"


def main(path):
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    st = os.stat(path)
    plugins = cfg.get("plugins") if isinstance(cfg.get("plugins"), dict) else {}
    enabled = plugins.get("enabled") if isinstance(plugins.get("enabled"), list) else []
    if PLUGIN in enabled:
        print("memory_config: unchanged")
        return 3
    plugins["enabled"] = enabled + [PLUGIN]
    cfg["plugins"] = plugins
    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    back = yaml.safe_load(open(tmp, encoding="utf-8"))
    rest = {k: v for k, v in back.items() if k != "plugins"}
    if PLUGIN not in back["plugins"]["enabled"] or rest != {k: v for k, v in cfg.items() if k != "plugins"}:
        os.unlink(tmp)
        sys.exit("memory_config: read-back mismatch, config not changed")
    os.replace(tmp, path)
    print("memory_config: td_memory on")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: memory_config.py <config.yaml>")
    sys.exit(main(sys.argv[1]))
