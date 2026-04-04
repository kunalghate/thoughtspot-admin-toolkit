"""
Metadata Explorer API — read-only endpoints for browsing cached TS content.

All responses come from the local SQLite cache.
Sync is triggered separately via POST /api/v1/sync/{cluster_id}/{org_id}/metadata.

Endpoints:
  GET  /api/v1/metadata                     list + filter metadata objects
  GET  /api/v1/metadata/stats               aggregate stats (for dashboard)
  GET  /api/v1/metadata/{guid}              single object detail
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
    tag_names: list[str]
    created_at: str | None
    modified_at: str | None
    last_accessed_at: str | None
    synced_at: str | None

    @classmethod
    def from_cache(cls, obj: CachedMetadata) -> "MetadataObjectResponse":
        return cls(
            ts_guid=obj.ts_guid,
            name=obj.name,
            object_type=obj.object_type,
            owner_guid=obj.owner_guid,
            owner_name=obj.owner_name,
            org_id=obj.org_id,
            tag_names=obj.get_tag_names(),
            created_at=obj.created_at.isoformat() if obj.created_at else None,
            modified_at=obj.modified_at.isoformat() if obj.modified_at else None,
            last_accessed_at=obj.last_accessed_at.isoformat() if obj.last_accessed_at else None,
            synced_at=obj.synced_at.isoformat() if obj.synced_at else None,
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


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=MetadataListResponse)
def list_metadata(
    cluster_id: str = Query(..., description="Cluster ID"),
    org_id: int = Query(..., description="Org ID"),
    types: list[str] | None = Query(default=None, description="Filter by object type"),
    owner_guid: str | None = Query(default=None, description="Filter by owner GUID"),
    tag_names: list[str] | None = Query(default=None, description="Filter by tag name(s)"),
    search: str | None = Query(default=None, description="Substring search on name"),
    stale_days: int | None = Query(default=None, ge=1, description="Only objects unused for N+ days"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=500, ge=1, le=2000),
) -> MetadataListResponse:
    """
    List metadata objects from the local cache with optional filters.

    Results are served entirely from SQLite — no ThoughtSpot API calls.
    Trigger a sync first via POST /api/v1/sync/{cluster_id}/{org_id}/metadata.
    """
    items, total = MetadataService.search(
        cluster_id=cluster_id,
        org_id=org_id,
        types=types,
        owner_guid=owner_guid,
        tag_names=tag_names,
        search=search,
        stale_days=stale_days,
        page=page,
        page_size=page_size,
    )
    return MetadataListResponse(
        items=[MetadataObjectResponse.from_cache(i) for i in items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=MetadataStatsResponse)
def metadata_stats(
    cluster_id: str = Query(...),
    org_id: int = Query(...),
) -> MetadataStatsResponse:
    """Aggregate stats for the dashboard health card."""
    stats = MetadataService.stats(cluster_id=cluster_id, org_id=org_id)
    return MetadataStatsResponse(**stats)


@router.get("/{ts_guid}", response_model=MetadataObjectResponse)
def get_metadata(
    ts_guid: str,
    cluster_id: str = Query(...),
    org_id: int = Query(...),
) -> MetadataObjectResponse:
    """Return a single metadata object by ThoughtSpot GUID."""
    obj = MetadataService.get(cluster_id=cluster_id, org_id=org_id, ts_guid=ts_guid)
    if obj is None:
        raise HTTPException(
            status_code=404,
            detail=f"Metadata object {ts_guid!r} not found in cache. Sync first.",
        )
    return MetadataObjectResponse.from_cache(obj)
