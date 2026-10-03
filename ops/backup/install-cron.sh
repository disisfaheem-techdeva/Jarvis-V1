#!/bin/bash
# Add the off-site backup to root's crontab, once, AFTER a manual run and a
# restore test have passed (docs/OFFSITE-BACKUP.md). Runs at 02:40 UTC, after
# the nightly backup at 02:15. Idempotent; changes no other cron line.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LINE="40 2 * * * $HERE/offsite.sh >> /var/log/techdeva-offsite.log 2>&1"
python3 -c 'import json,sys; d=json.load(open("/var/lib/techdeva/offsite-restore-last.json")); sys.exit(0 if d.get("ok") else 1)' 2>/dev/null \
  || { echo "Run ops/backup/offsite-restore-test.sh first: it has not passed yet."; exit 1; }
if crontab -l 2>/dev/null | grep -qF "$HERE/offsite.sh"; then echo "already scheduled:"; crontab -l | grep -F "$HERE/offsite.sh"; exit 0; fi
( crontab -l 2>/dev/null; echo "$LINE" ) | crontab -
echo "scheduled: $LINE"
