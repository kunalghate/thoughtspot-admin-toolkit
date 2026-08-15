"""
Integration tests for the User Management API.

Covers the list grid, the three preview endpoints, and execute endpoints
(which kick background jobs we don't run end-to-end here — only verify the
202 + job_id contract).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import (
    CachedUser,
    UserGroupMembership,
    UserOrgMembership,
)
from ts_admin.models.cluster import Cluster
from ts_admin.models.sync_log import SyncLog


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
        for guid, name in [("u-alice", "alice"), ("u-bob", "bob")]:
            session.add(
                CachedUser(
                    cluster_id="c1",
                    ts_guid=guid,
                    username=name,
                    display_name=name.title(),
                    email=f"{name}@co.com",
                    status="ACTIVE",
                    synced_at=now,
                )
            )
            session.add(UserOrgMembership(cluster_id="c1", ts_guid=guid, org_id=0, synced_at=now))

        # Certify the metadata cache as fully synced — /transfer/preview fails
        # closed (409 StaleCacheError) without it, because the objects it lists
        # come straight out of this cache and a truncated cache under-reports.
        session.add(SyncLog(cluster_id="c1", org_id=0, entity_type="metadata", status="SUCCESS", record_count=1))
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="lb-1",
                name="Sales",
                object_type="LIVEBOARD",
                owner_guid="u-alice",
                owner_name="Alice",
                tag_names=json.dumps([]),
                synced_at=now,
            )
        )
        session.commit()


# ── List + detail ──────────────────────────────────────────────────────────────


class TestListUsers:
    def test_returns_seeded_users(self, client, seeded):
        r = client.get("/api/v1/users?cluster_id=c1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        usernames = {u["username"] for u in body["items"]}
        assert usernames == {"alice", "bob"}

    def test_search_narrows_results(self, client, seeded):
        r = client.get("/api/v1/users?cluster_id=c1&search=alice")
        assert r.status_code == 200
        assert r.json()["total"] == 1


class TestUserDetail:
    def test_returns_owned_count(self, client, seeded):
        r = client.get("/api/v1/users/u-alice?cluster_id=c1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["owned_object_count"] == 1
        # Audit enrichment fields are always present (empty without group sync).
        assert body["privileges"] == []
        assert body["group_details"] == []

    def test_404_for_unknown_guid(self, client, seeded):
        r = client.get("/api/v1/users/u-ghost?cluster_id=c1")
        assert r.status_code == 404


class TestUserAccess:
    def test_returns_live_access_shape(self, client, seeded, monkeypatch):
        from ts_admin.services import user_management_service as svc

        async def _fake_access(*, cluster_id, org_id, ts_guid):
            assert (cluster_id, org_id, ts_guid) == ("c1", 0, "u-alice")
            return {
                "items": [
                    {
                        "metadata_id": "lb-1",
                        "metadata_name": "Sales",
                        "metadata_type": "LIVEBOARD",
                        "share_mode": "READ_ONLY",
                    }
                ],
                "total": 1,
                "by_type": {"LIVEBOARD": 1},
            }

        monkeypatch.setattr(svc, "get_user_access", _fake_access)
        r = client.get("/api/v1/users/u-alice/access?cluster_id=c1&org_id=0")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["by_type"] == {"LIVEBOARD": 1}
        assert body["items"][0]["share_mode"] == "READ_ONLY"


# ── Transfer ownership ─────────────────────────────────────────────────────────


class TestTransferPreview:
    def test_returns_alice_owned(self, client, seeded):
        r = client.post(
            "/api/v1/users/transfer/preview",
            json={"cluster_id": "c1", "org_id": 0, "from_user_guid": "u-alice"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 1

    def test_409_when_the_metadata_cache_is_not_authoritative(self, client, seeded, in_memory_db):
        """An interrupted metadata sync leaves a non-empty but truncated cache.
        The preview would then quietly list a subset of what alice owns, and the
        admin would transfer that subset believing it was everything — so the
        endpoint refuses instead, with an actionable 409."""
        from sqlmodel import select

        with Session(in_memory_db) as s:
            row = s.exec(select(SyncLog).where(SyncLog.entity_type == "metadata")).one()
            row.status = "IN_PROGRESS"
            s.add(row)
            s.commit()

        r = client.post(
            "/api/v1/users/transfer/preview",
            json={"cluster_id": "c1", "org_id": 0, "from_user_guid": "u-alice"},
        )
        assert r.status_code == 409, r.text
        body = r.json()
        assert body["error_type"] == "StaleCacheError"
        assert "Sync" in body["hint"]

        # Anti-vacuity: the cached row is still there — this is a refusal on a
        # populated cache, not an artefact of an empty one.
        with Session(in_memory_db) as s:
            assert s.exec(select(CachedMetadata)).all()


class TestTransferExecute:
    def test_returns_202_with_job_id(self, client, seeded, monkeypatch):
        # Replace the BG task entry so we don't reach out to ThoughtSpot
        async def _noop(*args, **kwargs):
            return None

        from ts_admin.services import user_management_service as svc

        monkeypatch.setattr(svc, "execute_transfer", _noop)

        r = client.post(
            "/api/v1/users/transfer/execute",
            json={
                "cluster_id": "c1",
                "org_id": 0,
                "from_user_guid": "u-alice",
                "to_user_identifier": "bob",
                "object_ids": ["lb-1"],
            },
        )
        assert r.status_code == 202, r.text
        assert r.json()["job_id"]
        assert r.json()["total"] == 1


class TestDeletePreview:
    def test_marks_unrecognized(self, client, seeded):
        r = client.post(
            "/api/v1/users/delete/preview",
            json={"cluster_id": "c1", "user_guids": ["u-alice", "u-ghost"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["unrecognized"] == ["u-ghost"]

    def test_admin_flag_present(self, client, seeded, in_memory_db):
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            s.add(
                CachedGroup(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid="g-admin",
                    name="Administrator",
                    display_name="Administrator",
                    synced_at=now,
                )
            )
            s.add(
                UserGroupMembership(
                    cluster_id="c1",
                    org_id=0,
                    user_guid="u-alice",
                    group_guid="g-admin",
                    synced_at=now,
                )
            )
            s.commit()
        r = client.post(
            "/api/v1/users/delete/preview",
            json={"cluster_id": "c1", "user_guids": ["u-alice"]},
        )
        assert r.status_code == 200
        assert r.json()["items"][0]["is_admin"] is True


class TestDeleteExecute:
    def test_returns_202(self, client, seeded, monkeypatch):
        async def _noop(*args, **kwargs):
            return None

        from ts_admin.services import user_management_service as svc

        monkeypatch.setattr(svc, "execute_delete", _noop)

        r = client.post(
            "/api/v1/users/delete/execute",
            json={"cluster_id": "c1", "org_id": 0, "user_guids": ["u-bob"], "user_identifiers": ["bob"]},
        )
        assert r.status_code == 202, r.text
        assert r.json()["total"] == 1
