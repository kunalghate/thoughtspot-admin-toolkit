"""
Unit tests for ts_admin.services.archiver_service.execute (tag / untag).

The delete action delegates to deletion_service and is covered there; this file
covers the tag/untag branch, which had no test at all and carried the M14
defect: `if failed_ids: mark_partial else mark_complete` — with no
`succeeded == 0` branch anywhere — so a run in which EVERY chunk failed reported
PARTIAL with zero objects tagged, and the audit row it left behind said PARTIAL
too. PARTIAL reads to an admin as "some of it worked, retry the rest".
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine, select

from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job
from ts_admin.ts_client.exceptions import TSObjectNotFoundError
from ts_admin.ts_client.models import TSTag

# ── Fake TS client ─────────────────────────────────────────────────────────────


class _FakeClient:
    tags: list[TSTag] = []
    assign_calls: list[tuple[list[str], str]] = []
    unassign_calls: list[tuple[list[str], str]] = []
    tag_call_should_fail: Exception | None = None
    fail_object_ids: set[str] = set()  # empty = every tag call fails

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def search_tags(self):
        return list(_FakeClient.tags)

    def _maybe_raise(self, object_ids):
        if _FakeClient.tag_call_should_fail is not None and (
            not _FakeClient.fail_object_ids or set(object_ids) & _FakeClient.fail_object_ids
        ):
            raise _FakeClient.tag_call_should_fail

    async def assign_tag(self, *, object_ids, tag_id):
        _FakeClient.assign_calls.append((list(object_ids), tag_id))
        self._maybe_raise(object_ids)

    async def unassign_tag(self, *, object_ids, tag_id):
        _FakeClient.unassign_calls.append((list(object_ids), tag_id))
        self._maybe_raise(object_ids)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.tags = [TSTag(id="t-1", name="INACTIVE")]
    _FakeClient.assign_calls = []
    _FakeClient.unassign_calls = []
    _FakeClient.tag_call_should_fail = None
    _FakeClient.fail_object_ids = set()


@pytest.fixture
def in_memory_db(monkeypatch):
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


@pytest.fixture
def patched_env(monkeypatch):
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id="c1",
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.BASIC,
    )
    config = AppConfig(clusters={"c1": cluster_cfg}, active_cluster_id="c1")
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    monkeypatch.setattr("ts_admin.config._load_secret", lambda cluster_id, field: "fake-secret")
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _FakeClient)


@pytest.fixture
def seeded(in_memory_db):
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as session:
        session.add(
            Cluster(
                id="c1",
                name="Prod",
                url="https://prod.thoughtspot.cloud",
                username="admin",
                auth_type="basic",
            )
        )
        for guid, name in [("lb-1", "Sales"), ("ans-1", "Revenue")]:
            session.add(
                CachedMetadata(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid=guid,
                    name=name,
                    object_type="LIVEBOARD",
                    owner_guid="u-alice",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    synced_at=now,
                )
            )
        session.commit()


def _create_job() -> str:
    import uuid

    from ts_admin.database import get_session

    job_id = str(uuid.uuid4())
    with get_session() as s:
        s.add(Job(id=job_id, cluster_id="c1", job_type="archive_tag", status="QUEUED"))
        s.commit()
    return job_id


def _run(job_id: str, object_ids: list[str], action: str = "tag") -> None:
    from ts_admin.services.archiver_service import execute

    asyncio.run(
        execute(
            job_id=job_id,
            cluster_id="c1",
            org_id=0,
            object_ids=object_ids,
            action=action,
        )
    )


def _job(job_id: str) -> Job:
    from ts_admin.database import get_session

    with get_session() as s:
        return s.get(Job, job_id)


def _audit_rows() -> list[AuditLog]:
    from ts_admin.database import get_session

    with get_session() as s:
        return list(s.exec(select(AuditLog)).all())


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestTaggingHappyPath:
    def test_tag_applies_live_and_mirrors_the_cache(self, in_memory_db, patched_env, seeded):
        job_id = _create_job()
        _run(job_id, ["lb-1", "ans-1"])

        assert _FakeClient.assign_calls == [(["lb-1", "ans-1"], "t-1")]
        job = _job(job_id)
        assert job.status == "COMPLETE"
        assert (job.get_result() or {})["succeeded"] == 2

        with Session(in_memory_db) as s:
            rows = s.exec(select(CachedMetadata).where(CachedMetadata.cluster_id == "c1")).all()
        assert all("INACTIVE" in r.get_tag_names() for r in rows)
        assert _audit_rows()[0].status == "COMPLETE"

    def test_untag_removes_the_label_from_the_cache(self, in_memory_db, patched_env, seeded):
        job_id = _create_job()
        _run(job_id, ["lb-1"], action="tag")
        job_id = _create_job()
        _run(job_id, ["lb-1"], action="untag")

        assert _FakeClient.unassign_calls == [(["lb-1"], "t-1")]
        with Session(in_memory_db) as s:
            row = s.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-1")).first()
        assert "INACTIVE" not in row.get_tag_names()


class TestNothingTaggedIsFailedNeverPartial:
    """Reverting to `if failed_ids: mark_partial` fails every test here."""

    def test_every_chunk_failing_ends_the_job_failed(self, in_memory_db, patched_env, seeded):
        _FakeClient.tag_call_should_fail = TSObjectNotFoundError(
            object_type="resource",
            identifier="/api/rest/2.0/tags/assign",
            detail="Not Found",
        )

        job_id = _create_job()
        _run(job_id, ["lb-1", "ans-1"])

        job = _job(job_id)
        assert job.status == "FAILED"
        error = job.error or ""
        assert "0 of 2 objects tagged" in error
        # The cause, not just a count.
        assert "/api/rest/2.0/tags/assign" in error
        assert "Not Found" in error

    def test_the_audit_row_says_failed_too(self, in_memory_db, patched_env, seeded):
        _FakeClient.tag_call_should_fail = TSObjectNotFoundError(
            object_type="resource",
            identifier="/api/rest/2.0/tags/assign",
            detail="Not Found",
        )

        job_id = _create_job()
        _run(job_id, ["lb-1"])

        rows = _audit_rows()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert rows[0].items_affected == 0
        assert "Not Found" in rows[0].get_parameters()["error"]

    def test_a_failed_chunk_never_mirrors_the_tag_into_the_cache(self, in_memory_db, patched_env, seeded):
        """The cache mirror used to sit inside the same `try` as the live call,
        so it could not run after a failure — but nor could a failure in the
        mirror be told apart from a failure on the wire."""
        _FakeClient.tag_call_should_fail = TSObjectNotFoundError(
            object_type="resource",
            identifier="/api/rest/2.0/tags/assign",
            detail="Not Found",
        )

        job_id = _create_job()
        _run(job_id, ["lb-1"])

        with Session(in_memory_db) as s:
            row = s.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-1")).first()
        assert row.get_tag_names() == []

    def test_a_bug_in_our_own_code_fails_the_job_instead_of_becoming_a_partial(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        _FakeClient.tag_call_should_fail = TypeError("assign_tag() got an unexpected keyword argument")

        job_id = _create_job()
        _run(job_id, ["lb-1"])

        job = _job(job_id)
        assert job.status == "FAILED"
        assert job.error_type == "TypeError"
        assert _audit_rows() == []


class TestPartialIsStillReachable:
    """Non-vacuity: the fix must not collapse PARTIAL into FAILED."""

    def test_one_failing_chunk_among_several_is_partial(self, in_memory_db, patched_env, seeded, monkeypatch):
        import ts_admin.services.archiver_service as svc

        # One object per chunk so the two objects take different code paths.
        monkeypatch.setattr(svc, "_chunks", lambda lst, size: ([x] for x in lst))
        _FakeClient.tag_call_should_fail = TSObjectNotFoundError(
            object_type="resource",
            identifier="/api/rest/2.0/tags/assign",
            detail="Not Found",
        )
        _FakeClient.fail_object_ids = {"ans-1"}

        job_id = _create_job()
        _run(job_id, ["lb-1", "ans-1"])

        job = _job(job_id)
        assert job.status == "PARTIAL"
        result = job.get_result() or {}
        assert result["succeeded"] == 1
        assert result["failed"] == 1
        assert _audit_rows()[0].status == "PARTIAL"
