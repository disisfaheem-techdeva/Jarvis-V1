#!/bin/bash
# One-time: route new clients' addresses automatically (every 2 minutes).
# Safe to run again; keeps every other line of root's crontab.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
chmod +x "$HERE/sync_subdomains.sh"
line="*/2 * * * * $HERE/sync_subdomains.sh >> /var/log/techdeva-subdomains.log 2>&1"
current=$(crontab -l 2>/dev/null || true)
if grep -qF "$HERE/sync_subdomains.sh" <<< "$current"; then echo "already installed"
else printf '%s\n%s\n' "$current" "$line" | sed '/^$/d' | crontab -; echo "installed: $line"; fi
