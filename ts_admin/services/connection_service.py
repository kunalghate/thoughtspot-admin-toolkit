"""
Data connections — the link between ThoughtSpot content and the warehouse.

ThoughtSpot's own UI makes it hard to see which connection a table sits on, and
offers no way at all to find connections nothing uses. Both are answered here
from the local cache.

Object counts are computed at read time (GROUP BY over ts_metadata) rather than
stored on the connection row. A stored counter drifts the moment a metadata
sync adds or removes a table; a GROUP BY cannot. It also means a connection
with zero objects — the one an admin is hunting for — falls out of the same
query instead of needing its own bookkeeping.
"""

from __future__ import annotations

import logging

from sqlmodel import Session, col, func, select

import ts_admin.database as _db
from ts_admin.models.cache.ts_connection import CachedConnection
from ts_admin.models.cache.ts_metadata import CachedMetadata

logger = logging.getLogger(__name__)

_SORTABLE = {"name", "data_warehouse_type", "object_count", "synced_at"}


def name_by_guid(*, cluster_id: str, org_id: int) -> dict[str, str]:
    """
    Connection GUID → display name, for resolving the Metadata list's
    `connection_guid` once per page rather than once per row.
    """
    with Session(_db.get_engine()) as session:
        rows = session.exec(
            select(CachedConnection.ts_guid, CachedConnection.name).where(
                CachedConnection.cluster_id == cluster_id,
                CachedConnection.org_id == org_id,
            )
        ).all()
    return {guid: name for guid, name in rows}


def list_connections(
    *,
    cluster_id: str,
    org_id: int,
    search: str | None = None,
    sort_field: str = "name",
    sort_order: str = "asc",
) -> dict:
    """
    Every cached connection with its object counts.

    Not paginated: a cluster has hundreds of connections (measured: 1,307 on
    se-demo), not hundreds of thousands, and the page's whole job is comparison
    across the full list — "which of these has nothing on it" cannot be
    answered a page at a time.

    Returns `linked_rows` alongside the items: the number of cached objects
    that carry a connection at all. Zero of those with a non-empty metadata
    cache means the cache predates the linkage columns, and every connection
    would otherwise look unused — the page says "re-sync metadata" instead of
    lying.
    """
    with Session(_db.get_engine()) as session:
        conn_q = select(CachedConnection).where(
            CachedConnection.cluster_id == cluster_id,
            CachedConnection.org_id == org_id,
        )
        if search:
            pattern = f"%{search}%"
            conn_q = conn_q.where(
                col(CachedConnection.name).ilike(pattern)
                | col(CachedConnection.description).ilike(pattern)
                | col(CachedConnection.data_warehouse_type).ilike(pattern)
            )
        connections = session.exec(conn_q).all()

        # One GROUP BY for every connection's counts, rather than a query per row.
        count_rows = session.exec(
            select(
                CachedMetadata.connection_guid,
                CachedMetadata.object_type,
                func.count().label("n"),
            )
            .where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.connection_guid != "",
            )
            .group_by(CachedMetadata.connection_guid, CachedMetadata.object_type)
        ).all()

        metadata_rows = session.exec(
            select(func.count())
            .select_from(CachedMetadata)
            .where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
            )
        ).one()

    counts: dict[str, dict[str, int]] = {}
    linked_rows = 0
    for guid, object_type, n in count_rows:
        counts.setdefault(guid, {})[object_type] = n
        linked_rows += n

    items = [
        {
            "ts_guid": c.ts_guid,
            "name": c.name,
            "description": c.description,
            "data_warehouse_type": c.data_warehouse_type,
            "counts_by_type": counts.get(c.ts_guid, {}),
            "object_count": sum(counts.get(c.ts_guid, {}).values()),
            "synced_at": c.synced_at.isoformat() if c.synced_at else None,
        }
        for c in connections
    ]

    reverse = sort_order.lower() == "desc"
    key = sort_field if sort_field in _SORTABLE else "name"
    if key == "object_count":
        items.sort(key=lambda i: i["object_count"], reverse=reverse)
    else:
        items.sort(key=lambda i: (i.get(key) or "").lower(), reverse=reverse)

    return {
        "items": items,
        "total": len(items),
        # Objects whose connection is known. Zero here with a non-empty
        # metadata cache means "re-sync metadata", not "nothing is connected".
        "linked_rows": linked_rows,
        "metadata_rows": metadata_rows,
    }
