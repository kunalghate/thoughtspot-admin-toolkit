"""
Update check — is a newer toolkit release available?

The toolkit ships as a wheel attached to a GitHub Release (not PyPI), and
install.sh / install.ps1 install it with `uv tool install --force`. Upgrading is
therefore just "install the newest wheel again" — the problem this service
solves is that a user has no way to LEARN a newer wheel exists.

Two consumers:
  - GET /api/v1/update  — the in-app "update available" pill.
  - `ts-admin-toolkit update` — resolves the wheel URL and installs it.

Everything here fails soft. A machine with no internet access, GitHub being
down, or a repo with no releases yet must never break the app or the banner —
those cases report "could not check", not an error.
"""

from __future__ import annotations

import logging
import re
import time

import httpx
from pydantic import BaseModel

logger = logging.getLogger(__name__)

GITHUB_REPO = "kunalghate/thoughtspot-admin-toolkit"
LATEST_RELEASE_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"

# The command a user types to upgrade. Same on every platform — the point of
# having a CLI subcommand rather than re-quoting the curl|sh one-liner.
UPDATE_COMMAND = "ts-admin-toolkit update"

# GitHub's unauthenticated API allows 60 requests/hour/IP. One check per app
# start plus one every 6 hours is far under that, and a new release is not
# something a user needs to hear about within the minute.
CACHE_TTL_SECONDS = 6 * 60 * 60

_HTTP_TIMEOUT = 6.0

# (expires_at, result)
_cache: tuple[float, UpdateCheck] | None = None


class UpdateCheck(BaseModel):
    """Result of an update check. `checked=False` means we could not reach GitHub."""

    current: str
    latest: str | None = None
    update_available: bool = False
    checked: bool = True
    command: str = UPDATE_COMMAND
    release_url: str = RELEASES_URL
    wheel_url: str | None = None
    error: str | None = None


def _parse_version(raw: str) -> tuple[int, ...] | None:
    """
    "v0.2.10" -> (0, 2, 10). Returns None for anything that is not a plain
    numeric release, which includes the "0.0.0+dev" a source checkout reports.

    Deliberately not a full PEP 440 parser: releases are cut by
    `make release-github v=X.Y.Z`, so the only shape that reaches here is
    numeric. Anything else compares as "unknown" and suppresses the banner
    rather than guessing wrong.
    """
    match = re.fullmatch(r"v?(\d+(?:\.\d+)*)", raw.strip())
    if not match:
        return None
    return tuple(int(part) for part in match.group(1).split("."))


def _is_newer(latest: str, current: str) -> bool:
    latest_parts = _parse_version(latest)
    current_parts = _parse_version(current)
    if latest_parts is None or current_parts is None:
        return False
    # Pad so (0, 2) and (0, 2, 0) compare equal rather than as an upgrade.
    length = max(len(latest_parts), len(current_parts))
    latest_parts += (0,) * (length - len(latest_parts))
    current_parts += (0,) * (length - len(current_parts))
    return latest_parts > current_parts


async def check_for_update(*, force: bool = False) -> UpdateCheck:
    """
    Ask GitHub for the latest release and compare it to the running version.

    Cached for CACHE_TTL_SECONDS. `force=True` bypasses the cache — used by
    `ts-admin-toolkit update`, which must never install a stale cached wheel.
    """
    global _cache

    from ts_admin import __version__

    if not force and _cache is not None and _cache[0] > time.monotonic():
        return _cache[1]

    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(
                LATEST_RELEASE_API,
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            release = response.json()
    except httpx.HTTPError as exc:
        logger.info("Update check could not reach GitHub: %s", exc)
        return UpdateCheck(current=__version__, checked=False, error="Could not reach GitHub.")
    except ValueError as exc:  # malformed JSON body
        logger.info("Update check got an unreadable response: %s", exc)
        return UpdateCheck(current=__version__, checked=False, error="Unreadable response from GitHub.")

    tag = str(release.get("tag_name") or "").lstrip("v")
    if not tag:
        return UpdateCheck(current=__version__, checked=False, error="No published release found.")

    wheel_url = next(
        (
            asset["browser_download_url"]
            for asset in release.get("assets") or []
            if str(asset.get("browser_download_url", "")).endswith(".whl")
        ),
        None,
    )

    result = UpdateCheck(
        current=__version__,
        latest=tag,
        update_available=_is_newer(tag, __version__) and wheel_url is not None,
        wheel_url=wheel_url,
        release_url=str(release.get("html_url") or RELEASES_URL),
    )

    _cache = (time.monotonic() + CACHE_TTL_SECONDS, result)
    return result


def reset_cache() -> None:
    """Drop the cached check. Used by tests."""
    global _cache
    _cache = None
