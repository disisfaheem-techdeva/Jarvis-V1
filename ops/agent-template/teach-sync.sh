#!/bin/bash
# Lessons the owner taught over WhatsApp (/sikho + /haan) -> the platform. Cron, every minute, root:
#
#   * * * * * /opt/techdeva-platform/techdeva-platform/ops/agent-template/teach-sync.sh >> /var/log/techdeva-teach.log 2>&1
#
# 1. For each template agent with the td_teach plugin: its td_teach/inbox.jsonl
#    is renamed (so the plugin starts a new one), handed to
#    `python -m app.teach ingest` in the api container, and removed only after
#    the platform said "ok" for every line (else it is retried next minute).
# 2. Every 10 minutes: the owner's number forms are re-checked (teach_config.py);
#    when the owner's WhatsApp LID shows up for the first time the agent is
#    re-created once so the gateway knows the owner in that form too.
# Never touches irfan or the workspace/ofsd agents. Lesson text is never logged.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/../.." && pwd)"
exec 9>/run/techdeva-teach.lock
flock -n 9 || exit 0
cd "$PLATFORM" || exit 1
log() { echo "$(date -u +%FT%TZ) $*"; }
protected() { case "$1" in irfan|clinic|salon|hermes-workspace*|workspace*|*ofsd*) return 0;; *) return 1;; esac; }

for d in /docker/hermes-*/; do
  a=$(basename "$d"); a=${a#hermes-}
  protected "$a" && continue
  v=$(docker volume inspect -f '{{.Mountpoint}}' "hermes-$a-data" 2>/dev/null) || continue
  [ -d "$v/plugins/td_teach" ] || continue
  t="$v/td_teach"
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
    except Exception: pass' "$f" "$a" | docker compose exec -T api python -m app.teach ingest 2>/dev/null | sed -n 's/^ok //p' | sort -u)
    if [ -n "$want" ] && [ "$want" = "$got" ]; then
      rm -f "$f"; log "$a: $(echo "$want" | wc -l) lesson(s) saved"
    elif [ -z "$want" ]; then
      rm -f "$f"; log "$a: empty or unreadable inbox file dropped"
    else
      log "$a: lessons not all saved yet; will retry"
    fi
  done
  if [ $(( $(date +%-M) % 10 )) -eq 0 ]; then
    phone=$(grep -oE 'WHATSAPP_HOME_CHANNEL: *"?[0-9]+@' "$d/docker-compose.yml" | grep -oE '[0-9]+' | head -1)
    [ -n "$phone" ] || continue
    python3 "$HERE/teach_config.py" "$v/config.yaml" "$v/whatsapp/session" "$phone" >/dev/null 2>&1
    if [ $? -eq 0 ] && [ "$(docker inspect -f '{{.State.Running}}' "hermes-agent-$a" 2>/dev/null)" = "true" ]; then
      (cd "$d" && docker compose up -d --force-recreate >/dev/null 2>&1 </dev/null) && log "$a: owner's number forms updated, agent re-created"
    fi
  fi
done
