"""
Unit tests for ts_admin.services.job_service — focused on mark_failed
capturing exception type and traceback.
"""

from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from ts_admin.models.job import Job


@pytest.fixture
def in_memory_db(monkeypatch):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


def _seed_job(engine) -> str:
    job_id = "job-1"
    with Session(engine) as session:
        # Use a minimal cluster row so the FK doesn't bite — enable_fk is off in
        # the in-memory test setup, but write an id consistent with archiver tests.
        from ts_admin.models.cluster import Cluster
        session.add(Cluster(
            id="c1", name="Prod",
            url="https://prod.thoughtspot.cloud",
            username="admin", auth_type="basic",
        ))
        session.add(Job(id=job_id, cluster_id="c1", job_type="bulk_delete"))
        session.commit()
    return job_id


def test_mark_failed_with_string_keeps_legacy_behavior(in_memory_db):
    from ts_admin.database import get_session
    from ts_admin.services.job_service import mark_failed

    job_id = _seed_job(in_memory_db)
    mark_failed(job_id, "0 objects deleted — 5 TML exports failed")

    with get_session() as session:
        job = session.get(Job, job_id)

    assert job.status == "FAILED"
    assert job.error == "0 objects deleted — 5 TML exports failed"
    assert job.error_type is None
    assert job.error_traceback is None


def test_mark_failed_with_exception_captures_friendly_message_and_traceback(in_memory_db):
    from ts_admin.database import get_session
    from ts_admin.services.job_service import mark_failed
    from ts_admin.ts_client.exceptions import TSTimeoutError

    job_id = _seed_job(in_memory_db)

    def _explode():
        raise TSTimeoutError("read timeout after 30s")

    try:
        _explode()
    except TSTimeoutError as exc:
        mark_failed(job_id, exc)

    with get_session() as session:
        job = session.get(Job, job_id)

    assert job.status == "FAILED"
    # Job.error is the friendly message, not the raw str(exc).
    assert "ThoughtSpot didn't respond in time" in job.error
    assert job.error_type == "TSTimeoutError"
    # Raw detail is preserved at the top of the traceback for debugging.
    assert "read timeout after 30s" in job.error_traceback
    assert "_explode" in job.error_traceback
    assert "TSTimeoutError" in job.error_traceback


def test_mark_failed_unknown_exception_falls_back_to_generic(in_memory_db):
    from ts_admin.database import get_session
    from ts_admin.services.job_service import mark_failed

    job_id = _seed_job(in_memory_db)
    try:
        raise ValueError("something weird")
    except ValueError as exc:
        mark_failed(job_id, exc)

    with get_session() as session:
        job = session.get(Job, job_id)

    assert job.error_type == "ValueError"
    assert "Something went wrong" in job.error
    assert "something weird" in job.error_traceback


def test_mark_failed_with_explicit_traceback_string(in_memory_db):
    from ts_admin.database import get_session
    from ts_admin.services.job_service import mark_failed

    job_id = _seed_job(in_memory_db)
    custom_tb = "Traceback (most recent call last):\n  ...custom...\n"
    mark_failed(job_id, RuntimeError("boom"), traceback_str=custom_tb)

    with get_session() as session:
        job = session.get(Job, job_id)

    assert job.error_type == "RuntimeError"
    assert job.error_traceback == custom_tb
