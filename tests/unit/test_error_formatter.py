"""
Parametrized check that every supported exception class maps to the
expected human-readable copy. New mapped exceptions should be added here
when they're added to error_formatter._MAPPINGS.
"""

from __future__ import annotations

import pytest

from ts_admin.services.error_formatter import format_error
from ts_admin.ts_client.exceptions import (
    ConfigInvalidError,
    ConfigNotFoundError,
    JobInterruptedError,
    KeyringError,
    StaleCacheError,
    TMLConflictError,
    TMLValidationError,
    TSAuthenticationError,
    TSConnectionError,
    TSInsufficientPrivilegesError,
    TSObjectNotFoundError,
    TSRateLimitError,
    TSResponseParseError,
    TSServerError,
    TSSSLError,
    TSTimeoutError,
)


@pytest.mark.parametrize(
    "exc, expected_title_substr, expected_type",
    [
        (TSAuthenticationError("401"), "login expired", "TSAuthenticationError"),
        (TSInsufficientPrivilegesError("403"), "privileges", "TSInsufficientPrivilegesError"),
        (TSTimeoutError("timeout"), "didn't respond", "TSTimeoutError"),
        (TSRateLimitError(retry_after=30), "rate-limited", "TSRateLimitError"),
        (TSSSLError("bad cert"), "TLS/SSL", "TSSSLError"),
        (TSConnectionError("dns failure"), "Couldn't reach", "TSConnectionError"),
        (TSServerError(500, "boom"), "server error", "TSServerError"),
        (TSResponseParseError("https://x", "bad json"), "Unexpected response", "TSResponseParseError"),
        (TSObjectNotFoundError("LIVEBOARD", "abc"), "not found", "TSObjectNotFoundError"),
        (TMLValidationError(["bad column"]), "TML validation", "TMLValidationError"),
        (TMLConflictError("dupe"), "TML import conflict", "TMLConflictError"),
        (KeyringError("locked"), "keychain", "KeyringError"),
        (ConfigNotFoundError("no config"), "No ThoughtSpot cluster", "ConfigNotFoundError"),
        (ConfigInvalidError("bad config"), "configuration is invalid", "ConfigInvalidError"),
        (JobInterruptedError("j1", 5, 10), "interrupted", "JobInterruptedError"),
        (StaleCacheError("metadata", "IN_PROGRESS"), "cache is incomplete", "StaleCacheError"),
    ],
)
def test_format_error_known_exceptions(exc, expected_title_substr, expected_type):
    formatted = format_error(exc)
    assert formatted.error_type == expected_type
    assert expected_title_substr.lower() in formatted.title.lower()
    assert formatted.hint  # non-empty
    assert formatted.display.startswith(formatted.title)


def test_stale_cache_error_hint_names_the_exact_recovery_step():
    """The hint has to be actionable: the ONLY fix is re-running the metadata
    sync, and the user needs to be told where that lives."""
    formatted = format_error(StaleCacheError("metadata", "IN_PROGRESS"))
    assert "Sync" in formatted.hint
    assert "Metadata" in formatted.hint


def test_format_error_unknown_falls_back_to_generic():
    formatted = format_error(ValueError("totally unexpected"))
    assert formatted.error_type == "ValueError"
    assert "Something went wrong" in formatted.title
    assert "support bundle" in formatted.hint.lower()


def test_format_error_httpx_connect_error_recognized_by_module():
    """httpx.ConnectError is matched by module + class name (no httpx import)."""

    class _FakeHttpxConnectError(Exception):
        pass

    _FakeHttpxConnectError.__module__ = "httpx._exceptions"
    _FakeHttpxConnectError.__name__ = "ConnectError"

    formatted = format_error(_FakeHttpxConnectError("dns fail"))
    assert "Couldn't reach" in formatted.title
    assert formatted.error_type == "ConnectError"
