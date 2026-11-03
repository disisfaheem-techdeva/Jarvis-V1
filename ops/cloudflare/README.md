# Cloudflare in front of techdeva.net

Cloudflare (Free plan, no cost) becomes the reverse proxy in front of the VPS:
visitors reach Cloudflare, Cloudflare reaches Traefik. It hides the server's
address, absorbs floods, blocks known attacks and rate-limits sign-in.

## What is already in the code

- `app/client_ip.py`: every lockout and rate limit uses the visitor's real
  address. Behind Cloudflare that is `CF-Connecting-IP`, trusted only when the
  request really came from a Cloudflare address. Deploy this BEFORE any
  hostname is proxied, or one person's wrong PINs lock out everyone behind the
  same Cloudflare edge.
- `ops/cloudflare/cf.sh`: sets up the zone through the API (`status`,
  `secure`, `proxy on|off <names>`).
- `ops/cloudflare/cloudflare-only.yml`: Traefik middleware so a proxied
  hostname can only be reached through Cloudflare (step 6).

## Steps

1. **Account (a person, once).** Sign up at dash.cloudflare.com with the
   company email, then Add a site: `techdeva.net`, Free plan. Cloudflare scans
   and imports the DNS records.
2. **Check the imported records** against today's (4 Nov 2026):

   | Name | Type | Points to | Cloudflare |
   |---|---|---|---|
   | `techdeva.net` | A | 77.37.48.122 (Hostinger WordPress) | DNS only first |
   | `www` | AAAA/CNAME | Hostinger | DNS only first |
   | `*` (every business subdomain) | A | 72.61.203.194 | DNS only first |
   | `api`, `portal`, `crm`, `leads`, `sales`, `mail` | A | 72.61.203.194 | DNS only first |
   | MX | MX | mx1/mx2.hostinger.com | always DNS only |
   | TXT | SPF, Facebook and Google verification | as now | — |

   Anything missing, add it before step 3. Set every record to **DNS only**
   (grey cloud) for now: the switch then changes who answers DNS, nothing else.
3. **Nameservers (the domain owner, in Hostinger).** Domains > techdeva.net >
   DNS / Nameservers > change to the two Cloudflare gives. Takes minutes to a
   few hours. Email and every site keep working, because the records are the same.
4. **API token.** Cloudflare > My Profile > API Tokens > Create token >
   Custom: Zone Read, Zone Settings Edit, DNS Edit, Zone WAF Edit, zone
   `techdeva.net` only. Run on the server (the token is never saved anywhere):

       export CF_API_TOKEN=...
       bash ops/cloudflare/cf.sh secure
       bash ops/cloudflare/cf.sh status

5. **Turn the orange cloud on, a few names at a time**, checking each:

       bash ops/cloudflare/cf.sh proxy on portal api
       bash ops/cloudflare/cf.sh proxy on crm leads sales '*'
       bash ops/cloudflare/cf.sh proxy on @ www          # WordPress last

   Check after each: the site opens, sign-in works, a WhatsApp message reaches
   the inbox, a Meta webhook arrives. Rollback is instant:
   `bash ops/cloudflare/cf.sh proxy off <name>`.
6. **Lock the origin** once a hostname is proxied and working: copy
   `cloudflare-only.yml` to `/docker/traefik/dynamic/`, then add the label
   `traefik.http.routers.<router>.middlewares=cloudflare-only@file` to that
   hostname's router and recreate the container. Never for instantgrill.shop
   or the hstgr.cloud dashboards (not on Cloudflare).

## Limits of the Free plan to know

- One request at most 100 MB: a video upload right at the app's 100 MB limit
  can be refused by Cloudflare.
- 100 seconds for the server to answer: a longer AI job in one request ends in
  a 524 error page.
- Bot Fight Mode stays OFF: it challenges WhatsApp/Meta and website webhooks.
