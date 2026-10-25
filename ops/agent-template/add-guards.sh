#!/usr/bin/env bash
# Give ONE existing template agent the TechDeva image guards, without changing
# anything else about it:
#   T10  a failed turn sends the WhatsApp customer nothing; the owner is alerted
#   T11  no session-title model request per conversation (free-tier quota)
#
#   bash ops/agent-template/add-guards.sh <key>          e.g. tech-deva-d57775
#
# It layers the patches on top of the image the agent runs NOW (so its T1-T9
# state is exactly what it was), tags the result "<base image>-g11", points
# /docker/hermes-<key>/docker-compose.yml at it (a dated copy of the old file
# is kept next to it), and re-creates only that agent's own compose project
# (its agent + pairctl, together, so pairctl joins the new agent's network).
# WhatsApp stays paired: the session lives in the agent's volume.
#
# Undo: copy the dated docker-compose.yml back and `docker compose up -d` in
# /docker/hermes-<key>.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
die() { echo "add-guards: $*" >&2; exit 1; }
SUFFIX="-g11"

KEY="${1:-}"
[ -n "$KEY" ] || die "usage: add-guards.sh <key>"
case "$KEY" in
  irfan|*ofsd*|workspace*) die "$KEY is not a template agent; it is refused by name (see README, Existing agents)";;
esac
DIR="/docker/hermes-$KEY"
AGENT="hermes-agent-$KEY"
[ -f "$DIR/docker-compose.yml" ] || die "$DIR/docker-compose.yml not found"
docker inspect "$AGENT" >/dev/null 2>&1 || die "container $AGENT not found"

CUR=$(docker inspect -f '{{.Config.Image}}' "$AGENT")
case "$CUR" in
  techdeva/hermes-agent:*"$SUFFIX") echo "add-guards: $AGENT already runs $CUR"; exit 0;;
  techdeva/hermes-agent:*) ;;
  *) die "$AGENT runs $CUR, not a TechDeva template image";;
esac
NEW="${CUR%-t10}$SUFFIX"
grep -q "image: $CUR " "$DIR/docker-compose.yml" || die "compose file does not name $CUR; refusing to guess"

if ! docker image inspect "$NEW" >/dev/null 2>&1; then
  BUILD=$(mktemp -d)
  cp "$HERE/td_final_reply.py" "$HERE/patch_final_reply.py" "$HERE/patch_no_autotitle.py" "$BUILD/"
  BASE_USER=$(docker image inspect -f '{{.Config.User}}' "$CUR")
  {
    echo "FROM $CUR"
    echo "USER root"
    echo "COPY td_final_reply.py /opt/hermes/gateway/td_final_reply.py"
    echo "COPY patch_final_reply.py patch_no_autotitle.py /opt/techdeva/"
    echo "RUN python3 /opt/hermes/gateway/td_final_reply.py && python3 /opt/techdeva/patch_final_reply.py /opt/hermes/gateway/platforms/base.py && python3 /opt/techdeva/patch_no_autotitle.py /opt/hermes/agent/title_generator.py && python3 -m py_compile /opt/hermes/gateway/platforms/base.py /opt/hermes/agent/title_generator.py && grep -q 'TechDeva T10' /opt/hermes/gateway/platforms/base.py && grep -q 'TechDeva T11' /opt/hermes/agent/title_generator.py"
    [ -n "$BASE_USER" ] && echo "USER $BASE_USER"
  } > "$BUILD/Dockerfile"
  docker build -q -t "$NEW" "$BUILD" >/dev/null || die "image build failed (a patch refused to apply? see: docker build $BUILD)"
  rm -rf "$BUILD"
  echo "built $NEW"
fi

cp -a "$DIR/docker-compose.yml" "$DIR/docker-compose.yml.pre-guards-$(date +%Y%m%d_%H%M%S)"
sed -i "s#image: $CUR #image: $NEW #" "$DIR/docker-compose.yml"
(cd "$DIR" && docker compose up -d --force-recreate >/dev/null)
sleep 20

fail=0
[ "$(docker inspect -f '{{.Config.Image}}' "$AGENT")" = "$NEW" ] && echo "  PASS $AGENT runs $NEW" || { echo "  FAIL image"; fail=1; }
docker exec "$AGENT" grep -q 'TechDeva T10' /opt/hermes/gateway/platforms/base.py && echo "  PASS T10 final-reply guard" || { echo "  FAIL T10 missing"; fail=1; }
docker exec "$AGENT" grep -q 'TechDeva T11' /opt/hermes/agent/title_generator.py && echo "  PASS T11 no title requests" || { echo "  FAIL T11 missing"; fail=1; }
docker exec "$AGENT" grep -q '== "whatsapp"' /opt/hermes/gateway/run.py && echo "  PASS T3 still in place" || { echo "  FAIL T3 missing"; fail=1; }
docker ps --format '{{.Names}}' | grep -qx "pairctl-$KEY" && echo "  PASS pairctl-$KEY running" || { echo "  FAIL pairctl-$KEY not running"; fail=1; }
exit $fail
