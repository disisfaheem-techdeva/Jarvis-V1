#!/bin/bash
# T7: prove the template on a throwaway agent, then remove it.
#   bash ops/agent-template/prove.sh
# Never touches hermes-agent-irfan, pairctl-irfan or hermes-workspace-ofsd-*:
# it creates hermes-agent-tdprobe<random>, inspects it, and removes it.
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; PLATFORM="$(cd "$HERE/../.." && pwd)"
KEY="tdprobe$(head -c4 /dev/urandom | od -An -tx1 | tr -d ' \n')"
A="hermes-agent-$KEY"; P="pairctl-$KEY"
QA=$(cd "$PLATFORM" && docker compose exec -T db psql -U postgres -d techdeva -tAc "SELECT id FROM tenants WHERE name='TechDeva QA Test Co'")
[ -n "$QA" ] || { echo "QA tenant not found"; exit 1; }
protected() { docker ps -aq --filter name=hermes-agent-irfan --filter name=pairctl-irfan --filter name=hermes-workspace-ofsd | xargs -r docker inspect -f '{{.Name}} {{slice .Id 0 12}} started={{.State.StartedAt}}' | sort; }
protected_before=$(protected)

echo "===== 1. provision $A from the template (QA tenant, not assigned)"
bash "$HERE/new-agent.sh" --key "$KEY" --tenant "$QA" --owner-phone 923000000000 \
  --home-name "QA Probe Owner" --business "TechDeva QA Test Co" --assistant Zara --no-assign
rc=$?

if [ $rc = 0 ]; then
  echo; echo "===== 2. effective config the gateway loads (T1/T2/T3)"
  docker exec "$A" python3 -c "
import yaml; c = yaml.safe_load(open('/opt/data/config.yaml'))
print('model           :', c['model'])
print('fallbacks       :', c['fallback_providers'])
print('providers       :', c.get('providers'))
print('disabled_toolsets:', c['agent']['disabled_toolsets'])
print('display         : tool_progress=%s show_reasoning=%s' % (c['display']['tool_progress'], c['display'].get('show_reasoning')))"
  echo; echo "===== 3. tools the agent reports"
  docker exec "$A" sh -c 'hermes tools list 2>/dev/null || hermes tools 2>/dev/null || hermes toolsets 2>/dev/null' | head -40 \
    || echo "(this Hermes version has no tools-list command; the lockdown is the disabled_toolsets above)"
  echo; echo "===== 4. T3 gateway patch in the running image"
  docker exec "$A" grep -n -A2 '== "whatsapp"' /opt/hermes/gateway/run.py | head -4
  echo; echo "===== 5. T4 home channel env"
  docker exec "$A" sh -c 'echo WHATSAPP_HOME_CHANNEL=$WHATSAPP_HOME_CHANNEL; echo WHATSAPP_HOME_CHANNEL_NAME=$WHATSAPP_HOME_CHANNEL_NAME; echo OPENROUTER_API_KEY=${OPENROUTER_API_KEY:-<empty>}'
  echo; echo "===== 6. T5 SOUL voice block"
  docker exec "$A" sed -n '/TECHDEVA-VOICE-START/,/TECHDEVA-VOICE-END/p' /opt/data/SOUL.md
  echo; echo "===== 7. T6 wiring: pairctl shares the agent network; pairwatch restarts after a pairing"
  echo "pairctl network: $(docker inspect -f '{{.HostConfig.NetworkMode}}' "$P") (agent id $(docker inspect -f '{{.Id}}' "$A" | cut -c1-12))"
  s1=$(docker inspect -f '{{.State.StartedAt}}' "$A")
  sleep 2
  docker exec "$P" node -e "require('fs').mkdirSync('/opt/data/pairing',{recursive:true});require('fs').writeFileSync('/opt/data/pairing/techdeva-paired.json','{\"probe\":true}')"
  bash "$HERE/pairwatch.sh" | grep "$KEY" || true
  s2=$(docker inspect -f '{{.State.StartedAt}}' "$A")
  if [ "$s1" != "$s2" ]; then echo "PASS T6 pairwatch restarted $A after the pairing marker ($s1 -> $s2)"; else echo "FAIL T6 pairwatch did not restart $A"; fi
  bash "$HERE/pairwatch.sh" | grep "$KEY" && echo "FAIL T6 restarted twice" || echo "PASS T6 second run does nothing (restart happens once)"
fi

echo; echo "===== 8. remove the throwaway agent"
bash "$HERE/remove-agent.sh" --key "$KEY" --yes
left=$(docker ps -a --format '{{.Names}}' | grep -c "$KEY" || true)
vol=$(docker volume ls --format '{{.Name}}' | grep -c "$KEY" || true)
[ -d "/docker/hermes-$KEY" ] && d=1 || d=0
echo "leftovers: containers=$left volumes=$vol dir=$d"
protected_after=$(protected)
if [ "$protected_before" = "$protected_after" ]; then echo "PASS protected containers untouched (same ids, same uptime)"; else echo "CHECK protected containers changed:"; diff <(echo "$protected_before") <(echo "$protected_after"); fi
echo "$protected_after"
