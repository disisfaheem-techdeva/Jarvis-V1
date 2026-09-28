#!/bin/bash
# T6 pairwatch: restart an agent once after its WhatsApp number is paired.
#
# Why: the Hermes gateway starts, finds no WhatsApp session, disables WhatsApp
# and never retries. pairctl (the pairing sidecar) writes
# /opt/data/pairing/techdeva-paired.json when a scan completes. If that marker
# is newer than the agent's start time, this restarts the agent (and its
# pairctl, which shares the agent's network) so the new session is used.
#
# Scope: ONLY containers carrying the label techdeva.pairwatch=true, which the
# agent template sets. Hand-built agents (Irfan's, the clinic's) are never
# touched by this script.
#
# cron (as root):  * * * * * /opt/techdeva-platform/techdeva-platform/ops/agent-template/pairwatch.sh >> /var/log/techdeva-pairwatch.log 2>&1
#   pairwatch.sh --dry-run   shows what it would do
set -u
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
now() { date -u +%FT%TZ; }
for c in $(docker ps --filter label=techdeva.pairwatch=true --format '{{.Names}}'); do
  key=$(docker inspect -f '{{index .Config.Labels "techdeva.agent"}}' "$c")
  case "$key" in ""|irfan|clinic|salon) echo "$(now) skip $c: refusing key '$key'"; continue;; esac
  src=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{.Source}}{{end}}{{end}}' "$c")
  marker="$src/pairing/techdeva-paired.json"
  [ -n "$src" ] && [ -f "$marker" ] || continue
  started=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$c")" +%s 2>/dev/null || echo 0)
  paired=$(stat -c %Y "$marker")
  if [ "$paired" -gt "$started" ]; then
    if [ $DRY = 1 ]; then echo "$(now) would restart $c (paired $(date -u -d @$paired +%T) > started $(date -u -d @$started +%T))"; continue; fi
    echo "$(now) $c: paired after start -> restarting agent and pairctl-$key"
    docker restart "$c" >/dev/null && sleep 3 && docker restart "pairctl-$key" >/dev/null 2>&1
    echo "$(now) $c restarted: $(docker inspect -f '{{.State.Status}} since {{.State.StartedAt}}' "$c")"
  fi
done
