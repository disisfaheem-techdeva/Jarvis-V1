#!/bin/bash
# T16: the agent remembers a returning WhatsApp customer (td_memory: name, when
# they last wrote, their last few exchanges), across the daily session reset.
# new-agent.sh runs this on every new agent.
#
#   add-td-memory.sh <agent-key>
#
# Copies the td_memory plugin into the agent's data volume, adds it to
# plugins.enabled in config.yaml (memory_config.py; a dated copy is kept in
# /root/techdeva-backups/before-tdmemory-<date>/) and, if the agent was running,
# re-creates its compose project (agent + pairctl). A stopped agent gets it on
# its next start. Never the hand-built or workspace agents. SOUL.md and the
# memory files are not touched; the plugin keeps its own td_memory/contacts.db.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
A="${1:?agent key}"
case "$A" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) echo "refuse: $A is managed by hand"; exit 1;; esac
D="/docker/hermes-$A"
[ -f "$D/docker-compose.yml" ] || { echo "no $D/docker-compose.yml"; exit 1; }
V=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$A-data")
[ -f "$V/config.yaml" ] || { echo "no config.yaml in $V"; exit 1; }
OWN=$(stat -c '%u:%g' "$V/config.yaml")
B="/root/techdeva-backups/before-tdmemory-$(date +%Y%m%d)"
mkdir -p "$B"
[ -f "$B/$A-config.yaml" ] || cp -a "$V/config.yaml" "$B/$A-config.yaml"
changed=0
if ! diff -rq "$HERE/td_memory" "$V/plugins/td_memory" >/dev/null 2>&1; then
  mkdir -p "$V/plugins/td_memory"
  cp "$HERE/td_memory/plugin.yaml" "$HERE/td_memory/__init__.py" "$V/plugins/td_memory/"
  chown -R "$OWN" "$V/plugins"; chmod 755 "$V/plugins" "$V/plugins/td_memory"; chmod 644 "$V/plugins/td_memory/"*
  changed=1
fi
set +e; python3 "$HERE/memory_config.py" "$V/config.yaml"; rc=$?; set -e
[ $rc -eq 0 ] && changed=1
[ $rc -eq 0 ] || [ $rc -eq 3 ] || { echo "memory_config failed ($rc); copy at $B/$A-config.yaml"; exit 1; }
if [ $changed -eq 0 ]; then echo "$A: already set up"; exit 0; fi
if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A" 2>/dev/null)" = "true" ]; then
  (cd "$D" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null)
  sleep 20
  [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$A")" = "true" ] || { echo "$A: NOT running after re-create"; exit 1; }
  echo "$A: re-created with T16 (td_memory)"
else
  echo "$A: set up; agent is stopped and loads it on its next start"
fi
