#!/bin/bash
# Put a client's own address live: https://<name>.techdeva.net -> the platform.
#
#   bash ops/setup/add_subdomain.sh drirfan
#
# Before running: (1) in the founder console, Clients, give the client the
# address <name>; (2) in Hostinger DNS add an A record <name> -> this server.
#
# What it does: checks the DNS already points here (the https certificate
# cannot be issued otherwise), backs up docker-compose.override.yml, adds
# <name>.techdeva.net to the Traefik rule of the platform's api, recreates
# ONLY the api container (about 10 s; clinic and Hermes untouched), then waits
# for https://<name>.techdeva.net/login to answer.
set -euo pipefail
cd "$(dirname "$0")/../.."
NAME="${1:-}"; BASE="${TENANT_BASE_DOMAIN:-techdeva.net}"
[[ "$NAME" =~ ^[a-z][a-z0-9-]{1,30}[a-z0-9]$ ]] || { echo "usage: $0 <name>   (lowercase letters, digits, -)"; exit 2; }
case "$NAME" in www|api|portal|crm|mail|admin|app|dashboard|login|pin|techdeva|founder) echo "ABORT: $NAME is reserved"; exit 2;; esac
HOST="$NAME.$BASE"; FILE=docker-compose.override.yml
[ -f "$FILE" ] || { echo "ABORT: $FILE not found"; exit 1; }

MYIP="${SERVER_IP:-$(hostname -I | awk '{print $1}')}"
DNSIP="$( (getent ahostsv4 "$HOST" || true) | awk 'NR==1{print $1}')"
if [ "$DNSIP" != "$MYIP" ]; then
  echo "ABORT: $HOST points to '${DNSIP:-nothing}', not this server ($MYIP)."
  echo "Add the DNS record in Hostinger (A, name $NAME, $MYIP), wait a few minutes, run again."
  exit 1
fi

if grep -qF "Host(\`$HOST\`)" "$FILE"; then
  echo "$HOST is already in $FILE"
else
  cp -p "$FILE" "$FILE.bak-$(date +%Y%m%d-%H%M%S)"
  python3 - "$FILE" "$HOST" <<'PY'
import re, sys
path, host = sys.argv[1], sys.argv[2]
s = open(path).read()
rule_re = re.compile(r'(- "traefik\.http\.routers\.techdeva-tenants\.rule=)([^"]*)(")')
if rule_re.search(s):
    s = rule_re.sub(lambda m: m.group(1) + m.group(2) + " || Host(`%s`)" % host + m.group(3), s, count=1)
else:
    anchor = "      - traefik.http.services.techdeva-api.loadbalancer.server.port=8000\n"
    if s.count(anchor) != 1:
        sys.exit("ABORT: could not find the api service port label exactly once; nothing changed")
    s = s.replace(anchor, anchor +
        '      # clients\' own addresses (ops/setup/add_subdomain.sh)\n'
        '      - "traefik.http.routers.techdeva-tenants.rule=Host(`%s`)"\n' % host +
        "      - traefik.http.routers.techdeva-tenants.entrypoints=websecure\n"
        "      - traefik.http.routers.techdeva-tenants.tls.certresolver=letsencrypt\n"
        "      - traefik.http.routers.techdeva-tenants.service=techdeva-api\n")
open(path, "w").write(s)
PY
  echo "added $HOST to $FILE (backup kept next to it)"
fi

docker compose config -q || { echo "ABORT: the compose file no longer parses; restore the .bak copy"; exit 1; }
docker compose up -d --no-deps api
echo "waiting for https://$HOST/login (the certificate can take a minute) ..."
for i in $(seq 1 24); do
  code=$(curl -s -o /dev/null -w '%{http_code}' "https://$HOST/login" || true)
  [ "$code" = "200" ] && { echo "LIVE: https://$HOST/login"; exit 0; }
  sleep 5
done
echo "Not answering yet (last HTTP $code). Check: docker logs traefik-traefik-1 --since 5m | grep -i $NAME"
exit 1
