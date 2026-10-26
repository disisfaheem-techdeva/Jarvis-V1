#!/bin/bash
# API Manager sync (founder console > API Manager). Run by cron every minute as root:
#
#   * * * * * /opt/techdeva-platform/techdeva-platform/ops/agent-template/ai-keys-sync.sh >> /var/log/techdeva-ai-keys.log 2>&1
#
# 1. Apply: for each agent the founder gave another key, write that key into
#    /docker/hermes-<key>/agent.env (GEMINI_API_KEY for a Gemini key; TD_AI_KEY
#    plus a custom:tdkey model in the agent's config.yaml, via ai_key_config.py,
#    for any other provider; dated copies of both old files are kept) and
#    re-create that agent's compose project (agent +
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
while IFS=$'\t' read -r agent last4 provider base model value; do
  [ -n "$agent" ] || continue
  if protected "$agent"; then log "refuse $agent (protected)"; continue; fi
  dir="/docker/hermes-$agent"
  if [ ! -f "$dir/agent.env" ] || [ ! -f "$dir/docker-compose.yml" ]; then
    pyx applied "$agent" failed "" "no agent.env / compose file in $dir" </dev/null; log "fail $agent: no template files"; continue
  fi
  vol=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$agent-data" 2>/dev/null)
  cfg="$vol/config.yaml"; saved="$dir/ai-key-gemini-model.json"
  if [ -z "$vol" ] || [ ! -f "$cfg" ]; then
    pyx applied "$agent" failed "" "no config.yaml for this agent" </dev/null; log "fail $agent: no config.yaml"; continue
  fi
  ts=$(date +%Y%m%d_%H%M%S)
  cp -a "$dir/agent.env" "$dir/agent.env.bak-apikeys-$ts"
  cp -a "$cfg" "$dir/config.yaml.bak-apikeys-$ts"
  tmp=$(mktemp "$dir/.agent.env.XXXXXX")
  if [ "$provider" = "gemini" ]; then
    # Gemini: the key is GEMINI_API_KEY; the agent's own Gemini model chain comes back
    grep -v -e '^GEMINI_API_KEY=' -e '^TD_AI_KEY=' "$dir/agent.env" > "$tmp"
    printf 'GEMINI_API_KEY=%s\n' "$value" >> "$tmp"
    if [ "$model" = "-" ]; then
      cfgout=$(python3 "$HERE/ai_key_config.py" "$cfg" "$saved" gemini 2>&1)
    else
      cfgout=$(python3 "$HERE/ai_key_config.py" "$cfg" "$saved" gemini-model "$model" 2>&1)
    fi
  else
    # any other provider: TD_AI_KEY + config.yaml custom:tdkey; GEMINI_API_KEY stays for the fallback
    grep -v '^TD_AI_KEY=' "$dir/agent.env" > "$tmp"
    printf 'TD_AI_KEY=%s\n' "$value" >> "$tmp"
    cfgout=$(python3 "$HERE/ai_key_config.py" "$cfg" "$saved" custom "$base" "$model" 2>&1)
  fi
  if [ $? -ne 0 ]; then
    rm -f "$tmp"
    pyx applied "$agent" failed "" "config.yaml not changed: ${cfgout: -200}" </dev/null; log "fail $agent: $cfgout"; continue
  fi
  chmod 600 "$tmp"; mv "$tmp" "$dir/agent.env"
  # which variable holds the key the agent really runs on (read by the live / usage reports)
  if [ "$provider" = "gemini" ]; then echo GEMINI_API_KEY > "$dir/ai-key-var"; else echo TD_AI_KEY > "$dir/ai-key-var"; fi
  log "$agent: $provider ${model#-} ($cfgout)"
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

# ---- 1b. On/Off (founder console) and auto-hold resume: stop or start the agent's compose project
power=$(pyx power-pending 2>/dev/null) || { log "could not read on/off requests"; power=""; }
while IFS=$'\t' read -r agent want; do
  [ -n "$agent" ] || continue
  if protected "$agent"; then log "refuse on/off for $agent (protected)"; continue; fi
  dir="/docker/hermes-$agent"
  [ -f "$dir/docker-compose.yml" ] || { pyx power-applied "$agent" failed "no compose file" </dev/null; continue; }
  if [ "$want" = "off" ]; then
    (cd "$dir" && docker compose stop >/dev/null 2>&1 </dev/null) && r=ok || r=failed
  else
    (cd "$dir" && docker compose up -d >/dev/null 2>&1 </dev/null) && r=ok || r=failed
  fi
  pyx power-applied "$agent" "$r" </dev/null
  log "agent $agent switched $want: $r"
done <<< "$power"

# ---- 1c. voice notes (founder console > API Manager > Voice notes): one Groq key for every agent.
#      The agent gets GROQ_API_KEY and stt on (stt_config.py), the current td_guard (its outbound
#      filter keeps the raw transcript from the customer), and is re-created once. Off = the reverse.
stt=$(pyx stt-key 2>/dev/null) || stt="__error__"
if [ "$stt" = "__error__" ]; then log "could not read the voice-notes key"; else
  IFS=$'\t' read -r s4 smodel skey <<< "$stt"
  want_all="${s4:-off}"; [ "$want_all" = "off" ] || want_all="$s4 $smodel"
  only=$(pyx stt-agents 2>/dev/null) || only="__error__"
  for d in /docker/hermes-*/; do
    a=$(basename "$d"); a=${a#hermes-}
    protected "$a" && continue
    [ -f "$d/agent.env" ] && [ -f "$d/docker-compose.yml" ] || continue
    v=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$a-data" 2>/dev/null) || continue
    [ -f "$v/config.yaml" ] || continue
    [ "$only" = "__error__" ] && break
    want="$want_all"
    if [ -n "$only" ] && ! grep -qxF "$a" <<< "$only"; then want=off; fi
    have=$(cat "$d/ai-stt" 2>/dev/null || echo off)
    [ "$have" = "$want" ] && continue
    ts=$(date +%Y%m%d_%H%M%S)
    cp -a "$d/agent.env" "$d/agent.env.bak-stt-$ts"; cp -a "$v/config.yaml" "$d/config.yaml.bak-stt-$ts"
    if ! diff -rq "$HERE/td_guard" "$v/plugins/td_guard" >/dev/null 2>&1; then
      own=$(stat -c '%u:%g' "$v/config.yaml")
      mkdir -p "$v/plugins/td_guard"; cp "$HERE/td_guard/plugin.yaml" "$HERE/td_guard/__init__.py" "$v/plugins/td_guard/"
      chown -R "$own" "$v/plugins"; chmod 755 "$v/plugins" "$v/plugins/td_guard"; chmod 644 "$v/plugins/td_guard/"*
      python3 "$HERE/guard_config.py" "$v/config.yaml" >/dev/null 2>&1
    fi
    tmp=$(mktemp "$d/.agent.env.XXXXXX")
    grep -v '^GROQ_API_KEY=' "$d/agent.env" > "$tmp"
    if [ "$want" = "off" ]; then
      out=$(python3 "$HERE/stt_config.py" "$v/config.yaml" off 2>&1); rc=$?
    else
      printf 'GROQ_API_KEY=%s\n' "$skey" >> "$tmp"
      out=$(python3 "$HERE/stt_config.py" "$v/config.yaml" on "$smodel" 2>&1); rc=$?
    fi
    if [ $rc -ne 0 ] && [ $rc -ne 3 ]; then rm -f "$tmp"; log "voice notes for $a not changed: $out"; continue; fi
    chmod 600 "$tmp"; mv "$tmp" "$d/agent.env"; echo "$want" > "$d/ai-stt"
    if [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$a" 2>/dev/null)" = "true" ]; then
      (cd "$d" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null) || log "re-create failed for $a"
    fi
    log "voice notes for $a: ${want%% *} ($out)"
  done
fi
unset stt skey

# ---- 2. live keys
agents=""
for d in /docker/hermes-*/; do
  a=$(basename "$d"); a=${a#hermes-}
  protected "$a" && continue
  [ -f "$d/agent.env" ] || continue
  docker inspect "hermes-agent-$a" >/dev/null 2>&1 || continue
  agents="$agents $a"
  # an agent switched to another provider (saved Gemini chain present) runs on TD_AI_KEY
  var=GEMINI_API_KEY; [ -f "$d/ai-key-gemini-model.json" ] && var=TD_AI_KEY
  [ -s "$d/ai-key-var" ] && var=$(head -1 "$d/ai-key-var")
  k=$(grep -E "^$var=" "$d/agent.env" | tail -1 | cut -d= -f2- | tr -d "'\"")
  r=$([ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$a")" = "true" ] && echo 1 || echo 0)
  printf '%s\t%s\t%s\n' "$a" "${k: -4}" "$r"
done | pyx live || log "could not report live keys"

# ---- 3. usage, every 5 minutes
if [ $(( $(date +%-M) % 5 )) -eq 0 ]; then
  list=$(for d in /docker/hermes-*/; do a=$(basename "$d"); a=${a#hermes-}; protected "$a" || echo "$a"; done)
  python3 "$HERE/ai_usage_collect.py" $list | pyx usage || log "could not store usage"
  # 4. the last 15 minutes of each agent, counted once:
  #    auto-switch first (founder turns it on): move to the next tested key in rotation;
  #    then auto-hold: whoever still has no working key is paused until its quota resets
  recent=$(python3 "$HERE/ai_recent.py" $list)
  sw=$(printf '%s\n' "$recent" | pyx autoswitch 2>/dev/null) || log "auto-switch check failed"
  [ -n "${sw:-}" ] && log "auto-switch: $sw"
  held=$(printf '%s\n' "$recent" | pyx autohold 2>/dev/null) || log "auto-hold check failed"
  [ -n "${held:-}" ] && log "auto-hold: $held"
fi
