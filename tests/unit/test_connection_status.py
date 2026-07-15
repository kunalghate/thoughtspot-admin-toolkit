"""
Unit tests for the in-memory connection_status registry.

This registry is the single source of truth for live cluster session health —
the thing that stops the "Connected" badge from lying after a session expires.
"""

from __future__ import annotations

import pytest

from ts_admin.services import connection_status
from ts_admin.services.connection_status import ConnectionState


@pytest.fixture(autouse=True)
def _clean_registry():
    """The registry is module-global; wipe it around every test."""
    connection_status._status.clear()
    yield
    connection_status._status.clear()


def test_unknown_by_default():
    health = connection_status.get("never-seen")
    assert health.state is ConnectionState.UNKNOWN
    assert health.detail is None
    assert health.checked_at is None


def test_mark_connected_records_version_and_timestamp():
    connection_status.mark_connected("c1", ts_version="10.5.0")
    health = connection_status.get("c1")
    assert health.state is ConnectionState.CONNECTED
    assert health.ts_version == "10.5.0"
    assert health.checked_at is not None  # ISO timestamp stamped


def test_mark_expired_carries_detail():
    connection_status.mark_expired("c1", detail="session may have expired")
    health = connection_status.get("c1")
    assert health.state is ConnectionState.EXPIRED
    assert "expired" in health.detail


def test_mark_unreachable():
    connection_status.mark_unreachable("c1", detail="Cannot reach ThoughtSpot")
    assert connection_status.get("c1").state is ConnectionState.UNREACHABLE


def test_latest_write_wins():
    connection_status.mark_expired("c1", detail="boom")
    connection_status.mark_connected("c1", ts_version="10.5.0")
    assert connection_status.get("c1").state is ConnectionState.CONNECTED


def test_clear_resets_to_unknown():
    connection_status.mark_expired("c1", detail="boom")
    connection_status.clear("c1")
    assert connection_status.get("c1").state is ConnectionState.UNKNOWN


def test_clear_is_safe_when_absent():
    connection_status.clear("never-seen")  # must not raise


def test_status_isolated_per_cluster():
    connection_status.mark_connected("c1")
    connection_status.mark_expired("c2", detail="dead")
    assert connection_status.get("c1").state is ConnectionState.CONNECTED
    assert connection_status.get("c2").state is ConnectionState.EXPIRED
