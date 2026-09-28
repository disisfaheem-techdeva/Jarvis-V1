#!/bin/bash
# Provision a NEW client WhatsApp agent from the TechDeva template.
#
#   ops/agent-template/new-agent.sh --key noor --tenant <uuid> --owner-phone 923001234567 \
#       --home-name "Dr Noor" --business "Noor Aesthetics" [--assistant Zara] [--no-assign]
#
# What every new agent gets (tonight's hand fixes, now automatic):
#   T1 tool lockdown       agent.disabled_toolsets            (apply_config.py)
#   T2 model chain         gemini-3.8-flash -> 2.5-flash-lite -> 3-flash-preview, no OpenRouter
#   T3 no status leaks     gateway patched at IMAGE BUILD (patch_gateway.py) + display.tool_progress off
#   T4 home channel        WHATSAPP_HOME_CHANNEL=<owner>@s.whatsapp.net + _NAME, per agent
#   T5 voice rules         SOUL.md block (apply_soul.py)
#   T6 pairing pickup      pairctl shares the agent's network (Send works) and marks a
#                          finished pairing; pairwatch.sh restarts the agent once
#
# Never touches an existing agent: refuses if the container, directory or
# volume already exists. Secrets are read from the platform .env (the platform's
# secret store) into a 600-mode agent.env beside the compose file; nothing
# secret is written to the repo or printed.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
PLATFORM="$(cd "$HERE/../.." && pwd)"
KEY= TENANT= PHONE= HOME_NAME= BUSINESS= ASSISTANT=Zara ASSIGN=1
SHARED_ENV=/docker/hermes-workspace-ofsd/.env ROOT_DIR=/docker AGENT_UID=10013 AGENT_GID=10000 REBUILD=0
while [ $# -gt 0 ]; do case "$1" in
  --key) KEY=$2; shift;; --tenant) TENANT=$2; shift;; --owner-phone) PHONE=$2; shift;;
  --home-name) HOME_NAME=$2; shift;; --business) BUSINESS=$2; shift;; --assistant) ASSISTANT=$2; shift;;
  --shared-env) SHARED_ENV=$2; shift;; --root) ROOT_DIR=$2; shift;; --uid) AGENT_UID=$2; shift;; --gid) AGENT_GID=$2; shift;;
  --no-assign) ASSIGN=0;; --rebuild) REBUILD=1;;
  *) echo "unknown option $1"; exit 2;; esac; shift; done

die() { echo "ABORT: $*" >&2; exit 1; }
[[ "$KEY" =~ ^[a-z][a-z0-9-]{1,30}$ ]] || die "--key must be lowercase letters/digits/dash"
case "$KEY" in irfan|clinic|salon|workspace*) die "'$KEY' is a hand-built live agent; this script never touches it";; esac
[[ "$TENANT" =~ ^[0-9a-f-]{36}$ ]] || die "--tenant must be the tenant uuid"
PHONE="${PHONE//[^0-9]/}"; [[ ${#PHONE} -ge 10 && ${#PHONE} -le 15 ]] || die "--owner-phone must be the owner's number in international digits (92...)"
[ -n "$HOME_NAME" ] && [ -n "$BUSINESS" ] || die "--home-name and --business are required"
[ -f "$SHARED_ENV" ] || die "shared agent env $SHARED_ENV not found"
[ -f "$PLATFORM/.env" ] || die "platform .env not found at $PLATFORM/.env"

AGENT="hermes-agent-$KEY" PAIR="pairctl-$KEY" VOL="hermes-$KEY-data" DIR="$ROOT_DIR/hermes-$KEY"
docker inspect "$AGENT" >/dev/null 2>&1 && die "container $AGENT already exists"
docker inspect "$PAIR" >/dev/null 2>&1 && die "container $PAIR already exists"
docker volume inspect "$VOL" >/dev/null 2>&1 && die "volume $VOL already exists"
[ -e "$DIR" ] && die "$DIR already exists"

envval() { grep -E "^$1=" "$PLATFORM/.env" | tail -1 | cut -d= -f2- | sed -E 's/^["'"'"'](.*)["'"'"']$/\1/'; }
GEMINI_API_KEY="$(envval GEMINI_API_KEY)"; INTERNAL="$(envval HERMES_INTERNAL_KEY)"
[ -n "$GEMINI_API_KEY" ] || die "GEMINI_API_KEY is not set in the platform .env"
[ -n "$INTERNAL" ] || die "HERMES_INTERNAL_KEY is not set in the platform .env"

# ---- image: upstream + T3 patch, built once per upstream version
BASE_IMG=nousresearch/hermes-agent:latest
BASE_ID=$(docker image inspect -f '{{.Id}}' "$BASE_IMG" 2>/dev/null | cut -c8-19) || true
[ -n "$BASE_ID" ] || { docker pull "$BASE_IMG" >/dev/null; BASE_ID=$(docker image inspect -f '{{.Id}}' "$BASE_IMG" | cut -c8-19); }
IMG="techdeva/hermes-agent:$BASE_ID-td1"
if [ $REBUILD = 1 ] || ! docker image inspect "$IMG" >/dev/null 2>&1; then
  BASE_USER=$(docker image inspect -f '{{.Config.User}}' "$BASE_IMG")
  BUILD=$(mktemp -d); cp "$HERE/patch_gateway.py" "$HERE/apply_config.py" "$HERE/apply_soul.py" "$BUILD/"; cp -r "$HERE/soul" "$BUILD/"
  {
    echo "FROM $BASE_IMG"
    echo "USER root"
    echo "COPY patch_gateway.py apply_config.py apply_soul.py /opt/techdeva/"
    echo "COPY soul /opt/techdeva/soul"
    echo "RUN python3 /opt/techdeva/patch_gateway.py /opt/hermes/gateway/run.py && grep -q '== \"whatsapp\"' /opt/hermes/gateway/run.py"
    [ -n "$BASE_USER" ] && echo "USER $BASE_USER"
    echo "LABEL techdeva.template=agent-td1"
  } > "$BUILD/Dockerfile"
  docker build -q -t "$IMG" "$BUILD" >/dev/null || die "image build failed (the T3 patch refused to apply? see: docker build $BUILD)"
  rm -rf "$BUILD"
  echo "built $IMG"
fi

# ---- files
mkdir -p "$DIR"; chmod 700 "$DIR"
umask 077
cat > "$DIR/agent.env" <<EOF
GEMINI_API_KEY=$GEMINI_API_KEY
HERMES_INTERNAL_KEY=$INTERNAL
EOF
echo "HERMES_INTERNAL_KEY=$INTERNAL" > "$DIR/pairctl.env"
umask 022
sed -e "s|__KEY__|$KEY|g" -e "s|__IMG__|$IMG|g" -e "s|__SHARED_ENV__|$SHARED_ENV|g" \
    -e "s|__UID__|$AGENT_UID|g" -e "s|__GID__|$AGENT_GID|g" -e "s|__PHONE__|$PHONE|g" \
    -e "s|__HOME_NAME__|${HOME_NAME//|/}|g" -e "s|__PAIRCTL_DIR__|$PLATFORM/ops/pairctl|g" \
    "$HERE/docker-compose.agent.yml" > "$DIR/docker-compose.yml"
echo "wrote $DIR (compose + agent.env + pairctl.env, secrets mode 600)"

# ---- volume + SOUL (T5)
docker volume create --label techdeva.agent="$KEY" "$VOL" >/dev/null
docker run --rm --user 0 -v "$VOL:/opt/data" --entrypoint sh "$IMG" -c \
  "python3 /opt/techdeva/apply_soul.py /opt/data/SOUL.md --new --assistant \"\$0\" --business \"\$1\" && chown -R $AGENT_UID:$AGENT_GID /opt/data" \
  "$ASSISTANT" "$BUSINESS"

# ---- first boot creates config.yaml; then enforce T1/T2/T3 on it and start for real
cd "$DIR"
docker compose up -d "$AGENT" >/dev/null
for i in $(seq 1 60); do docker exec "$AGENT" test -f /opt/data/config.yaml 2>/dev/null && break; sleep 2; done
docker exec "$AGENT" test -f /opt/data/config.yaml || die "$AGENT did not create /opt/data/config.yaml (docker logs $AGENT)"
docker compose stop "$AGENT" >/dev/null
docker run --rm --user 0 -v "$VOL:/opt/data" --entrypoint sh "$IMG" -c \
  "cp /opt/data/config.yaml /opt/data/config.yaml.template-default && python3 /opt/techdeva/apply_config.py /opt/data/config.yaml && chown $AGENT_UID:$AGENT_GID /opt/data/config.yaml*"
docker compose up -d >/dev/null
sleep 5

# ---- verify what the running agent will use
fail=0
ok() { echo "  PASS $1"; }; bad() { echo "  FAIL $1"; fail=1; }
docker exec "$AGENT" grep -q '== "whatsapp"' /opt/hermes/gateway/run.py && ok "T3 gateway patch in the running image" || bad "T3 gateway patch missing"
docker exec "$AGENT" python3 -c "import yaml,sys;c=yaml.safe_load(open('/opt/data/config.yaml'));print('  model:',c['model']['provider'],c['model']['default'],'| fallbacks:',[f['model'] for f in c['fallback_providers']],'| disabled_toolsets:',c['agent']['disabled_toolsets'],'| tool_progress:',c['display']['tool_progress'])"
docker exec "$AGENT" python3 -c "import yaml;c=yaml.safe_load(open('/opt/data/config.yaml'));assert c['model']['provider']=='gemini' and all(f['provider']=='gemini' for f in c['fallback_providers']) and len(c['agent']['disabled_toolsets'])==10" && ok "T1/T2 config" || bad "T1/T2 config"
[ "$(docker exec "$AGENT" printenv WHATSAPP_HOME_CHANNEL)" = "$PHONE@s.whatsapp.net" ] && ok "T4 home channel $PHONE@s.whatsapp.net ($(docker exec "$AGENT" printenv WHATSAPP_HOME_CHANNEL_NAME))" || bad "T4 home channel"
[ -z "$(docker exec "$AGENT" printenv OPENROUTER_API_KEY)" ] && ok "T2 no OpenRouter key in the agent" || bad "T2 OpenRouter key present"
docker exec "$AGENT" grep -q "TECHDEVA-VOICE-START" /opt/data/SOUL.md && ok "T5 voice rules in SOUL.md" || bad "T5 voice rules"
docker exec "$PAIR" node -e "require('http').get({host:'127.0.0.1',port:3010,path:'/health',headers:{'x-internal-key':process.env.HERMES_INTERNAL_KEY}},r=>{let b='';r.on('data',c=>b+=c);r.on('end',()=>{console.log('  pairctl:',b);process.exit(/can_send/.test(b)?0:1)})}).on('error',e=>{console.log(e.message);process.exit(1)})" && ok "T6 pairctl up, shares the agent network (can_send)" || bad "T6 pairctl"
[ $fail = 0 ] || die "verification failed; the agent is up but NOT assigned to the tenant. Remove with remove-agent.sh --key $KEY --yes"

# ---- tell the platform which agent this tenant uses (TechDeva action)
if [ $ASSIGN = 1 ]; then
  cd "$PLATFORM"
  TOKEN=$(docker compose exec -T api python -c "from app.auth import issue_token;print(issue_token(tenant_id=None,user_id='agent-template',role='techdeva_admin',ttl=120))")
  curl -fsS -X POST https://api.techdeva.net/whatsapp/assign -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"agent_key\":\"$KEY\",\"pairctl_url\":\"http://$AGENT:3010\"}" && echo && echo "assigned to tenant $TENANT"
fi
echo "DONE: $AGENT is running. The owner connects WhatsApp from their dashboard (WhatsApp Connection -> scan)."
echo "Make sure pairwatch runs:  * * * * * $HERE/pairwatch.sh >> /var/log/techdeva-pairwatch.log 2>&1"
