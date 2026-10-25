#!/usr/bin/env bash
# Give ONE existing template agent the T10 final-reply guard, without changing
# anything else about it.
#
#   bash ops/agent-template/add-t10.sh <key>          e.g. tech-deva-d57775
#
# It layers td_final_reply.py + patch_final_reply.py on top of the image the
# agent runs NOW (so its T1-T9 state is exactly what it was), tags the result
# "<current image>-t10", points /docker/hermes-<key>/docker-compose.yml at it
# (a dated copy of the old file is kept next to it), and re-creates only that
# agent's own compose project (its agent + pairctl). WhatsApp stays paired: the
# session lives in the agent's volume.
#
# Undo: copy the dated docker-compose.yml back and `docker compose up -d` in
# /docker/hermes-<key>.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
die() { echo "add-t10: $*" >&2; exit 1; }

KEY="${1:-}"
[ -n "$KEY" ] || die "usage: add-t10.sh <key>"
case "$KEY" in
  irfan|*ofsd*|workspace*) die "$KEY is not a template agent; it is refused by name (see README, Existing agents)";;
esac
DIR="/docker/hermes-$KEY"
AGENT="hermes-agent-$KEY"
[ -f "$DIR/docker-compose.yml" ] || die "$DIR/docker-compose.yml not found"
docker inspect "$AGENT" >/dev/null 2>&1 || die "container $AGENT not found"

CUR=$(docker inspect -f '{{.Config.Image}}' "$AGENT")
case "$CUR" in
  techdeva/hermes-agent:*-t10) echo "add-t10: $AGENT already runs $CUR"; exit 0;;
  techdeva/hermes-agent:*) ;;
  *) die "$AGENT runs $CUR, not a TechDeva template image";;
esac
NEW="$CUR-t10"
grep -q "image: $CUR " "$DIR/docker-compose.yml" || die "compose file does not name $CUR; refusing to guess"

if ! docker image inspect "$NEW" >/dev/null 2>&1; then
  BUILD=$(mktemp -d)
  cp "$HERE/td_final_reply.py" "$HERE/patch_final_reply.py" "$BUILD/"
  BASE_USER=$(docker image inspect -f '{{.Config.User}}' "$CUR")
  {
    echo "FROM $CUR"
    echo "USER root"
    echo "COPY td_final_reply.py /opt/hermes/gateway/td_final_reply.py"
    echo "COPY patch_final_reply.py /opt/techdeva/"
    echo "RUN python3 /opt/hermes/gateway/td_final_reply.py && python3 /opt/techdeva/patch_final_reply.py /opt/hermes/gateway/platforms/base.py && python3 -m py_compile /opt/hermes/gateway/platforms/base.py && grep -q 'TechDeva T10' /opt/hermes/gateway/platforms/base.py"
    [ -n "$BASE_USER" ] && echo "USER $BASE_USER"
  } > "$BUILD/Dockerfile"
  docker build -q -t "$NEW" "$BUILD" >/dev/null || die "image build failed (the T10 patch refused to apply? see: docker build $BUILD)"
  rm -rf "$BUILD"
  echo "built $NEW"
fi

cp -a "$DIR/docker-compose.yml" "$DIR/docker-compose.yml.pre-t10-$(date +%Y%m%d_%H%M%S)"
sed -i "s#image: $CUR #image: $NEW #" "$DIR/docker-compose.yml"
(cd "$DIR" && docker compose up -d >/dev/null)
sleep 20

fail=0
[ "$(docker inspect -f '{{.Config.Image}}' "$AGENT")" = "$NEW" ] && echo "  PASS $AGENT runs $NEW" || { echo "  FAIL image"; fail=1; }
docker exec "$AGENT" grep -q 'TechDeva T10' /opt/hermes/gateway/platforms/base.py && echo "  PASS T10 guard in the running image" || { echo "  FAIL T10 guard missing"; fail=1; }
docker exec "$AGENT" grep -q '== "whatsapp"' /opt/hermes/gateway/run.py && echo "  PASS T3 still in place" || { echo "  FAIL T3 missing"; fail=1; }
docker ps --format '{{.Names}}' | grep -qx "pairctl-$KEY" && echo "  PASS pairctl-$KEY running" || { echo "  FAIL pairctl-$KEY not running"; fail=1; }
exit $fail
