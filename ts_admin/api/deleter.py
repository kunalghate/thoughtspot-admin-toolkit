"""
Bulk Deleter API.

Three intake modes turn a user selection into a list of GUIDs; the dryrun
and execute endpoints then delegate to the shared deletion_service that
also powers the Archiver's Phase 5 delete.

Resolve modes:
  POST /api/v1/deleter/resolve/downstream  — root GUID → its dependents
  POST /api/v1/deleter/resolve/tag         — tag name → all tagged objects
  POST /api/v1/deleter/resolve/list        — GUID list → matched rows + unrecognized

Helpers for the UI:
  GET  /api/v1/deleter/tags                — list of available tags for picker
  GET  /api/v1/deleter/roots/search        — autocomplete for Downstream mode

Dryrun + execute (delegate to deletion_service):
  POST /api/v1/deleter/dryrun              — start impact check job
  GET  /api/v1/deleter/dryrun/{job_id}/objects — paginated objects in dryrun
  POST /api/v1/deleter/execute             — start TML-backup-then-delete job
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from ts_admin.api.archiver import ArchiverResultItem, DryRunObjectsResponse
from ts_admin.services import deleter_service, deletion_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/deleter", tags=["Bulk Deleter"])


# ── Schemas ────────────────────────────────────────────────────────────────────


class DownstreamRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    root_guid: str
    root_type: str  # "WORKSHEET" | "TABLE" | "MODEL" | "VIEW" | …


class TagRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    tag_name: str


class ListRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    guids: list[str]


class ResolveResponse(BaseModel):
    items: list[ArchiverResultItem]
    total: int
    by_type: dict[str, int]
    # Mode-specific extras (always present, mode-irrelevant fields are empty):
    root_guid: str | None = None
    tag_name: str | None = None
    unrecognized: list[str] = []


class RootSearchItem(BaseModel):
    ts_guid: str
    name: str
    object_type: str
    owner_name: str


class DryRunRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    object_ids: list[str]


class DryRunResponse(BaseModel):
    job_id: str
    total: int


class ExecuteRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    object_ids: list[str]


class ExecuteResponse(BaseModel):
    job_id: str
    total: int


def _resolve_cluster_id(cluster_id: str | None) -> str:
    if cluster_id:
        return cluster_id
    from ts_admin.config import load_config

    return load_config().active_cluster.id


# ── Resolve endpoints ──────────────────────────────────────────────────────────


@router.post("/resolve/downstream", response_model=ResolveResponse)
async def resolve_downstream(body: DownstreamRequest) -> ResolveResponse:
    """Return objects that depend on the given root GUID. Root excluded."""
    if not body.root_guid:
        raise HTTPException(status_code=422, detail="root_guid is required")
    cluster_id = _resolve_cluster_id(body.cluster_id)

    result = await deleter_service.resolve_downstream(
        root_guid=body.root_guid,
        root_type=body.root_type,
        cluster_id=cluster_id,
        org_id=body.org_id,
    )
    return ResolveResponse(
        items=[ArchiverResultItem(**i) for i in result["items"]],
        total=result["total"],
        by_type=result["by_type"],
        root_guid=result.get("root_guid"),
    )


class DeleteTagOnlyResponse(BaseModel):
    tag_id: str
    tag_name: str
    removed_from: int


@router.post("/delete-tag-only", response_model=DeleteTagOnlyResponse)
async def delete_tag_only(body: TagRequest) -> DeleteTagOnlyResponse:
    """
    Delete just the tag itself — leaves objects in place, only strips the label.

    Synchronous because it's a single TS API call + a small local cache update.
    Mirrors the CLI's `bulk-deleter from-tag --tag-only`.
    """
    if not body.tag_name:
        raise HTTPException(status_code=422, detail="tag_name is required")
    cluster_id = _resolve_cluster_id(body.cluster_id)

    try:
        result = await deleter_service.delete_tag_only(
            tag_name=body.tag_name,
            cluster_id=cluster_id,
            org_id=body.org_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return DeleteTagOnlyResponse(**result)


@router.post("/resolve/tag", response_model=ResolveResponse)
def resolve_tag(body: TagRequest) -> ResolveResponse:
    """Return all CachedMetadata rows tagged with the given tag name."""
    if not body.tag_name:
        raise HTTPException(status_code=422, detail="tag_name is required")
    cluster_id = _resolve_cluster_id(body.cluster_id)

    result = deleter_service.resolve_tag(
        tag_name=body.tag_name,
        cluster_id=cluster_id,
        org_id=body.org_id,
    )
    return ResolveResponse(
        items=[ArchiverResultItem(**i) for i in result["items"]],
        total=result["total"],
        by_type=result["by_type"],
        tag_name=result.get("tag_name"),
    )


@router.post("/resolve/list", response_model=ResolveResponse)
def resolve_list(body: ListRequest) -> ResolveResponse:
    """Look up an explicit GUID list; report unrecognized GUIDs separately."""
    if not body.guids:
        raise HTTPException(status_code=422, detail="guids must not be empty")
    cluster_id = _resolve_cluster_id(body.cluster_id)

    result = deleter_service.resolve_list(
        guids=body.guids,
        cluster_id=cluster_id,
        org_id=body.org_id,
    )
    return ResolveResponse(
        items=[ArchiverResultItem(**i) for i in result["items"]],
        total=result["total"],
        by_type=result["by_type"],
        unrecognized=result.get("unrecognized", []),
    )


# ── UI helper endpoints ────────────────────────────────────────────────────────


@router.get("/tags", response_model=list[str])
def deleter_tags(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> list[str]:
    """Distinct tag names present on cached, user-owned content."""
    return deleter_service.list_available_tags(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
    )


@router.get("/roots/search", response_model=list[RootSearchItem])
def deleter_root_search(
    query: str = Query(min_length=1),
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    types: list[str] | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
) -> list[RootSearchItem]:
    """Autocomplete for Downstream mode — pick a root by name."""
    rows = deleter_service.search_roots(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        query=query,
        types=types,
        limit=limit,
    )
    return [RootSearchItem(**r) for r in rows]


# ── Dryrun + Execute (delegate to deletion_service) ────────────────────────────


@router.post("/dryrun", response_model=DryRunResponse, status_code=202)
async def deleter_dryrun(
    body: DryRunRequest,
    background_tasks: BackgroundTasks,
) -> DryRunResponse:
    """
    Start a background impact check (permissions + dependencies) for the
    selected GUIDs. Identical pipeline to the Archiver dryrun, but tagged
    with job_type='bulk_delete_dryrun' so History can attribute the source.
    """
    from ts_admin.services.job_service import create_job

    if not body.object_ids:
        raise HTTPException(status_code=422, detail="object_ids must not be empty")

    cluster_id = _resolve_cluster_id(body.cluster_id)

    job_id = create_job(
        job_type="bulk_delete_dryrun",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "object_ids": body.object_ids,
        },
    )
    background_tasks.add_task(
        deletion_service.dryrun,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        object_ids=body.object_ids,
    )
    return DryRunResponse(job_id=job_id, total=len(body.object_ids))


@router.get("/dryrun/{job_id}/objects", response_model=DryRunObjectsResponse)
def deleter_dryrun_objects(
    job_id: str,
    cluster_id: str | None = Query(default=None),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=100, ge=1, le=500),
) -> DryRunObjectsResponse:
    """Paginated list of objects queued in a dryrun job (for the modal grid)."""
    items, total = deletion_service.dryrun_objects(
        job_id=job_id,
        cluster_id=_resolve_cluster_id(cluster_id),
        record_offset=record_offset,
        page_size=page_size,
    )
    return DryRunObjectsResponse(
        items=[ArchiverResultItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


@router.post("/execute", response_model=ExecuteResponse, status_code=202)
async def deleter_execute(
    body: ExecuteRequest,
    background_tasks: BackgroundTasks,
) -> ExecuteResponse:
    """
    Run TML backup → delete → audit on the selected GUIDs.

    Records land in the same archive_records table the Archiver uses, so
    Restore from History works identically. Audit log uses
    action_type='bulk_delete' to differentiate from Archiver deletes.
    """
    from ts_admin.services.job_service import create_job

    if not body.object_ids:
        raise HTTPException(status_code=422, detail="object_ids must not be empty")

    cluster_id = _resolve_cluster_id(body.cluster_id)

    job_id = create_job(
        job_type="bulk_delete",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "object_ids": body.object_ids,
        },
    )
    background_tasks.add_task(
        deletion_service._execute_delete,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        object_ids=body.object_ids,
        action_type="bulk_delete",
    )
    return ExecuteResponse(job_id=job_id, total=len(body.object_ids))
