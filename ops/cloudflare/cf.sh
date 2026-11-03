#!/usr/bin/env bash
# Cloudflare for techdeva.net, through the Cloudflare API (4 Nov 2026).
#
#   export CF_API_TOKEN=...      # a token with Zone:Read, Zone Settings:Edit, DNS:Edit,
#                                # Zone WAF:Edit for techdeva.net ONLY; never commit it
#   bash ops/cloudflare/cf.sh status                 # what is set now; changes nothing
#   bash ops/cloudflare/cf.sh secure                 # TLS, HTTPS, WAF rules, rate limit (idempotent)
#   bash ops/cloudflare/cf.sh proxy on  api portal   # orange cloud for these names
#   bash ops/cloudflare/cf.sh proxy off api          # back to DNS only (instant rollback)
#
# Mail records (MX, mail.*) and anything that is not an A/AAAA/CNAME are never
# proxied: Hostinger email must keep reaching Hostinger directly.
# Free plan only: no setting here costs money.
set -euo pipefail
ZONE_NAME="${CF_ZONE:-techdeva.net}"
: "${CF_API_TOKEN:?export CF_API_TOKEN first (Cloudflare > My Profile > API Tokens)}"
API=https://api.cloudflare.com/client/v4

cf() {  # cf METHOD PATH [JSON]
  local out
  out=$(curl -sS -X "$1" "$API$2" -H "Authorization: Bearer $CF_API_TOKEN" -H "Content-Type: application/json" ${3:+--data "$3"})
  python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("success") else (print("Cloudflare said: %s" % d.get("errors"), file=sys.stderr) or 1))' <<<"$out" || return 1
  printf '%s' "$out"
}
py() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }

ZONE=$(cf GET "/zones?name=$ZONE_NAME" | py 'r=d["result"]; print(r[0]["id"] if r else "")')
[ -n "$ZONE" ] || { echo "STOP: $ZONE_NAME is not in this Cloudflare account yet (Add a site first)."; exit 1; }

setting() { cf PATCH "/zones/$ZONE/settings/$1" "{\"value\": $2}" >/dev/null && echo "  $1 = $2"; }

status() {
  echo "zone $ZONE_NAME ($ZONE): $(cf GET "/zones/$ZONE" | py 'r=d["result"]; print(r["status"], "plan:", r["plan"]["name"], "ns:", ",".join(r["name_servers"]))')"
  for s in ssl always_use_https min_tls_version tls_1_3 automatic_https_rewrites security_level websockets brotli; do
    echo "  $s = $(cf GET "/zones/$ZONE/settings/$s" | py 'print(d["result"]["value"])')"
  done
  echo "DNS records (proxied = orange cloud):"
  cf GET "/zones/$ZONE/dns_records?per_page=500" | py '
for r in sorted(d["result"], key=lambda r: (r["type"], r["name"])):
    print("  %-6s %-40s %-42s %s" % (r["type"], r["name"], r["content"][:42], "PROXIED" if r.get("proxied") else "dns-only"))'
}

secure() {
  echo "TLS and HTTPS:"
  # strict: Cloudflare checks the server's own certificate (Traefik's Let's Encrypt)
  setting ssl '"strict"'
  setting always_use_https '"on"'
  setting min_tls_version '"1.2"'
  setting tls_1_3 '"on"'
  setting automatic_https_rewrites '"on"'
  setting opportunistic_encryption '"on"'
  setting 0rtt '"off"'
  setting websockets '"on"'
  setting brotli '"on"'
  setting security_level '"medium"'
  setting browser_check '"on"'
  echo "WAF custom rules (block probes for secrets and WordPress brute force):"
  # Bot Fight Mode is deliberately NOT turned on: it challenges Meta/WhatsApp
  # webhooks and website storefront webhooks, which are machines by design.
  cf PUT "/zones/$ZONE/rulesets/phases/http_request_firewall_custom/entrypoint" '{
    "rules": [
      {"description": "TechDeva: block secret-file probes", "action": "block", "enabled": true,
       "expression": "(http.request.uri.path contains \"/.env\") or (http.request.uri.path contains \"/.git\") or (http.request.uri.path contains \"/.aws\") or (ends_with(http.request.uri.path, \".sql\")) or (ends_with(http.request.uri.path, \".bak\"))"},
      {"description": "TechDeva: block WordPress xmlrpc (brute force)", "action": "block", "enabled": true,
       "expression": "(http.request.uri.path eq \"/xmlrpc.php\")"},
      {"description": "TechDeva: challenge wp-login (people pass, bots do not)", "action": "managed_challenge", "enabled": true,
       "expression": "(http.request.uri.path contains \"wp-login.php\")"}
    ]}' >/dev/null && echo "  3 rules set"
  echo "Rate limit (the free plan allows one rule): sign-in and PIN endpoints"
  # On top of the app's own lockouts (pins.py, security.py): this one stops a
  # flood at Cloudflare before it reaches the server at all.
  cf PUT "/zones/$ZONE/rulesets/phases/http_ratelimit/entrypoint" '{
    "rules": [
      {"description": "TechDeva: sign-in flood", "action": "block", "enabled": true,
       "expression": "(starts_with(http.request.uri.path, \"/auth/\") or starts_with(http.request.uri.path, \"/pos/manager-pin\"))",
       "ratelimit": {"characteristics": ["cf.colo.id", "ip.src"], "period": 10, "requests_per_period": 20, "mitigation_timeout": 10}}
    ]}' >/dev/null && echo "  20 requests / 10 s per visitor, then 10 s block"
  echo "done. Check with: bash ops/cloudflare/cf.sh status"
}

proxy() {
  local want="$1"; shift
  [ "$want" = on ] || [ "$want" = off ] || { echo "usage: proxy on|off <name> [name...]"; exit 1; }
  local recs; recs=$(cf GET "/zones/$ZONE/dns_records?per_page=500")
  for n in "$@"; do
    local fq="$n"; [[ "$n" == *"$ZONE_NAME" ]] || fq="$n.$ZONE_NAME"; [ "$n" = "@" ] && fq="$ZONE_NAME"
    case "$fq" in mail.*|webmail.*|autodiscover.*|autoconfig.*|ftp.*) echo "  skip $fq: mail/ftp stays DNS only"; continue;; esac
    local ids; ids=$(py "print(' '.join(r['id'] for r in d['result'] if r['name']=='$fq' and r['type'] in ('A','AAAA','CNAME')))" <<<"$recs")
    [ -n "$ids" ] || { echo "  $fq: no A/AAAA/CNAME record"; continue; }
    for id in $ids; do
      cf PATCH "/zones/$ZONE/dns_records/$id" "{\"proxied\": $([ "$want" = on ] && echo true || echo false)}" >/dev/null
    done
    echo "  $fq -> $([ "$want" = on ] && echo PROXIED || echo dns-only)"
  done
}

case "${1:-status}" in
  status) status ;;
  secure) secure ;;
  proxy)  shift; proxy "$@" ;;
  *) echo "usage: cf.sh status | secure | proxy on|off <names>"; exit 1 ;;
esac
