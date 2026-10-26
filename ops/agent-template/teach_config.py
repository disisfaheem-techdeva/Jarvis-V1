#!/usr/bin/env python3
"""Owner-only slash commands and the td_teach plugin in one agent's config.yaml.
Run as root on the host by add-teach.sh / teach-sync.sh.

  teach_config.py <config.yaml> <whatsapp session dir> <owner phone digits>

  * plugins.enabled gets td_teach (anything already listed is kept);
  * whatsapp.allow_admin_from = the owner's number in every form the bridge
    can report it: digits, digits@s.whatsapp.net, and the WhatsApp LID (with
    and without @lid) from the bridge's lid-mapping-<digits>.json. With this
    list set the gateway lets nobody else run slash commands (customers keep
    /help and /whoami), which also closes /new, /model and the rest to them;
  * whatsapp.user_allowed_commands = [] (nothing extra for customers).

Exit 0 when the file changed (the agent must be re-created to load it), 3 when
it already had all of this, anything else on error. Owner and mode are kept;
the write is atomic and read back.
"""
import json
import os
import re
import sys

import yaml

PLUGIN = "td_teach"


def aliases(session_dir, phone):
    out = [phone, phone + "@s.whatsapp.net"]
    try:
        with open(os.path.join(session_dir, "lid-mapping-%s.json" % phone), encoding="utf-8") as f:
            lid = str(json.load(f)).split("@")[0].split(":")[0]
        if re.fullmatch(r"\d{6,20}", lid):
            out += [lid, lid + "@lid"]
    except (OSError, ValueError):
        pass                      # the owner has not written to this number yet: phone forms only
    return out


def main(argv):
    if len(argv) != 4 or not re.fullmatch(r"\d{8,15}", argv[3]):
        sys.exit("usage: teach_config.py <config.yaml> <session dir> <owner phone digits>")
    path, session_dir, phone = argv[1:]
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

    wa = cfg.get("whatsapp") if isinstance(cfg.get("whatsapp"), dict) else {}
    wa["allow_admin_from"] = aliases(session_dir, phone)
    wa["user_allowed_commands"] = []
    cfg["whatsapp"] = wa

    if json.dumps(cfg, sort_keys=True, default=str) == before:
        print("teach_config: unchanged (%d owner forms)" % len(wa["allow_admin_from"]))
        return 3
    tmp = path + ".td-tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        yaml.safe_dump(cfg, f, sort_keys=False, allow_unicode=True)
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    back = yaml.safe_load(open(tmp, encoding="utf-8"))
    if back.get("whatsapp") != wa or PLUGIN not in back["plugins"]["enabled"]:
        os.unlink(tmp)
        sys.exit("teach_config: read-back mismatch, config not changed")
    os.replace(tmp, path)
    print("teach_config: td_teach on, %d owner forms (%s)" % (
        len(wa["allow_admin_from"]), "with LID" if len(wa["allow_admin_from"]) > 2 else "no LID yet"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
