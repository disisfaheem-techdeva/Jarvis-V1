#!/usr/bin/env python3
"""Point one agent's config.yaml at the model its API Manager key is for.
Run by ai-keys-sync.sh as root on the host, never by hand on a protected agent.

  ai_key_config.py <config.yaml> <saved.json> custom <base_url> <model>
      The agent answers with <model> at the OpenAI-compatible <base_url>, with
      the key from TD_AI_KEY in its agent.env. Its own Gemini model chain is
      saved to <saved.json> (first switch only) and kept as the fallback, so a
      failing paid key still falls back to the free Gemini key.
  ai_key_config.py <config.yaml> <saved.json> gemini
      Back to Gemini: the saved model chain is put back and <saved.json> removed.
      Nothing changes when there is no saved chain (the agent is on Gemini).

The file keeps its owner and mode (the agent runs as its own user), is written
atomically, and is read back; any mismatch exits non-zero with the old file in
place.
"""
import json
import os
import sys

import yaml

NAME = "tdkey"


def main(argv):
    if len(argv) < 4 or argv[3] not in ("custom", "gemini") or (argv[3] == "custom" and len(argv) != 6):
        sys.exit("usage: ai_key_config.py <config.yaml> <saved.json> custom <base_url> <model> | gemini")
    path, saved, mode = argv[1], argv[2], argv[3]
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    st = os.stat(path)
    if mode == "custom":
        base, model = argv[4].rstrip("/"), argv[5]
        if not base.startswith("https://"):
            sys.exit("ai_key_config: base_url must be https")
        if not os.path.exists(saved):
            keep = {"model": cfg.get("model") or {}, "fallback_providers": cfg.get("fallback_providers") or [],
                    "providers": cfg.get("providers") or {}}
            with open(saved + ".tmp", "w", encoding="utf-8") as f:
                json.dump(keep, f)
            os.chmod(saved + ".tmp", 0o600)
            os.replace(saved + ".tmp", saved)
        with open(saved, encoding="utf-8") as f:
            keep = json.load(f)
        gem = keep.get("model") or {}
        fallbacks = []
        if gem.get("provider") == "gemini" and gem.get("default"):
            fallbacks.append({"provider": "gemini", "model": gem["default"]})
        fallbacks += [x for x in keep.get("fallback_providers") or [] if x not in fallbacks]
        cfg["providers"] = {NAME: {"name": NAME, "base_url": base, "key_env": "TD_AI_KEY",
                                   "api_mode": "chat_completions", "model": model}}
        cfg["model"] = {"provider": "custom:" + NAME, "default": model}
        cfg["fallback_providers"] = fallbacks
    else:
        if not os.path.exists(saved):
            print("ai_key_config: already on gemini, nothing to do")
            return 0
        with open(saved, encoding="utf-8") as f:
            keep = json.load(f)
        cfg["model"] = keep.get("model") or {}
        cfg["fallback_providers"] = keep.get("fallback_providers") or []
        cfg["providers"] = keep.get("providers") or {}

    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    back = yaml.safe_load(open(tmp, encoding="utf-8"))
    if back.get("model") != cfg["model"] or back.get("fallback_providers") != cfg["fallback_providers"]:
        os.unlink(tmp)
        sys.exit("ai_key_config: read-back mismatch, config not changed")
    os.replace(tmp, path)
    if mode == "gemini":
        os.unlink(saved)
    print("ai_key_config: model=%s/%s fallbacks=%s" % (
        cfg["model"].get("provider"), cfg["model"].get("default"),
        ",".join(str(x.get("model")) for x in cfg["fallback_providers"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
