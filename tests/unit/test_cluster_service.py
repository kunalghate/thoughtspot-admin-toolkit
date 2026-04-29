"""
Unit tests for cluster_service.validate_cluster_url.

This is a security boundary: the function prevents an attacker (or careless admin)
from pointing the toolkit at internal infrastructure via SSRF. It must reject:
  - non-HTTPS schemes
  - localhost / loopback hostnames and IPs
  - private IP ranges (RFC 1918)
  - link-local addresses (cloud metadata endpoints, e.g. 169.254.169.254)
"""

from __future__ import annotations

import pytest

from ts_admin.services.cluster_service import validate_cluster_url
from ts_admin.ts_client.exceptions import ConfigInvalidError


class TestValidUrls:
    def test_accepts_public_https_host(self):
        assert validate_cluster_url("https://acme.thoughtspot.cloud") == "https://acme.thoughtspot.cloud"

    def test_strips_trailing_slash(self):
        assert validate_cluster_url("https://acme.thoughtspot.cloud/") == "https://acme.thoughtspot.cloud"

    def test_strips_whitespace(self):
        assert validate_cluster_url("  https://acme.thoughtspot.cloud  ") == "https://acme.thoughtspot.cloud"

    def test_accepts_with_port(self):
        assert validate_cluster_url("https://acme.thoughtspot.cloud:443") == "https://acme.thoughtspot.cloud:443"

    def test_accepts_subdomain(self):
        assert validate_cluster_url("https://team.acme.thoughtspot.cloud") == "https://team.acme.thoughtspot.cloud"


class TestRejectedScheme:
    @pytest.mark.parametrize(
        "url",
        [
            "http://acme.thoughtspot.cloud",
            "ftp://acme.thoughtspot.cloud",
            "ws://acme.thoughtspot.cloud",
            "file:///etc/passwd",
            "acme.thoughtspot.cloud",  # no scheme at all
        ],
    )
    def test_non_https_rejected(self, url):
        with pytest.raises(ConfigInvalidError, match="HTTPS"):
            validate_cluster_url(url)


class TestRejectedLoopback:
    @pytest.mark.parametrize(
        "url",
        [
            "https://localhost",
            "https://localhost:8080",
            "https://127.0.0.1",
            "https://127.0.0.1:443",
            "https://[::1]",
        ],
    )
    def test_loopback_rejected(self, url):
        with pytest.raises(ConfigInvalidError):
            validate_cluster_url(url)


class TestRejectedPrivateIPv4:
    @pytest.mark.parametrize(
        "ip",
        [
            "10.0.0.1",          # RFC 1918 — Class A private
            "10.255.255.255",
            "172.16.0.1",        # RFC 1918 — Class B private
            "172.31.255.255",
            "192.168.1.1",       # RFC 1918 — Class C private
            "192.168.0.100",
            "169.254.169.254",   # AWS / GCP / Azure metadata endpoint (link-local)
            "127.0.0.2",         # other loopback addresses (127.0.0.0/8)
        ],
    )
    def test_private_ipv4_rejected(self, ip):
        with pytest.raises(ConfigInvalidError, match="private|localhost"):
            validate_cluster_url(f"https://{ip}")


class TestRejectedPrivateIPv6:
    @pytest.mark.parametrize(
        "ip",
        [
            "fc00::1",       # unique local address (RFC 4193)
            "fd12:3456::1",
            "fe80::1",       # link-local
        ],
    )
    def test_private_ipv6_rejected(self, ip):
        with pytest.raises(ConfigInvalidError, match="private|localhost"):
            validate_cluster_url(f"https://[{ip}]")


class TestIPv4MappedIPv6:
    # Python 3.12+ correctly treats IPv4-mapped IPv6 as private/loopback via
    # ipaddress.is_private. Locking that in so a future Python regression or a
    # refactor of validate_cluster_url that bypasses ipaddress.ip_address can't
    # silently re-open this SSRF vector.
    @pytest.mark.parametrize("ip", ["::ffff:127.0.0.1", "::ffff:10.0.0.1", "::ffff:192.168.1.1"])
    def test_ipv4_mapped_rejected(self, ip):
        with pytest.raises(ConfigInvalidError):
            validate_cluster_url(f"https://[{ip}]")
