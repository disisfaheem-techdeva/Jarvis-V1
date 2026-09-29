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

# the name of Traefik's port-80 entry point (for the http -> https redirect)
WEBEP="$(docker inspect traefik-traefik-1 --format '{{join .Config.Cmd " "}} {{join .Args " "}}' 2>/dev/null \
         | grep -oE 'entrypoints\.[A-Za-z0-9_-]+\.address=:80\b' | head -1 | cut -d. -f2 || true)"
cp -p "$FILE" "$FILE.bak-$(date +%Y%m%d-%H%M%S)"
python3 - "$FILE" "$HOST" "$WEBEP" <<'PY2'
import re, sys
path, host, webep = sys.argv[1], sys.argv[2], sys.argv[3]
s = open(path).read()
before = s
anchor = "      - traefik.http.services.techdeva-api.loadbalancer.server.port=8000\n"
rule_re = re.compile(r'(- "traefik\.http\.routers\.techdeva-tenants\.rule=)([^"]*)(")')
m = rule_re.search(s)
if not m:
    if s.count(anchor) != 1:
        sys.exit("ABORT: could not find the api service port label exactly once; nothing changed")
    s = s.replace(anchor, anchor +
        "      # clients' own addresses (ops/setup/add_subdomain.sh)\n"
        '      - "traefik.http.routers.techdeva-tenants.rule=Host(`%s`)"\n' % host +
        "      - traefik.http.routers.techdeva-tenants.entrypoints=websecure\n"
        "      - traefik.http.routers.techdeva-tenants.tls.certresolver=letsencrypt\n"
        "      - traefik.http.routers.techdeva-tenants.service=techdeva-api\n")
    rule = "Host(`%s`)" % host
elif ("Host(`%s`)" % host) not in m.group(2):
    rule = m.group(2) + " || Host(`%s`)" % host
    s = rule_re.sub(lambda x: x.group(1) + rule + x.group(3), s, count=1)
else:
    rule = m.group(2)
# plain http://<name>.techdeva.net -> https (browsers try http first)
if webep:
    http_re = re.compile(r'(- "traefik\.http\.routers\.techdeva-tenants-http\.rule=)([^"]*)(")')
    if http_re.search(s):
        s = http_re.sub(lambda x: x.group(1) + rule + x.group(3), s, count=1)
    else:
        tail = "      - traefik.http.routers.techdeva-tenants.service=techdeva-api\n"
        s = s.replace(tail, tail +
            '      - "traefik.http.routers.techdeva-tenants-http.rule=%s"\n' % rule +
            "      - traefik.http.routers.techdeva-tenants-http.entrypoints=%s\n" % webep +
            "      - traefik.http.routers.techdeva-tenants-http.middlewares=techdeva-to-https\n"
            "      - traefik.http.routers.techdeva-tenants-http.service=techdeva-api\n"
            "      - traefik.http.middlewares.techdeva-to-https.redirectscheme.scheme=https\n"
            "      - traefik.http.middlewares.techdeva-to-https.redirectscheme.permanent=true\n", 1)
open(path, "w").write(s)
print("updated %s%s" % (path, "" if webep else " (no port-80 entry point found: http is not redirected)") if s != before else "%s already up to date" % path)
PY2
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
