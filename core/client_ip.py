"""
AMIKO - Trusted Client IP Resolution
X-Forwarded-For is honoured only when the TCP peer is a trusted reverse proxy
(AMIKO_TRUSTED_PROXIES, default loopback). Otherwise any client could spoof the header
to evade Fail2Ban bans or to get an arbitrary IP (e.g. an administrator's) banned.
"""

import os
import ipaddress
from functools import lru_cache


def parse_ip(value):
    """Parse an IP string into an ipaddress object (IPv4-mapped IPv6 normalized to IPv4)."""
    try:
        ip = ipaddress.ip_address(str(value or "").strip().split("%")[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped:
        return ip.ipv4_mapped
    return ip


@lru_cache(maxsize=4)
def _parse_trusted(raw: str):
    if raw.strip().lower() in ("", "none"):
        return ()
    nets = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            print(f"[AMIKO Security] Ignoring invalid AMIKO_TRUSTED_PROXIES entry: {part}")
    return tuple(nets)


def trusted_proxy_networks():
    return _parse_trusted(os.environ.get("AMIKO_TRUSTED_PROXIES", "127.0.0.1/32,::1/128"))


def is_trusted_proxy(ip) -> bool:
    ip_obj = ip if isinstance(ip, (ipaddress.IPv4Address, ipaddress.IPv6Address)) else parse_ip(ip)
    if ip_obj is None:
        return False
    return any(ip_obj.version == n.version and ip_obj in n for n in trusted_proxy_networks())


def client_ip_from_environ(environ) -> str:
    """Resolve the real client IP from a WSGI environ (rightmost untrusted X-Forwarded-For hop)."""
    peer = parse_ip(environ.get("REMOTE_ADDR"))
    if peer is None:
        return environ.get("REMOTE_ADDR") or "unknown"
    if not is_trusted_proxy(peer):
        return str(peer)

    hops = [h.strip() for h in environ.get("HTTP_X_FORWARDED_FOR", "").split(",") if h.strip()]
    for hop in reversed(hops):
        hop_ip = parse_ip(hop)
        if hop_ip is None:
            # Malformed chain: do not trust anything beyond the proxy itself
            return str(peer)
        if not is_trusted_proxy(hop_ip):
            return str(hop_ip)
    return str(parse_ip(hops[0])) if hops else str(peer)


def get_client_ip() -> str:
    """Resolve the real client IP for the current Flask request."""
    from flask import request, has_request_context
    if not has_request_context():
        return "unknown"
    return client_ip_from_environ(request.environ)
