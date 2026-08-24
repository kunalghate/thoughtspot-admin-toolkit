"""
Background job status endpoints.

GET  /api/v1/jobs           — paginated list of jobs (filterable)
GET  /api/v1/jobs/{job_id}  — get status of a specific job
"""

import logging
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    id: str
    job_type: str
    # From the parameters JSON (jobs have no org column): lets the UI match a
    # job to the org it is viewing, e.g. the Topbar adopting an in-flight sync.
    org_id: int | None = None
    status: str
    progress: int
    total: int
    progress_pct: float
    error: str | None = None
    error_type: str | None = None
    error_traceback: str | None = None
    result: dict | None = None
    # Cancel is a request, not an act: the flag is set here and the background
    # task acts on it at its next page/chunk boundary. Exposed so the UI can show
    # "cancelling…" for the window in between instead of a job that looks stuck.
    is_cancelled: bool = False
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


class JobListResponse(BaseModel):
    items: list[JobResponse]
    total: int
    record_offset: int
    page_size: int


_SORTABLE_FIELDS = {"created_at", "completed_at", "started_at", "status", "job_type", "progress"}


@router.get("", response_model=JobListResponse)
async def list_jobs(
    cluster_id: str | None = Query(default=None),
    job_types: list[str] | None = Query(default=None, description="Filter by job_type — repeat the param"),
    statuses: list[str] | None = Query(
        default=None,
        description="Filter by status (QUEUED|RUNNING|COMPLETE|PARTIAL|FAILED)",
    ),
    sort_field: str = Query(
        default="created_at",
        description="created_at|completed_at|started_at|status|job_type|progress",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=50, ge=1, le=1000),
) -> JobListResponse:
    """
    Paginated list of background jobs for the active cluster (or a
    specific cluster_id). Surfaces every job_type: archive,
    archive_dryrun, bulk_delete, bulk_delete_dryrun, sync, etc.

    Sortable columns: created_at, completed_at, started_at, status,
    job_type, progress. Default sort is created_at desc (newest first).
    """
    from sqlalchemy import asc, desc, nulls_last
    from sqlmodel import col, func, select

    from ts_admin.config import load_config
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    if not cluster_id:
        cluster_id = load_config().active_cluster.id

    conditions = [Job.cluster_id == cluster_id]
    if job_types:
        conditions.append(col(Job.job_type).in_(job_types))
    if statuses:
        conditions.append(col(Job.status).in_(statuses))

    # Whitelist the sort field; fall back to created_at on anything unexpected.
    sf = sort_field if sort_field in _SORTABLE_FIELDS else "created_at"
    sort_col = getattr(Job, sf)
    direction = asc if sort_order.lower() == "asc" else desc
    order_expr = nulls_last(direction(sort_col))

    with get_session() as session:
        total = session.exec(select(func.count()).select_from(Job).where(*conditions)).one()
        rows = session.exec(
            select(Job).where(*conditions).order_by(order_expr).offset(record_offset).limit(page_size)
        ).all()

    return JobListResponse(
        items=[_job_to_response(j) for j in rows],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


@router.get("/{job_id}", response_model=JobResponse)
async def get_job(job_id: str) -> JobResponse:
    """Get the current status of a specific job. Poll this while job is running."""
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")

    return _job_to_response(job)


@router.delete("/{job_id}/cancel", status_code=204)
async def cancel_job(job_id: str) -> None:
    """
    Request cancellation of a running job.

    Sets ``job.is_cancelled = True`` and returns immediately — cancellation is
    cooperative. The background task re-reads the flag at its next page/chunk
    boundary and stops there, so the job keeps running until the in-flight call
    to ThoughtSpot returns. Poll ``GET /jobs/{id}``: ``is_cancelled`` flips first
    (cancel-pending), then ``status`` lands on a terminal state.

    Every job type honours the flag: the bulk write paths (delete, share, user
    management) and — since the sweeps below read it too — sync and the lineage
    crawls. A cancelled job ends PARTIAL, never COMPLETE, and its cache purge /
    delete-before-insert is skipped, because a partial sweep cannot tell "deleted
    upstream" from "not reached yet". See `sync_service._finish_cancelled` and
    `lineage_service.SyncCancelled`.

    Returns 409 if the job is already done.
    """
    from ts_admin.database import get_session
    from ts_admin.models.job import Job

    with get_session() as session:
        job = session.get(Job, job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found")
        if job.is_done:
            raise HTTPException(status_code=409, detail=f"Job {job_id!r} is already {job.status}")
        job.is_cancelled = True
        session.add(job)
        session.commit()


def _job_to_response(job) -> JobResponse:
    return JobResponse(
        id=job.id,
        job_type=job.job_type,
        org_id=job.get_parameters().get("org_id"),
        status=job.status,
        progress=job.progress,
        total=job.total,
        progress_pct=job.progress_pct,
        error=job.error,
        error_type=job.error_type,
        error_traceback=job.error_traceback,
        result=job.get_result(),
        is_cancelled=job.is_cancelled,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
