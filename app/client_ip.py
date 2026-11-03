"""The visitor's real address, behind Traefik and (optionally) Cloudflare (4 Nov 2026).

Every rate limit and lockout keys on this address (PIN login, password login,
sign-up, email codes, partner forms), so it must be the person, not a proxy:

  * Traefik appends the address it saw as the LAST X-Forwarded-For entry. The
    entries before it are whatever the caller sent, so they are never trusted.
  * Behind Cloudflare, the address Traefik saw is a Cloudflare edge server,
    shared by thousands of visitors. Then, and only then, the visitor is in
    CF-Connecting-IP, which Cloudflare sets and a visitor cannot forge through
    it. A request that did NOT come from a Cloudflare address keeps its own
    address, so sending a fake CF-Connecting-IP straight to the server gains
    nothing.

Cloudflare's ranges: https://www.cloudflare.com/ips (checked 4 Nov 2026).
CLOUDFLARE_IPS (comma-separated CIDRs) replaces the list without a code change.
"""
from __future__ import annotations

import ipaddress
import os

CLOUDFLARE_DEFAULT = (
    "173.245.48.0/20,103.21.244.0/22,103.22.200.0/22,103.31.4.0/22,141.101.64.0/18,108.162.192.0/18,"
    "190.93.240.0/20,188.114.96.0/20,197.234.240.0/22,198.41.128.0/17,162.158.0.0/15,104.16.0.0/13,"
    "104.24.0.0/14,172.64.0.0/13,131.0.72.0/22,"
    "2400:cb00::/32,2606:4700::/32,2803:f800::/32,2405:b500::/32,2405:8100::/32,2a06:98c0::/29,2c0f:f248::/32"
)


def _networks(spec):
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if part:
            try:
                out.append(ipaddress.ip_network(part, strict=False))
            except ValueError:
                continue
    return tuple(out)


CLOUDFLARE = _networks(os.environ.get("CLOUDFLARE_IPS") or CLOUDFLARE_DEFAULT)


def _ip(value):
    try:
        return ipaddress.ip_address(str(value or "").strip())
    except ValueError:
        return None


def is_cloudflare(addr) -> bool:
    ip = _ip(addr)
    return bool(ip) and any(ip in n for n in CLOUDFLARE)


def peer(request) -> str:
    """The address our own proxy saw (last X-Forwarded-For entry), else the socket."""
    fwd = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
    return fwd[-1] if fwd else (request.client.host if request.client else "")


def client_ip(request) -> str:
    seen = peer(request)
    if is_cloudflare(seen):
        real = _ip(request.headers.get("cf-connecting-ip"))
        if real:
            return str(real)[:64]
    return seen[:64]
