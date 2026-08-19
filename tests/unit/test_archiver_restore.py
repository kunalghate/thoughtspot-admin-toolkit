"""
Unit tests for ts_admin.services.archiver_service.restore.

Restore is not an undelete: the TML is re-imported as a brand-new object with a
NEW GUID. These tests pin the three things that were wrong in production —

  1. a restored object came straight back as an Archiver delete candidate
     (the cache row carried the pre-deletion last_accessed_at and NULL
     modified_at/created_at, so it satisfied BOTH halves of _stale_conditions),
  2. the cache row claimed tags nothing had re-applied on the cluster,
  3. import results were matched to records by list position, so a reordered
     response wrote another object's GUID onto the record.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, create_engine, select

from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job

# ── Fake TS client ─────────────────────────────────────────────────────────────


class _FakeClient:
    """Stand-in for ThoughtSpotClient; import_tml is driven per-test."""

    # name → new GUID assigned by the "cluster"
    guid_by_name: dict[str, str] = {}
    # if set, import_tml returns this literal payload instead of building one
    canned_results: list[dict] | None = None
    # if True, results are returned in reverse request order
    reverse_results: bool = False
    import_calls: list[list[str]] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def import_tml(self, *, tml_strings, import_policy="PARTIAL"):
        _FakeClient.import_calls.append(list(tml_strings))
        if _FakeClient.canned_results is not None:
            return list(_FakeClient.canned_results)
        # Each TML string here is just the object's name (see _archive_record).
        results = [
            {
                "response": {
                    "header": {"id_guid": _FakeClient.guid_by_name.get(name, ""), "name": name},
                    "status": {"status_code": "OK"},
                }
            }
            for name in tml_strings
        ]
        if _FakeClient.reverse_results:
            results.reverse()
        return results


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.guid_by_name = {}
    _FakeClient.canned_results = None
    _FakeClient.reverse_results = False
    _FakeClient.import_calls = []


# ── Fixtures ──────────────────────────────────────────────────────────────────


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
def cluster_row(in_memory_db):
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
        session.commit()


def _archive_record(
    tmp_path,
    *,
    record_id: str,
    name: str,
    guid: str,
    days_unused: int = 400,
    tags: list[str] | None = None,
    object_type: str = "LIVEBOARD",
) -> ArchiveRecord:
    """An archived record whose TML file on disk is just the object's name."""
    tml_path = tmp_path / f"{guid}.tml"
    tml_path.write_text(name, encoding="utf-8")
    stale = datetime.now(tz=timezone.utc) - timedelta(days=days_unused)
    return ArchiveRecord(
        id=record_id,
        cluster_id="c1",
        job_id="archive-job-1",
        ts_guid=guid,
        name=name,
        object_type=object_type,
        owner_guid="u1",
        owner_name="Alice",
        org_id=0,
        last_accessed_at=stale,
        days_unused=days_unused,
        tags=json.dumps(tags if tags is not None else ["Finance"]),
        tml_path=str(tml_path),
        tml_export_status="SUCCESS",
    )


def _create_job(job_type: str) -> str:
    import uuid

    from ts_admin.database import get_session

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, cluster_id="c1", job_type=job_type, status="QUEUED")
    job.set_parameters({"cluster_id": "c1", "org_id": 0})
    with get_session() as s:
        s.add(job)
        s.commit()
    return job_id


def _run_restore(record_ids: list[str]) -> str:
    from ts_admin.services.archiver_service import restore

    job_id = _create_job("archive_restore")
    asyncio.run(restore(job_id=job_id, cluster_id="c1", org_id=0, archive_record_ids=record_ids))
    return job_id


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestRestoreDoesNotReArchive:
    def test_restored_object_is_not_an_archiver_delete_candidate(
        self, in_memory_db, patched_env, cluster_row, tmp_path
    ):
        from ts_admin.services.archiver_service import ArchiverService

        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.commit()
        _FakeClient.guid_by_name = {"Sales": "lb-new"}

        # Sanity: with the pre-fix cache semantics this row WOULD be selected —
        # assert the archiver is actually looking at something afterwards.
        _run_restore(["r1"])

        with Session(in_memory_db) as s:
            row = s.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-new")).one()
            assert row.name == "Sales"

        preview = ArchiverService.preview(cluster_id="c1", org_id=0, stale_activity_days=90, stale_modified_days=90)
        assert preview["total"] == 0

    def test_cache_row_carries_restore_time_stamps_not_pre_deletion_ones(
        self, in_memory_db, patched_env, cluster_row, tmp_path
    ):
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.commit()
        _FakeClient.guid_by_name = {"Sales": "lb-new"}

        _run_restore(["r1"])

        with Session(in_memory_db) as s:
            row = s.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-new")).one()
        # NULL is the dangerous value here: _stale_conditions counts NULL
        # modified_at / last_accessed_at as stale.
        assert row.created_at is not None
        assert row.modified_at is not None
        assert row.last_accessed_at is not None
        # Read back from SQLite as naive UTC (SQLAlchemy's SQLite DATETIME drops
        # tzinfo), so compare against naive UTC — not local naive.
        now_naive_utc = datetime.now(tz=timezone.utc).replace(tzinfo=None)
        assert row.last_accessed_at > now_naive_utc - timedelta(minutes=5)
        assert row.modified_at > now_naive_utc - timedelta(minutes=5)

    def test_pre_fix_row_shape_is_a_delete_candidate(self, in_memory_db, cluster_row):
        """
        Non-vacuity guard for the test above: prove the Archiver really would
        have re-selected the row shape restore used to write (stale
        last_accessed_at + NULL modified_at). Without this, "preview total == 0"
        could pass simply because preview never matches anything here.
        """
        from ts_admin.services.archiver_service import ArchiverService

        with Session(in_memory_db) as s:
            s.add(
                CachedMetadata(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid="lb-new",
                    name="Sales",
                    object_type="LIVEBOARD",
                    owner_guid="u1",
                    owner_name="Alice",
                    tag_names=json.dumps(["Finance"]),
                    created_at=None,
                    modified_at=None,
                    last_accessed_at=datetime.now(tz=timezone.utc) - timedelta(days=400),
                    synced_at=datetime.now(tz=timezone.utc),
                )
            )
            s.commit()

        preview = ArchiverService.preview(cluster_id="c1", org_id=0, stale_activity_days=90, stale_modified_days=90)
        assert preview["total"] == 1

    def test_cache_row_claims_no_tags_because_none_were_re_applied(
        self, in_memory_db, patched_env, cluster_row, tmp_path
    ):
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old", tags=["Finance"]))
            s.commit()
        _FakeClient.guid_by_name = {"Sales": "lb-new"}

        _run_restore(["r1"])

        with Session(in_memory_db) as s:
            row = s.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-new")).one()
        assert row.get_tag_names() == []


class TestRestoreResultMatching:
    def test_out_of_order_results_are_matched_by_name(self, in_memory_db, patched_env, cluster_row, tmp_path):
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old-1"))
            s.add(_archive_record(tmp_path, record_id="r2", name="Revenue", guid="lb-old-2", object_type="ANSWER"))
            s.add(_archive_record(tmp_path, record_id="r3", name="Churn", guid="lb-old-3"))
            s.commit()
        _FakeClient.guid_by_name = {"Sales": "new-1", "Revenue": "new-2", "Churn": "new-3"}
        _FakeClient.reverse_results = True

        job_id = _run_restore(["r1", "r2", "r3"])

        with Session(in_memory_db) as s:
            assert s.get(ArchiveRecord, "r1").restored_as_guid == "new-1"
            assert s.get(ArchiveRecord, "r2").restored_as_guid == "new-2"
            assert s.get(ArchiveRecord, "r3").restored_as_guid == "new-3"
            assert s.get(Job, job_id).status == "COMPLETE"

    def test_records_sharing_a_name_are_split_across_requests(self, in_memory_db, patched_env, cluster_row, tmp_path):
        # Two archived objects called "Sales" cannot be told apart inside one
        # response, so they must not travel in the same request.
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old-1"))
            s.add(_archive_record(tmp_path, record_id="r2", name="Sales", guid="lb-old-2"))
            s.commit()
        _FakeClient.guid_by_name = {"Sales": "new-1"}

        _run_restore(["r1", "r2"])

        assert len(_FakeClient.import_calls) == 2
        assert all(len(call) == 1 for call in _FakeClient.import_calls)

    def test_a_result_for_an_unknown_name_is_not_attributed(self, in_memory_db, patched_env, cluster_row, tmp_path):
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.commit()
        # TS answers about a different object entirely.
        _FakeClient.canned_results = [
            {"response": {"header": {"id_guid": "someone-else", "name": "Marketing"}, "status": {"status_code": "OK"}}}
        ]

        job_id = _run_restore(["r1"])

        with Session(in_memory_db) as s:
            assert s.get(ArchiveRecord, "r1").restored_as_guid is None
            assert s.exec(select(CachedMetadata)).all() == []
            assert s.get(Job, job_id).status == "FAILED"

    def test_duplicate_results_for_one_name_are_refused(self, in_memory_db, patched_env, cluster_row, tmp_path):
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.commit()
        _FakeClient.canned_results = [
            {"response": {"header": {"id_guid": "new-a", "name": "Sales"}, "status": {"status_code": "OK"}}},
            {"response": {"header": {"id_guid": "new-b", "name": "Sales"}, "status": {"status_code": "OK"}}},
        ]

        _run_restore(["r1"])

        with Session(in_memory_db) as s:
            assert s.get(ArchiveRecord, "r1").restored_as_guid is None

    def test_nameless_response_falls_back_to_position_only_when_one_to_one(
        self, in_memory_db, patched_env, cluster_row, tmp_path
    ):
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.commit()
        _FakeClient.canned_results = [{"object_id": "new-1", "status": {"status_code": "OK"}}]

        _run_restore(["r1"])

        with Session(in_memory_db) as s:
            assert s.get(ArchiveRecord, "r1").restored_as_guid == "new-1"


class TestRestoreDiscoverability:
    def test_job_result_states_the_new_guid_and_lost_acl_semantics(
        self, in_memory_db, patched_env, cluster_row, tmp_path
    ):
        from ts_admin.services.archiver_service import RESTORE_NOTES

        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.commit()
        _FakeClient.guid_by_name = {"Sales": "lb-new"}

        job_id = _run_restore(["r1"])

        with Session(in_memory_db) as s:
            result = s.get(Job, job_id).get_result()
        assert result["succeeded"] == 1
        assert result["notes"] == list(RESTORE_NOTES)
        assert any("NEW GUID" in note for note in result["notes"])
        assert any("ACL" in note for note in result["notes"])

    def test_already_restored_record_is_skipped_not_re_imported(self, in_memory_db, patched_env, cluster_row, tmp_path):
        rec = _archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old")
        rec.restored_at = datetime.now(tz=timezone.utc)
        rec.restored_as_guid = "lb-new"
        with Session(in_memory_db) as s:
            s.add(rec)
            s.commit()

        job_id = _run_restore(["r1"])

        assert _FakeClient.import_calls == []
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            status, error = job.status, job.error or ""
        # A restore that restored NOTHING is FAILED, not COMPLETE — an
        # all-skipped run used to report COMPLETE with succeeded=0, which reads
        # as "the restore ran and it's done". The message names the bucket and
        # the reason, because the count alone is not actionable.
        assert status == "FAILED"
        assert "0 of 1 objects restored" in error
        assert "1 record(s) were not restorable" in error
        assert "already restored" in error


# ── M14: zero restores is FAILED, never PARTIAL and never COMPLETE ────────────


class TestNothingRestoredIsFailed:
    """
    `if succeeded == 0 and failed: mark_failed` covered only the import-failure
    case; an ALL-SKIPPED run fell through to `mark_complete`, so a restore that
    restored nothing reported COMPLETE with succeeded=0 — and the audit row was
    computed from a COMPLETE-or-PARTIAL expression that could not say FAILED at
    all.
    """

    def _audit_rows(self):
        from ts_admin.database import get_session

        with get_session() as s:
            return list(s.exec(select(AuditLog).where(AuditLog.action_type == "restore")).all())

    def test_every_import_failing_ends_failed_in_the_job_and_the_audit_row(
        self,
        in_memory_db,
        patched_env,
        cluster_row,
        tmp_path,
    ):
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.add(_archive_record(tmp_path, record_id="r2", name="Revenue", guid="ans-old"))
            s.commit()
        _FakeClient.canned_results = [
            {"response": {"header": {"name": "Sales"}, "status": {"status_code": "ERROR", "error_message": "nope"}}},
            {"response": {"header": {"name": "Revenue"}, "status": {"status_code": "ERROR", "error_message": "nope"}}},
        ]

        job_id = _run_restore(["r1", "r2"])

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        assert job.status == "FAILED"
        assert "0 of 2 objects restored" in (job.error or "")
        assert "2 TML import(s) failed" in (job.error or "")

        rows = self._audit_rows()
        assert len(rows) == 1
        assert rows[0].status == "FAILED"
        assert rows[0].items_affected == 0

    def test_a_genuinely_partial_restore_is_still_partial(
        self,
        in_memory_db,
        patched_env,
        cluster_row,
        tmp_path,
    ):
        """Non-vacuity: the fix must not collapse PARTIAL into FAILED."""
        with Session(in_memory_db) as s:
            s.add(_archive_record(tmp_path, record_id="r1", name="Sales", guid="lb-old"))
            s.add(_archive_record(tmp_path, record_id="r2", name="Revenue", guid="ans-old"))
            s.commit()
        _FakeClient.canned_results = [
            {"response": {"header": {"id_guid": "lb-new", "name": "Sales"}, "status": {"status_code": "OK"}}},
            {"response": {"header": {"name": "Revenue"}, "status": {"status_code": "ERROR", "error_message": "nope"}}},
        ]

        job_id = _run_restore(["r1", "r2"])

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        assert job.status == "PARTIAL"
        assert (job.get_result() or {})["succeeded"] == 1
        assert self._audit_rows()[0].status == "PARTIAL"
