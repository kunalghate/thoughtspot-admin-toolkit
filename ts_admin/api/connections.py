"""
Data Connections API — read-only browsing of cached ThoughtSpot connections.

  GET /api/v1/connections   list connections with per-connection object counts

Served entirely from the local cache; run a connections sync to refresh it.
The object counts come from the metadata cache, so they also need a metadata
sync — the response carries `linked_rows` so the page can say which of the two
is missing rather than reporting every connection as unused.

There is deliberately no /connections/{guid}/objects: the Metadata page already
lists objects with every filter an admin needs, so a connection row links there
with `connection_guid` set instead.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ts_admin.services import connection_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/connections", tags=["Connections"])


def _resolve_cluster_id(cluster_id: str | None) -> str:
    if cluster_id:
        return cluster_id
    from ts_admin.config import load_config

    return load_config().active_cluster.id


class ConnectionItem(BaseModel):
    ts_guid: str
    name: str
    description: str
    data_warehouse_type: str
    # Cached objects sitting on this connection, split by type. Empty dict for
    # a connection nothing uses — which is the row an admin is looking for.
    counts_by_type: dict[str, int]
    object_count: int
    synced_at: str | None


class ConnectionListResponse(BaseModel):
    items: list[ConnectionItem]
    total: int
    # Cached objects that carry a connection at all. Zero with a non-empty
    # metadata cache means the cache predates the linkage columns and needs a
    # re-sync — without this the page would report every connection as unused.
    linked_rows: int
    metadata_rows: int


@router.get("", response_model=ConnectionListResponse)
def list_connections(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    search: str | None = Query(default=None, description="Substring match on name, description or type"),
    sort_field: str = Query(default="name"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
) -> ConnectionListResponse:
    """
    Every cached connection, with how many objects sit on each.

    Not paginated: a cluster has hundreds of connections, and the question this
    page exists to answer — "which of these has nothing on it" — cannot be
    answered a page at a time.
    """
    result = svc.list_connections(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        search=search,
        sort_field=sort_field,
        sort_order=sort_order,
    )
    return ConnectionListResponse(
        items=[ConnectionItem(**i) for i in result["items"]],
        total=result["total"],
        linked_rows=result["linked_rows"],
        metadata_rows=result["metadata_rows"],
    )
