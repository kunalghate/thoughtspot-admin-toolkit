"""
Unit tests for ts_admin.services.deletion_service.

Mocks ThoughtSpotClient at the import boundary; verifies the full
TML-export → delete → audit pipeline writes the expected ArchiveRecord
rows, removes objects from CachedMetadata, and finalizes the Job.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine, select

from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job

# ── Fake TS client ─────────────────────────────────────────────────────────────


class _FakeClient:
    """
    Stand-in for ThoughtSpotClient. Configurable per-test via class
    attributes set in fixtures so `async with _FakeClient(...) as c:` works.
    """

    tml_results: list[dict] = []  # what tml_export returns
    delete_calls: list[tuple[str, list[str]]] = []  # (object_type, ids)

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def tml_export(self, *, object_ids):
        # Return only the configured items whose info.id is in object_ids.
        return [r for r in _FakeClient.tml_results if (r.get("info") or {}).get("id") in object_ids]

    async def delete_metadata(self, *, object_ids, object_type):
        _FakeClient.delete_calls.append((str(object_type), list(object_ids)))


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.tml_results = []
    _FakeClient.delete_calls = []


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
def patched_env(monkeypatch, tmp_path):
    """Patch TS client + load_config + redirect TML_EXPORT_DIR to a tmp path."""
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

    # Patch every place ThoughtSpotClient is resolved by deferred imports.
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _FakeClient)

    # TML files would otherwise land under ~/.ts-admin/tml-exports/
    import ts_admin.services.deletion_service as ds

    monkeypatch.setattr(ds, "TML_EXPORT_DIR", tmp_path / "tml-exports")


@pytest.fixture
def seeded(in_memory_db):
    with Session(in_memory_db) as session:
        session.add(Cluster(
            id="c1", name="Prod",
            url="https://prod.thoughtspot.cloud",
            username="admin", auth_type="basic",
        ))
        now = datetime.now(tz=timezone.utc)
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0,
            ts_guid="lb-1", name="Sales", object_type="LIVEBOARD",
            owner_guid="u1", owner_name="Alice",
            tag_names=json.dumps([]),
            synced_at=now,
        ))
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0,
            ts_guid="ans-1", name="Revenue", object_type="ANSWER",
            owner_guid="u2", owner_name="Bob",
            tag_names=json.dumps([]),
            synced_at=now,
        ))
        session.commit()


def _create_job(job_type: str, parameters: dict) -> str:
    """Create a job row directly (avoids load_config inside create_job)."""
    import uuid

    from ts_admin.database import get_session

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, cluster_id="c1", job_type=job_type, status="QUEUED")
    job.set_parameters(parameters)
    with get_session() as s:
        s.add(job)
        s.commit()
    return job_id


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestExecuteDeleteHappyPath:
    def test_full_pipeline(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.deletion_service import _execute_delete

        # Configure the fake to succeed for both objects
        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1"}, "edoc": "answer:\n  name: Revenue\n"},
        ]

        job_id = _create_job(
            "bulk_delete",
            {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]},
        )

        asyncio.run(_execute_delete(
            job_id=job_id, cluster_id="c1", org_id=0,
            object_ids=["lb-1", "ans-1"],
            action_type="bulk_delete",
        ))

        # ── Job is COMPLETE
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            assert job is not None
            assert job.status == "COMPLETE"
            assert job.progress == 2
            assert job.total == 2

        # ── delete_metadata was called once per object_type group
        types_called = {t for t, _ in _FakeClient.delete_calls}
        assert types_called == {"LIVEBOARD", "ANSWER"}

        # ── ArchiveRecord rows exist with TML SUCCESS
        with Session(in_memory_db) as s:
            recs = s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()
        assert {r.ts_guid for r in recs} == {"lb-1", "ans-1"}
        for r in recs:
            assert r.tml_export_status == "SUCCESS"
            assert r.tml_path is not None and r.tml_path.endswith(f"{r.ts_guid}.tml")

        # ── CachedMetadata rows are gone
        with Session(in_memory_db) as s:
            remaining = s.exec(select(CachedMetadata.ts_guid)).all()
        assert "lb-1" not in remaining
        assert "ans-1" not in remaining

        # ── Audit log
        with Session(in_memory_db) as s:
            audits = s.exec(select(AuditLog)).all()
        assert any(a.action_type == "bulk_delete" for a in audits)


class TestExecuteDeletePartial:
    def test_tml_export_failure_excludes_object_from_delete(
        self, in_memory_db, patched_env, seeded,
    ):
        from ts_admin.services.deletion_service import _execute_delete

        # ans-1 has empty edoc (export "failed"). lb-1 succeeds.
        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1", "error_message": "missing edoc"}, "edoc": ""},
        ]

        job_id = _create_job(
            "bulk_delete",
            {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]},
        )

        asyncio.run(_execute_delete(
            job_id=job_id, cluster_id="c1", org_id=0,
            object_ids=["lb-1", "ans-1"],
            action_type="bulk_delete",
        ))

        # delete_metadata was called only for the type with successful TML
        called_ids = sorted(i for _, ids in _FakeClient.delete_calls for i in ids)
        assert called_ids == ["lb-1"]

        with Session(in_memory_db) as s:
            recs = {r.ts_guid: r for r in
                    s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()}
            job = s.get(Job, job_id)

        assert recs["lb-1"].tml_export_status == "SUCCESS"
        assert recs["ans-1"].tml_export_status == "FAILED"
        assert recs["ans-1"].tml_export_error and "missing edoc" in recs["ans-1"].tml_export_error
        assert job.status == "PARTIAL"
        assert (job.get_result() or {}).get("succeeded") == 1
