#!/bin/bash
# T17: fast replies. On 28 Oct 2026 gemini-3.5-flash-lite took 11-46 s a reply;
# gemini-3-flash-preview with no thinking answered in ~1.1 s. The model itself
# comes from the API Manager (Default key "Model"); this sets the rest:
# the td_speed plugin, reasoning_effort minimal, and gemini-3.5-flash-lite as
# the only fallback (speed_config.py). new-agent.sh runs this on every new agent.
#
#   add-td-speed.sh <agent-key> [--no-restart]
#
# Dated copies go to /root/techdeva-backups/before-tdspeed-<date>/. With
# --no-restart the agent is left running (ai-keys-sync.sh re-creates it when
# its key assignment is applied); otherwise a running agent's compose project
# (agent + pairctl) is re-created. Never the hand-built or workspace agents.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A="${1:?agent key}"; NORESTART="${2:-}"
case "$A" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) echo "refuse: $A is managed by hand"; exit 1;; esac
D="/docker/hermes-$A"
[ -f "$D/docker-compose.yml" ] || { echo "no $D/docker-compose.yml"; exit 1; }
V=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$A-data")
[ -f "$V/config.yaml" ] || { echo "no config.yaml in $V"; exit 1; }
OWN=$(stat -c '%u:%g' "$V/config.yaml")
B="/root/techdeva-backups/before-tdspeed-$(date +%Y%m%d)"
mkdir -p "$B"
[ -f "$B/$A-config.yaml" ] || cp -a "$V/config.yaml" "$B/$A-config.yaml"
[ -f "$D/ai-key-gemini-model.json" ] && { [ -f "$B/$A-ai-key-gemini-model.json" ] || cp -a "$D/ai-key-gemini-model.json" "$B/$A-ai-key-gemini-model.json"; }
changed=0
if ! diff -rq "$HERE/td_speed" "$V/plugins/td_speed" >/dev/null 2>&1; then
  mkdir -p "$V/plugins/td_speed"
  cp "$HERE/td_speed/plugin.yaml" "$HERE/td_speed/__init__.py" "$V/plugins/td_speed/"
  chown -R "$OWN" "$V/plugins"; chmod 755 "$V/plugins" "$V/plugins/td_speed"; chmod 644 "$V/plugins/td_speed/"*
  changed=1
fi
set +e; python3 "$HERE/speed_config.py" "$V/config.yaml" "$D/ai-key-gemini-model.json"; rc=$?; set -e
[ $rc -eq 0 ] && changed=1
[ $rc -eq 0 ] || [ $rc -eq 3 ] || { echo "speed_config failed ($rc); copies in $B"; exit 1; }
if [ $changed -eq 0 ]; then echo "$A: already set up"; exit 0; fi
if [ "$NORESTART" = "--no-restart" ]; then echo "$A: set up; loads on its next re-create"; exit 0; fi
if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A" 2>/dev/null)" = "true" ]; then
  (cd "$D" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null)
  sleep 20
  [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A")" = "true" ] || { echo "$A: NOT running after re-create"; exit 1; }
  echo "$A: re-created with T17 (td_speed)"
else
  echo "$A: set up; agent is stopped and loads it on its next start"
fi
