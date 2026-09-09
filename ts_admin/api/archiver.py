"""
Content Archiver API.

Phase 2 (read-only):
  GET  /api/v1/archiver/preview              — stale object count + breakdown
  GET  /api/v1/archiver/results              — paginated stale object list
  GET  /api/v1/archiver/tags                 — available tags for filter

Phase 3 (tag / untag):
  POST /api/v1/archiver/execute              — tag or untag a selection

Phase 4 (dry-run impact check):
  POST /api/v1/archiver/dryrun               — start background impact check
  GET  /api/v1/archiver/dryrun/{job_id}/objects — paginated objects for modal grid
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from ts_admin.services import archiver_service, deletion_service
from ts_admin.services.archiver_service import ArchiverService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/archiver", tags=["Archiver"])


# ── Shared item schema (used by results + dryrun objects) ─────────────────────


class ArchiverResultItem(BaseModel):
    ts_guid: str
    name: str
    object_type: str
    owner_guid: str
    owner_name: str
    org_id: int
    last_accessed_at: str | None
    modified_at: str | None
    created_at: str | None
    view_count: int
    days_unused: int
    tags: list[str]


# ── Phase 2 schemas ───────────────────────────────────────────────────────────


class ArchiverPreviewResponse(BaseModel):
    total: int
    by_type: dict[str, int]
    criteria_summary: str


class ArchiverResultsResponse(BaseModel):
    items: list[ArchiverResultItem]
    total: int
    record_offset: int
    page_size: int


class ArchiverTagItem(BaseModel):
    ts_guid: str
    name: str
    color: str


# ── Phase 3 schemas ───────────────────────────────────────────────────────────


class ExecuteRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    object_ids: list[str]
    # "export" writes the TML backup and stops — the deliberate half-step for
    # an admin who wants the backup in hand before trusting the delete.
    action: Literal["tag", "untag", "delete", "export"]
    tag_name: str = "INACTIVE"
    create_tag_if_missing: bool = True


class ExecuteResponse(BaseModel):
    job_id: str
    action: str
    total: int


# ── Phase 4 schemas ───────────────────────────────────────────────────────────


class DryRunRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    object_ids: list[str]


class DryRunResponse(BaseModel):
    job_id: str
    total: int


class DryRunObjectsResponse(BaseModel):
    items: list[ArchiverResultItem]
    total: int
    record_offset: int
    page_size: int


# ── Phase 6 schemas ───────────────────────────────────────────────────────────


class ArchiveSessionSummary(BaseModel):
    job_id: str
    archived_at: str | None
    total: int
    # Objects with a TML backup on disk. Separate from `succeeded` (objects
    # actually deleted) so an export-only run doesn't read as "0 of 300".
    exported: int
    succeeded: int
    failed_tml_export: int
    failed_delete: int
    export_only: bool


class ArchiveRecordResponse(BaseModel):
    id: str
    ts_guid: str
    name: str
    object_type: str
    owner_name: str
    last_accessed_at: str | None
    days_unused: int
    tags: list[str]
    tml_export_status: str
    archived_at: str
    restored_at: str | None
    restored_as_guid: str | None
    is_restorable: bool


class RestoreRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    archive_record_ids: list[str]


class RestoreResponse(BaseModel):
    job_id: str
    total: int


class ArchiveRecordFlatItem(BaseModel):
    id: str
    ts_guid: str
    name: str
    object_type: str
    owner_name: str
    archived_at: str
    tml_export_status: str
    job_id: str


class ArchiveRecordListResponse(BaseModel):
    items: list[ArchiveRecordFlatItem]
    total: int
    record_offset: int
    page_size: int


class ArchiveHistoryResponse(BaseModel):
    items: list[ArchiveSessionSummary]
    total: int
    record_offset: int
    page_size: int


class ArchiveSessionResponse(BaseModel):
    items: list[ArchiveRecordResponse]
    total: int
    record_offset: int
    page_size: int


# ── Phase 2 endpoints ──────────────────────────────────────────────────────────


@router.get("/preview", response_model=ArchiverPreviewResponse)
def archiver_preview(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    stale_activity_days: int = Query(default=90, ge=1),
    stale_modified_days: int = Query(default=90, ge=1),
    types: list[str] | None = Query(default=None),
    exclude_tags: list[str] | None = Query(default=None),
    stale_operator: Literal["AND", "OR"] = Query(default="AND"),
    owner_guid: str | None = Query(default=None),
    exclude_owner_guids: list[str] | None = Query(default=None),
) -> ArchiverPreviewResponse:
    """Count stale objects matching criteria. Reads from SQLite — instant."""
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id
    result = ArchiverService.preview(
        cluster_id=cluster_id,
        org_id=org_id,
        stale_activity_days=stale_activity_days,
        stale_modified_days=stale_modified_days,
        types=types,
        exclude_tags=exclude_tags,
        stale_operator=stale_operator,
        owner_guid=owner_guid,
        exclude_owner_guids=exclude_owner_guids,
    )
    return ArchiverPreviewResponse(**result)


@router.get("/results", response_model=ArchiverResultsResponse)
def archiver_results(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    stale_activity_days: int = Query(default=90, ge=1),
    stale_modified_days: int = Query(default=90, ge=1),
    types: list[str] | None = Query(default=None),
    exclude_tags: list[str] | None = Query(default=None),
    filter_tags: list[str] | None = Query(default=None, description="Include only objects with ALL of these tags"),
    search: str | None = Query(default=None, description="Substring match on object name"),
    stale_operator: Literal["AND", "OR"] = Query(default="AND"),
    owner_guid: str | None = Query(default=None),
    exclude_owner_guids: list[str] | None = Query(default=None),
    owner_name_search: str | None = Query(default=None, description="Substring match on owner name"),
    tag_search: str | None = Query(default=None, description="Substring match on tag names"),
    days_unused_min: int | None = Query(default=None, ge=0),
    days_unused_max: int | None = Query(default=None, ge=0),
    views_min: int | None = Query(default=None, ge=0),
    views_max: int | None = Query(default=None, ge=0),
    last_accessed_before: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    last_accessed_after: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    modified_before: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    modified_after: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    created_before: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    created_after: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    sort_field: str = Query(default="days_unused"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=200, ge=1, le=1000),
) -> ArchiverResultsResponse:
    """Paginated stale objects for the AG Grid infinite row model."""
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id
    items, total = ArchiverService.search(
        cluster_id=cluster_id,
        org_id=org_id,
        stale_activity_days=stale_activity_days,
        stale_modified_days=stale_modified_days,
        types=types,
        exclude_tags=exclude_tags,
        filter_tags=filter_tags,
        search=search,
        stale_operator=stale_operator,
        owner_guid=owner_guid,
        exclude_owner_guids=exclude_owner_guids,
        owner_name_search=owner_name_search,
        tag_search=tag_search,
        days_unused_min=days_unused_min,
        days_unused_max=days_unused_max,
        views_min=views_min,
        views_max=views_max,
        last_accessed_before=last_accessed_before,
        last_accessed_after=last_accessed_after,
        modified_before=modified_before,
        modified_after=modified_after,
        created_before=created_before,
        created_after=created_after,
        sort_field=sort_field,
        sort_order=sort_order,
        record_offset=record_offset,
        page_size=page_size,
    )
    return ArchiverResultsResponse(
        items=[ArchiverResultItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


class ResolveRequest(BaseModel):
    """The /results filter set, as a body — "everything matching what I see"."""

    cluster_id: str | None = None
    org_id: int = 0
    stale_activity_days: int = 90
    stale_modified_days: int = 90
    types: list[str] | None = None
    exclude_tags: list[str] | None = None
    filter_tags: list[str] | None = None
    search: str | None = None
    stale_operator: Literal["AND", "OR"] = "AND"
    owner_guid: str | None = None
    exclude_owner_guids: list[str] | None = None
    owner_name_search: str | None = None
    tag_search: str | None = None
    days_unused_min: int | None = None
    days_unused_max: int | None = None
    views_min: int | None = None
    views_max: int | None = None
    last_accessed_before: str | None = None
    last_accessed_after: str | None = None
    modified_before: str | None = None
    modified_after: str | None = None
    created_before: str | None = None
    created_after: str | None = None
    # "Select all, then untick a few."
    excluded_guids: list[str] = []


class ResolveResponse(BaseModel):
    guids: list[str]
    total: int


@router.post("/resolve", response_model=ResolveResponse)
def archiver_resolve(body: ResolveRequest) -> ResolveResponse:
    """
    Expand the current filters into the full list of matching GUIDs.

    Read-only: no job, no audit row, nothing mutated. It exists because AG
    Grid's infinite row model has no working header checkbox — the grid only
    holds a few cached blocks, so "select all 2,000 stale objects" cannot be
    answered client-side.

    The result feeds the UNCHANGED dryrun/execute endpoints as an explicit GUID
    list, so the dry-run guard, the confirmation modal and the audit trail all
    keep working on a concrete set of objects rather than on a stored filter
    that might resolve differently later.

    413 above ArchiverService.RESOLVE_MAX rather than a truncated list, which
    the caller would silently act on.
    """
    from ts_admin.config import load_config

    cluster_id = body.cluster_id or load_config().active_cluster.id
    filters = body.model_dump(exclude={"cluster_id", "org_id", "excluded_guids"})

    try:
        guids, total = ArchiverService.resolve_guids(
            cluster_id=cluster_id,
            org_id=body.org_id,
            excluded_guids=body.excluded_guids,
            **filters,
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    return ResolveResponse(guids=guids, total=total)


@router.get("/tags", response_model=list[ArchiverTagItem])
def archiver_tags(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    stale_activity_days: int = Query(default=90, ge=1),
    stale_modified_days: int = Query(default=90, ge=1),
    types: list[str] | None = Query(default=None),
) -> list[ArchiverTagItem]:
    """
    Tags found on stale objects matching the current criteria.
    Only returns tags that will yield results when used as a filter.
    """
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id
    tags = ArchiverService.list_tags(
        cluster_id=cluster_id,
        org_id=org_id,
        stale_activity_days=stale_activity_days,
        stale_modified_days=stale_modified_days,
        types=types,
    )
    return [ArchiverTagItem(**t) for t in tags]


# ── Phase 3 endpoints ──────────────────────────────────────────────────────────


@router.post("/execute", response_model=ExecuteResponse, status_code=202)
async def archiver_execute(
    body: ExecuteRequest,
    background_tasks: BackgroundTasks,
) -> ExecuteResponse:
    """
    Tag, untag, export, or delete a selection of stale objects.

    Creates a background job, returns immediately with job_id.
    Poll GET /api/v1/jobs/{job_id} to track progress.

    `action="export"` takes the TML backup and stops — nothing is deleted, so
    it needs no dry-run. The resulting records download through
    GET /archiver/export/{job_id}/download.
    """
    from ts_admin.config import load_config
    from ts_admin.services.job_service import create_job

    if not body.object_ids:
        raise HTTPException(status_code=422, detail="object_ids must not be empty")
    if body.action not in ("tag", "untag", "delete", "export"):
        raise HTTPException(
            status_code=422,
            detail="action must be 'tag', 'untag', 'export', or 'delete'",
        )

    cluster_id = body.cluster_id or load_config().active_cluster.id

    job_id = create_job(
        job_type="archive",
        parameters={
            "action": body.action,
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "object_ids": body.object_ids,
            "tag_name": body.tag_name,
        },
    )

    background_tasks.add_task(
        archiver_service.execute,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        object_ids=body.object_ids,
        action=body.action,
        tag_name=body.tag_name,
        create_tag_if_missing=body.create_tag_if_missing,
    )

    return ExecuteResponse(job_id=job_id, action=body.action, total=len(body.object_ids))


# ── Phase 4 endpoints ──────────────────────────────────────────────────────────


@router.post("/dryrun", response_model=DryRunResponse, status_code=202)
async def archiver_dryrun(
    body: DryRunRequest,
    background_tasks: BackgroundTasks,
) -> DryRunResponse:
    """
    Start a background impact check for a proposed deletion.

    Checks permissions + dependencies for every selected object concurrently.
    Returns job_id immediately — poll GET /api/v1/jobs/{job_id} until COMPLETE.
    Job.result contains: total, by_type, shared_count, affected_principals,
    dependency_warnings, errors.
    """
    from ts_admin.config import load_config
    from ts_admin.services.job_service import create_job

    if not body.object_ids:
        raise HTTPException(status_code=422, detail="object_ids must not be empty")

    cluster_id = body.cluster_id or load_config().active_cluster.id

    job_id = create_job(
        job_type="archive_dryrun",
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
def dryrun_objects(
    job_id: str,
    cluster_id: str | None = Query(default=None),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=100, ge=1, le=500),
) -> DryRunObjectsResponse:
    """
    Paginated list of objects queued in a dryrun job, sorted by staleness.

    Used to populate the object grid in the DryRunModal ready state.
    Objects missing from the local cache (deleted between selection and dryrun)
    are silently omitted.
    """
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id

    items, total = deletion_service.dryrun_objects(
        job_id=job_id,
        cluster_id=cluster_id,
        record_offset=record_offset,
        page_size=page_size,
    )
    return DryRunObjectsResponse(
        items=[ArchiverResultItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


# ── Phase 6 endpoints ─────────────────────────────────────────────────────────


@router.post("/restore", response_model=RestoreResponse, status_code=202)
async def archiver_restore(
    body: RestoreRequest,
    background_tasks: BackgroundTasks,
) -> RestoreResponse:
    """
    Re-import deleted objects from their TML backups.

    Creates a background job, returns immediately with job_id.
    Poll GET /api/v1/jobs/{job_id} to track progress.
    On completion, restored objects appear in ThoughtSpot with new GUIDs.
    """
    from ts_admin.config import load_config
    from ts_admin.services.job_service import create_job

    if not body.archive_record_ids:
        raise HTTPException(status_code=422, detail="archive_record_ids must not be empty")

    cluster_id = body.cluster_id or load_config().active_cluster.id

    job_id = create_job(
        job_type="archive_restore",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "archive_record_ids": body.archive_record_ids,
        },
    )

    background_tasks.add_task(
        archiver_service.restore,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        archive_record_ids=body.archive_record_ids,
    )

    return RestoreResponse(job_id=job_id, total=len(body.archive_record_ids))


@router.get("/download/{archive_record_id}")
def download_tml(
    archive_record_id: str,
    cluster_id: str | None = Query(default=None),
) -> FileResponse:
    """
    Download the TML backup file for an archived object.
    Returns the raw YAML file as an attachment.
    """
    from pathlib import Path

    from ts_admin.database import get_session
    from ts_admin.models.archive_record import ArchiveRecord

    with get_session() as session:
        record = session.get(ArchiveRecord, archive_record_id)

    if not record:
        raise HTTPException(status_code=404, detail="Archive record not found")
    if record.tml_export_status != "SUCCESS" or not record.tml_path:
        raise HTTPException(status_code=404, detail="TML backup not available for this record")

    path = Path(record.tml_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"TML file not found on disk: {path}")

    filename = f"{record.name.replace('/', '_')}.tml"
    return FileResponse(
        path=str(path),
        media_type="text/plain",
        filename=filename,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/export/{job_id}/download")
def download_export_bundle(
    job_id: str,
    cluster_id: str | None = Query(default=None),
) -> StreamingResponse:
    """
    Download every TML file one job exported, as a single zip.

    The per-record endpoint above is fine for one object; an admin who just
    exported 300 of them wants one file, not 300 clicks.

    Scoped to the cluster: the records are filtered by cluster_id, so a job id
    from another instance yields a 404 rather than that instance's TML.
    """
    import io
    import zipfile
    from pathlib import Path

    from sqlmodel import select

    from ts_admin.config import load_config
    from ts_admin.database import get_session
    from ts_admin.models.archive_record import ArchiveRecord

    resolved = cluster_id or load_config().active_cluster.id
    with get_session() as session:
        records = session.exec(
            select(ArchiveRecord).where(
                ArchiveRecord.job_id == job_id,
                ArchiveRecord.cluster_id == resolved,
                ArchiveRecord.tml_export_status == "SUCCESS",
            )
        ).all()

    if not records:
        raise HTTPException(
            status_code=404,
            detail=f"No successful TML exports found for job {job_id!r} on this instance",
        )

    buf = io.BytesIO()
    written = 0
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for record in records:
            if not record.tml_path:
                continue
            path = Path(record.tml_path)
            if not path.exists():
                # A file cleaned up or moved since the export. Skip it rather
                # than failing the whole bundle — the rest is still useful, and
                # the manifest below records what was expected.
                logger.warning("TML file missing for record %s: %s", record.id, path)
                continue
            # GUID-suffixed so two objects sharing a name cannot overwrite each
            # other inside the archive.
            safe_name = record.name.replace("/", "_").replace("\\", "_") or record.ts_guid
            zf.writestr(f"{safe_name}-{record.ts_guid}.tml", path.read_text(encoding="utf-8"))
            written += 1

    if written == 0:
        raise HTTPException(
            status_code=404,
            detail=f"TML files for job {job_id!r} are no longer on disk",
        )

    buf.seek(0)
    filename = f"tml-export-{job_id}.zip"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/records", response_model=ArchiveRecordListResponse)
def archiver_all_records(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    sort_field: str = Query(default="archived_at"),
    sort_order: Literal["asc", "desc"] = Query(default="desc"),
    search: str | None = Query(default=None, description="Substring match on name"),
    types: list[str] | None = Query(default=None, description="LIVEBOARD and/or ANSWER"),
    owner_name_search: str | None = Query(default=None, description="Substring match on owner name"),
    archived_before: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    archived_after: str | None = Query(default=None, description="ISO date YYYY-MM-DD"),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=200, ge=1, le=1000),
) -> ArchiveRecordListResponse:
    """All deleted objects across all sessions, paginated, sortable, and filterable."""
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id
    items, total = archiver_service.all_archive_records(
        cluster_id=cluster_id,
        org_id=org_id,
        sort_field=sort_field,
        sort_order=sort_order,
        search=search,
        types=types,
        owner_name_search=owner_name_search,
        archived_before=archived_before,
        archived_after=archived_after,
        record_offset=record_offset,
        page_size=page_size,
    )
    return ArchiveRecordListResponse(
        items=[ArchiveRecordFlatItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


@router.get("/history", response_model=ArchiveHistoryResponse)
def archiver_history(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=20, ge=1, le=100),
) -> ArchiveHistoryResponse:
    """List all archive sessions (delete jobs) for this cluster, newest first."""
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id
    items, total = archiver_service.history(
        cluster_id=cluster_id,
        org_id=org_id,
        record_offset=record_offset,
        page_size=page_size,
    )
    return ArchiveHistoryResponse(
        items=[ArchiveSessionSummary(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


@router.get("/history/{job_id}", response_model=ArchiveSessionResponse)
def archiver_history_session(
    job_id: str,
    cluster_id: str | None = Query(default=None),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=100, ge=1, le=500),
) -> ArchiveSessionResponse:
    """Return individual ArchiveRecord rows for one archive session."""
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id
    items, total = archiver_service.history_session(
        job_id=job_id,
        cluster_id=cluster_id,
        record_offset=record_offset,
        page_size=page_size,
    )
    return ArchiveSessionResponse(
        items=[ArchiveRecordResponse(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )
