#!/usr/bin/env python3
"""Voice notes on/off in one agent's config.yaml (run as root by ai-keys-sync.sh).

  stt_config.py <config.yaml> on <groq whisper model>
  stt_config.py <config.yaml> off

on:  stt.enabled true, stt.provider groq, stt.groq.model <model>. The key is
     GROQ_API_KEY in the agent's agent.env (written by ai-keys-sync.sh). A voice
     note is transcribed and the agent answers what was said.
off: stt.enabled false (as T11 left it: no setup text to a customer).

Exit 0 when the file changed, 3 when it already was so. Owner and mode kept;
atomic; read back.
"""
import json
import os
import re
import sys

import yaml


def main(argv):
    if len(argv) < 3 or argv[2] not in ("on", "off") or (argv[2] == "on" and len(argv) != 4):
        sys.exit("usage: stt_config.py <config.yaml> on <model> | off")
    path, mode = argv[1], argv[2]
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    st = os.stat(path)
    before = json.dumps(cfg, sort_keys=True, default=str)
    stt = cfg.setdefault("stt", {})
    if mode == "on":
        model = argv[3]
        if not re.fullmatch(r"[A-Za-z0-9._:/@+-]{1,120}", model):
            sys.exit("stt_config: bad model name")
        stt["enabled"] = True
        stt["provider"] = "groq"
        stt.setdefault("groq", {})["model"] = model
    else:
        stt["enabled"] = False
    if json.dumps(cfg, sort_keys=True, default=str) == before:
        print("stt_config: unchanged (%s)" % mode)
        return 3
    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    back = (yaml.safe_load(open(tmp, encoding="utf-8")) or {}).get("stt") or {}
    if back.get("enabled") is not (mode == "on") or (mode == "on" and back.get("provider") != "groq"):
        os.unlink(tmp)
        sys.exit("stt_config: read-back mismatch, config not changed")
    os.replace(tmp, path)
    print("stt_config: voice notes %s%s" % (mode, (" (groq %s)" % argv[3]) if mode == "on" else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
