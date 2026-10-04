#!/bin/bash
# Health monitor, every 5 minutes (root cron runs the copy at /opt/techdeva-monitor.sh).
# Alerts when something breaks, when a NEW problem appears while others are
# still open, once a day while a problem stays unfixed, and on recovery.
# An alert counts as sent only when the notifier really delivered it; until then
# it is retried every 30 minutes and an incident says alerts are not arriving.
#
#   techdeva-monitor.sh               normal run (cron)
#   techdeva-monitor.sh --dry-run     run every check, print the problems, change nothing, send nothing
#   techdeva-monitor.sh --test-alert  send one test message and say whether it was delivered
set -uo pipefail
STATE=/var/lib/techdeva-monitor/state
ALERT=/opt/techdeva-alert.sh          # pluggable notifier; created separately
PLATFORM=/opt/techdeva-platform/techdeva-platform
MODE="${1:-}"
P=()   # problems
mkdir -p "$(dirname "$STATE")"

# deliver <message>: true only if the notifier really sent it. The notifier
# prints "alert http=<code> <reply>"; the alert service has answered HTTP 201
# with "ERROR: apikey can not be empty" (Oct 2026), so a 2xx alone is not proof.
deliver() {
  local out; out=$("$ALERT" "$1" 2>&1); echo "$out"
  grep -q 'http=2' <<<"$out" && ! grep -qi 'error' <<<"$out"
}
if [ "$MODE" = "--test-alert" ]; then
  [ -x "$ALERT" ] || { echo "no notifier at $ALERT"; exit 1; }
  if deliver "TechDeva test alert $(date -u +%H:%MZ): if you read this on WhatsApp, alerts work."; then
    echo "DELIVERED"; exit 0; else echo "NOT DELIVERED (see the reply above)"; exit 1; fi
fi

# 1. platform API through the public route
curl -sf -m 15 https://api.techdeva.net/health | grep -q '"ok":true' || P+=("API /health not ok")

# 2. containers running
for c in techdeva-platform-api-1 techdeva-platform-worker-1 techdeva-platform-db-1 \
         techdeva-platform-redis-1 hermes-workspace-ofsd-hermes-agent-clinic-1; do
  [ "$(docker inspect -f '{{.State.Running}}' $c 2>/dev/null)" = "true" ] || P+=("container down: $c")
done

# 3. the exact failure that ran 22 days undetected, and recurred twice last night
ERRS=$(docker logs --since 15m techdeva-platform-worker-1 2>&1 | grep -ci 'error' || true)
[ "${ERRS:-0}" -gt 5 ] && P+=("worker logging $ERRS errors in 15m")

# 4. redis must be master, not a replica (SLAVEOF hijack signature)
R=$(docker exec techdeva-platform-redis-1 redis-cli info replication 2>/dev/null | grep -o 'role:[a-z]*')
[ "$R" = "role:master" ] || P+=("redis $R (expected master)")

# 5. postgres accepting connections
docker exec techdeva-platform-db-1 sh -c 'pg_isready -U "$POSTGRES_USER" -q' 2>/dev/null || P+=("postgres not ready")

# 6. clinic must be serving
systemctl is-active --quiet beauty-dashboard || P+=("clinic service not active")
curl -sf -m 15 -o /dev/null https://crm.techdeva.net/login || P+=("clinic CRM not responding")

# 7. disk
D=$(df --output=pcent / | tail -1 | tr -dc '0-9')
[ "$D" -ge 85 ] && P+=("disk ${D}% full")

# 8. certificates
for h in api.techdeva.net crm.techdeva.net portal.techdeva.net; do
  E=$(echo | timeout 15 openssl s_client -servername $h -connect $h:443 2>/dev/null \
      | openssl x509 -noout -enddate 2>/dev/null | cut -d= -f2)
  [ -n "$E" ] || { P+=("$h: no certificate"); continue; }
  DAYS=$(( ( $(date -d "$E" +%s) - $(date +%s) ) / 86400 ))
  [ "$DAYS" -lt 14 ] && P+=("$h cert expires in ${DAYS}d")
done

# 9. backups must be fresh
NEW=$(find /opt/backups/daily -name 'techdeva_*.dump' -mtime -2 2>/dev/null | wc -l)
[ "$NEW" -eq 0 ] && P+=("no platform backup in 48h")

# 10. the AI workforce must be able to think. This is the check that was
#     missing: the model account ran dry and the agents went quiet for weeks
#     while every other check here kept reporting all clear.
AIH=$( (cd /opt/techdeva-platform/techdeva-platform && \
        docker compose exec -T api python -c \
        "from app.ai import health; h=health(); print(h['status'], h['usable'])" 2>/dev/null) || echo "unreachable 0" )
[ "${AIH%% *}" = "ok" ] || P+=("AI workforce degraded ($AIH)")

# Working but not on first choice is not an emergency and should not wake
# anyone -- it is still worth saying out loud in the product, because "slower
# and a bit dumber than usual" is exactly the kind of thing a client notices
# and nobody ever explains.
AIFIRST=$( (cd /opt/techdeva-platform/techdeva-platform && \
            docker compose exec -T api python -c \
            "from app.ai import health; c=health()['chain']; print(c[0]['state'] if c else 'none', (c[0].get('reason') or '-') if c else '-')" 2>/dev/null) || echo "unknown -" )
INC=$PLATFORM/scripts/incident.sh
if [ -x "$INC" ] && [ "$MODE" != "--dry-run" ]; then
  if [ "${AIFIRST%% *}" = "resting" ]; then
    "$INC" open ai.fallback warn "AI is running on a fallback model" \
           "First choice is resting (${AIFIRST#* }). Work continues on the next model in the chain and may be slower." || true
  else
    "$INC" clear ai.fallback || true
  fi
fi

# 11. WhatsApp agents the platform built: container up, WhatsApp still linked.
#     Read-only. Skips the clinic's hand-built agents (they have their own
#     watchdog), agents paused for the free-trial limit, and numbers never linked.
PSQL() { (cd "$PLATFORM" && docker compose exec -T db psql -U postgres -d techdeva -tAc "$1") 2>/dev/null; }
LINKED=$(PSQL "SELECT DISTINCT agent_key FROM whatsapp_connections WHERE connected_at IS NOT NULL" | tr -d ' \r')
for c in $(docker ps -a --filter label=techdeva.pairwatch=true --format '{{.Names}}' | grep '^hermes-agent-'); do
  k=$(docker inspect -f '{{index .Config.Labels "techdeva.agent"}}' "$c" 2>/dev/null)
  case "$k" in ""|*irfan*|*clinic*|*salon*|*workspace*) continue;; esac
  [ -f "/var/lib/techdeva/trial-paused/$k" ] && continue
  if [ "$(docker inspect -f '{{.State.Running}}' "$c" 2>/dev/null)" != "true" ]; then
    P+=("agent $k: container down"); continue
  fi
  printf '%s\n' "$LINKED" | grep -qx "$k" || continue
  H=$(timeout 20 docker exec "$c" node -e "fetch('http://127.0.0.1:3000/health').then(r=>r.text()).then(t=>console.log(t)).catch(()=>console.log('unreachable'))" 2>/dev/null)
  grep -q '"status":"connected"' <<<"$H" || P+=("agent $k: WhatsApp not connected (${H:0:80})")
done

# 12. agent builds that failed (founder console -> Requests shows why)
FB=$(PSQL "SELECT count(*) FROM agent_requests WHERE status = 'failed' AND updated_at > now() - interval '2 days'" | tr -dc '0-9')
[ "${FB:-0}" -gt 0 ] && P+=("WhatsApp agent build failed (${FB} in 2 days; founder console, Requests)")

# 13. the off-site backup, once it is scheduled (ops/backup/install-cron.sh)
if crontab -l 2>/dev/null | grep -q 'ops/backup/offsite.sh'; then
  OF=/var/lib/techdeva/offsite-last.json
  if [ ! -f "$OF" ] || [ $(( $(date +%s) - $(stat -c %Y "$OF") )) -gt 108000 ]; then
    P+=("off-site backup has not run in 30h")
  elif ! grep -q '"ok": true' "$OF"; then
    P+=("off-site backup failed ($(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("problems","")[:100])' "$OF" 2>/dev/null))")
  fi
fi

# 14. work piling up: the worker is alive but not keeping up
QL=$(docker exec techdeva-platform-redis-1 sh -c 'echo $(( $(redis-cli llen events:queue) + $(redis-cli llen tasks:queue) ))' 2>/dev/null | tr -dc '0-9')
[ -n "$QL" ] && [ "$QL" -gt 200 ] && P+=("job queue backed up (${QL} waiting)")

if [ "$MODE" = "--dry-run" ]; then
  if [ ${#P[@]} -eq 0 ]; then echo "dry run: all checks passed"; else printf 'dry run problem: %s\n' "${P[@]}"; fi
  exit 0
fi

NOW="OK"; [ ${#P[@]} -gt 0 ] && NOW="FAIL"
WAS=$(head -1 "$STATE" 2>/dev/null || echo "OK")
echo "$NOW" > "$STATE"
TS="[$(date -uIs)]"
# a problem's identity, without the numbers and details that change every run
CUR=$(printf '%s\n' "${P[@]}" | sed -E 's/\([^)]*\)//g; s/[0-9]+//g; s/ +$//' | sed '/^$/d' | sort -u)
PREV=$(cat "$STATE.keys" 2>/dev/null || true)
NEWP=$(comm -13 <(printf '%s\n' "$PREV" | sed '/^$/d') <(printf '%s\n' "$CUR" | sed '/^$/d'))
printf '%s\n' "$CUR" > "$STATE.keys"
LAST=$(cat "$STATE.alerted" 2>/dev/null || echo 0)

# Put the finding somewhere the product can show it, not only in this log.
INC=$PLATFORM/scripts/incident.sh
if [ -x "$INC" ]; then
  if [ "$NOW" = "FAIL" ]; then
    "$INC" open platform.monitor critical "Platform check failing" \
           "$(printf '%s; ' "${P[@]}")" || true
  else
    "$INC" clear platform.monitor || true
  fi
fi

send() {   # send <message>: deliver, or keep it for a retry and say so
  [ -x "$ALERT" ] || return 0
  if deliver "$1"; then
    date +%s > "$STATE.alerted"; rm -f "$STATE.undelivered"
    [ -x "$INC" ] && { "$INC" clear monitor.alerts || true; }
  else
    echo "$TS ALERT NOT DELIVERED: $1"
    printf '%s\n' "$1" > "$STATE.undelivered"
    [ -x "$INC" ] && { "$INC" open monitor.alerts critical "Alerts are not reaching Faheem" \
         "The WhatsApp notifier refused the last alert. Check /opt/techdeva-alert.env, then run: /opt/techdeva-monitor.sh --test-alert" || true; }
  fi
}

SENT=0
if [ "$NOW" = "FAIL" ]; then
  MSG="TechDeva ALERT: $(printf '%s; ' "${P[@]}")"
  echo "$TS $MSG"
  if [ "$WAS" != "FAIL" ] || [ -n "$NEWP" ]; then send "$MSG"; SENT=1
  elif [ $(( $(date +%s) - LAST )) -gt 86400 ]; then send "TechDeva STILL FAILING (daily reminder): $(printf '%s; ' "${P[@]}")"; SENT=1
  fi
else
  echo "$TS all checks passed"
  if [ "$WAS" = "FAIL" ]; then send "TechDeva RECOVERED: all checks passing"; SENT=1; fi
fi
# an alert that could not be delivered is tried again every 30 minutes
if [ $SENT = 0 ] && [ -f "$STATE.undelivered" ] && [ $(( $(date +%s) - $(stat -c %Y "$STATE.undelivered") )) -gt 1800 ]; then
  send "$(cat "$STATE.undelivered")"
fi
