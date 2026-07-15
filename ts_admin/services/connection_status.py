"""
In-memory registry of per-cluster ThoughtSpot session health.

Why this exists
---------------
The "Connected" indicator used to be ephemeral React state: it was set the last
time the user clicked "Test connection" (or when the Shell auto-tested on cluster
switch) and never re-validated. So a cluster could show "Connected" long after
its ThoughtSpot session had expired — the mismatch only surfaced when an
operation (e.g. sync) actually hit a 401.

This registry is the single source of truth for live session health. Any code
path that talks to ThoughtSpot reports the outcome here (connected / expired /
unreachable), and the clusters API surfaces it so the UI can tell the truth.

Why in-memory (not the DB / config)
-----------------------------------
Connection health reflects the *live* session, which is process-scoped. It is
intentionally NOT persisted: on restart every cluster is UNKNOWN until the next
call re-establishes the truth. Persisting a stale "connected" across restarts
would re-introduce the exact bug this module fixes.

This stays out of ts_client/ on purpose — the client is a thin HTTP wrapper with
no business logic. Callers (services, API routers) report status here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum


class ConnectionState(str, Enum):
    """Live health of a cluster's ThoughtSpot session."""

    UNKNOWN = "unknown"  # never checked this process, or checked-then-cleared
    CONNECTED = "connected"  # last call succeeded
    EXPIRED = "expired"  # auth rejected (401) — credentials/session need a reconnect
    UNREACHABLE = "unreachable"  # network / TLS / server error — cluster down or misconfigured


@dataclass(frozen=True)
class ClusterHealth:
    """A point-in-time snapshot of a cluster's session health."""

    state: ConnectionState
    detail: str | None = None
    ts_version: str | None = None
    checked_at: str | None = None  # ISO-8601 UTC


_UNKNOWN = ClusterHealth(state=ConnectionState.UNKNOWN)

# cluster_id → latest health. Process-scoped, never persisted.
_status: dict[str, ClusterHealth] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def mark_connected(cluster_id: str, *, ts_version: str | None = None) -> None:
    """Record that the most recent call to this cluster succeeded."""
    _status[cluster_id] = ClusterHealth(
        state=ConnectionState.CONNECTED,
        ts_version=ts_version,
        checked_at=_now_iso(),
    )


def mark_expired(cluster_id: str, *, detail: str | None = None) -> None:
    """Record that ThoughtSpot rejected auth (401) — the cluster needs a reconnect."""
    _status[cluster_id] = ClusterHealth(
        state=ConnectionState.EXPIRED,
        detail=detail,
        checked_at=_now_iso(),
    )


def mark_unreachable(cluster_id: str, *, detail: str | None = None) -> None:
    """Record that the cluster could not be reached (network / TLS / server error)."""
    _status[cluster_id] = ClusterHealth(
        state=ConnectionState.UNREACHABLE,
        detail=detail,
        checked_at=_now_iso(),
    )


def get(cluster_id: str) -> ClusterHealth:
    """Return the latest known health for a cluster (UNKNOWN if never checked)."""
    return _status.get(cluster_id, _UNKNOWN)


def clear(cluster_id: str) -> None:
    """Forget a cluster's health (e.g. when it is removed or its config changes)."""
    _status.pop(cluster_id, None)


def snapshot() -> dict[str, ClusterHealth]:
    """Return a copy of all known cluster health (for diagnostics/tests)."""
    return dict(_status)
