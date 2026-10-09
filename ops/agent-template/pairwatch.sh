#!/bin/bash
# T6 pairwatch: switch WhatsApp on, and restart an agent once, after its
# WhatsApp number is paired.
#
# WhatsApp on (Oct 2026): the gateway only starts its WhatsApp bridge when
# WHATSAPP_ENABLED=true, and the template never set it (the shared env does not
# reach the agents), so a paired agent stayed silent: Networkometis answered no
# one for 35 hours. It is switched on only once the number is paired, never
# before, so the gateway can never compete with pairctl's pairing bridge for the
# login folder. It goes in the agent's own agent.env and the agent is recreated
# with docker compose (a plain restart does not reload env files).
#
# Restart: the Hermes gateway starts, finds no WhatsApp session, disables WhatsApp
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
set -f   # no filename globbing: WHATSAPP_ALLOWED_USERS=* must stay literal
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
now() { date -u +%FT%TZ; }
# What a paired template agent needs to answer customers (each found missing
# on the live server, 4 Oct 2026).
# Busy settings (5 Oct 2026): a customer's second message while the agent is still
# answering used to interrupt it and send "Interrupting current task (iteration 2/60)"
# plus a "/busy queue" tip to the customer. Queue it instead, and send no notice.
REQUIRED="WHATSAPP_ENABLED=true WHATSAPP_MODE=bot WHATSAPP_ALLOWED_USERS=* HERMES_GATEWAY_BUSY_INPUT_MODE=queue HERMES_GATEWAY_BUSY_ACK_ENABLED=false"
for c in $(docker ps --filter label=techdeva.pairwatch=true --format '{{.Names}}' | grep '^hermes-agent-'); do
  key=$(docker inspect -f '{{index .Config.Labels "techdeva.agent"}}' "$c")
  case "$key" in ""|*irfan*|*clinic*|*salon*|*workspace*) echo "$(now) skip $c: refusing key '$key'"; continue;; esac
  src=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/opt/data"}}{{.Source}}{{end}}{{end}}' "$c")
  marker="$src/pairing/techdeva-paired.json"
  [ -n "$src" ] && [ -f "$marker" ] || continue
  # paired: the bridge packages must carry the gateway's "already installed"
  # stamp, or it tries npm install inside the agent, which fails (agents built
  # before template bridge-deps-v2). Written on the host into the agent's volume.
  bdir="$src/scripts/whatsapp-bridge"
  if [ -f "$bdir/package.json" ] && [ -d "$bdir/node_modules" ]; then
    want=$(sha256sum "$bdir/package.json" | cut -c1-16)
    if [ "$(cat "$bdir/node_modules/.hermes-pkg-hash" 2>/dev/null)" != "$want" ]; then
      if [ $DRY = 1 ]; then echo "$(now) would stamp the WhatsApp bridge packages of $c"
      else
        chmod u+w "$bdir/node_modules" 2>/dev/null
        printf '%s\n' "$want" > "$bdir/node_modules/.hermes-pkg-hash" \
          && chown --reference="$bdir/package.json" "$bdir/node_modules/.hermes-pkg-hash" \
          && echo "$(now) $c: bridge packages stamped ($want)" || echo "$(now) $c: could not stamp the bridge packages"
      fi
    fi
  fi
  # paired: WhatsApp must be on, in bot mode. The bridge defaults to
  # WHATSAPP_MODE=self-chat, which answers only the phone's owner and drops every
  # customer ("self_chat_mode_rejects_non_self", Networkometis 4 Oct 2026).
  envnow=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$c")
  # It must also let every sender through: the bridge's own allow-list is empty
  # by default, which means nobody ("allowlist_mismatch"; the gateway's
  # *_ALLOW_ALL_USERS switches are not read by the bridge).
  need=""; for kv in $REQUIRED; do grep -qxF "$kv" <<<"$envnow" || need="$need $kv"; done
  if [ -n "$need" ]; then
    dir=$(docker inspect -f '{{index .Config.Labels "com.docker.compose.project.working_dir"}}' "$c" 2>/dev/null)
    [ -n "$dir" ] || dir="/docker/hermes-$key"
    if [ ! -f "$dir/agent.env" ] || [ ! -f "$dir/docker-compose.yml" ]; then echo "$(now) $c: paired but WhatsApp off, and no template files in $dir: left alone"; continue; fi
    if [ $DRY = 1 ]; then echo "$(now) would set$need for $c and recreate it"; continue; fi
    for kv in $REQUIRED; do
      if ! grep -qxF "$kv" "$dir/agent.env"; then
        sed -i "/^${kv%%=*}=/d" "$dir/agent.env" && echo "$kv" >> "$dir/agent.env"
      fi
    done
    chmod 600 "$dir/agent.env"
    echo "$(now) $c: paired, missing$need -> set, recreating"
    (cd "$dir" && docker compose up -d --force-recreate >/dev/null 2>&1) \
      && echo "$(now) $c recreated: $(docker inspect -f '{{.State.Status}} since {{.State.StartedAt}}' "$c" 2>/dev/null)" \
      || echo "$(now) $c: RECREATE FAILED (cd $dir && docker compose up -d)"
    continue
  fi
  started=$(date -d "$(docker inspect -f '{{.State.StartedAt}}' "$c")" +%s 2>/dev/null || echo 0)
  paired=$(stat -c %Y "$marker")
  if [ "$paired" -gt "$started" ]; then
    if [ $DRY = 1 ]; then echo "$(now) would restart $c (paired $(date -u -d @$paired +%T) > started $(date -u -d @$started +%T))"; continue; fi
    echo "$(now) $c: paired after start -> restarting agent and pairctl-$key"
    docker restart "$c" >/dev/null && sleep 3 && docker restart "pairctl-$key" >/dev/null 2>&1
    echo "$(now) $c restarted: $(docker inspect -f '{{.State.Status}} since {{.State.StartedAt}}' "$c")"
  fi
done
