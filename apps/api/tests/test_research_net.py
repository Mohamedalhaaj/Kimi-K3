"""SSRF and DNS-rebinding tests.

AUDIT §5 (web_tools.py:133,251): the prototype resolved a hostname, checked the
addresses, then discarded them; httpx re-resolved at connect time, so a
short-TTL name could pass validation and connect to 127.0.0.1 or the cloud
metadata endpoint.
"""

from __future__ import annotations

import socket
from unittest.mock import patch

import pytest

from kimi.research.net import (
    UnsafeUrlError,
    _address_is_public,
    resolve_public_addresses,
    validate_url,
)


def fake_getaddrinfo(*addresses: str):
    """Build a getaddrinfo stub returning the given literals."""

    def _stub(host, port, *args, **kwargs):
        out = []
        for addr in addresses:
            family = socket.AF_INET6 if ":" in addr else socket.AF_INET
            sockaddr = (addr, port, 0, 0) if family == socket.AF_INET6 else (addr, port)
            out.append((family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", sockaddr))
        return out

    return _stub


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost/admin",
        "http://localhost.localdomain/",
        "https://metadata.google.internal/computeMetadata/v1/",
        "http://127.0.0.1:8787/api",
        "http://[::1]/",
        "http://169.254.169.254/latest/meta-data/",  # AWS IMDS
        "http://10.0.0.5/internal",
        "http://192.168.1.1/",
        "http://172.16.0.1/",
        "http://0.0.0.0/",
        "http://[fd00:ec2::254]/",  # IPv6 IMDS
        "http://[fe80::1]/",
    ],
)
def test_private_and_metadata_targets_are_refused(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com/",
        "data:text/html,hello",
        "javascript:alert(1)",
        "//example.com/x",
    ],
)
def test_non_http_schemes_are_refused(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        validate_url(url)


def test_dns_rebinding_is_refused_when_any_address_is_private() -> None:
    """A name answering with one public and one private address is rejected."""
    with patch("socket.getaddrinfo", fake_getaddrinfo("93.184.216.34", "127.0.0.1")):
        with pytest.raises(UnsafeUrlError, match="non-public"):
            validate_url("https://rebind.example/")


def test_all_private_resolution_is_refused() -> None:
    with patch("socket.getaddrinfo", fake_getaddrinfo("10.1.2.3")):
        with pytest.raises(UnsafeUrlError):
            validate_url("https://internal.example/")


def test_public_resolution_is_pinned_to_a_validated_address() -> None:
    """The validated address is carried forward, closing the TOCTOU window."""
    with patch("socket.getaddrinfo", fake_getaddrinfo("93.184.216.34")):
        target = validate_url("https://example.com/story?a=1")

    assert target.address == "93.184.216.34"
    assert target.hostname == "example.com"
    # We connect to the IP literal...
    assert target.connect_url.startswith("https://93.184.216.34:443/")
    assert "a=1" in target.connect_url
    # ...while still presenting the hostname, so certificate validation binds
    # to the name rather than the address.
    assert target.host_header == "example.com"


def test_ipv6_literal_is_bracketed_in_the_connect_url() -> None:
    with patch("socket.getaddrinfo", fake_getaddrinfo("2606:2800:220:1:248:1893:25c8:1946")):
        target = validate_url("https://example.com/")
    assert target.connect_url.startswith("https://[2606:2800:")


def test_non_default_port_is_kept_in_the_host_header() -> None:
    with patch("socket.getaddrinfo", fake_getaddrinfo("93.184.216.34")):
        target = validate_url("https://example.com:8443/x")
    assert target.host_header == "example.com:8443"
    assert ":8443" in target.connect_url


def test_resolution_failure_fails_closed() -> None:
    def boom(*_a: object, **_k: object) -> None:
        raise OSError("nxdomain")

    with patch("socket.getaddrinfo", boom):
        with pytest.raises(UnsafeUrlError):
            resolve_public_addresses("nope.example", 443)


def test_ipv4_mapped_ipv6_loopback_is_refused() -> None:
    """::ffff:127.0.0.1 is loopback wearing an IPv6 costume."""
    with pytest.raises(UnsafeUrlError):
        validate_url("http://[::ffff:127.0.0.1]/")


def test_address_policy_directly() -> None:
    import ipaddress

    assert _address_is_public(ipaddress.ip_address("93.184.216.34")) is True
    for bad in ("127.0.0.1", "10.0.0.1", "192.168.0.1", "169.254.169.254", "224.0.0.1", "0.0.0.0"):
        assert _address_is_public(ipaddress.ip_address(bad)) is False


def test_public_ip_literal_url_is_allowed_without_dns() -> None:
    target = validate_url("http://93.184.216.34/page")
    assert target.address == "93.184.216.34"
    assert target.hostname == "93.184.216.34"


def test_error_messages_do_not_leak_the_resolved_address() -> None:
    with patch("socket.getaddrinfo", fake_getaddrinfo("10.1.2.3")):
        with pytest.raises(UnsafeUrlError) as exc:
            validate_url("https://internal.example/")
    assert "10.1.2.3" not in str(exc.value)
