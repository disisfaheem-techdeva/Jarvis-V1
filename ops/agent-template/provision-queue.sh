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

# Businesses deleted by the trial data rule (db/45): remove their WhatsApp
# agents with remove-agent.sh, which refuses irfan / clinic / salon /
# workspace* and anything the template did not make.
removals=$(curl -fsS "$API/techdeva/retention/agent-removals" "${auth[@]}" 2>/dev/null || true)
printf '%s' "$removals" | python3 -c '
import json, re, sys
try:
    rows = json.load(sys.stdin).get("removals") or []
except ValueError:
    rows = []
for r in rows:
    keys = [k for k in r.get("agent_keys") or [] if re.match(r"^[a-z][a-z0-9-]{1,30}$", k)]
    print("%s\t%s" % (r["id"], " ".join(keys)))
' | while IFS=$'\t' read -r DID KEYS; do
  [ -n "$DID" ] || continue
  if [ $DRY = 1 ]; then echo "$(now) would remove agents [$KEYS] of a deleted business"; continue; fi
  ok=true; err=""
  for K in $KEYS; do
    if out=$(bash "$HERE/remove-agent.sh" --key "$K" --yes 2>&1); then echo "$out"; else ok=false; err="$K: $(printf '%s' "$out" | tail -1)"; echo "$out"; fi
  done
  body=$(python3 -c 'import json,sys;print(json.dumps({"removed":sys.argv[1]=="true","error":sys.argv[2]}))' "$ok" "$err")
  curl -fsS -X POST "$API/techdeva/retention/agent-removals/$DID" "${auth[@]}" -H 'Content-Type: application/json' -d "$body" >/dev/null \
    || echo "$(now) could not report agent removal $DID"
  echo "$(now) deleted business agents [$KEYS]: removed=$ok"
done

# Free-trial limit (app/trialcap.py): a business on trial gets 50 customer
# messages; then its agent is paused (stopped) until it pays, and started again
# once it has. Every 5 minutes. Only template agents the platform names, never
# irfan / clinic / salon / workspace*; only agents this guard paused are started.
if [ $(( 10#$(date +%M) % 5 )) -eq 0 ]; then
  PAUSED_DIR=/var/lib/techdeva/trial-paused; mkdir -p "$PAUSED_DIR"
  curl -fsS "$API/techdeva/trial-guard" "${auth[@]}" 2>/dev/null | python3 -c '
import json, re, sys
try:
    rows = json.load(sys.stdin).get("agents") or []
except ValueError:
    rows = []
for r in rows:
    k = r.get("key") or ""
    if re.match(r"^[a-z0-9][a-z0-9-]{2,60}$", k) and not re.search(r"irfan|clinic|salon|workspace", k) and r.get("run") is not None:
        print("%s\t%s\t%s" % (k, "run" if r["run"] else "pause", r.get("why", "")))
' | while IFS=$'\t' read -r K WANT WHY; do
    running=$(docker inspect -f '{{.State.Running}}' "hermes-agent-$K" 2>/dev/null || echo missing)
    [ "$running" = missing ] && continue
    if [ "$WANT" = pause ] && [ "$running" = true ]; then
      [ $DRY = 1 ] && { echo "$(now) would pause $K ($WHY)"; continue; }
      docker stop "hermes-agent-$K" >/dev/null && touch "$PAUSED_DIR/$K" && echo "$(now) paused $K: $WHY"
    elif [ "$WANT" = run ] && [ -f "$PAUSED_DIR/$K" ]; then
      [ $DRY = 1 ] && { echo "$(now) would start $K again ($WHY)"; continue; }
      # pairctl lives in the agent's network: restart it after the agent
      docker start "hermes-agent-$K" >/dev/null && sleep 3 && docker restart "pairctl-$K" >/dev/null \
        && rm -f "$PAUSED_DIR/$K" && echo "$(now) started $K again: $WHY"
    fi
  done
fi

list=$(curl -fsS "$API/techdeva/agent-requests?status=approved" "${auth[@]}") || { echo "$(now) cannot read the queue"; exit 1; }
job=$(printf '%s' "$list" | python3 -c '
import json, re, sys
rows = json.load(sys.stdin).get("requests") or []
clean = lambda v: re.sub(r"[\t\r\n]", " ", str(v or ""))
if rows:
    r = rows[0]
    print("\t".join([clean(r[k]) for k in ("id","agent_key","tenant_id","owner_phone","home_name","business_name","assistant")]
                    + ["rebuild" if r.get("rebuild") else "new"]))
')
[ -n "$job" ] || exit 0
# HOME_NAME, never HOME: overwriting $HOME made docker write its settings into
# a folder named after the client inside the repo ("Networkometis Pakistan/"),
# which then blocked deploy.sh (2 Oct 2026).
IFS=$'\t' read -r RID KEY TENANT PHONE HOME_NAME BUSINESS ASSISTANT KIND <<< "$job"

free=$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)
agents=$(docker ps -a --filter label=techdeva.pairwatch=true --format '{{.Names}}' | wc -l)
if [ "$free" -lt "$MIN_FREE_MB" ]; then echo "$(now) waiting to build $KEY: only ${free} MB free (need $MIN_FREE_MB)"; exit 0; fi
if [ "$agents" -ge "$MAX_AGENTS" ]; then echo "$(now) waiting to build $KEY: $agents template agents already (MAX_AGENTS=$MAX_AGENTS)"; exit 0; fi

if [ $DRY = 1 ]; then echo "$(now) would build $KEY for tenant $TENANT (${free} MB free, $agents agents)"; exit 0; fi

echo "$(now) building $KEY for tenant $TENANT"
report "$RID" building
# "Rebuild agent" (founder console): the old agent goes first. remove-agent.sh
# refuses irfan / clinic / salon / workspace* and anything the template did not make.
if [ "${KIND:-new}" = rebuild ]; then
  echo "$(now) rebuild: removing the old $KEY first"
  if ! out=$(bash "$HERE/remove-agent.sh" --key "$KEY" --yes 2>&1); then
    echo "$out"; report "$RID" failed "rebuild: could not remove the old agent: $(printf '%s' "$out" | tail -1)"; exit 1
  fi
  echo "$out"; rm -f "/var/lib/techdeva/trial-paused/$KEY"
fi
LOG=$(mktemp)
if bash "$HERE/new-agent.sh" --key "$KEY" --tenant "$TENANT" --owner-phone "$PHONE" \
     --home-name "$HOME_NAME" --business "$BUSINESS" --assistant "$ASSISTANT" > "$LOG" 2>&1; then
  cat "$LOG"
  report "$RID" ready
  echo "$(now) $KEY ready"
else
  cat "$LOG"
  report "$RID" failed "$(grep -E 'ABORT|FAIL' "$LOG" | tail -3 | tr '\n' ' ' | cut -c1-600)"
  echo "$(now) $KEY failed; see above. Retry from the founder console after fixing."
fi
rm -f "$LOG"
