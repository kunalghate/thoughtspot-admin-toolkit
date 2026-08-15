"""
Unit tests for user_management_service.

Covers list_users / preview_transfer (sync, SQLite-only) and the three
async write paths (transfer, transfer_sharing, delete) with a fake TS client.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine, select

from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import (
    CachedUser,
    UserGroupMembership,
    UserOrgMembership,
)
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job
from ts_admin.models.sync_log import SyncLog
from ts_admin.models.user_action_record import UserActionRecord

# ── Fake TS client ─────────────────────────────────────────────────────────────


class _FakeClient:
    assign_calls: list[tuple[list[str], str]] = []
    delete_calls: list[list[str]] = []
    share_calls: list[tuple[list[str], list[str], str]] = []
    principal_perm_results: list[dict] = []
    delete_user_should_fail: set[str] = set()
    delete_attempts: dict[str, int] = {}
    users_pages: list[list] = []  # pages of objects with .id and .name (for search_users)

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return None

    async def assign_metadata_owner(self, *, object_ids, new_owner_identifier):
        _FakeClient.assign_calls.append((list(object_ids), new_owner_identifier))

    async def delete_users(self, *, user_identifiers):
        for ident in user_identifiers:
            _FakeClient.delete_attempts[ident] = _FakeClient.delete_attempts.get(ident, 0) + 1
            if ident in _FakeClient.delete_user_should_fail:
                raise RuntimeError(f"simulated failure deleting {ident}")
            _FakeClient.delete_calls.append(list(user_identifiers))

    async def share_objects(self, *, object_ids, principal_ids, permission):
        _FakeClient.share_calls.append((list(object_ids), list(principal_ids), str(permission)))

    async def principal_permissions(self, *, principal_identifier, metadata_types=None, permission_type="DEFINED"):
        return list(_FakeClient.principal_perm_results)

    async def search_users(self, *, org_id=None):
        for page in _FakeClient.users_pages:
            yield page


@pytest.fixture(autouse=True)
def reset_fake():
    _FakeClient.assign_calls = []
    _FakeClient.delete_calls = []
    _FakeClient.share_calls = []
    _FakeClient.principal_perm_results = []
    _FakeClient.delete_user_should_fail = set()
    _FakeClient.delete_attempts = {}
    _FakeClient.users_pages = []


# ── DB + env fixtures ─────────────────────────────────────────────────────────


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
        # Two users
        session.add(
            CachedUser(
                cluster_id="c1",
                ts_guid="u-alice",
                username="alice",
                display_name="Alice",
                email="alice@co.com",
                status="ACTIVE",
                synced_at=now,
            )
        )
        session.add(
            CachedUser(
                cluster_id="c1",
                ts_guid="u-bob",
                username="bob",
                display_name="Bob",
                email="bob@co.com",
                status="ACTIVE",
                synced_at=now,
            )
        )
        session.add(
            CachedUser(
                cluster_id="c1",
                ts_guid="u-admin",
                username="admin-user",
                display_name="Cluster Admin",
                status="ACTIVE",
                synced_at=now,
            )
        )
        # Org membership for all users
        for guid in ("u-alice", "u-bob", "u-admin"):
            session.add(UserOrgMembership(cluster_id="c1", ts_guid=guid, org_id=0, synced_at=now))
        # Admin group + membership
        session.add(
            CachedGroup(
                cluster_id="c1",
                org_id=0,
                ts_guid="g-admin",
                name="Administrator",
                display_name="Administrator",
                privileges=json.dumps(["ADMINISTRATION"]),
                synced_at=now,
            )
        )
        session.add(
            UserGroupMembership(
                cluster_id="c1",
                org_id=0,
                user_guid="u-admin",
                group_guid="g-admin",
                synced_at=now,
            )
        )
        # Two overlapping-privilege groups for alice (effective-privileges union)
        for guid, name, privs in [
            ("g-analysts", "analysts", ["DATADOWNLOADING", "USERDATAUPLOADING"]),
            ("g-viewers", "viewers", ["DATADOWNLOADING"]),
        ]:
            session.add(
                CachedGroup(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid=guid,
                    name=name,
                    display_name=name.title(),
                    privileges=json.dumps(privs),
                    synced_at=now,
                )
            )
            session.add(
                UserGroupMembership(
                    cluster_id="c1",
                    org_id=0,
                    user_guid="u-alice",
                    group_guid=guid,
                    synced_at=now,
                )
            )
        # Certify the metadata cache as completely synced. preview_transfer /
        # execute_transfer fail closed without this (S23): an interrupted
        # metadata sync leaves a non-empty but truncated cache, and the transfer
        # set IS a cache query, so it would silently under-report. The refusal
        # itself is covered in tests/unit/test_stale_cache_guard.py.
        session.add(SyncLog(cluster_id="c1", org_id=0, entity_type="metadata", status="SUCCESS", record_count=2))
        # Two metadata objects owned by alice
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="lb-1",
                name="Sales Liveboard",
                object_type="LIVEBOARD",
                owner_guid="u-alice",
                owner_name="Alice",
                tag_names=json.dumps(["finance"]),
                synced_at=now,
            )
        )
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="ans-1",
                name="Revenue Answer",
                object_type="ANSWER",
                owner_guid="u-alice",
                owner_name="Alice",
                tag_names=json.dumps([]),
                synced_at=now,
            )
        )
        session.commit()


def _create_job(job_type: str, parameters: dict) -> str:
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


class TestListUsers:
    def test_returns_all_users_for_cluster(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        items, total = svc.list_users(cluster_id="c1")
        assert total == 3
        usernames = {u["username"] for u in items}
        assert usernames == {"alice", "bob", "admin-user"}

    def test_search_filters_by_username(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        items, total = svc.list_users(cluster_id="c1", search="alice")
        assert total == 1
        assert items[0]["username"] == "alice"

    def test_org_scoped_listing(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        items, total = svc.list_users(cluster_id="c1", org_id=0)
        assert total == 3
        items_other, total_other = svc.list_users(cluster_id="c1", org_id=99)
        assert total_other == 0
        assert items_other == []


class TestGetUserDetail:
    def test_includes_owned_count_and_admin_flag(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        detail = svc.get_user_detail(cluster_id="c1", ts_guid="u-alice")
        assert detail is not None
        assert detail["owned_object_count"] == 2
        assert detail["is_admin"] is False
        assert detail["groups"] == ["analysts", "viewers"]

        admin = svc.get_user_detail(cluster_id="c1", ts_guid="u-admin")
        assert admin["is_admin"] is True
        assert "Administrator" in admin["groups"]

    def test_effective_privileges_are_deduped_union_of_groups(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        detail = svc.get_user_detail(cluster_id="c1", ts_guid="u-alice")
        # DATADOWNLOADING appears in both groups — union, not concat.
        assert detail["privileges"] == ["DATADOWNLOADING", "USERDATAUPLOADING"]
        assert [g["name"] for g in detail["group_details"]] == ["analysts", "viewers"]
        assert detail["group_details"][0]["privileges"] == ["DATADOWNLOADING", "USERDATAUPLOADING"]

    def test_no_groups_means_no_privileges(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        detail = svc.get_user_detail(cluster_id="c1", ts_guid="u-bob")
        assert detail["groups"] == []
        assert detail["group_details"] == []
        assert detail["privileges"] == []


class TestGetUserAccess:
    def test_returns_live_permissions_with_type_breakdown(self, in_memory_db, patched_env, seeded):
        from ts_admin.services import user_management_service as svc

        _FakeClient.principal_perm_results = [
            {"metadata_id": "lb-1", "metadata_name": "Sales", "metadata_type": "LIVEBOARD", "share_mode": "READ_ONLY"},
            {"metadata_id": "ans-1", "metadata_name": "Rev", "metadata_type": "ANSWER", "share_mode": "MODIFY"},
            {"metadata_id": "lb-2", "metadata_name": "Ops", "metadata_type": "LIVEBOARD", "share_mode": "READ_ONLY"},
        ]
        result = asyncio.run(svc.get_user_access(cluster_id="c1", org_id=0, ts_guid="u-alice"))
        assert result["total"] == 3
        assert result["by_type"] == {"LIVEBOARD": 2, "ANSWER": 1}
        assert result["items"][1]["share_mode"] == "MODIFY"


class TestPreviewTransfer:
    def test_returns_alice_owned_objects(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        result = svc.preview_transfer(cluster_id="c1", org_id=0, from_user_guid="u-alice")
        assert result["total"] == 2
        assert result["by_type"] == {"LIVEBOARD": 1, "ANSWER": 1}

    def test_type_filter(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        result = svc.preview_transfer(cluster_id="c1", org_id=0, from_user_guid="u-alice", object_types=["LIVEBOARD"])
        assert result["total"] == 1
        assert result["items"][0]["object_type"] == "LIVEBOARD"

    def test_tag_filter(self, in_memory_db, seeded):
        from ts_admin.services import user_management_service as svc

        result = svc.preview_transfer(cluster_id="c1", org_id=0, from_user_guid="u-alice", tag_names=["finance"])
        assert result["total"] == 1
        assert result["items"][0]["ts_guid"] == "lb-1"


class TestExecuteTransfer:
    def test_happy_path(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.user_management_service import execute_transfer

        job_id = _create_job(
            "transfer_ownership",
            {"cluster_id": "c1", "org_id": 0, "from_user_guid": "u-alice"},
        )
        asyncio.run(
            execute_transfer(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                from_user_guid="u-alice",
                to_user_identifier="bob",
                object_ids=["lb-1", "ans-1"],
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            assert job.status == "COMPLETE"
            assert job.progress == 2

            # Cached owner was rewritten to bob
            row = s.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-1")).first()
            assert row.owner_guid == "u-bob"
            assert row.owner_name == "Bob"

            # UserActionRecord written
            rec = s.exec(select(UserActionRecord)).first()
            assert rec.action_type == "transfer"
            assert rec.items_succeeded == 2
            assert rec.status == "SUCCESS"

            # Audit log
            audits = s.exec(select(AuditLog)).all()
            assert any(a.action_type == "transfer_ownership" for a in audits)

        assert len(_FakeClient.assign_calls) == 1
        ids, target = _FakeClient.assign_calls[0]
        assert ids == ["lb-1", "ans-1"]
        assert target == "bob"


class TestExecuteTransferSharing:
    def test_admin_target_rejected(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.user_management_service import execute_transfer_sharing

        job_id = _create_job("transfer_sharing", {"cluster_id": "c1", "org_id": 0})
        asyncio.run(
            execute_transfer_sharing(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                from_user_guid="u-alice",
                to_user_identifier="admin-user",
            )
        )
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
        assert job.status == "FAILED"
        assert "admin" in (job.error or "").lower()

    def test_happy_path_buckets_by_mode(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.user_management_service import execute_transfer_sharing

        _FakeClient.principal_perm_results = [
            {"metadata_id": "lb-1", "metadata_name": "Sales", "metadata_type": "LIVEBOARD", "share_mode": "READ_ONLY"},
            {"metadata_id": "lb-2", "metadata_name": "Ops", "metadata_type": "LIVEBOARD", "share_mode": "READ_ONLY"},
            {"metadata_id": "ans-1", "metadata_name": "Rev", "metadata_type": "ANSWER", "share_mode": "MODIFY"},
        ]
        job_id = _create_job("transfer_sharing", {"cluster_id": "c1", "org_id": 0})
        asyncio.run(
            execute_transfer_sharing(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                from_user_guid="u-alice",
                to_user_identifier="bob",
            )
        )
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            assert job.status == "COMPLETE"
            assert job.progress == 3

        # One call per share_mode bucket
        modes_called = {p[2] for p in _FakeClient.share_calls}
        assert "SharePermission.READ_ONLY" in modes_called or "READ_ONLY" in modes_called
        # All target the same principal
        for ids, principals, _mode in _FakeClient.share_calls:
            assert principals == ["bob"]


class TestExecuteDelete:
    def test_happy_path(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.user_management_service import execute_delete

        job_id = _create_job("delete_users", {"cluster_id": "c1", "org_id": 0})
        asyncio.run(
            execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                user_guids=["u-bob"],
                user_identifiers=["bob"],
            )
        )
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            assert job.status == "COMPLETE"
            # CachedUser row gone
            remaining = s.exec(select(CachedUser.username)).all()
            assert "bob" not in remaining

    def test_failure_retries_then_gives_up_at_10(self, in_memory_db, patched_env, seeded):
        from ts_admin.services.user_management_service import execute_delete

        _FakeClient.delete_user_should_fail = {"bob"}
        job_id = _create_job("delete_users", {"cluster_id": "c1", "org_id": 0})
        asyncio.run(
            execute_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                user_guids=["u-bob"],
                user_identifiers=["bob"],
            )
        )
        # 10 attempts before giving up
        assert _FakeClient.delete_attempts.get("bob", 0) == 10
        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            assert job.status in {"FAILED", "PARTIAL"}
            # User still in cache (we only remove on success)
            remaining = s.exec(select(CachedUser.username)).all()
            assert "bob" in remaining


class TestDryRunDelete:
    def test_flags_users_missing_upstream_and_writes_no_changes(self, in_memory_db, patched_env, seeded):
        from types import SimpleNamespace

        from ts_admin.services.user_management_service import dryrun_delete

        # Live cluster still has alice, but bob was already deleted upstream.
        _FakeClient.users_pages = [[SimpleNamespace(id="u-alice", name="alice")]]

        job_id = _create_job("user_delete_dryrun", {"cluster_id": "c1", "org_id": 0})
        asyncio.run(
            dryrun_delete(
                job_id=job_id,
                cluster_id="c1",
                org_id=0,
                user_guids=["u-alice", "u-bob"],
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            assert job.status == "COMPLETE"
            result = job.get_result()
            assert result["total"] == 2
            # bob is gone live; alice still exists
            assert result["missing_live"] == ["bob"]
            assert result["owned_total"] == 2  # alice owns lb-1 + ans-1
            # No user was deleted from the cache — dry run writes nothing
            remaining = set(s.exec(select(CachedUser.username)).all())
            assert {"alice", "bob"} <= remaining
            # No audit row written
            assert s.exec(select(AuditLog)).all() == []
