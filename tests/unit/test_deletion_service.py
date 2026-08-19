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
from ts_admin.ts_client.exceptions import TSInvalidParametersError

# ── Fake TS client ─────────────────────────────────────────────────────────────


class _FakeClient:
    """
    Stand-in for ThoughtSpotClient. Configurable per-test via class
    attributes set in fixtures so `async with _FakeClient(...) as c:` works.
    """

    tml_results: list[dict] = []  # what tml_export returns
    delete_calls: list[tuple[str, list[str]]] = []  # (object_type, ids)
    delete_should_raise: Exception | None = None

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
        if _FakeClient.delete_should_raise is not None:
            raise _FakeClient.delete_should_raise


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.tml_results = []
    _FakeClient.delete_calls = []
    _FakeClient.delete_should_raise = None


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
                synced_at=now,
            )
        )
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="ans-1",
                name="Revenue",
                object_type="ANSWER",
                owner_guid="u2",
                owner_name="Bob",
                tag_names=json.dumps([]),
                synced_at=now,
            )
        )
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

        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

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


class TestTmlExportOutcomeNesting:
    """`tml/export` puts the per-object verdict one level deeper than it looks::

        {"edoc": "…", "info": {"id": "<guid>",
                               "status": {"status_code": "OK"|"ERROR",
                                          "error_message": "…"}}}

    Reading `info["error_message"]` was always None, so every failure was
    recorded as the generic placeholder and the admin never saw the reason.
    """

    FORBIDDEN = "Error Code: FORBIDDEN. Cannot download TML due to lack of access to objects."

    def _run(self, guids):
        from ts_admin.services.deletion_service import _execute_delete

        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": guids})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=guids,
                action_type="bulk_delete",
            )
        )
        return job_id

    def test_thoughtspots_own_reason_is_recorded_not_the_placeholder(self, in_memory_db, patched_env, seeded):
        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
            {
                "info": {"id": "ans-1", "status": {"status_code": "ERROR", "error_message": self.FORBIDDEN}},
                "edoc": "",
            },
        ]
        job_id = self._run(["lb-1", "ans-1"])

        with Session(in_memory_db) as s:
            recs = {r.ts_guid: r for r in s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()}

        assert recs["ans-1"].tml_export_status == "FAILED"
        assert recs["ans-1"].tml_export_error == self.FORBIDDEN
        assert "returned empty content" not in (recs["ans-1"].tml_export_error or "")

    def test_a_batch_carries_per_object_verdicts_not_one_all_or_nothing_result(self, in_memory_db, patched_env, seeded):
        """Measured live: one 60-object batch returned HTTP 200 with 58 OK and
        2 ERROR. A failure inside the batch must cost only its own object."""
        _FakeClient.tml_results = [
            {"info": {"id": "lb-1", "status": {"status_code": "OK"}}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1", "status": {"status_code": "ERROR", "error_message": self.FORBIDDEN}}, "edoc": ""},
        ]
        job_id = self._run(["lb-1", "ans-1"])

        called_ids = sorted(i for _, ids in _FakeClient.delete_calls for i in ids)
        assert called_ids == ["lb-1"], "the OK object in the same batch is still exported and still deleted"

        with Session(in_memory_db) as s:
            recs = {r.ts_guid: r for r in s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()}
        assert recs["lb-1"].tml_export_status == "SUCCESS"
        assert recs["ans-1"].tml_export_status == "FAILED"

    def test_an_error_status_fails_the_object_even_when_an_edoc_came_with_it(self, in_memory_db, patched_env, seeded):
        """This gates a permanent delete, so anything ThoughtSpot flagged is
        treated as "no trustworthy backup" — the fail-safe direction."""
        _FakeClient.tml_results = [
            {
                "info": {"id": "lb-1", "status": {"status_code": "ERROR", "error_message": self.FORBIDDEN}},
                "edoc": "liveboard:\n  name: Sales\n",
            },
        ]
        job_id = self._run(["lb-1"])

        assert _FakeClient.delete_calls == [], "never delete an object whose backup ThoughtSpot flagged"
        with Session(in_memory_db) as s:
            rec = s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).first()
            job = s.get(Job, job_id)
        assert rec.tml_export_status == "FAILED"
        assert rec.tml_export_error == self.FORBIDDEN
        assert job.status == "FAILED", "the only requested object was never deleted"


class TestExecuteDeletePartial:
    def test_tml_export_failure_excludes_object_from_delete(
        self,
        in_memory_db,
        patched_env,
        seeded,
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

        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        # delete_metadata was called only for the type with successful TML
        called_ids = sorted(i for _, ids in _FakeClient.delete_calls for i in ids)
        assert called_ids == ["lb-1"]

        with Session(in_memory_db) as s:
            recs = {r.ts_guid: r for r in s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()}
            job = s.get(Job, job_id)

        assert recs["lb-1"].tml_export_status == "SUCCESS"
        assert recs["ans-1"].tml_export_status == "FAILED"
        assert recs["ans-1"].tml_export_error and "missing edoc" in recs["ans-1"].tml_export_error
        assert job.status == "PARTIAL"
        assert (job.get_result() or {}).get("succeeded") == 1

    def test_omitted_from_export_response_is_a_failure_not_a_silent_skip(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        """
        Real clusters return an export response that simply OMITS objects they
        cannot export — no entry, no error row. Iterating the response instead
        of the request made those GUIDs vanish from every count: the job read
        COMPLETE with succeeded=2/failed=0 while one object was never touched
        and its ArchiveRecord stayed PENDING forever.
        """
        from ts_admin.services.deletion_service import _execute_delete

        # ans-1 is requested but entirely absent from the response.
        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
        ]

        job_id = _create_job(
            "bulk_delete",
            {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]},
        )

        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        # Only the exported object was deleted.
        called_ids = sorted(i for _, ids in _FakeClient.delete_calls for i in ids)
        assert called_ids == ["lb-1"]

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            recs = {r.ts_guid: r for r in s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()}
            audits = s.exec(select(AuditLog)).all()

        # ── The job is PARTIAL, never COMPLETE
        assert job.status == "PARTIAL"

        # ── The omitted GUID is named in the result payload
        result = job.get_result() or {}
        assert result["failed_tml_export"] == 1
        assert result["failed_tml_guids"] == ["ans-1"]

        # ── Totals reconcile against the request
        assert result["requested"] == 2
        assert result["reconciled"] is True
        assert (
            result["succeeded"] + result["failed_tml_export"] + result["failed_delete"] + result["not_attempted"] == 2
        )

        # ── The audit row agrees
        assert [a.status for a in audits if a.action_type == "bulk_delete"] == ["PARTIAL"]

        # ── The omitted object is not stranded at PENDING
        assert recs["ans-1"].tml_export_status == "FAILED"
        assert "omitted" in (recs["ans-1"].tml_export_error or "")

    def test_export_entry_without_an_id_does_not_swallow_a_requested_guid(
        self,
        in_memory_db,
        patched_env,
        seeded,
        monkeypatch,
    ):
        """An `info` block with no id cannot be attributed to a request — the
        requested GUID it was meant to answer for must still be accounted."""
        from ts_admin.services.deletion_service import _execute_delete

        class _NoIdClient(_FakeClient):
            async def tml_export(self, *, object_ids):
                return [
                    {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
                    {"info": {}, "edoc": "answer:\n  name: Revenue\n"},  # unattributable
                ]

        monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _NoIdClient)

        job_id = _create_job(
            "bulk_delete",
            {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]},
        )
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        called_ids = sorted(i for _, ids in _FakeClient.delete_calls for i in ids)
        assert called_ids == ["lb-1"]

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        result = job.get_result() or {}
        assert job.status == "PARTIAL"
        assert result["failed_tml_guids"] == ["ans-1"]
        assert result["reconciled"] is True


class TestPoisonObjectIsolation:
    """
    `metadata/tml/export` is all-or-nothing with no per-object status, and every
    real cluster carries a few dozen objects it refuses to export (43 on
    se-demo, 36 on ps-internal-prod). Before this, one poison GUID in a chunk of
    50 raised, and the handler marked all 50 FAILED with the *poison object's*
    error, deleted none, and failed the job — so an admin cleaning up 300 stale
    liveboards was stopped dead on the first batch.
    """

    @pytest.fixture
    def four_liveboards(self, in_memory_db):
        with Session(in_memory_db) as s:
            s.add(
                Cluster(
                    id="c1",
                    name="Prod",
                    url="https://prod.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )
            now = datetime.now(tz=timezone.utc)
            for i in range(1, 5):
                s.add(
                    CachedMetadata(
                        cluster_id="c1",
                        org_id=0,
                        ts_guid=f"lb-{i}",
                        name=f"Board {i}",
                        object_type="LIVEBOARD",
                        owner_guid="u1",
                        owner_name="Alice",
                        tag_names=json.dumps([]),
                        synced_at=now,
                    )
                )
            s.commit()

    def test_one_unexportable_object_costs_only_itself(
        self,
        in_memory_db,
        patched_env,
        four_liveboards,
        monkeypatch,
    ):
        from ts_admin.services.deletion_service import _execute_delete
        from ts_admin.ts_client.exceptions import TSServerError

        class _PoisonClient(_FakeClient):
            async def tml_export(self, *, object_ids):
                if "lb-3" in object_ids:
                    # The message encodes the batch size, so the assertion below
                    # can tell "the error from the batch of 4" apart from "the
                    # error from the isolated retry of this one object".
                    raise TSServerError(status_code=500, body=f"No value present (batch of {len(object_ids)})")
                return [{"info": {"id": g}, "edoc": f"liveboard:\n  name: {g}\n"} for g in object_ids]

        monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _PoisonClient)

        guids = ["lb-1", "lb-2", "lb-3", "lb-4"]
        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": guids})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=guids,
                action_type="bulk_delete",
            )
        )

        # ── The three healthy objects were still deleted
        deleted_ids = sorted(i for _, ids in _FakeClient.delete_calls for i in ids)
        assert deleted_ids == ["lb-1", "lb-2", "lb-4"]

        with Session(in_memory_db) as s:
            recs = {r.ts_guid: r for r in s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()}
            job = s.get(Job, job_id)
            remaining = {r.ts_guid for r in s.exec(select(CachedMetadata)).all()}

        # ── Exactly one FAILED record, and it is the poison object
        failed = [g for g, r in recs.items() if r.tml_export_status == "FAILED"]
        assert failed == ["lb-3"]
        assert sorted(g for g, r in recs.items() if r.tml_export_status == "SUCCESS") == ["lb-1", "lb-2", "lb-4"]

        # ── It carries the error from ITS OWN isolated export, not the batch's
        assert "batch of 1" in (recs["lb-3"].tml_export_error or "")
        assert "batch of 4" not in (recs["lb-3"].tml_export_error or "")

        # ── The poison object survives in the cache; the rest are purged
        assert remaining == {"lb-3"}

        # ── Reconciliation still holds: every requested GUID in exactly one bucket
        result = job.get_result() or {}
        assert job.status == "PARTIAL"
        assert result["requested"] == 4
        assert result["succeeded"] == 3
        assert result["failed_tml_export"] == 1
        assert result["failed_tml_guids"] == ["lb-3"]
        assert result["reconciled"] is True
        assert result["unaccounted_guids"] == []
        assert (
            result["succeeded"] + result["failed_tml_export"] + result["failed_delete"] + result["not_attempted"] == 4
        )

    def test_two_poison_objects_each_record_their_own_error(
        self,
        in_memory_db,
        patched_env,
        four_liveboards,
        monkeypatch,
    ):
        """Two un-exportable objects in one batch must not share one error string."""
        from ts_admin.services.deletion_service import _execute_delete
        from ts_admin.ts_client.exceptions import TSInvalidParametersError, TSServerError

        class _TwoPoisonClient(_FakeClient):
            async def tml_export(self, *, object_ids):
                if "lb-2" in object_ids:
                    raise TSServerError(status_code=500, body="No value present")
                if "lb-3" in object_ids:
                    raise TSInvalidParametersError("Invalid parameter values: metadata_identifiers")
                return [{"info": {"id": g}, "edoc": f"liveboard:\n  name: {g}\n"} for g in object_ids]

        monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _TwoPoisonClient)

        guids = ["lb-1", "lb-2", "lb-3", "lb-4"]
        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": guids})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=guids,
                action_type="bulk_delete",
            )
        )

        deleted_ids = sorted(i for _, ids in _FakeClient.delete_calls for i in ids)
        assert deleted_ids == ["lb-1", "lb-4"]

        with Session(in_memory_db) as s:
            recs = {r.ts_guid: r for r in s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()}

        assert "No value present" in (recs["lb-2"].tml_export_error or "")
        assert "metadata_identifiers" in (recs["lb-3"].tml_export_error or "")
        assert recs["lb-2"].tml_export_error != recs["lb-3"].tml_export_error

    def test_a_whole_cluster_failure_is_not_bisected(
        self,
        in_memory_db,
        patched_env,
        four_liveboards,
        monkeypatch,
    ):
        """
        A 403 is a whole-cluster condition — bisecting would just repeat the same
        failure once per object. `_export_tml_resilient` re-raises it, and the
        delete path must fail the chunk cleanly rather than crash the job.
        """
        from ts_admin.services.deletion_service import _execute_delete
        from ts_admin.ts_client.exceptions import TSInsufficientPrivilegesError

        calls: list[int] = []

        class _ForbiddenClient(_FakeClient):
            async def tml_export(self, *, object_ids):
                calls.append(len(object_ids))
                raise TSInsufficientPrivilegesError("Insufficient privileges for POST /metadata/tml/export")

        monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _ForbiddenClient)

        guids = ["lb-1", "lb-2", "lb-3", "lb-4"]
        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": guids})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=guids,
                action_type="bulk_delete",
            )
        )

        assert calls == [4], "a privilege error must not be bisected"
        assert _FakeClient.delete_calls == []

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            recs = s.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()

        assert job.status == "FAILED"
        assert {r.tml_export_status for r in recs} == {"FAILED"}
        assert all("Insufficient privileges" in (r.tml_export_error or "") for r in recs)

    def test_backup_export_never_sends_edoc_format(
        self,
        in_memory_db,
        patched_env,
        seeded,
        monkeypatch,
    ):
        """
        The `.tml` backup is the edoc written verbatim, so the delete path's
        request body must stay byte-identical — no `edoc_format`. The resilient
        helper it now shares with lineage passes `edoc_format="JSON"`;
        `_BackupExporter` has to absorb that kwarg, not forward it.
        """
        from ts_admin.services.deletion_service import _execute_delete

        _sentinel = object()
        seen: list[object] = []

        class _FormatSpyClient(_FakeClient):
            async def tml_export(self, *, object_ids, edoc_format=_sentinel):
                seen.append(edoc_format)
                return [{"info": {"id": g}, "edoc": "liveboard:\n  name: x\n"} for g in object_ids]

        monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _FormatSpyClient)

        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1"],
                action_type="bulk_delete",
            )
        )

        assert seen == [_sentinel]


class TestCachePurgeScoping:
    def test_purge_only_touches_the_deleting_cluster_and_org(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        """
        The post-delete CachedMetadata purge used to match on ts_guid alone, so
        deleting `lb-1` in (c1, org 0) also wiped the (c1, org 5) and (c2, org 0)
        rows — objects that still exist in ThoughtSpot. Dropping just cluster_id,
        or just org_id, is enough to reintroduce the bug, so both are asserted.
        """
        from ts_admin.services.deletion_service import _execute_delete

        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            s.add(
                Cluster(
                    id="c2",
                    name="Dev",
                    url="https://dev.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )
            for cluster_id, org_id in [("c1", 5), ("c2", 0)]:
                s.add(
                    CachedMetadata(
                        cluster_id=cluster_id,
                        org_id=org_id,
                        ts_guid="lb-1",  # same GUID, different scope
                        name="Sales",
                        object_type="LIVEBOARD",
                        owner_guid="u1",
                        owner_name="Alice",
                        tag_names=json.dumps([]),
                        synced_at=now,
                    )
                )
            s.commit()

        _FakeClient.tml_results = [
            {"info": {"id": "lb-1"}, "edoc": "liveboard:\n  name: Sales\n"},
        ]

        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1"],
                action_type="bulk_delete",
            )
        )

        with Session(in_memory_db) as s:
            rows = s.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-1")).all()

        # Exactly one row removed — the (c1, org 0) one.
        assert {(r.cluster_id, r.org_id) for r in rows} == {("c1", 5), ("c2", 0)}


# ── M14: zero deletions is FAILED, never PARTIAL ──────────────────────────────


class TestNothingDeletedIsFailedNeverPartial:
    """
    The job status already evaluated `succeeded == 0` first, but the AUDIT row
    did not: it was computed from a COMPLETE-or-PARTIAL expression that could
    not say FAILED at all, so a job that deleted nothing left a PARTIAL row in
    the permanent audit trail while the job itself said FAILED. The failure
    message also named only the TML bucket, so a run that failed entirely at the
    delete call reported "0 TML exports failed".
    """

    def test_audit_row_says_failed_when_every_delete_call_fails(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [
            {"info": {"id": "lb-1", "status": {"status_code": "OK"}}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1", "status": {"status_code": "OK"}}, "edoc": "answer:\n  name: Revenue\n"},
        ]
        _FakeClient.delete_should_raise = TSInvalidParametersError("deleteMetadata rejected the request")

        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            audits = s.exec(select(AuditLog).where(AuditLog.action_type == "bulk_delete")).all()

        assert job.status == "FAILED"
        assert len(audits) == 1
        assert audits[0].status == "FAILED"
        assert audits[0].items_affected == 0

        error = job.error or ""
        assert "0 of 2 objects deleted" in error
        # The delete bucket AND the reason — not "0 TML exports failed".
        assert "2 delete call(s) failed" in error
        assert "deleteMetadata rejected the request" in error

    def test_the_message_names_thoughtspots_own_tml_error(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [
            {
                "info": {
                    "id": "lb-1",
                    "status": {"status_code": "ERROR", "error_message": "Cannot download TML due to lack of access"},
                },
                "edoc": "",
            },
        ]

        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1"],
                action_type="bulk_delete",
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            audit = s.exec(select(AuditLog).where(AuditLog.action_type == "bulk_delete")).one()

        assert job.status == "FAILED"
        assert audit.status == "FAILED"
        assert "Cannot download TML due to lack of access" in (job.error or "")
        assert _FakeClient.delete_calls == [], "nothing may be deleted without a backup"

    def test_a_genuinely_partial_delete_still_says_partial(self, in_memory_db, patched_env, seeded):
        """Non-vacuity: the fix must not collapse PARTIAL into FAILED."""
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [
            {"info": {"id": "lb-1", "status": {"status_code": "OK"}}, "edoc": "liveboard:\n  name: Sales\n"},
            {"info": {"id": "ans-1", "status": {"status_code": "ERROR", "error_message": "nope"}}, "edoc": ""},
        ]

        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1", "ans-1"],
                action_type="bulk_delete",
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            audit = s.exec(select(AuditLog).where(AuditLog.action_type == "bulk_delete")).one()

        assert job.status == "PARTIAL"
        assert audit.status == "PARTIAL"
        assert (job.get_result() or {})["succeeded"] == 1

    def test_a_bug_in_our_own_code_fails_the_job_instead_of_becoming_a_failed_delete(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        """The per-chunk catch is narrowed to (TSAdminError, httpx.HTTPError) so
        a bug in our own code cannot masquerade as a cluster-side refusal on a
        permanently destructive path."""
        from ts_admin.services.deletion_service import _execute_delete

        _FakeClient.tml_results = [
            {"info": {"id": "lb-1", "status": {"status_code": "OK"}}, "edoc": "liveboard:\n  name: Sales\n"},
        ]
        _FakeClient.delete_should_raise = TypeError("delete_metadata() got an unexpected keyword argument")

        job_id = _create_job("bulk_delete", {"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]})
        asyncio.run(
            _execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                object_ids=["lb-1"],
                action_type="bulk_delete",
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            audits = s.exec(select(AuditLog).where(AuditLog.action_type == "bulk_delete")).all()

        assert job.status == "FAILED"
        assert job.error_type == "TypeError"
        # It never reached the bucketing, so no audit row claims a partial delete.
        assert audits == []
