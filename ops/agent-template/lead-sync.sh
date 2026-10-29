#!/bin/bash
# Leads the WhatsApp agents saved (td_lead's save_lead tool) -> the platform's Leads.
# Cron, every minute, root:
#
#   * * * * * /opt/techdeva-platform/techdeva-platform/ops/agent-template/lead-sync.sh >> /var/log/techdeva-leads.log 2>&1
#
# For each template agent with the td_lead plugin: its td_lead/inbox.jsonl is
# renamed (so the plugin starts a new one), handed to
# `python -m app.lead_inbox ingest` in the api container with the agent's key
# (taken from the volume, never from the file), and removed only after the
# platform said "ok" for every line (else it is retried next minute).
# Never touches irfan or the workspace/ofsd agents. Customer details are never logged.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/../.." && pwd)"
exec 9>/run/techdeva-leads.lock
flock -n 9 || exit 0
cd "$PLATFORM" || exit 1
log() { echo "$(date -u +%FT%TZ) $*"; }
protected() { case "$1" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) return 0;; *) return 1;; esac; }

for d in /docker/hermes-*/; do
  a=$(basename "$d"); a=${a#hermes-}
  protected "$a" && continue
  v=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$a-data" 2>/dev/null) || continue
  [ -d "$v/plugins/td_lead" ] || continue
  t="$v/td_lead"
  [ -s "$t/inbox.jsonl" ] && mv "$t/inbox.jsonl" "$t/processing-$(date +%s).jsonl"
  for f in "$t"/processing-*.jsonl; do
    [ -f "$f" ] || continue
    want=$(python3 -c 'import json,sys
for l in open(sys.argv[1], encoding="utf-8"):
    try: print(json.loads(l)["id"])
    except Exception: pass' "$f" | sort -u)
    got=$(python3 -c 'import json,sys
a=sys.argv[2]
for l in open(sys.argv[1], encoding="utf-8"):
    try: e=json.loads(l); e["agent_key"]=a; print(json.dumps(e, ensure_ascii=False))
    except Exception: pass' "$f" "$a" | docker compose exec -T api python -m app.lead_inbox ingest 2>/dev/null | sed -n 's/^ok //p' | sort -u)
    if [ -n "$want" ] && [ "$want" = "$got" ]; then
      rm -f "$f"; log "$a: $(echo "$want" | wc -l) lead line(s) saved"
    elif [ -z "$want" ]; then
      rm -f "$f"; log "$a: empty or unreadable inbox file dropped"
    else
      log "$a: lead lines not all saved yet; will retry"
    fi
  done
done
