#!/bin/bash
# T12: the owner teaches the agent from their own WhatsApp number (/sikho, /haan, /nahi)
# and only the owner can run slash commands on it.
#
#   add-teach.sh <agent-key>
#
# Copies the td_teach plugin into the agent's data volume, sets plugins.enabled
# and whatsapp.allow_admin_from (the owner's number from WHATSAPP_HOME_CHANNEL,
# in phone and LID forms; teach_config.py) in its config.yaml, then re-creates
# the agent's compose project (agent + pairctl together) if it was running.
# Dated copies of config.yaml are kept beside the compose file. Never for the
# hand-built or workspace agents. Lessons reach the platform through
# teach-sync.sh (cron).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A="${1:?agent key}"
case "$A" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) echo "refuse: $A is managed by hand"; exit 1;; esac
D="/docker/hermes-$A"
[ -f "$D/docker-compose.yml" ] || { echo "no $D/docker-compose.yml"; exit 1; }
V=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$A-data")
[ -f "$V/config.yaml" ] || { echo "no config.yaml in $V"; exit 1; }
PHONE=$(grep -oE 'WHATSAPP_HOME_CHANNEL: *"?[0-9]+@' "$D/docker-compose.yml" | grep -oE '[0-9]+' | head -1 || true)
[ -n "$PHONE" ] || PHONE=$(docker exec "hermes-agent-$A" printenv WHATSAPP_HOME_CHANNEL 2>/dev/null | grep -oE '^[0-9]+' || true)
[ -n "$PHONE" ] || { echo "no owner number (WHATSAPP_HOME_CHANNEL) for $A"; exit 1; }
OWN=$(stat -c '%u:%g' "$V/config.yaml")

TS=$(date +%Y%m%d_%H%M%S)
cp -a "$V/config.yaml" "$D/config.yaml.bak-teach-$TS"
changed=0
if ! diff -rq "$HERE/td_teach" "$V/plugins/td_teach" >/dev/null 2>&1; then
  mkdir -p "$V/plugins/td_teach"
  cp "$HERE/td_teach/plugin.yaml" "$HERE/td_teach/__init__.py" "$V/plugins/td_teach/"
  chown -R "$OWN" "$V/plugins"; chmod 755 "$V/plugins" "$V/plugins/td_teach"; chmod 644 "$V/plugins/td_teach/"*
  changed=1
fi
set +e; python3 "$HERE/teach_config.py" "$V/config.yaml" "$V/whatsapp/session" "$PHONE"; rc=$?; set -e
[ $rc -eq 0 ] && changed=1
[ $rc -eq 0 ] || [ $rc -eq 3 ] || { echo "teach_config failed ($rc); config.yaml copy at $D/config.yaml.bak-teach-$TS"; exit 1; }

if [ $changed -eq 0 ]; then echo "$A: already set up"; exit 0; fi
if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A" 2>/dev/null)" = "true" ]; then
  (cd "$D" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null)
  sleep 20
  [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A")" = "true" ] || { echo "$A: NOT running after re-create"; exit 1; }
  echo "$A: re-created with td_teach"
else
  echo "$A: set up; agent is stopped and loads it on its next start"
fi
