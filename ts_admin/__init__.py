"""ThoughtSpot Admin Toolkit."""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    # Single source of truth: the version recorded at install time from
    # pyproject.toml. `make release-github` bumps pyproject.toml only, so a
    # literal here would silently report a stale version in the CLI banner,
    # /health, and the in-app update check.
    __version__ = _pkg_version("ts-admin-toolkit")
except PackageNotFoundError:  # running from a source tree with no install
    __version__ = "0.0.0+dev"
