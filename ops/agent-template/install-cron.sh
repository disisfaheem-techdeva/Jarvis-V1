#!/bin/bash
# One-time: install the two server jobs new client agents rely on.
#   pairwatch.sh        restarts a template agent once after its number is paired (T6)
#   provision-queue.sh  builds agents Faheem approved in the founder console
# Safe to run again: it only adds the lines that are missing, and keeps every
# other line of root's crontab as it is.
#   bash ops/agent-template/install-cron.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$HERE/pairwatch.sh" "$HERE/provision-queue.sh"
current=$(crontab -l 2>/dev/null || true)
add=""
grep -qF "$HERE/pairwatch.sh" <<< "$current" || add+="* * * * * $HERE/pairwatch.sh >> /var/log/techdeva-pairwatch.log 2>&1"$'\n'
grep -qF "$HERE/provision-queue.sh" <<< "$current" || add+="* * * * * $HERE/provision-queue.sh >> /var/log/techdeva-provision.log 2>&1"$'\n'
if [ -z "$add" ]; then echo "both jobs already installed"; else
  printf '%s\n%s' "$current" "$add" | sed '/^$/d' | crontab -
  echo "installed:"; printf '%s' "$add"
fi
echo "--- root crontab now:"; crontab -l
echo "--- check: $HERE/pairwatch.sh --dry-run ; $HERE/provision-queue.sh --dry-run"
