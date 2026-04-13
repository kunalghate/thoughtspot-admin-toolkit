"""
Metadata Explorer API — read-only endpoints for browsing cached TS content.

All responses come from the local SQLite cache, except /permissions which
calls ThoughtSpot live (permissions are never cached).

Endpoints:
  GET  /api/v1/metadata                          list + filter metadata objects
  GET  /api/v1/metadata/stats                    aggregate stats (for dashboard)
  GET  /api/v1/metadata/{guid}                   single object detail
  GET  /api/v1/metadata/{guid}/permissions       live permissions from ThoughtSpot
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.services.metadata_service import MetadataService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/metadata", tags=["Metadata"])

# ── Response schemas ───────────────────────────────────────────────────────────

class MetadataObjectResponse(BaseModel):
    ts_guid: str
    name: str
    object_type: str
    owner_guid: str
    owner_name: str
    org_id: int
    tags: list[str]
    created_at: str | None
    modified_at: str | None
    last_accessed_at: str | None
    view_count: int

    @classmethod
    def from_cache(cls, obj: CachedMetadata) -> "MetadataObjectResponse":
        return cls(
            ts_guid=obj.ts_guid,
            name=obj.name,
            object_type=obj.object_type,
            owner_guid=obj.owner_guid,
            owner_name=obj.owner_name,
            org_id=obj.org_id,
            tags=obj.get_tag_names(),
            created_at=obj.created_at.isoformat() if obj.created_at else None,
            modified_at=obj.modified_at.isoformat() if obj.modified_at else None,
            last_accessed_at=obj.last_accessed_at.isoformat() if obj.last_accessed_at else None,
            view_count=obj.view_count,
        )


class MetadataListResponse(BaseModel):
    items: list[MetadataObjectResponse]
    total: int
    page: int
    page_size: int


class MetadataStatsResponse(BaseModel):
    total: int
    by_type: dict[str, int]
    stale_90d: int
    last_synced: str | None


class PermissionEntry(BaseModel):
    principal_id: str
    principal_name: str
    principal_type: str   # "USER" or "USER_GROUP"
    share_mode: str       # "READ_ONLY" or "MODIFY"


class PermissionsResponse(BaseModel):
    ts_guid: str
    object_name: str
    permissions: list[PermissionEntry]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=MetadataListResponse)
def list_metadata(
    cluster_id: str | None = Query(default=None, description="Cluster ID (defaults to active cluster)"),
    org_id: int = Query(default=0, description="Org ID"),
    types: list[str] | None = Query(default=None, description="Filter by object type"),
    owner_guid: str | None = Query(default=None, description="Filter by owner GUID"),
    tag_names: list[str] | None = Query(default=None, description="Filter by tag name(s)"),
    search: str | None = Query(default=None, description="Substring search on name"),
    stale_days: int | None = Query(default=None, ge=1, description="Only objects unused for N+ days"),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=200, ge=1, le=1000),
) -> MetadataListResponse:
    """
    List metadata objects from the local cache with optional filters.

    Results are served entirely from SQLite — no ThoughtSpot API calls.
    Trigger a sync first via POST /api/v1/sync/{cluster_id}/{org_id}/metadata.
    """
    if not cluster_id:
        from ts_admin.config import load_config
        cluster_id = load_config().active_cluster.id

    items, total = MetadataService.search(
        cluster_id=cluster_id,
        org_id=org_id,
        types=types,
        owner_guid=owner_guid,
        tag_names=tag_names,
        search=search,
        stale_days=stale_days,
        record_offset=record_offset,
        page_size=page_size,
    )
    return MetadataListResponse(
        items=[MetadataObjectResponse.from_cache(i) for i in items],
        total=total,
        page=record_offset // page_size + 1,
        page_size=page_size,
    )


@router.get("/stats", response_model=MetadataStatsResponse)
def metadata_stats(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> MetadataStatsResponse:
    """Aggregate stats for the dashboard health card."""
    if not cluster_id:
        from ts_admin.config import load_config
        cluster_id = load_config().active_cluster.id
    stats = MetadataService.stats(cluster_id=cluster_id, org_id=org_id)
    return MetadataStatsResponse(**stats)


@router.get("/{ts_guid}/permissions", response_model=PermissionsResponse)
async def get_permissions(
    ts_guid: str,
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> PermissionsResponse:
    """
    Fetch live permissions for a metadata object from ThoughtSpot.

    This endpoint calls ThoughtSpot directly — results are not cached.
    Requires an active connection to the ThoughtSpot cluster.
    """
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient

    config = load_config()
    if not cluster_id:
        cluster_id = config.active_cluster.id

    # Look up the object in cache to get its name and type
    obj = MetadataService.get(cluster_id=cluster_id, org_id=org_id, ts_guid=ts_guid)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metadata object {ts_guid!r} not found in cache. Sync first.",
        )

    cluster = config.active_cluster
    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy()) as client:
        perms = await client.fetch_permissions(
            ts_guid=ts_guid,
            object_type=obj.object_type,
        )

    return PermissionsResponse(
        ts_guid=ts_guid,
        object_name=obj.name,
        permissions=[
            PermissionEntry(
                principal_id=p.principal_id,
                principal_name=p.principal_name,
                principal_type=p.principal_type,
                share_mode=p.share_mode,
            )
            for p in perms
        ],
    )


@router.get("/{ts_guid}", response_model=MetadataObjectResponse)
def get_metadata(
    ts_guid: str,
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> MetadataObjectResponse:
    """Return a single metadata object by ThoughtSpot GUID."""
    if not cluster_id:
        from ts_admin.config import load_config
        cluster_id = load_config().active_cluster.id
    obj = MetadataService.get(cluster_id=cluster_id, org_id=org_id, ts_guid=ts_guid)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metadata object {ts_guid!r} not found in cache. Sync first.",
        )
    return MetadataObjectResponse.from_cache(obj)
