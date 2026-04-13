"""
Job service — creates and updates background job records in SQLite.
"""

import uuid
from datetime import datetime, timezone

from ts_admin.database import get_session
from ts_admin.models.job import Job


def create_job(*, job_type: str, parameters: dict) -> str:
    """
    Create a new job record in QUEUED state.
    Returns the job ID.
    """
    from ts_admin.config import load_config
    config = load_config()
    cluster_id = config.active_cluster.id

    job_id = str(uuid.uuid4())
    job = Job(
        id=job_id,
        cluster_id=cluster_id,
        job_type=job_type,
        status="QUEUED",
    )
    job.set_parameters(parameters)

    with get_session() as session:
        session.add(job)
        session.commit()

    return job_id


def mark_running(job_id: str, total: int) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "RUNNING"
            job.total = total
            job.started_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()


def update_progress(job_id: str, progress: int) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.progress = progress
            session.add(job)
            session.commit()


def mark_complete(job_id: str, result: dict) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "COMPLETE"
            job.completed_at = datetime.now(timezone.utc)
            job.set_result(result)
            session.add(job)
            session.commit()


def mark_failed(job_id: str, error: str) -> None:
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "FAILED"
            job.error = error
            job.completed_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()


def mark_partial(job_id: str, result: dict) -> None:
    """Mark a job PARTIAL — some items succeeded, some failed."""
    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "PARTIAL"
            job.completed_at = datetime.now(timezone.utc)
            job.set_result(result)
            session.add(job)
            session.commit()


def is_cancelled(job_id: str) -> bool:
    """Return True if the job has been cancelled. Reads fresh from DB each call."""
    with get_session() as session:
        job = session.get(Job, job_id)
        return bool(job and job.is_cancelled)
