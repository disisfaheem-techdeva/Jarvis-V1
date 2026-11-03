#!/bin/bash
# T21 logoutwatch: notice when WhatsApp logs a template agent's number out.
#
# Why (3 Nov 2026): WhatsApp removed the linked device of three agents
# (stream error 401 "device_removed"). Their bridges kept starting, printing
# "Logged out" and dying, for up to 13 days, while whatsapp_connections still
# said "connected": the dashboard showed nothing wrong and no owner was told.
# pairwatch.sh only acts AFTER a new pairing, and pairctl's /pair/status is the
# pairing state ('idle'), not the login, so nothing in the platform could see it.
#
# Every 5 minutes, for each template agent (label techdeva.pairwatch=true):
#   logged out  = the newest of "WhatsApp connected!" / "Logged out" in its
#                 bridge.log is "Logged out" AND its bridge /health is not connected
#   then        = its whatsapp_connections row goes connected -> disconnected
#                 (disconnected_at, last_error) with an audit row. Nothing else:
#                 no message, no restart, the old login is left where it is.
# The support agent (app/support_agent.py) then shows the owner "WhatsApp ...
# is logged out, scan the QR code"; pairing again sets the row back to connected.
#
# cron (as root):  */5 * * * * /opt/techdeva-platform/techdeva-platform/ops/agent-template/logoutwatch.sh >> /var/log/techdeva-logoutwatch.log 2>&1
#   logoutwatch.sh --dry-run   shows what it would mark, changes nothing
#   logoutwatch.sh --check     prints every agent's login state, changes nothing
set -u
MODE="${1:-}"
DB="${DB_CONTAINER:-techdeva-platform-db-1}"
now() { date -u +%FT%TZ; }
psqlc() { docker exec "$DB" psql -v ON_ERROR_STOP=1 -U postgres -d techdeva -tAc "$1" </dev/null; }

for c in $(docker ps --filter label=techdeva.pairwatch=true --format '{{.Names}}' | grep '^hermes-agent-'); do
  key=$(docker inspect -f '{{index .Config.Labels "techdeva.agent"}}' "$c")
  case "$key" in ""|*irfan*|*clinic*|*workspace*) continue;; esac
  [[ "$key" =~ ^[a-z0-9-]+$ ]] || { echo "$(now) skip $c: odd key '$key'"; continue; }
  last=$(docker exec "$c" sh -c 'f=/opt/data/whatsapp/bridge.log; [ -f "$f" ] && tail -c 200000 "$f" | grep -aoE "WhatsApp connected!|Logged out" | tail -1' 2>/dev/null)
  health=$(timeout 20 docker exec "$c" node -e "fetch('http://127.0.0.1:3000/health').then(r=>r.text()).then(t=>console.log(t)).catch(()=>console.log('unreachable'))" 2>/dev/null)
  linked=no; grep -q '"status":"connected"' <<<"$health" && linked=yes
  if [ "$MODE" = "--check" ]; then
    echo "$key: last='${last:-no log}' bridge_linked=$linked"; continue
  fi
  [ "$last" = "Logged out" ] && [ "$linked" = no ] || continue
  row=$(psqlc "SELECT id FROM whatsapp_connections WHERE agent_key = '$key' AND status = 'connected'")
  [ -n "$row" ] || continue
  if [ "$MODE" = "--dry-run" ]; then echo "$(now) would mark $key logged out"; continue; fi
  psqlc "BEGIN;
    UPDATE whatsapp_connections SET status = 'disconnected', disconnected_at = now(), updated_at = now(),
           last_error = 'WhatsApp logged this device out; scan the QR again'
     WHERE id = '$row' AND status = 'connected';
    INSERT INTO executions (tenant_id, action, resource, resource_id, actor, actor_type, status, detail)
    SELECT tenant_id, 'whatsapp.marked_logged_out', 'whatsapp_connections', id::text, 'logoutwatch', 'agent', 'ok',
           jsonb_build_object('agent', agent_key, 'why', 'bridge.log says Logged out and the bridge is not linked')
      FROM whatsapp_connections WHERE id = '$row';
    COMMIT;" >/dev/null && echo "$(now) marked $key logged out" || echo "$(now) FAILED to mark $key"
done
