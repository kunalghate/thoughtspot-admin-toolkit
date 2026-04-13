"""
Background job status endpoints.

GET  /api/v1/jobs           — list recent jobs
GET  /api/v1/jobs/{job_id}  — get status of a specific job
"""

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from datetime import datetime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobResponse(BaseModel):
    id: str
    job_type: str
    status: str
    progress: int
    total: int
    progress_pct: float
    error: str | None = None
    result: dict | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None


@router.get("", response_model=list[JobResponse])
async def list_jobs(limit: int = 20) -> list[JobResponse]:
    """Return the most recent jobs for the active cluster."""
    from ts_admin.config import load_config
    from ts_admin.database import get_session
    from ts_admin.models.job import Job
    from sqlmodel import select

    config = load_config()
    cluster_id = config.active_cluster.id

    with get_session() as session:
        jobs = session.exec(
            select(Job)
            .where(Job.cluster_id == cluster_id)
            .order_by(Job.created_at.desc())
            .limit(limit)
        ).all()

    return [_job_to_response(j) for j in jobs]


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

    Sets job.is_cancelled = True. The running background task checks this
    flag at each chunk boundary and stops cleanly if set.

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
        status=job.status,
        progress=job.progress,
        total=job.total,
        progress_pct=job.progress_pct,
        error=job.error,
        result=job.get_result(),
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )
