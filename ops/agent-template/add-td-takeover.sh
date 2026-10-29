#!/bin/bash
# T18: human takeover. A message from the business number that Hermes did not
# send (phone, WhatsApp Web, dashboard reply) pauses that chat for
# TD_TAKEOVER_MINUTES (default 1): the customer's messages do not reach Hermes
# and Hermes' replies to it are held (td_takeover.js, wired in by
# takeover_patch.py). Tested by Rafay on tech-deva-8e9f58, 29 Oct 2026.
# new-agent.sh runs this on every new agent.
#
#   add-td-takeover.sh <agent-key> [--no-restart]
#
# Dated copies go to /root/techdeva-backups/before-takeover-<date>/. A running
# agent's compose project (agent + pairctl) is re-created so the bridge loads
# it. Never the hand-built or workspace agents.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A="${1:?agent key}"; NORESTART="${2:-}"
case "$A" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) echo "refuse: $A is managed by hand"; exit 1;; esac
D="/docker/hermes-$A"
[ -f "$D/docker-compose.yml" ] || { echo "no $D/docker-compose.yml"; exit 1; }
V=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$A-data")
BR="$V/scripts/whatsapp-bridge"
[ -f "$BR/bridge.js" ] || { echo "no bridge.js in $BR"; exit 1; }
OWN=$(stat -c '%u:%g' "$BR/bridge.js")
B="/root/techdeva-backups/before-takeover-$(date +%Y%m%d)"
mkdir -p "$B"
[ -f "$B/$A-bridge.js" ] || cp -a "$BR/bridge.js" "$B/$A-bridge.js"
changed=0
if ! cmp -s "$HERE/td_takeover.js" "$BR/td_takeover.js"; then
  install -m 644 -o "${OWN%:*}" -g "${OWN#*:}" "$HERE/td_takeover.js" "$BR/td_takeover.js"
  changed=1
fi
set +e; python3 "$HERE/takeover_patch.py" "$BR/bridge.js"; rc=$?; set -e
[ $rc -eq 0 ] && changed=1
[ $rc -eq 0 ] || [ $rc -eq 3 ] || { echo "takeover_patch failed ($rc); copy in $B"; exit 1; }
if [ $changed -eq 0 ]; then echo "$A: already set up"; exit 0; fi
if [ "$NORESTART" = "--no-restart" ]; then echo "$A: set up; loads on its next re-create"; exit 0; fi
if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A" 2>/dev/null)" = "true" ]; then
  (cd "$D" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null)
  sleep 30
  [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A")" = "true" ] || { echo "$A: NOT running after re-create"; exit 1; }
  echo "$A: re-created with T18 (td_takeover)"
else
  echo "$A: set up; agent is stopped and loads it on its next start"
fi
