#!/bin/bash
# Provision a NEW client WhatsApp agent from the TechDeva template.
#
#   ops/agent-template/new-agent.sh --key noor --tenant <uuid> --owner-phone 923001234567 \
#       --home-name "Dr Noor" --business "Noor Aesthetics" [--assistant Zara] [--no-assign]
#
# What every new agent gets (tonight's hand fixes, now automatic):
#   T1 tool lockdown       agent.disabled_toolsets            (apply_config.py)
#   T2 model chain         gemini-3-flash-preview -> 3.5-flash-lite, no OpenRouter (T17: td_speed, minimal thinking)
#   T3 no status leaks     gateway patched at IMAGE BUILD (patch_gateway.py) + display.tool_progress off
#   T10 no error replies   a failed turn sends the customer nothing; the owner is alerted (patch_final_reply.py)
#   T11 quota              no session-title request per conversation (patch_no_autotitle.py); STT off
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
# These end up inside a YAML string (via sed) and a shell argument: allow only
# plain name characters, never quotes, backslashes, & or newlines.
SAFE_NAME="^[A-Za-z0-9][A-Za-z0-9 .'-]{1,59}$"
[[ "$HOME_NAME" =~ $SAFE_NAME ]] || die "--home-name may use letters, digits, space . ' - only"
[[ "$BUSINESS" =~ $SAFE_NAME ]] || die "--business may use letters, digits, space . ' - only"
[[ "$ASSISTANT" =~ ^[A-Za-z]{2,20}$ ]] || die "--assistant must be a single name of letters"
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

# ---- image: upstream + T3 patch, built once per upstream version AND per
# version of our own files. The tag used to be "<base>-td1" only, so an image
# built before apply_config.py learned to strip model.base_url kept the old
# script for ever: new agents still pointed at openrouter.ai and failed T1/T2
# (Networkometis Pakistan, 2 Oct 2026). A fingerprint of the files copied in
# means any change to them builds a fresh image; running agents keep theirs.
BASE_IMG=nousresearch/hermes-agent:latest
BASE_ID=$(docker image inspect -f '{{.Id}}' "$BASE_IMG" 2>/dev/null | cut -c8-19) || true
[ -n "$BASE_ID" ] || { docker pull "$BASE_IMG" >/dev/null; BASE_ID=$(docker image inspect -f '{{.Id}}' "$BASE_IMG" | cut -c8-19); }
TPL_ID=$( (cd "$HERE" && cat patch_gateway.py patch_bridge.py apply_config.py apply_soul.py td_final_reply.py patch_final_reply.py patch_no_autotitle.py $(find soul -type f | sort); echo "bridge-deps-v2") | sha256sum | cut -c1-8)
IMG="techdeva/hermes-agent:$BASE_ID-td-$TPL_ID"
if [ $REBUILD = 1 ] || ! docker image inspect "$IMG" >/dev/null 2>&1; then
  BASE_USER=$(docker image inspect -f '{{.Config.User}}' "$BASE_IMG")
  BUILD=$(mktemp -d); cp "$HERE/patch_gateway.py" "$HERE/patch_bridge.py" "$HERE/apply_config.py" "$HERE/apply_soul.py" "$HERE/td_final_reply.py" "$HERE/patch_final_reply.py" "$HERE/patch_no_autotitle.py" "$BUILD/"; cp -r "$HERE/soul" "$BUILD/"
  {
    echo "FROM $BASE_IMG"
    echo "USER root"
    echo "COPY patch_gateway.py patch_bridge.py apply_config.py apply_soul.py /opt/techdeva/"
    echo "COPY soul /opt/techdeva/soul"
    echo "RUN python3 /opt/techdeva/patch_gateway.py /opt/hermes/gateway/run.py && grep -q '== \"whatsapp\"' /opt/hermes/gateway/run.py"
    # T10: a failure reply ("Sorry, I encountered an error", raw Gemini quota
    # errors...) never reaches a WhatsApp customer; the owner is alerted instead.
    echo "COPY td_final_reply.py /opt/hermes/gateway/td_final_reply.py"
    echo "COPY patch_final_reply.py patch_no_autotitle.py /opt/techdeva/"
    # T11: no session-title model request per conversation (free-tier quota).
    echo "RUN python3 /opt/techdeva/patch_no_autotitle.py /opt/hermes/agent/title_generator.py && grep -q 'TechDeva T11' /opt/hermes/agent/title_generator.py"
    echo "RUN python3 /opt/hermes/gateway/td_final_reply.py && python3 /opt/techdeva/patch_final_reply.py /opt/hermes/gateway/platforms/base.py && grep -q 'TechDeva T10' /opt/hermes/gateway/platforms/base.py"
    # The WhatsApp bridge ships without its packages (node_modules was empty in
    # the upstream image, 2 Oct 2026): install them here so the pairing helper
    # and the gateway can both run it.
    # The gateway reinstalls the packages (npm install, which fails in the agent)
    # unless node_modules/.hermes-pkg-hash holds the first 16 hex characters of
    # the SHA-256 of package.json (whatsapp adapter.py in the image, read on the
    # server 4 Oct 2026). Write that stamp with the packages.
    echo "RUN cd /opt/hermes/scripts/whatsapp-bridge && npm ci --omit=dev --no-audit --no-fund && test -n \"\$(ls node_modules)\" && sha256sum package.json | cut -c1-16 > node_modules/.hermes-pkg-hash"
    # The stock bridge only draws the QR in the terminal; give it the clinic's
    # GET /session so the dashboard can show the code (patch_bridge.py).
    echo "RUN python3 /opt/techdeva/patch_bridge.py /opt/hermes/scripts/whatsapp-bridge/bridge.js && node --check /opt/hermes/scripts/whatsapp-bridge/bridge.js"
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
  "python3 /opt/techdeva/apply_soul.py /opt/data/SOUL.md --new --assistant \"\$0\" --business \"\$1\" && mkdir -p /opt/data/scripts && cp -a /opt/hermes/scripts/whatsapp-bridge /opt/data/scripts/ && chmod -R u+w /opt/data/scripts/whatsapp-bridge && chown -R $AGENT_UID:$AGENT_GID /opt/data" \
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
docker exec "$AGENT" grep -q 'TechDeva T10' /opt/hermes/gateway/platforms/base.py && ok "T10 failure replies go to the owner, not the customer" || bad "T10 final-reply guard missing"
docker exec "$AGENT" grep -q 'TechDeva T11' /opt/hermes/agent/title_generator.py && ok "T11 no session-title requests" || bad "T11 title guard missing"
docker exec "$AGENT" python3 -c "import yaml,sys;c=yaml.safe_load(open('/opt/data/config.yaml'));print('  model:',c['model']['provider'],c['model']['default'],'| fallbacks:',[f['model'] for f in c['fallback_providers']],'| disabled_toolsets:',c['agent']['disabled_toolsets'],'| tool_progress:',c['display']['tool_progress'])"
docker exec "$AGENT" python3 -c "import yaml;c=yaml.safe_load(open('/opt/data/config.yaml'));assert c['model']['provider']=='gemini' and 'openrouter' not in str(c['model']).lower() and all(f['provider']=='gemini' for f in c['fallback_providers']) and len(c['agent']['disabled_toolsets'])==10" && ok "T1/T2 config" || bad "T1/T2 config"
[ "$(docker exec "$AGENT" printenv WHATSAPP_HOME_CHANNEL)" = "$PHONE@s.whatsapp.net" ] && ok "T4 home channel $PHONE@s.whatsapp.net ($(docker exec "$AGENT" printenv WHATSAPP_HOME_CHANNEL_NAME))" || bad "T4 home channel"
[ "$(docker exec "$AGENT" printenv WHATSAPP_MODE)" = "bot" ] && ok "T4 WhatsApp in bot mode (answers customers, not only the owner)" || bad "T4 WHATSAPP_MODE is not bot (customers would be ignored)"
[ "$(docker exec "$AGENT" printenv WHATSAPP_ALLOWED_USERS)" = "*" ] && ok "T4 WhatsApp lets every customer through (allow-list *)" || bad "T4 WHATSAPP_ALLOWED_USERS is not * (every customer would be rejected)"
[ -z "$(docker exec "$AGENT" sh -c 'echo $OPENROUTER_API_KEY$GROQ_API_KEY$AWS_ACCESS_KEY_ID$OPENAI_API_KEY')" ] && ok "T2 no OpenRouter/Groq/AWS/OpenAI key in the agent" || bad "T2 another provider key is present"
docker exec "$AGENT" grep -q "TECHDEVA-VOICE-START" /opt/data/SOUL.md && ok "T5 voice rules in SOUL.md" || bad "T5 voice rules"
docker exec "$PAIR" node -e "require('http').get({host:'127.0.0.1',port:3010,path:'/health',headers:{'x-internal-key':process.env.HERMES_INTERNAL_KEY}},r=>{let b='';r.on('data',c=>b+=c);r.on('end',()=>{console.log('  pairctl:',b);process.exit(/can_send/.test(b)?0:1)})}).on('error',e=>{console.log(e.message);process.exit(1)})" && ok "T6 pairctl up, shares the agent network (can_send)" || bad "T6 pairctl"
docker exec "$PAIR" sh -c 'test -f /opt/data/scripts/whatsapp-bridge/bridge.js && test -n "$(ls /opt/data/scripts/whatsapp-bridge/node_modules 2>/dev/null)"' \
  && ok "T7 WhatsApp bridge and its packages are where pairing looks for them" || bad "T7 WhatsApp bridge missing (the QR could never start)"
docker exec "$PAIR" grep -q "'/session'" /opt/data/scripts/whatsapp-bridge/bridge.js \
  && ok "T8 bridge hands the QR code to the dashboard (/session)" || bad "T8 bridge has no /session (the dashboard would wait for a code forever)"
docker exec "$PAIR" sh -c 'cd /opt/data/scripts/whatsapp-bridge && [ "$(sha256sum package.json | cut -c1-16)" = "$(cat node_modules/.hermes-pkg-hash 2>/dev/null)" ]' \
  && ok "T9 bridge packages stamped (the gateway will not try to reinstall them)" || bad "T9 bridge package stamp missing (WhatsApp would never start after pairing)"
[ $fail = 0 ] || die "verification failed; the agent is up but NOT assigned to the tenant. Remove with remove-agent.sh --key $KEY --yes"

# ---- T13/T14: the reply guard (td_guard: no photo replies, nothing internal to a customer),
#      HERMES_MAX_TOKENS 4096 and reasoning low, so a reply is never cut and sent in pieces
bash "$HERE/add-td-guard.sh" "$KEY" || die "add-td-guard.sh failed for $KEY"

# ---- T16: the agent remembers a returning customer across the daily session reset
#      (td_memory: name, when they last wrote, their last few exchanges; groups skipped)
bash "$HERE/add-td-memory.sh" "$KEY" || die "add-td-memory.sh failed for $KEY"

# ---- T17: fast replies (td_speed: Gemini 3 Flash Preview with no thinking, reasoning minimal,
#      gemini-3.5-flash-lite the only fallback)
bash "$HERE/add-td-speed.sh" "$KEY" || die "add-td-speed.sh failed for $KEY"

# ---- T18: human takeover (td_takeover: a message the owner sends from the business
#      number pauses Hermes in that chat for 2 minutes after their latest message)
bash "$HERE/add-td-takeover.sh" "$KEY" || die "add-td-takeover.sh failed for $KEY"

# ---- tell the platform which agent this tenant uses (TechDeva action)
if [ $ASSIGN = 1 ]; then
  cd "$PLATFORM"
  TOKEN=$(docker compose exec -T api python -c "from app.auth import issue_token;print(issue_token(tenant_id=None,user_id='agent-template',role='techdeva_admin',ttl=120))")
  curl -fsS -X POST https://api.techdeva.net/whatsapp/assign -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    -d "{\"tenant_id\":\"$TENANT\",\"agent_key\":\"$KEY\",\"pairctl_url\":\"http://$AGENT:3010\"}" && echo && echo "assigned to tenant $TENANT"
fi
echo "DONE: $AGENT is running. The owner connects WhatsApp from their dashboard (WhatsApp Connection -> scan)."
echo "Make sure pairwatch runs:  * * * * * $HERE/pairwatch.sh >> /var/log/techdeva-pairwatch.log 2>&1"
