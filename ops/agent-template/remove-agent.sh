#!/bin/bash
# Remove an agent that new-agent.sh created:  remove-agent.sh --key <key> --yes [--keep-data]
# Refuses anything the template did not make (no techdeva.agent label) and the
# hand-built live agents by name. Also clears the platform's assignment when it
# points at this agent.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PLATFORM="$(cd "$HERE/../.." && pwd)"
KEY= YES=0 KEEP=0 ROOT_DIR=/docker
while [ $# -gt 0 ]; do case "$1" in --key) KEY=$2; shift;; --yes) YES=1;; --keep-data) KEEP=1;; --root) ROOT_DIR=$2; shift;; *) echo "unknown $1"; exit 2;; esac; shift; done
die() { echo "ABORT: $*" >&2; exit 1; }
[[ "$KEY" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || die "bad --key"
case "$KEY" in irfan|clinic|salon|workspace*) die "'$KEY' is a hand-built live agent; never removed by this script";; esac
[ $YES = 1 ] || die "add --yes to confirm removing hermes-agent-$KEY"
DIR="$ROOT_DIR/hermes-$KEY"
if docker inspect "hermes-agent-$KEY" >/dev/null 2>&1; then
  lab=$(docker inspect -f '{{index .Config.Labels "techdeva.agent"}}' "hermes-agent-$KEY")
  [ "$lab" = "$KEY" ] || die "hermes-agent-$KEY was not made by the template (label '$lab'); refusing"
fi
if [ -f "$DIR/docker-compose.yml" ]; then (cd "$DIR" && docker compose down --remove-orphans >/dev/null 2>&1) || true; fi
docker rm -f "pairctl-$KEY" "hermes-agent-$KEY" >/dev/null 2>&1 || true
if [ $KEEP = 0 ]; then
  vlab=$(docker volume inspect -f '{{index .Labels "techdeva.agent"}}' "hermes-$KEY-data" 2>/dev/null || true)
  if [ "$vlab" = "$KEY" ]; then docker volume rm "hermes-$KEY-data" >/dev/null && echo "volume hermes-$KEY-data removed"; fi
fi
if [ -d "$DIR" ]; then rm -rf "$DIR" && echo "$DIR removed"; fi
cd "$PLATFORM"
n=$(docker compose exec -T db psql -U postgres -d techdeva -tAc "DELETE FROM whatsapp_connections WHERE agent_key='$KEY' RETURNING 1" | grep -c 1 || true)
echo "platform assignments cleared: $n"
for x in "hermes-agent-$KEY" "pairctl-$KEY"; do
  if docker inspect "$x" >/dev/null 2>&1; then die "$x still exists"; fi
done
echo "REMOVED: hermes-agent-$KEY and pairctl-$KEY"
