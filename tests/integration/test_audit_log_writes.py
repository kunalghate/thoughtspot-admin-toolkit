"""
Audit log shape: every successful destructive execute writes exactly one row
with the expected fields, and the row is differentiated by action_type so a
later auditor can tell whether the delete came from the Archiver or the Bulk
Deleter.

Complements the smoke check in tests/unit/test_deletion_service.py, which
only asserts that *some* audit log row exists. This file pins down the row
shape (items_affected, entity_type, parameters JSON, status) so a future
refactor can't quietly degrade the audit trail.
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
from ts_admin.ts_client.exceptions import TSServerError

# ── Fake TS client (mirrors the pattern in test_deletion_service.py) ───────────


class _FakeClient:
    tml_results: list[dict] = []
    delete_calls: list[tuple[str, list[str]]] = []
    raise_on_delete: bool = False

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def tml_export(self, *, object_ids):
        return [r for r in _FakeClient.tml_results if (r.get("info") or {}).get("id") in object_ids]

    async def delete_metadata(self, *, object_ids, object_type):
        if _FakeClient.raise_on_delete:
            # A cluster-side failure, which is what the per-chunk catch in
            # `_execute_delete` is for. It has to be a real TS exception: that
            # catch is narrowed to (TSAdminError, httpx.HTTPError), so a
            # RuntimeError would be a bug in OUR code and is deliberately no
            # longer bucketed as "this chunk failed upstream".
            raise TSServerError(status_code=503, body="simulated TS delete failure")
        _FakeClient.delete_calls.append((str(object_type), list(object_ids)))


# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.tml_results = []
    _FakeClient.delete_calls = []
    _FakeClient.raise_on_delete = False


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

    import ts_admin.services.deletion_service as ds

    monkeypatch.setattr(ds, "TML_EXPORT_DIR", tmp_path / "tml-exports")


@pytest.fixture
def seeded(in_memory_db):
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
        now = datetime.now(tz=timezone.utc)
        for guid, otype, oname in [("lb-1", "LIVEBOARD", "Sales"), ("ans-1", "ANSWER", "Revenue")]:
            session.add(
                CachedMetadata(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid=guid,
                    name=oname,
                    object_type=otype,
                    owner_guid="u1",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    synced_at=now,
                )
            )
        session.commit()


def _create_job(parameters: dict, *, job_type: str = "bulk_delete") -> str:
    import uuid

    from ts_admin.database import get_session

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, cluster_id="c1", job_type=job_type, status="QUEUED")
    job.set_parameters(parameters)
    with get_session() as s:
        s.add(job)
        s.commit()
    return job_id


def _audit_rows(engine) -> list[AuditLog]:
    with Session(engine) as s:
        return list(s.exec(select(AuditLog)))


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestAuditLogShapeOnSuccess:
    def test_single_row_with_expected_shape(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1"}, "edoc": "answer:\n  name: Revenue\n"},
        ]
        job_id = _create_job({"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]})

        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        rows = _audit_rows(in_memory_db)
        assert len(rows) == 1, f"Expected exactly one audit row per execute, got {len(rows)}"

        row = rows[0]
        assert row.cluster_id == "c1"
        assert row.action_type == "bulk_delete"
        assert row.entity_type == "metadata"
        assert row.items_affected == 2
        assert row.status == "COMPLETE"
        assert row.error is None

        params = row.get_parameters()
        assert params["object_ids"] == ["lb-1", "ans-1"]
        assert params["succeeded"] == 2
        assert params["failed_tml_export"] == 0
        assert params["failed_delete"] == 0


class TestAuditLogActionTypeIsCallerSpecific:
    """
    The Archiver and Bulk Deleter share the delete pipeline but pass different
    action_type values so the audit trail can attribute the delete to its
    feature. This invariant catches a refactor that hard-codes the value.
    """

    @pytest.mark.parametrize("action_type", ["bulk_delete", "delete"])
    def test_action_type_is_passed_through(self, in_memory_db, patched_env, seeded, action_type):
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [{"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"}]
        job_id = _create_job({"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]})

        asyncio.run(
            _execute_delete(job_id=job_id, cluster_id="c1", org_id=0, object_ids=["lb-1"], action_type=action_type)
        )

        rows = _audit_rows(in_memory_db)
        assert len(rows) == 1
        assert rows[0].action_type == action_type


class TestAuditLogShapeOnPartialFailure:
    def test_partial_status_when_tml_export_partially_fails(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1", "error_message": "missing edoc"}, "edoc": ""},
        ]
        job_id = _create_job({"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]})

        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        rows = _audit_rows(in_memory_db)
        assert len(rows) == 1
        row = rows[0]
        assert row.status == "PARTIAL"
        assert row.items_affected == 1  # only lb-1 succeeded

        params = row.get_parameters()
        assert params["succeeded"] == 1
        assert params["failed_tml_export"] == 1
        assert params["failed_delete"] == 0

    def test_failed_status_when_every_delete_call_fails(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.deletion_service import _execute_delete

        # TML succeeds for both; delete_metadata raises.
        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1"}, "edoc": "answer:\n  name: Revenue\n"},
        ]
        _FakeClient.raise_on_delete = True

        job_id = _create_job({"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]})

        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        rows = _audit_rows(in_memory_db)
        assert len(rows) == 1
        row = rows[0]
        # A run in which NOTHING was deleted is FAILED, never PARTIAL. This
        # assertion used to read PARTIAL — and PARTIAL with items_affected == 0
        # is precisely the shape that hid three non-existent endpoints for the
        # life of the project: it reads to an admin as "some of it worked,
        # retry the rest". The audit row must carry the same terminal status as
        # the job, and the reason must be in the row, not only in a log line.
        assert row.status == "FAILED"
        assert row.items_affected == 0
        params = row.get_parameters()
        assert params["failed_delete"] == 2
        assert "0 of 2 objects deleted" in params["error"]
        assert "simulated TS delete failure" in params["error"]

        with Session(in_memory_db) as session:
            job = session.get(Job, job_id)
        assert job.status == "FAILED"
        assert "simulated TS delete failure" in (job.error or "")


class TestNoDuplicateAuditRows:
    def test_back_to_back_executes_produce_one_row_each(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [{"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"}]
        job_a = _create_job({"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]})
        asyncio.run(
            _execute_delete(job_id=job_a, cluster_id="c1", org_id=0, object_ids=["lb-1"], action_type="bulk_delete")
        )

        # Re-seed lb-1 (the previous run deleted it from CachedMetadata) so the
        # second execute has something to operate on.
        with Session(in_memory_db) as session:
            session.add(
                CachedMetadata(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid="lb-1",
                    name="Sales",
                    object_type="LIVEBOARD",
                    owner_guid="u1",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    synced_at=datetime.now(tz=timezone.utc),
                )
            )
            session.commit()

        job_b = _create_job({"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]})
        asyncio.run(
            _execute_delete(job_id=job_b, cluster_id="c1", org_id=0, object_ids=["lb-1"], action_type="bulk_delete")
        )

        rows = _audit_rows(in_memory_db)
        assert len(rows) == 2, f"Each execute should write exactly one audit row; got {len(rows)}"
