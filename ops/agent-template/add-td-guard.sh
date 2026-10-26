#!/bin/bash
# T13: on WhatsApp a photo is not answered, and no "💾 Self-improvement review"
# (or any background review) reaches a customer.
# T14: every outgoing WhatsApp text passes the reply guard (td_guard.clean_reply),
# the agent has HERMES_MAX_TOKENS=4096 and reasoning_effort low (no cut replies).
# new-agent.sh runs this on every new agent.
#
#   add-td-guard.sh <agent-key>
#
# Copies the td_guard plugin into the agent's data volume, sets its config.yaml
# (guard_config.py; a dated copy is kept beside the compose file) and, if the
# agent was running, re-creates its compose project (agent + pairctl). A
# stopped agent gets it on its next start. Never the hand-built or workspace agents.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A="${1:?agent key}"
case "$A" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) echo "refuse: $A is managed by hand"; exit 1;; esac
D="/docker/hermes-$A"
[ -f "$D/docker-compose.yml" ] || { echo "no $D/docker-compose.yml"; exit 1; }
V=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$A-data")
[ -f "$V/config.yaml" ] || { echo "no config.yaml in $V"; exit 1; }
OWN=$(stat -c '%u:%g' "$V/config.yaml")
TS=$(date +%Y%m%d_%H%M%S)
cp -a "$V/config.yaml" "$D/config.yaml.bak-guard-$TS"
changed=0
if ! diff -rq "$HERE/td_guard" "$V/plugins/td_guard" >/dev/null 2>&1; then
  mkdir -p "$V/plugins/td_guard"
  cp "$HERE/td_guard/plugin.yaml" "$HERE/td_guard/__init__.py" "$V/plugins/td_guard/"
  chown -R "$OWN" "$V/plugins"; chmod 755 "$V/plugins" "$V/plugins/td_guard"; chmod 644 "$V/plugins/td_guard/"*
  changed=1
fi
# T14: the shared workspace .env sets HERMES_MAX_TOKENS=800 (the clinic stack's setting); a
# Gemini 3.x reply thinks first and was cut at 800, continued, and sent joined. This
# agent's own agent.env (read after the shared file) gives it room.
if ! grep -qx 'HERMES_MAX_TOKENS=4096' "$D/agent.env"; then
  cp -a "$D/agent.env" "$D/agent.env.bak-guard-$TS"
  tmp=$(mktemp "$D/.agent.env.XXXXXX")
  grep -v '^HERMES_MAX_TOKENS=' "$D/agent.env" > "$tmp"; echo 'HERMES_MAX_TOKENS=4096' >> "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$D/agent.env"
  changed=1
fi
set +e; python3 "$HERE/guard_config.py" "$V/config.yaml"; rc=$?; set -e
[ $rc -eq 0 ] && changed=1
[ $rc -eq 0 ] || [ $rc -eq 3 ] || { echo "guard_config failed ($rc); copy at $D/config.yaml.bak-guard-$TS"; exit 1; }
if [ $changed -eq 0 ]; then echo "$A: already set up"; exit 0; fi
if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A" 2>/dev/null)" = "true" ]; then
  (cd "$D" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null)
  sleep 20
  [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A")" = "true" ] || { echo "$A: NOT running after re-create"; exit 1; }
  echo "$A: re-created with T13"
else
  echo "$A: set up; agent is stopped and loads it on its next start"
fi
