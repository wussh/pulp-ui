import ipaddress

import pytest

from app.safety import is_forbidden_address, validate_source_url


def test_validate_source_url_accepts_allowlisted_https(monkeypatch):
    monkeypatch.setattr(
        "app.safety.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
        ],
    )
    url = validate_source_url(
        "https://mirror.example.com/pub/rpm/", ("mirror.example.com",)
    )
    assert url == "https://mirror.example.com/pub/rpm/"


def test_validate_source_url_rejects_http():
    with pytest.raises(ValueError):
        validate_source_url("http://mirror.example.com/", ("mirror.example.com",))


def test_validate_source_url_rejects_unlisted_host():
    with pytest.raises(ValueError):
        validate_source_url("https://evil.example.com/", ("mirror.example.com",))


def test_validate_source_url_rejects_credentials_in_url():
    with pytest.raises(ValueError):
        validate_source_url(
            "https://user:pass@mirror.example.com/", ("mirror.example.com",)
        )


def test_validate_source_url_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(
        "app.safety.socket.getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("169.254.169.254", 443)),
        ],
    )
    with pytest.raises(ValueError):
        validate_source_url("https://mirror.example.com/", ("mirror.example.com",))


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.5",
        "172.16.0.5",
        "192.168.1.5",
        "0.0.0.0",
        "224.0.0.1",
        "::1",
        "fe80::1",
        "fd00::1",
    ],
)
def test_is_forbidden_address_rejects_internal_ranges(address):
    assert is_forbidden_address(ipaddress.ip_address(address)) is True


def test_is_forbidden_address_allows_public():
    assert is_forbidden_address(ipaddress.ip_address("93.184.216.34")) is False
