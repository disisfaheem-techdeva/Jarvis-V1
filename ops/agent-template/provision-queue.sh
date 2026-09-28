#!/bin/bash
# Build the WhatsApp agents TechDeva approved in the founder console.
#
# A business asks for its agent on the dashboard (WhatsApp Connection), Faheem
# presses Approve in the founder console (Requests), and this script, run by
# cron every minute on the server, builds it with new-agent.sh and reports
# back. The owner then sees "Connect WhatsApp" and scans the code.
#
#   cron (as root):  * * * * * <repo>/ops/agent-template/provision-queue.sh >> /var/log/techdeva-provision.log 2>&1
#   provision-queue.sh --dry-run    shows what it would build, builds nothing
#
# Safety:
#   - one run at a time (flock); one agent per run
#   - refuses to build when the server has less than MIN_FREE_MB memory free,
#     or already runs MAX_AGENTS template agents: the clinic shares this machine
#   - never touches an existing container: new-agent.sh refuses existing names,
#     and refuses irfan / clinic / salon / workspace* outright
#   - no secret is printed; new-agent.sh reads them from the platform .env
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/../.." && pwd)"
API="${API:-https://api.techdeva.net}"
MIN_FREE_MB="${MIN_FREE_MB:-1500}"
MAX_AGENTS="${MAX_AGENTS:-15}"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
now() { date -u +%FT%TZ; }

exec 9>/var/lock/techdeva-provision.lock
flock -n 9 || exit 0
cd "$PLATFORM" || exit 1

TOKEN=$(docker compose exec -T api python -c "from app.auth import issue_token;print(issue_token(tenant_id=None,user_id='provisioner',role='techdeva_admin',ttl=1800))" 2>/dev/null | tr -d '\r')
[ -n "$TOKEN" ] || { echo "$(now) cannot mint a token (is the api container up?)"; exit 1; }
auth=(-H "Authorization: Bearer $TOKEN")

report() {  # report <request id> <status> [error]
  local body
  body=$(python3 -c 'import json,sys;print(json.dumps({"status":sys.argv[1],"error":sys.argv[2]}))' "$2" "${3:-}")
  curl -fsS -X POST "$API/techdeva/agent-requests/$1/state" "${auth[@]}" -H 'Content-Type: application/json' -d "$body" >/dev/null \
    || echo "$(now) could not report $2 for $1"
}

list=$(curl -fsS "$API/techdeva/agent-requests?status=approved" "${auth[@]}") || { echo "$(now) cannot read the queue"; exit 1; }
job=$(printf '%s' "$list" | python3 -c '
import json, re, sys
rows = json.load(sys.stdin).get("requests") or []
clean = lambda v: re.sub(r"[\t\r\n]", " ", str(v or ""))
if rows:
    r = rows[0]
    print("\t".join(clean(r[k]) for k in ("id","agent_key","tenant_id","owner_phone","home_name","business_name","assistant")))
')
[ -n "$job" ] || exit 0
IFS=$'\t' read -r RID KEY TENANT PHONE HOME BUSINESS ASSISTANT <<< "$job"

free=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
agents=$(docker ps -a --filter label=techdeva.pairwatch=true --format '{{.Names}}' | wc -l)
if [ "$free" -lt "$MIN_FREE_MB" ]; then echo "$(now) waiting to build $KEY: only ${free} MB free (need $MIN_FREE_MB)"; exit 0; fi
if [ "$agents" -ge "$MAX_AGENTS" ]; then echo "$(now) waiting to build $KEY: $agents template agents already (MAX_AGENTS=$MAX_AGENTS)"; exit 0; fi

if [ $DRY = 1 ]; then echo "$(now) would build $KEY for tenant $TENANT (${free} MB free, $agents agents)"; exit 0; fi

echo "$(now) building $KEY for tenant $TENANT"
report "$RID" building
LOG=$(mktemp)
if bash "$HERE/new-agent.sh" --key "$KEY" --tenant "$TENANT" --owner-phone "$PHONE" \
     --home-name "$HOME" --business "$BUSINESS" --assistant "$ASSISTANT" > "$LOG" 2>&1; then
  cat "$LOG"
  report "$RID" ready
  echo "$(now) $KEY ready"
else
  cat "$LOG"
  report "$RID" failed "$(grep -E 'ABORT|FAIL' "$LOG" | tail -3 | tr '\n' ' ' | cut -c1-600)"
  echo "$(now) $KEY failed; see above. Retry from the founder console after fixing."
fi
rm -f "$LOG"
