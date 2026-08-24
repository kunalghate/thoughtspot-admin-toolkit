"""
Job service — creates and updates background job records in SQLite.
"""

import traceback
import uuid
from datetime import datetime, timezone

from ts_admin.database import get_session
from ts_admin.models.job import Job


def create_job(*, job_type: str, parameters: dict, cluster_id: str | None = None) -> str:
    """
    Create a new job record in QUEUED state.

    `cluster_id` should be passed by the caller (they've already resolved it from
    the request body or the active cluster). It also accepts `parameters["cluster_id"]`
    when present, so existing callers that pass it inside `parameters` keep working.
    Falls back to load_config() only if neither source supplies it.

    Returns the job ID.
    """
    if cluster_id is None:
        cluster_id = parameters.get("cluster_id")
    if cluster_id is None:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id

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


def find_in_flight_sync(*, cluster_id: str, org_id: int, entity_type: str) -> Job | None:
    """The QUEUED/RUNNING sync job for (cluster, org, entity), or None.

    The concurrency guard behind POST /sync/{entity} (S24/S34) and the
    dependencies sync's wait-for-metadata step. Job rows cannot outlive their
    runner and wedge callers — startup marks any leftover RUNNING/QUEUED jobs
    FAILED (``main.py::lifespan``).

    ``org_id`` lives inside the job's parameters JSON, not a column, so rows
    are filtered in Python — the QUEUED/RUNNING set is at most a handful.
    """
    from sqlmodel import col, select

    with get_session() as session:
        rows = session.exec(
            select(Job).where(
                Job.cluster_id == cluster_id,
                Job.job_type == f"sync:{entity_type}",
                col(Job.status).in_(["QUEUED", "RUNNING"]),
            )
        ).all()

    for job in rows:
        if job.get_parameters().get("org_id", 0) == org_id:
            return job
    return None


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


def mark_failed(
    job_id: str,
    error: "str | Exception",
    *,
    traceback_str: str | None = None,
) -> None:
    """Mark a job FAILED.

    `error` accepts either a plain string (legacy callers) or an Exception
    instance. When an Exception is passed we capture its class name and a
    full traceback so the failure can be debugged after the process exits.
    """
    if isinstance(error, BaseException):
        from ts_admin.services.error_formatter import format_error

        exc = error
        formatted = format_error(exc)
        raw_detail = str(exc) or type(exc).__name__
        # Friendly line for the grid; raw detail prepended to traceback so it's
        # never lost.
        error_str = formatted.display
        error_type = formatted.error_type
        if traceback_str is None:
            tb = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
            traceback_str = f"{raw_detail}\n\n{tb}"
    else:
        error_str = error
        error_type = None

    with get_session() as session:
        job = session.get(Job, job_id)
        if job:
            job.status = "FAILED"
            job.error = error_str
            job.error_type = error_type
            job.error_traceback = traceback_str
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
