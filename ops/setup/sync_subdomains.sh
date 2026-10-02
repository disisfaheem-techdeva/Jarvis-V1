#!/bin/bash
# Put every client's own address live: https://<name>.techdeva.net
#
# Runs from cron every 2 minutes (ops/setup/install-subdomain-cron.sh). Every
# business gets an address automatically at signup; this job routes it.
#
#   bash ops/setup/sync_subdomains.sh            route what is missing
#   bash ops/setup/sync_subdomains.sh --dry-run  show what it would do
#
# How: one tiny container, techdeva-tenant-routes (the api's own image running
# `sleep`, about 1 MB), carries the Traefik routing labels for all client
# addresses and sends them to the platform api. Only that container is
# recreated when a name is added: the api, the clinic and Hermes are never
# restarted, and no file in the repo changes (deploys stay clean).
#
# Needs once, in Hostinger DNS: an A record  *  -> this server's IP
# (wildcard). Names whose DNS does not point here yet are skipped and tried
# again next run (an https certificate cannot be issued without it).
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
BASE="${TENANT_BASE_DOMAIN:-techdeva.net}"
NAME=techdeva-tenant-routes
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1
now() { date -u +%FT%TZ; }
exec 9>/var/lock/techdeva-subdomains.lock
flock -n 9 || exit 0

API=$(docker compose ps -q api 2>/dev/null | head -1)
[ -n "$API" ] || { echo "$(now) api container not running"; exit 1; }

subs=$(docker compose exec -T db psql -U postgres -d techdeva -At -c \
  "SELECT subdomain FROM tenants WHERE subdomain IS NOT NULL ORDER BY subdomain" 2>/dev/null | tr -d '\r')
MYIP="${SERVER_IP:-$(hostname -I | awk '{print $1}')}"
# names already routed by docker-compose.override.yml (e.g. drirfan) stay there
fixed=$(grep -oE "Host\(\`[a-z0-9-]+\.$BASE\`\)" docker-compose.override.yml 2>/dev/null | sed -E "s/Host\(\`([a-z0-9-]+)\..*/\1/" | sort -u)

hosts=(); waiting=()
for s in $subs; do
  [[ "$s" =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]] || continue
  grep -qx "$s" <<< "$fixed" && continue
  ip=$( (getent ahostsv4 "$s.$BASE" || true) | awk 'NR==1{print $1}')
  if [ "$ip" = "$MYIP" ]; then hosts+=("$s.$BASE"); else waiting+=("$s"); fi
done
[ ${#waiting[@]} -gt 0 ] && echo "$(now) DNS not pointing here yet (add the wildcard * A record): ${waiting[*]}"

rule=""
for h in "${hosts[@]}"; do rule+="${rule:+ || }Host(\`$h\`)"; done
current=$(docker inspect "$NAME" --format '{{index .Config.Labels "traefik.http.routers.techdeva-auto.rule"}}' 2>/dev/null || true)
if [ -z "$rule" ]; then
  [ -n "$current" ] && [ $DRY = 0 ] && { docker rm -f "$NAME" >/dev/null; echo "$(now) no client addresses to route; removed $NAME"; }
  exit 0
fi
[ "$rule" = "$current" ] && exit 0

# the api's own settings: network Traefik reaches it on, entry points, image
img=$(docker inspect "$API" --format '{{.Config.Image}}')
lab() { docker inspect "$API" --format "{{index .Config.Labels \"$1\"}}" 2>/dev/null; }
net=$(lab traefik.docker.network); [ -n "$net" ] || net=$(docker inspect "$API" --format '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
secure=$(lab traefik.http.routers.techdeva-tenants.entrypoints); secure=${secure:-websecure}
plain=$(lab traefik.http.routers.techdeva-tenants-http.entrypoints)
resolver=$(lab traefik.http.routers.techdeva-tenants.tls.certresolver); resolver=${resolver:-letsencrypt}

if [ $DRY = 1 ]; then echo "$(now) would route: ${hosts[*]} (network $net, entry $secure${plain:+/$plain})"; exit 0; fi
args=(--label traefik.enable=true --label "traefik.docker.network=$net"
      --label "traefik.http.routers.techdeva-auto.rule=$rule"
      --label "traefik.http.routers.techdeva-auto.entrypoints=$secure"
      --label "traefik.http.routers.techdeva-auto.tls.certresolver=$resolver"
      --label "traefik.http.routers.techdeva-auto.service=techdeva-api@docker")
if [ -n "$plain" ]; then
  args+=(--label "traefik.http.routers.techdeva-auto-http.rule=$rule"
         --label "traefik.http.routers.techdeva-auto-http.entrypoints=$plain"
         --label "traefik.http.routers.techdeva-auto-http.middlewares=techdeva-to-https@docker"
         --label "traefik.http.routers.techdeva-auto-http.service=techdeva-api@docker")
fi
docker rm -f "$NAME" >/dev/null 2>&1 || true
docker run -d --name "$NAME" --restart unless-stopped --network "$net" --memory 32m \
  --entrypoint sleep "${args[@]}" "$img" infinity >/dev/null \
  && echo "$(now) routing ${#hosts[@]} client address(es): ${hosts[*]}" \
  || echo "$(now) could not start $NAME"
