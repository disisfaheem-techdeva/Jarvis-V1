"""The visitor's address behind Traefik and Cloudflare: what every rate limit
and lockout keys on. No database needed."""
from types import SimpleNamespace

from app import account, main, pins, security
from app.client_ip import client_ip, is_cloudflare

CF_EDGE = "172.71.10.5"        # inside 172.64.0.0/13
CF_EDGE6 = "2606:4700:10::ac43:1"


def req(xff=None, cf=None, peer="10.0.0.2"):
    h = {}
    if xff is not None:
        h["x-forwarded-for"] = xff
    if cf is not None:
        h["cf-connecting-ip"] = cf
    return SimpleNamespace(headers=h, client=SimpleNamespace(host=peer))


def test_without_cloudflare_the_address_traefik_saw_wins():
    assert client_ip(req("203.0.113.9")) == "203.0.113.9"


def test_caller_written_entries_are_ignored():
    # the caller sent "1.2.3.4"; Traefik appended the real 203.0.113.9
    assert client_ip(req("1.2.3.4, 203.0.113.9")) == "203.0.113.9"


def test_behind_cloudflare_the_visitor_is_cf_connecting_ip():
    assert client_ip(req("198.51.100.7, " + CF_EDGE, cf="198.51.100.7")) == "198.51.100.7"
    assert client_ip(req(CF_EDGE6, cf="2001:db8::5")) == "2001:db8::5"


def test_a_forged_cf_header_sent_straight_to_the_server_counts_for_nothing():
    assert client_ip(req("203.0.113.9", cf="9.9.9.9")) == "203.0.113.9"


def test_garbage_cf_header_falls_back_to_the_edge_address():
    assert client_ip(req(CF_EDGE, cf="not-an-ip")) == CF_EDGE


def test_no_proxy_header_uses_the_socket():
    assert client_ip(req()) == "10.0.0.2"


def test_ranges():
    assert is_cloudflare("104.16.1.1") and is_cloudflare(CF_EDGE6)
    assert not is_cloudflare("8.8.8.8") and not is_cloudflare("") and not is_cloudflare("x")


def test_every_limiter_uses_the_same_answer():
    r = req("1.2.3.4, " + CF_EDGE, cf="198.51.100.7")
    assert security.client_ip(r) == main._client_ip(r) == account._ip(r) == pins._ip(r) == "198.51.100.7"
