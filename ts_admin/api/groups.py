"""
Group Management API (v1 is read-only — CS Tools precedent).

  GET  /api/v1/groups             — paginated group grid (cluster + org scoped)
  GET  /api/v1/groups/export.csv  — every matching group as a CSV download
  GET  /api/v1/groups/{ts_guid}   — single group detail with member users

Data comes from the local cache; run a groups sync to refresh it.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from ts_admin.api.csv_export import csv_response, paged_rows
from ts_admin.services import group_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/groups", tags=["Group Management"])


# ── Schemas ────────────────────────────────────────────────────────────────────


class GroupListItem(BaseModel):
    ts_guid: str
    name: str
    display_name: str
    description: str
    org_id: int
    privileges: list[str]
    member_count: int
    # Display name of the creating user, or the raw GUID when that user is not
    # in the cache. None when the group predates the author_guid backfill.
    created_by: str | None
    created_at: str | None
    modified_at: str | None
    synced_at: str | None


class GroupListResponse(BaseModel):
    items: list[GroupListItem]
    total: int
    record_offset: int
    page_size: int


class GroupMember(BaseModel):
    ts_guid: str
    username: str
    display_name: str
    email: str
    status: str


class GroupDetail(GroupListItem):
    members: list[GroupMember]


# ── Helpers ────────────────────────────────────────────────────────────────────


def _resolve_cluster_id(cluster_id: str | None) -> str:
    if cluster_id:
        return cluster_id
    from ts_admin.config import load_config

    return load_config().active_cluster.id


# ── List / detail ──────────────────────────────────────────────────────────────


@router.get("", response_model=GroupListResponse)
def list_groups(
    cluster_id: str | None = Query(default=None),
    org_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_field: str = Query(default="name"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=200, ge=1, le=1000),
) -> GroupListResponse:
    """Paginated group grid for the Groups page."""
    items, total = svc.list_groups(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        search=search,
        sort_field=sort_field,
        sort_order=sort_order,
        record_offset=record_offset,
        page_size=page_size,
    )
    return GroupListResponse(
        items=[GroupListItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


# Column order mirrors the Groups grid, plus the GUID an admin needs to act on a
# row outside the UI.
_CSV_COLUMNS = [
    ("name", "Name"),
    ("display_name", "Display name"),
    ("description", "Description"),
    ("privileges", "Privileges"),
    ("member_count", "Members"),
    ("created_by", "Created by"),
    ("created_at", "Created"),
    ("modified_at", "Modified"),
    ("ts_guid", "GUID"),
    ("org_id", "Org ID"),
]


@router.get("/export.csv")
def export_groups_csv(
    cluster_id: str | None = Query(default=None),
    org_id: int | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_field: str = Query(default="name"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
) -> StreamingResponse:
    """
    Every group matching the current filters, as a CSV download.

    Deliberately not paginated: the grid's own CSV button would only ever export
    the rows AG Grid had cached. Takes the same filter/sort params as the list
    endpoint so the file matches what is on screen.
    """
    resolved = _resolve_cluster_id(cluster_id)
    rows = paged_rows(
        lambda offset, size: svc.list_groups(
            cluster_id=resolved,
            org_id=org_id,
            search=search,
            sort_field=sort_field,
            sort_order=sort_order,
            record_offset=offset,
            page_size=size,
        )
    )
    return csv_response(filename_stem="groups", columns=_CSV_COLUMNS, rows=rows)


@router.get("/{ts_guid}", response_model=GroupDetail)
def get_group(
    ts_guid: str,
    cluster_id: str | None = Query(default=None),
    org_id: int | None = Query(default=None, description="Disambiguates a GUID cached in several orgs."),
) -> GroupDetail:
    """Single group detail — feeds the detail drawer on the Groups page."""
    detail = svc.get_group_detail(
        cluster_id=_resolve_cluster_id(cluster_id),
        ts_guid=ts_guid,
        org_id=org_id,
    )
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Group {ts_guid!r} not found in cache")
    return GroupDetail(**detail)
