"""
Dry-run safety: parametrized invariant test for every destructive endpoint.

The non-negotiable rule from CLAUDE.md is that dry-run endpoints must NEVER
call the destructive write path (`deletion_service._execute_delete`,
`archiver_service.execute`, `archiver_service.restore`). They must only:

  - return 202 with a job_id
  - create a Job row whose job_type ends with `_dryrun`
  - leave ts_metadata, archive_records, and audit_log unchanged

When you ship a new destructive feature, add an entry to DRYRUN_ENDPOINTS
below and the safety check is extended automatically.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster

# ── Registry: every (endpoint, payload, expected_job_type_suffix) ──────────────
#
# When you add a destructive feature with a dryrun variant, register it here.

DRYRUN_ENDPOINTS = [
    pytest.param(
        "/api/v1/deleter/dryrun",
        {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]},
        "_dryrun",
        id="deleter-dryrun",
    ),
    pytest.param(
        "/api/v1/archiver/dryrun",
        {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]},
        "_dryrun",
        id="archiver-dryrun",
    ),
]


# ── Fixtures (copy of the established pattern from test_deleter_api.py) ────────


@pytest.fixture(autouse=True)
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
def client(in_memory_db):
    from ts_admin.main import create_app

    return TestClient(create_app())


@pytest.fixture
def seeded(in_memory_db):
    """One cluster + one cached object so the resolve calls succeed."""
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
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="lb-1",
                name="Sales Dashboard",
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="Alice",
                tag_names=json.dumps([]),
                synced_at=datetime.now(tz=timezone.utc),
            )
        )
        session.commit()


@pytest.fixture
def trip_wires(monkeypatch):
    """
    Replace every destructive write entry-point with a function that fails the
    test immediately if invoked. A dry-run endpoint must never trip these.
    """

    async def _fail_if_called(*args, **kwargs):  # noqa: ARG001
        pytest.fail("Destructive write path invoked from a dry-run endpoint")

    from ts_admin.services import archiver_service, deletion_service

    monkeypatch.setattr(deletion_service, "_execute_delete", _fail_if_called)
    monkeypatch.setattr(archiver_service, "execute", _fail_if_called)
    monkeypatch.setattr(archiver_service, "restore", _fail_if_called)


@pytest.fixture
def neutered_dryrun(monkeypatch):
    """
    Stub the dry-run BG task so we don't reach out to ThoughtSpot. The endpoint's
    safety properties (job_type, status code, no writes) are observable without
    actually running the impact check.
    """

    async def _noop(*args, **kwargs):  # noqa: ARG001
        return None

    from ts_admin.services import deletion_service

    monkeypatch.setattr(deletion_service, "dryrun", _noop)


def _snapshot_writeable_tables(engine) -> dict[str, int]:
    """Row counts that a dry-run is forbidden from changing."""
    from ts_admin.models.archive_record import ArchiveRecord
    from ts_admin.models.audit_log import AuditLog

    with Session(engine) as s:
        return {
            "ts_metadata": len(s.exec(select(CachedMetadata)).all()),
            "archive_records": len(s.exec(select(ArchiveRecord)).all()),
            "audit_log": len(s.exec(select(AuditLog)).all()),
        }


# ── The invariant ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("endpoint", "payload", "expected_suffix"), DRYRUN_ENDPOINTS)
class TestDryRunSafety:
    def test_returns_202_with_job_id(
        self, client, seeded, trip_wires, neutered_dryrun, endpoint, payload, expected_suffix
    ):
        r = client.post(endpoint, json=payload)
        assert r.status_code == 202, r.text
        assert r.json()["job_id"]

    def test_job_type_marks_it_as_dryrun(
        self, client, seeded, trip_wires, neutered_dryrun, in_memory_db, endpoint, payload, expected_suffix
    ):
        r = client.post(endpoint, json=payload)
        job_id = r.json()["job_id"]

        from ts_admin.models.job import Job

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        assert job is not None
        assert job.job_type.endswith(expected_suffix), (
            f"{endpoint} created job with type {job.job_type!r} — expected suffix {expected_suffix!r}. "
            "If this is intentional, register a different suffix in DRYRUN_ENDPOINTS."
        )

    def test_no_writes_to_protected_tables(
        self, client, seeded, trip_wires, neutered_dryrun, in_memory_db, endpoint, payload, expected_suffix
    ):
        before = _snapshot_writeable_tables(in_memory_db)
        client.post(endpoint, json=payload)
        after = _snapshot_writeable_tables(in_memory_db)
        assert before == after, (
            f"{endpoint} mutated protected tables during dry-run: before={before} after={after}. "
            "Dry-run must never write to ts_metadata, archive_records, or audit_log."
        )
