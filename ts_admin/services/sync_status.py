"""
Cache-completeness helper — the single source of truth for "is the local cache
certified complete for this (cluster, org, entity)?"

Why this exists
---------------
``sync_service._sync_metadata`` commits a DELETE-all for the org and then
re-pages ``search_metadata`` in spec order (LIVEBOARD, ANSWER, then the five
logical-table subtypes), committing once per page. An interrupted metadata sync
therefore leaves the cache **non-empty but truncated** — liveboards and answers
present, every model and table missing. A row count cannot tell that apart from
a healthy cache.

The completeness signal is the ``sync_log`` row, not the row count. That row is
written IN_PROGRESS *before* the delete (write-ahead invalidation) and flipped
to SUCCESS only after the last page commits, so any interruption anywhere in
between leaves a non-SUCCESS marker behind.

Callers pick one of two postures:

* **Refuse** (:func:`require_authoritative_metadata`) — for operations whose
  *input set* is read from the cache, where a truncated cache silently narrows
  what the user is about to act on.
* **Flag** (:func:`metadata_is_authoritative`) — for read/browse paths, which
  stay usable and just tell the UI the data may be partial.
"""

from __future__ import annotations

from sqlmodel import Session, col, select

from ts_admin.models.sync_log import SyncLog
from ts_admin.ts_client.exceptions import StaleCacheError

# The status written before the destructive part of a sync begins. Part of the
# existing vocabulary (`api/sync.py::EntitySyncStatus.status`) — do not invent
# a new one, the UI renders off this set.
NOT_SYNCED = "NOT_SYNCED"


def last_successful_sync(
    session: Session,
    *,
    cluster_id: str,
    org_id: int,
    entity_type: str,
) -> SyncLog | None:
    """Return the newest SUCCESS ``sync_log`` row for this scope, or None.

    ``sync_log`` has no unique constraint on (cluster_id, org_id, entity_type) —
    every writer today upserts, but nothing enforces it — so the ORDER BY is
    mandatory rather than cosmetic.
    """
    return session.exec(
        select(SyncLog)
        .where(
            SyncLog.cluster_id == cluster_id,
            SyncLog.org_id == org_id,
            SyncLog.entity_type == entity_type,
            SyncLog.status == "SUCCESS",
        )
        .order_by(col(SyncLog.synced_at).desc())
    ).first()


def _current_status(session: Session, *, cluster_id: str, org_id: int, entity_type: str) -> str:
    """Observed status of the newest ``sync_log`` row, whatever it is."""
    row = session.exec(
        select(SyncLog)
        .where(
            SyncLog.cluster_id == cluster_id,
            SyncLog.org_id == org_id,
            SyncLog.entity_type == entity_type,
        )
        .order_by(col(SyncLog.synced_at).desc())
    ).first()
    return row.status if row else NOT_SYNCED


def metadata_is_authoritative(*, cluster_id: str, org_id: int) -> bool:
    """True when the metadata cache for this scope is certified complete."""
    import ts_admin.database as _db  # module import keeps test monkeypatching working

    with Session(_db.get_engine()) as session:
        return last_successful_sync(session, cluster_id=cluster_id, org_id=org_id, entity_type="metadata") is not None


def require_authoritative_metadata(*, cluster_id: str, org_id: int) -> None:
    """Raise :class:`StaleCacheError` unless the metadata cache is complete.

    Fail-closed: an operation that derives its input set from the cache must
    refuse, not warn. The message carries the *observed* status so the user is
    told which failure they are looking at (never synced vs interrupted vs
    failed).
    """
    import ts_admin.database as _db

    with Session(_db.get_engine()) as session:
        if last_successful_sync(session, cluster_id=cluster_id, org_id=org_id, entity_type="metadata") is not None:
            return
        status = _current_status(session, cluster_id=cluster_id, org_id=org_id, entity_type="metadata")
    raise StaleCacheError("metadata", status)
