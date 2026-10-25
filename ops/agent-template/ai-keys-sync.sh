#!/bin/bash
# API Manager sync (founder console > API Manager). Run by cron every minute as root:
#
#   * * * * * /opt/techdeva-platform/techdeva-platform/ops/agent-template/ai-keys-sync.sh >> /var/log/techdeva-ai-keys.log 2>&1
#
# 1. Apply: for each agent the founder gave another key, write that key into
#    /docker/hermes-<key>/agent.env (GEMINI_API_KEY only; a dated copy of the
#    old file is kept) and re-create that agent's compose project (agent +
#    pairctl together, so pairctl joins the new agent's network). A stopped
#    agent (e.g. paused for its trial) gets the key but stays stopped.
# 2. Live: report the last 4 characters of the key each agent really runs with.
# 3. Usage (every 5 minutes): count each agent's model calls from its log.
#
# Safety: one run at a time (flock); never touches irfan or the workspace/ofsd
# agents; the key goes from the api container to the agent.env through a pipe
# and is never printed or logged.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/../.." && pwd)"
exec 9>/run/techdeva-ai-keys.lock
flock -n 9 || exit 0
cd "$PLATFORM" || exit 1
log() { echo "$(date -u +%FT%TZ) $*"; }
pyx() { docker compose exec -T api python -m app.ai_keys "$@"; }
protected() { case "$1" in irfan|hermes-workspace*|workspace*|*ofsd*) return 0;; *) return 1;; esac; }

# ---- 1. apply pending choices
pending=$(pyx pending 2>/dev/null) || { log "could not read pending keys from the api"; pending=""; }
while IFS=$'\t' read -r agent last4 value; do
  [ -n "$agent" ] || continue
  if protected "$agent"; then log "refuse $agent (protected)"; continue; fi
  dir="/docker/hermes-$agent"
  if [ ! -f "$dir/agent.env" ] || [ ! -f "$dir/docker-compose.yml" ]; then
    pyx applied "$agent" failed "" "no agent.env / compose file in $dir" </dev/null; log "fail $agent: no template files"; continue
  fi
  cp -a "$dir/agent.env" "$dir/agent.env.bak-apikeys-$(date +%Y%m%d_%H%M%S)"
  tmp=$(mktemp "$dir/.agent.env.XXXXXX")
  grep -v '^GEMINI_API_KEY=' "$dir/agent.env" > "$tmp"
  printf 'GEMINI_API_KEY=%s\n' "$value" >> "$tmp"
  chmod 600 "$tmp"; mv "$tmp" "$dir/agent.env"
  if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$agent" 2>/dev/null)" = "true" ]; then
    if ! (cd "$dir" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null); then
      pyx applied "$agent" failed "" "re-create failed; previous agent.env kept as .bak" </dev/null
      log "fail $agent: re-create failed"; continue
    fi
    sleep 15
    if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$agent" 2>/dev/null)" != "true" ]; then
      pyx applied "$agent" failed "$last4" "agent is not running after the re-create" </dev/null
      log "fail $agent: not running after re-create"; continue
    fi
  fi
  pyx applied "$agent" ok "$last4" </dev/null
  log "applied key ...$last4 to $agent"
done <<< "$pending"
unset pending value

# ---- 2. live keys
agents=""
for d in /docker/hermes-*/; do
  a=$(basename "$d"); a=${a#hermes-}
  protected "$a" && continue
  [ -f "$d/agent.env" ] || continue
  docker inspect "hermes-agent-$a" >/dev/null 2>&1 || continue
  agents="$agents $a"
  k=$(grep -E '^GEMINI_API_KEY=' "$d/agent.env" | tail -1 | cut -d= -f2- | tr -d "'\"")
  r=$([ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$a")" = "true" ] && echo 1 || echo 0)
  printf '%s\t%s\t%s\n' "$a" "${k: -4}" "$r"
done | pyx live || log "could not report live keys"

# ---- 3. usage, every 5 minutes
if [ $(( $(date +%-M) % 5 )) -eq 0 ]; then
  list=$(for d in /docker/hermes-*/; do a=$(basename "$d"); a=${a#hermes-}; protected "$a" || echo "$a"; done)
  python3 "$HERE/ai_usage_collect.py" $list | pyx usage || log "could not store usage"
fi
