"""
Unit tests for ts_admin.main._recover_stuck_jobs.

The behaviour under test is a data-loss guard, so the tests are written to
fail if the guard is removed rather than to describe the happy path:

  - crash-recovery must NOT infer "deleted" from tml_export_status
  - it must not reach across cluster or org boundaries
  - it must treat Bulk Deleter jobs the same as Archiver deletes
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, create_engine, select

from ts_admin.main import _recover_stuck_jobs
from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)


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
    with Session(engine) as session:
        for cid in ("c1", "c2"):
            session.add(
                Cluster(
                    id=cid,
                    name=cid,
                    url=f"https://{cid}.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )
        session.commit()
    return engine


def _cached(session, *, cluster_id: str, org_id: int, ts_guid: str) -> None:
    session.add(
        CachedMetadata(
            cluster_id=cluster_id,
            org_id=org_id,
            ts_guid=ts_guid,
            name=ts_guid,
            object_type="LIVEBOARD",
            owner_guid="u1",
            owner_name="Alice",
            tag_names=json.dumps([]),
            synced_at=NOW,
        )
    )


def _record(
    session,
    *,
    cluster_id: str,
    job_id: str,
    ts_guid: str,
    org_id: int = 0,
    confirmed: bool = False,
) -> None:
    session.add(
        ArchiveRecord(
            cluster_id=cluster_id,
            job_id=job_id,
            ts_guid=ts_guid,
            name=ts_guid,
            object_type="LIVEBOARD",
            owner_guid="u1",
            owner_name="Alice",
            org_id=org_id,
            tml_path=f"/tmp/{ts_guid}.tml",
            tml_export_status="SUCCESS",
            deleted_confirmed_at=NOW if confirmed else None,
        )
    )


def _stuck_job(session, *, job_id: str, cluster_id: str = "c1", job_type: str = "archive") -> None:
    job = Job(id=job_id, cluster_id=cluster_id, job_type=job_type, status="RUNNING", total=1)
    params = {"cluster_id": cluster_id, "org_id": 0, "object_ids": []}
    if job_type == "archive":
        params["action"] = "delete"
    job.set_parameters(params)
    session.add(job)


def _guids(engine, cluster_id: str = "c1", org_id: int = 0) -> set[str]:
    with Session(engine) as session:
        rows = session.exec(
            select(CachedMetadata.ts_guid).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
            )
        ).all()
    return set(rows)


# ── The core regression: TML export is not evidence of deletion ───────────────


@pytest.mark.parametrize("job_type", ["archive", "bulk_delete"])
def test_exported_but_unconfirmed_objects_keep_their_cache_rows(in_memory_db, job_type):
    """
    The crash window this guards is the common one: `_execute_delete` exports
    EVERY object in Phase A before Phase B deletes any, so a crash between the
    phases leaves every record SUCCESS-exported and zero objects deleted.
    Purging there hides live ThoughtSpot objects from the admin.

    Runs for the Bulk Deleter too — it reaches the same `_execute_delete` but
    creates `job_type="bulk_delete"`, which recovery used to ignore entirely.
    """
    with Session(in_memory_db) as session:
        _stuck_job(session, job_id="j1", job_type=job_type)
        for guid in ("lb-1", "lb-2", "lb-3"):
            _cached(session, cluster_id="c1", org_id=0, ts_guid=guid)
            _record(session, cluster_id="c1", job_id="j1", ts_guid=guid, confirmed=False)
        session.commit()

    _recover_stuck_jobs()

    assert _guids(in_memory_db) == {"lb-1", "lb-2", "lb-3"}


@pytest.mark.parametrize("job_type", ["archive", "bulk_delete"])
def test_confirmed_deletes_are_purged_and_unconfirmed_are_kept(in_memory_db, job_type):
    """A partially-completed job: only the confirmed half leaves the cache."""
    with Session(in_memory_db) as session:
        _stuck_job(session, job_id="j1", job_type=job_type)
        for guid, confirmed in (("lb-1", True), ("lb-2", False)):
            _cached(session, cluster_id="c1", org_id=0, ts_guid=guid)
            _record(session, cluster_id="c1", job_id="j1", ts_guid=guid, confirmed=confirmed)
        session.commit()

    _recover_stuck_jobs()

    assert _guids(in_memory_db) == {"lb-2"}


# ── Scoping ───────────────────────────────────────────────────────────────────


def test_purge_does_not_cross_cluster_boundaries(in_memory_db):
    """The same GUID in two clusters: recovery for cluster A must not touch B."""
    with Session(in_memory_db) as session:
        _stuck_job(session, job_id="j1", cluster_id="c1")
        _cached(session, cluster_id="c1", org_id=0, ts_guid="lb-1")
        _cached(session, cluster_id="c2", org_id=0, ts_guid="lb-1")
        _record(session, cluster_id="c1", job_id="j1", ts_guid="lb-1", confirmed=True)
        session.commit()

    _recover_stuck_jobs()

    assert _guids(in_memory_db, cluster_id="c1") == set()
    assert _guids(in_memory_db, cluster_id="c2") == {"lb-1"}


def test_purge_does_not_cross_org_boundaries(in_memory_db):
    """The same GUID in two orgs on one cluster: only the deleted org's row goes."""
    with Session(in_memory_db) as session:
        _stuck_job(session, job_id="j1", cluster_id="c1")
        _cached(session, cluster_id="c1", org_id=0, ts_guid="lb-1")
        _cached(session, cluster_id="c1", org_id=7, ts_guid="lb-1")
        _record(session, cluster_id="c1", job_id="j1", ts_guid="lb-1", org_id=0, confirmed=True)
        session.commit()

    _recover_stuck_jobs()

    assert _guids(in_memory_db, cluster_id="c1", org_id=0) == set()
    assert _guids(in_memory_db, cluster_id="c1", org_id=7) == {"lb-1"}


def test_purge_is_scoped_to_the_stuck_job(in_memory_db):
    """A confirmed record from a DIFFERENT, completed job is not collateral."""
    with Session(in_memory_db) as session:
        _stuck_job(session, job_id="j1", cluster_id="c1")
        _cached(session, cluster_id="c1", org_id=0, ts_guid="lb-1")
        _cached(session, cluster_id="c1", org_id=0, ts_guid="lb-other")
        _record(session, cluster_id="c1", job_id="j1", ts_guid="lb-1", confirmed=True)
        _record(session, cluster_id="c1", job_id="j-other", ts_guid="lb-other", confirmed=True)
        session.commit()

    _recover_stuck_jobs()

    assert _guids(in_memory_db) == {"lb-other"}


# ── Non-delete jobs ───────────────────────────────────────────────────────────


def test_non_delete_archive_job_purges_nothing(in_memory_db):
    """`job_type="archive"` also covers non-destructive actions (e.g. tagging)."""
    with Session(in_memory_db) as session:
        job = Job(id="j1", cluster_id="c1", job_type="archive", status="RUNNING", total=1)
        job.set_parameters({"cluster_id": "c1", "org_id": 0, "action": "tag"})
        session.add(job)
        _cached(session, cluster_id="c1", org_id=0, ts_guid="lb-1")
        _record(session, cluster_id="c1", job_id="j1", ts_guid="lb-1", confirmed=True)
        session.commit()

    _recover_stuck_jobs()

    assert _guids(in_memory_db) == {"lb-1"}


def test_sync_job_purges_nothing_and_is_still_failed(in_memory_db):
    with Session(in_memory_db) as session:
        job = Job(id="j1", cluster_id="c1", job_type="sync", status="QUEUED", total=1)
        session.add(job)
        _cached(session, cluster_id="c1", org_id=0, ts_guid="lb-1")
        session.commit()

    _recover_stuck_jobs()

    assert _guids(in_memory_db) == {"lb-1"}
    with Session(in_memory_db) as session:
        assert session.get(Job, "j1").status == "FAILED"


# ── Job finalisation is unchanged ─────────────────────────────────────────────


def test_stuck_jobs_are_marked_failed(in_memory_db):
    with Session(in_memory_db) as session:
        _stuck_job(session, job_id="j1")
        session.add(Job(id="j2", cluster_id="c1", job_type="sync", status="QUEUED", total=1))
        session.add(Job(id="j3", cluster_id="c1", job_type="sync", status="COMPLETE", total=1))
        session.commit()

    _recover_stuck_jobs()

    with Session(in_memory_db) as session:
        assert session.get(Job, "j1").status == "FAILED"
        assert session.get(Job, "j2").status == "FAILED"
        assert session.get(Job, "j3").status == "COMPLETE"
        assert session.get(Job, "j1").error == "Server restarted while job was running"


# ── The additive column migration ─────────────────────────────────────────────


def test_migration_backfills_legacy_rows_as_confirmed(monkeypatch, tmp_path):
    """
    An existing install's archive_records has no deleted_confirmed_at column.
    Upgrading must add it AND stamp historical SUCCESS rows, or every archive
    the admin already has becomes silently unrestorable.
    """
    from sqlalchemy import text

    import ts_admin.database as db_module

    db_file = tmp_path / "legacy.sqlite"
    engine = create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)

    # Build the table as it existed BEFORE the column was added.
    db_module.init_db()
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE archive_records DROP COLUMN deleted_confirmed_at"))
        conn.execute(
            text(
                "INSERT INTO archive_records "
                "(id, cluster_id, job_id, ts_guid, name, object_type, owner_guid, owner_name, "
                " org_id, days_unused, tags, tml_export_status, archived_at) VALUES "
                "('r1','c1','j1','lb-1','Sales','LIVEBOARD','u1','Alice',0,0,'[]','SUCCESS',:t),"
                "('r2','c1','j1','lb-2','Ops','LIVEBOARD','u1','Alice',0,0,'[]','FAILED',:t)"
            ),
            {"t": NOW - timedelta(days=30)},
        )

    db_module.init_db()

    with Session(engine) as session:
        restorable = session.get(ArchiveRecord, "r1")
        failed = session.get(ArchiveRecord, "r2")
    # The SUCCESS row keeps the pre-upgrade reading: assumed deleted, restorable.
    assert restorable.deleted_confirmed_at is not None
    # A FAILED export was never deleted, then or now.
    assert failed.deleted_confirmed_at is None


def test_migration_does_not_reconfirm_on_every_startup(in_memory_db):
    """
    The backfill runs with the ALTER, once. If it re-ran, it would resurrect
    exactly the rows recovery deliberately left unconfirmed.
    """
    import ts_admin.database as db_module

    with Session(in_memory_db) as session:
        _record(session, cluster_id="c1", job_id="j1", ts_guid="lb-1", confirmed=False)
        session.commit()

    db_module.init_db()

    with Session(in_memory_db) as session:
        assert session.get(ArchiveRecord, session.exec(select(ArchiveRecord.id)).one()).deleted_confirmed_at is None
