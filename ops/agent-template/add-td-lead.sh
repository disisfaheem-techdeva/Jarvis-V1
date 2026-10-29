#!/bin/bash
# T19: the WhatsApp agent saves a customer as a lead. On 29 Oct 2026 a customer
# gave name, email, package and a call-back time, and the agent said "noted",
# but nothing reached the dashboard: template agents had no way to save a lead.
# td_lead gives the agent a save_lead tool; lead-sync.sh (cron) moves what it
# saves into the platform's Leads. new-agent.sh runs this on every new agent.
#
#   add-td-lead.sh <agent-key> [--no-restart]
#
# Dated copies go to /root/techdeva-backups/before-tdlead-<date>/. A running
# agent's compose project (agent + pairctl) is re-created so the plugin loads.
# Never the hand-built or workspace agents.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A="${1:?agent key}"; NORESTART="${2:-}"
case "$A" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) echo "refuse: $A is managed by hand"; exit 1;; esac
D="/docker/hermes-$A"
[ -f "$D/docker-compose.yml" ] || { echo "no $D/docker-compose.yml"; exit 1; }
V=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$A-data")
[ -f "$V/config.yaml" ] || { echo "no config.yaml in $V"; exit 1; }
OWN=$(stat -c '%u:%g' "$V/config.yaml")
B="/root/techdeva-backups/before-tdlead-$(date +%Y%m%d)"
mkdir -p "$B"
[ -f "$B/$A-config.yaml" ] || cp -a "$V/config.yaml" "$B/$A-config.yaml"
changed=0
if ! diff -rq --exclude=__pycache__ "$HERE/td_lead" "$V/plugins/td_lead" >/dev/null 2>&1; then
  mkdir -p "$V/plugins/td_lead"
  cp "$HERE/td_lead/plugin.yaml" "$HERE/td_lead/__init__.py" "$V/plugins/td_lead/"
  chown -R "$OWN" "$V/plugins"; chmod 755 "$V/plugins" "$V/plugins/td_lead"; chmod 644 "$V/plugins/td_lead/"*
  changed=1
fi
set +e; python3 "$HERE/lead_config.py" "$V/config.yaml"; rc=$?; set -e
[ $rc -eq 0 ] && changed=1
[ $rc -eq 0 ] || [ $rc -eq 3 ] || { echo "lead_config failed ($rc); copy at $B/$A-config.yaml"; exit 1; }
if [ $changed -eq 0 ]; then echo "$A: already set up"; exit 0; fi
if [ "$NORESTART" = "--no-restart" ]; then echo "$A: set up; loads on its next re-create"; exit 0; fi
if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A" 2>/dev/null)" = "true" ]; then
  (cd "$D" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null)
  sleep 30
  [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A")" = "true" ] || { echo "$A: NOT running after re-create"; exit 1; }
  echo "$A: re-created with T19 (td_lead)"
else
  echo "$A: set up; agent is stopped and loads it on its next start"
fi
