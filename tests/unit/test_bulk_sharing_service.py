"""
Unit tests for ts_admin.services.bulk_sharing_service.

The load-bearing invariant here: `preview_share` and `execute_share` operate on
the IDENTICAL resolved set. A GUID with no CachedMetadata row for
`(cluster_id, org_id)` is excluded from both and reported by both — it is never
shared under a guessed `object_type`, and never revoked (NO_ACCESS) behind a
preview that did not mention it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job
from ts_admin.models.sync_log import SyncLog

CLUSTER_ID = "c1"
ORG_ID = 0


# ── Fake TS client ─────────────────────────────────────────────────────────────


class _FakeClient:
    """Stand-in for ThoughtSpotClient; records every share_objects call."""

    share_calls: list[tuple[list[str], list[str], str]] = []  # (object_ids, principal_ids, permission)
    permission_calls: list[tuple[str, str]] = []  # (ts_guid, object_type)

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def fetch_permissions(self, *, ts_guid, object_type):
        _FakeClient.permission_calls.append((ts_guid, str(object_type)))
        return []  # no existing ACL — every pair is a change

    async def share_objects(self, *, object_ids, principal_ids, permission):
        _FakeClient.share_calls.append((list(object_ids), list(principal_ids), str(permission)))


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.share_calls = []
    _FakeClient.permission_calls = []


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
    """Patch TS client + load_config so no live call is ever attempted."""
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id=CLUSTER_ID,
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.BASIC,
    )
    config = AppConfig(clusters={CLUSTER_ID: cluster_cfg}, active_cluster_id=CLUSTER_ID)
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    monkeypatch.setattr("ts_admin.config._load_secret", lambda cluster_id, field: "fake-secret")
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", _FakeClient)


@pytest.fixture
def seeded(in_memory_db):
    """lb-1 (LIVEBOARD) + ans-1 (ANSWER) cached; `ghost-1` deliberately is not."""
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as session:
        session.add(
            Cluster(
                id=CLUSTER_ID,
                name="Prod",
                url="https://prod.thoughtspot.cloud",
                username="admin",
                auth_type="basic",
            )
        )
        # Certifies the metadata cache as complete — both entry points fail
        # closed without it.
        session.add(
            SyncLog(cluster_id=CLUSTER_ID, org_id=ORG_ID, entity_type="metadata", status="SUCCESS", record_count=2)
        )
        session.add(
            CachedGroup(
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                ts_guid="g-finance",
                name="Finance",
                display_name="Finance",
                synced_at=now,
            )
        )
        for guid, name, obj_type in [("lb-1", "Sales", "LIVEBOARD"), ("ans-1", "Revenue", "ANSWER")]:
            session.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=ORG_ID,
                    ts_guid=guid,
                    name=name,
                    object_type=obj_type,
                    owner_guid="u-alice",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    synced_at=now,
                )
            )
        session.commit()


def _create_job(job_type: str) -> str:
    import uuid

    from ts_admin.database import get_session

    job_id = str(uuid.uuid4())
    job = Job(id=job_id, cluster_id=CLUSTER_ID, job_type=job_type, status="QUEUED")
    with get_session() as s:
        s.add(job)
        s.commit()
    return job_id


def _preview(object_guids: list[str], mode: str = "READ_ONLY") -> dict:
    from ts_admin.services.bulk_sharing_service import preview_share

    return asyncio.run(
        preview_share(
            cluster_id=CLUSTER_ID,
            org_id=ORG_ID,
            object_guids=object_guids,
            principal_guids=["g-finance"],
            mode=mode,
        )
    )


def _execute(job_id: str, object_guids: list[str], mode: str = "READ_ONLY") -> None:
    from ts_admin.services.bulk_sharing_service import execute_share

    asyncio.run(
        execute_share(
            job_id,
            CLUSTER_ID,
            ORG_ID,
            object_guids,
            ["g-finance"],
            mode,
        )
    )


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestPreviewExecuteAgree:
    def test_uncached_guid_is_excluded_from_both_and_reported_in_both(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        requested = ["lb-1", "ghost-1"]

        preview = _preview(requested)
        assert {r["object_guid"] for r in preview["items"]} == {"lb-1"}
        assert [s["object_guid"] for s in preview["skipped"]] == ["ghost-1"]
        assert preview["skipped_count"] == 1

        job_id = _create_job("bulk_share")
        _execute(job_id, requested)

        # The exact set the preview described is the exact set that was shared.
        shared_guids = {g for ids, _p, _m in _FakeClient.share_calls for g in ids}
        assert shared_guids == {r["object_guid"] for r in preview["items"]}
        assert "ghost-1" not in shared_guids

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        result = job.get_result() or {}
        assert job.status == "PARTIAL"  # something requested did not happen
        assert [x["object_guid"] for x in result["skipped"]] == ["ghost-1"]
        assert result["skipped_count"] == 1
        assert result["succeeded_pairs"] == 1
        assert result["requested_pairs"] == 2

    def test_no_access_revoke_never_exceeds_the_preview(self, in_memory_db, patched_env, seeded):
        # The dangerous direction: a divergence here REVOKES access the admin
        # was never shown.
        requested = ["lb-1", "ghost-1"]
        preview = _preview(requested, mode="NO_ACCESS")

        job_id = _create_job("bulk_share")
        _execute(job_id, requested, mode="NO_ACCESS")

        shared_guids = {g for ids, _p, _m in _FakeClient.share_calls for g in ids}
        assert shared_guids == {r["object_guid"] for r in preview["items"]} == {"lb-1"}

    def test_object_type_is_never_guessed(self, in_memory_db, patched_env, seeded):
        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1", "ghost-1"])

        # One call per cached type, and the ANSWER never rides in a LIVEBOARD call.
        by_ids = {tuple(sorted(ids)) for ids, _p, _m in _FakeClient.share_calls}
        assert by_ids == {("lb-1",), ("ans-1",)}

    def test_all_guids_uncached_fails_the_job_without_sharing_anything(
        self,
        in_memory_db,
        patched_env,
        seeded,
    ):
        job_id = _create_job("bulk_share")
        _execute(job_id, ["ghost-1", "ghost-2"])

        assert _FakeClient.share_calls == []
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        assert job.status == "FAILED"
        assert "0 of 2 objects resolved" in (job.error or "")

    def test_fully_cached_request_still_completes(self, in_memory_db, patched_env, seeded):
        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "ans-1"])

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        result = job.get_result() or {}
        assert job.status == "COMPLETE"
        assert result["skipped"] == []
        assert result["succeeded_pairs"] == result["total_pairs"] == result["requested_pairs"] == 2


class TestResolutionScoping:
    def test_resolution_is_scoped_to_cluster_and_org(self, in_memory_db, patched_env, seeded):
        # Same GUID cached under a different org — it must NOT resolve for org 0.
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            s.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=5,
                    ts_guid="other-org-1",
                    name="Elsewhere",
                    object_type="LIVEBOARD",
                    owner_guid="u-alice",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    synced_at=now,
                )
            )
            s.commit()

        preview = _preview(["lb-1", "other-org-1"])
        assert [x["object_guid"] for x in preview["skipped"]] == ["other-org-1"]

        job_id = _create_job("bulk_share")
        _execute(job_id, ["lb-1", "other-org-1"])
        shared_guids = {g for ids, _p, _m in _FakeClient.share_calls for g in ids}
        assert shared_guids == {"lb-1"}


class TestDryRunReportsSkipped:
    def test_dryrun_job_result_carries_the_skipped_list(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.bulk_sharing_service import dryrun_share

        job_id = _create_job("bulk_share_dryrun")
        asyncio.run(
            dryrun_share(
                job_id=job_id,
                cluster_id=CLUSTER_ID,
                org_id=ORG_ID,
                object_guids=["lb-1", "ghost-1"],
                principal_guids=["g-finance"],
                mode="NO_ACCESS",
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        result = job.get_result() or {}
        assert job.status == "COMPLETE"
        assert result["skipped_count"] == 1
        assert [x["object_guid"] for x in result["skipped"]] == ["ghost-1"]
