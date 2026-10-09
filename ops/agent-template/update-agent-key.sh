#!/bin/bash
# Give every WhatsApp agent the platform's current Gemini key.
#
# The key lives in ONE place: GEMINI_API_KEY in the platform .env
# (/opt/techdeva-platform/techdeva-platform/.env). The platform (Jarvis, AI
# pictures) reads it when the api restarts; each agent the template built got
# a copy in /docker/hermes-<key>/agent.env when it was made. After changing
# the key in the .env, run this once so the agents use it too:
#
#   bash ops/agent-template/update-agent-key.sh            update and restart them
#   bash ops/agent-template/update-agent-key.sh --dry-run  show what it would do
#
# Only agents the template made (techdeva.pairwatch label); never irfan /
# clinic / salon / workspace*. An agent paused for the free-trial limit gets
# the new key but stays paused. The key is never printed.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/../.." && pwd)"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
KEY_LINE=$(grep -E '^GEMINI_API_KEY=' "$PLATFORM/.env" | tail -1)
[ -n "${KEY_LINE#GEMINI_API_KEY=}" ] || { echo "GEMINI_API_KEY is not set in $PLATFORM/.env"; exit 1; }
VALUE=$(printf '%s' "${KEY_LINE#GEMINI_API_KEY=}" | sed -E 's/^["'"'"'](.*)["'"'"']$/\1/')
n=0
for c in $(docker ps -a --filter label=techdeva.pairwatch=true --format '{{.Names}}'); do
  key=$(docker inspect -f '{{index .Config.Labels "techdeva.agent"}}' "$c")
  case "$key" in ""|*irfan*|*clinic*|*salon*|*workspace*) echo "skip $c"; continue;; esac
  dir="/docker/hermes-$key"
  [ -f "$dir/agent.env" ] && [ -f "$dir/docker-compose.yml" ] || { echo "skip $key: no template files in $dir"; continue; }
  if grep -qxF "GEMINI_API_KEY=$VALUE" "$dir/agent.env"; then echo "$key: already has the current key"; continue; fi
  if [ $DRY = 1 ]; then echo "$key: would update the key and restart"; continue; fi
  tmp=$(mktemp "$dir/.agent.env.XXXX"); chmod 600 "$tmp"
  grep -v '^GEMINI_API_KEY=' "$dir/agent.env" > "$tmp"; printf 'GEMINI_API_KEY=%s\n' "$VALUE" >> "$tmp"
  mv "$tmp" "$dir/agent.env"
  if [ -f "/var/lib/techdeva/trial-paused/$key" ]; then echo "$key: key updated (paused for the trial limit, left paused)"; n=$((n+1)); continue; fi
  (cd "$dir" && docker compose up -d --force-recreate >/dev/null 2>&1) && echo "$key: key updated, restarted" || echo "$key: key updated, RESTART FAILED (cd $dir && docker compose up -d)"
  n=$((n+1))
done
echo "done: $n agent(s) updated"
