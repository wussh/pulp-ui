import ipaddress
import socket
from urllib.parse import urlsplit

_FORBIDDEN_FLAGS = (
    "is_private",
    "is_loopback",
    "is_link_local",
    "is_multicast",
    "is_unspecified",
    "is_reserved",
)


def is_forbidden_address(address) -> bool:
    if address.is_global is False:
        return True
    return any(getattr(address, flag) for flag in _FORBIDDEN_FLAGS)


def validate_source_url(url: str, allowed_hosts: tuple[str, ...]) -> str:
    candidate = (url or "").strip()
    parts = urlsplit(candidate)
    if parts.scheme != "https":
        raise ValueError("source URL must use https")
    if parts.username or parts.password:
        raise ValueError("source URL must not embed credentials")
    host = (parts.hostname or "").lower()
    if not host:
        raise ValueError("source URL must include a hostname")
    if host not in allowed_hosts:
        raise ValueError("source host is not allowlisted")
    for entry in socket.getaddrinfo(host, parts.port or 443, proto=socket.IPPROTO_TCP):
        address = ipaddress.ip_address(entry[4][0])
        if is_forbidden_address(address):
            raise ValueError("source host resolves to a forbidden address")
    return candidate
