"""
Bulk Sharing API.

Phase B1 (selection mode):
  POST /api/v1/sharing/preview         — diff (object × principal) current vs proposed
  POST /api/v1/sharing/execute         — kick share job
  GET  /api/v1/sharing/principals      — picker for users + groups
  GET  /api/v1/sharing/history         — past share jobs

Phase B2:
  POST /api/v1/sharing/preview-by-tag  — same preview, intake by tag name
  POST /api/v1/sharing/execute-by-tag

Filled in below.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from ts_admin.services import bulk_sharing_service as svc

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sharing", tags=["Bulk Sharing"])


# ── Schemas ────────────────────────────────────────────────────────────────────


class PrincipalItem(BaseModel):
    ts_guid: str
    name: str
    display_name: str
    principal_type: Literal["USER", "USER_GROUP"]


class PrincipalListResponse(BaseModel):
    items: list[PrincipalItem]
    total: int


class PreviewRow(BaseModel):
    object_guid: str
    object_name: str
    object_type: str
    principal_guid: str
    principal_name: str
    principal_type: str
    previous_mode: str
    new_mode: str
    will_change: bool


class PreviewRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    object_guids: list[str] | None = None
    tag_name: str | None = None
    principal_guids: list[str]
    principal_types: list[Literal["USER", "USER_GROUP"]] | None = None  # parallel to principal_guids
    mode: Literal["READ_ONLY", "MODIFY", "NO_ACCESS"]


class PreviewResponse(BaseModel):
    items: list[PreviewRow]
    total: int
    will_change_count: int


class ExecuteRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    object_guids: list[str] | None = None
    tag_name: str | None = None
    principal_guids: list[str]
    principal_types: list[Literal["USER", "USER_GROUP"]] | None = None
    mode: Literal["READ_ONLY", "MODIFY", "NO_ACCESS"]
    notify: bool = False


class JobAcceptedResponse(BaseModel):
    job_id: str
    total: int


class HistoryItem(BaseModel):
    job_id: str
    executed_at: str
    object_count: int
    principal_count: int
    succeeded: int
    failed: int
    status: str


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    total: int
    record_offset: int
    page_size: int


# ── Helpers ────────────────────────────────────────────────────────────────────


def _resolve_cluster_id(cluster_id: str | None) -> str:
    if cluster_id:
        return cluster_id
    from ts_admin.config import load_config

    return load_config().active_cluster.id


# ── Principal picker ───────────────────────────────────────────────────────────


@router.get("/principals", response_model=PrincipalListResponse)
def list_principals(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    search: str | None = Query(default=None),
    include_users: bool = Query(default=True),
    include_groups: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=1000),
) -> PrincipalListResponse:
    """Picker dropdown for users + groups, scoped to a cluster + org."""
    items = svc.list_principals(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        search=search,
        include_users=include_users,
        include_groups=include_groups,
        limit=limit,
    )
    return PrincipalListResponse(items=[PrincipalItem(**i) for i in items], total=len(items))


# ── Preview / Execute ──────────────────────────────────────────────────────────


def _resolve_objects(body: PreviewRequest | ExecuteRequest, cluster_id: str) -> list[str]:
    """Either object_guids OR tag_name must be set. Tag mode resolves to GUIDs."""
    if body.object_guids:
        return list(body.object_guids)
    if body.tag_name:
        return svc.resolve_tag_to_guids(
            cluster_id=cluster_id,
            org_id=body.org_id,
            tag_name=body.tag_name,
        )
    raise HTTPException(status_code=422, detail="Either object_guids or tag_name is required")


@router.post("/preview", response_model=PreviewResponse)
async def preview(body: PreviewRequest) -> PreviewResponse:
    """Diff (object × principal) — current ACL vs. proposed mode."""
    if not body.principal_guids:
        raise HTTPException(status_code=422, detail="principal_guids must not be empty")

    cluster_id = _resolve_cluster_id(body.cluster_id)
    object_guids = _resolve_objects(body, cluster_id)

    result = await svc.preview_share(
        cluster_id=cluster_id,
        org_id=body.org_id,
        object_guids=object_guids,
        principal_guids=body.principal_guids,
        mode=body.mode,
    )
    return PreviewResponse(
        items=[PreviewRow(**r) for r in result["items"]],
        total=result["total"],
        will_change_count=result["will_change_count"],
    )


@router.post("/dryrun", response_model=JobAcceptedResponse, status_code=202)
async def dryrun(body: ExecuteRequest, background_tasks: BackgroundTasks) -> JobAcceptedResponse:
    """
    Start a live, no-write impact check (current ACL vs. proposed mode) as a
    background job. This is the dry-run that gates the destructive execute —
    notably NO_ACCESS revokes. Poll /api/v1/jobs/{job_id} for the summary.
    """
    if not body.principal_guids:
        raise HTTPException(status_code=422, detail="principal_guids must not be empty")

    cluster_id = _resolve_cluster_id(body.cluster_id)
    object_guids = _resolve_objects(body, cluster_id)
    if not object_guids:
        raise HTTPException(status_code=422, detail="0 objects resolved — nothing to share")

    from ts_admin.services.job_service import create_job

    job_id = create_job(
        job_type="bulk_share_dryrun",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "object_guids": object_guids,
            "tag_name": body.tag_name,
            "principal_guids": body.principal_guids,
            "mode": body.mode,
        },
    )
    background_tasks.add_task(
        svc.dryrun_share,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        object_guids=object_guids,
        principal_guids=body.principal_guids,
        mode=body.mode,
    )
    return JobAcceptedResponse(job_id=job_id, total=len(object_guids) * len(body.principal_guids))


@router.post("/execute", response_model=JobAcceptedResponse, status_code=202)
async def execute(body: ExecuteRequest, background_tasks: BackgroundTasks) -> JobAcceptedResponse:
    """Kick the share job."""
    if not body.principal_guids:
        raise HTTPException(status_code=422, detail="principal_guids must not be empty")

    cluster_id = _resolve_cluster_id(body.cluster_id)
    object_guids = _resolve_objects(body, cluster_id)
    if not object_guids:
        raise HTTPException(status_code=422, detail="0 objects resolved — nothing to share")

    from ts_admin.services.job_service import create_job

    job_id = create_job(
        job_type="bulk_share",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "object_guids": object_guids,
            "tag_name": body.tag_name,
            "principal_guids": body.principal_guids,
            "mode": body.mode,
            "notify": body.notify,
        },
    )
    background_tasks.add_task(
        svc.execute_share,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        object_guids=object_guids,
        principal_guids=body.principal_guids,
        mode=body.mode,
        notify=body.notify,
    )
    return JobAcceptedResponse(job_id=job_id, total=len(object_guids) * len(body.principal_guids))


@router.get("/history", response_model=HistoryResponse)
def history(
    cluster_id: str | None = Query(default=None),
    org_id: int | None = Query(default=None),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=50, ge=1, le=200),
) -> HistoryResponse:
    """List past share jobs aggregated by job_id (newest first)."""
    items, total = svc.list_history(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        record_offset=record_offset,
        page_size=page_size,
    )
    return HistoryResponse(
        items=[HistoryItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )
