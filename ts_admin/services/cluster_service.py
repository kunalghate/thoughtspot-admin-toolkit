"""
Cluster service — validation and helpers for cluster config operations.
"""

import ipaddress
import re
from urllib.parse import urlparse

from ts_admin.ts_client.exceptions import ConfigInvalidError


def validate_cluster_url(url: str) -> str:
    """
    Validate a ThoughtSpot cluster URL.

    Rules:
      - Must use HTTPS
      - Must not be localhost or a private/loopback IP (SSRF prevention)
      - Strips trailing slash

    Returns the validated URL.
    Raises ConfigInvalidError on failure.
    """
    url = url.strip().rstrip("/")

    parsed = urlparse(url)

    if parsed.scheme != "https":
        raise ConfigInvalidError(
            f"ThoughtSpot URL must use HTTPS, got: {parsed.scheme!r}"
        )

    hostname = parsed.hostname or ""

    # Block loopback
    if hostname in ("localhost", "127.0.0.1", "::1"):
        raise ConfigInvalidError(
            "ThoughtSpot URL must not point to localhost."
        )

    # Block private IP ranges (SSRF prevention)
    try:
        addr = ipaddress.ip_address(hostname)
        if addr.is_private or addr.is_loopback or addr.is_link_local:
            raise ConfigInvalidError(
                f"ThoughtSpot URL must not point to a private/internal IP address: {hostname}"
            )
    except ValueError:
        # Not an IP address — it's a hostname, which is fine
        pass

    return url
