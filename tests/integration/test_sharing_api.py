"""
Integration tests for the Bulk Sharing API.

Covers principals listing, preview (mocking the live fetch_permissions call),
execute happy-path (202 + job_id), and history aggregation.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
from ts_admin.models.cluster import Cluster
from ts_admin.models.share_record import ShareRecord


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
                id="c1", name="Prod", url="https://prod.thoughtspot.cloud",
                username="admin", auth_type="basic",
            )
        )
        for guid, name in [("u-alice", "alice"), ("u-bob", "bob")]:
            session.add(
                CachedUser(
                    cluster_id="c1", ts_guid=guid, username=name,
                    display_name=name.title(), email=f"{name}@co.com",
                    status="ACTIVE", synced_at=now,
                )
            )
            session.add(UserOrgMembership(cluster_id="c1", ts_guid=guid, org_id=0, synced_at=now))

        session.add(
            CachedGroup(
                cluster_id="c1", org_id=0, ts_guid="g-finance",
                name="Finance", display_name="Finance", synced_at=now,
            )
        )

        session.add(
            CachedMetadata(
                cluster_id="c1", org_id=0, ts_guid="lb-1",
                name="Sales Liveboard", object_type="LIVEBOARD",
                owner_guid="u-alice", owner_name="Alice",
                tag_names=json.dumps(["finance"]), synced_at=now,
            )
        )
        session.add(
            CachedMetadata(
                cluster_id="c1", org_id=0, ts_guid="ans-1",
                name="Revenue Answer", object_type="ANSWER",
                owner_guid="u-alice", owner_name="Alice",
                tag_names=json.dumps([]), synced_at=now,
            )
        )
        session.commit()


class TestListPrincipals:
    def test_returns_users_and_groups(self, client, seeded):
        r = client.get("/api/v1/sharing/principals?cluster_id=c1&org_id=0")
        assert r.status_code == 200, r.text
        types = {p["principal_type"] for p in r.json()["items"]}
        assert "USER" in types and "USER_GROUP" in types

    def test_search_narrows_results(self, client, seeded):
        r = client.get("/api/v1/sharing/principals?cluster_id=c1&org_id=0&search=alice")
        assert r.status_code == 200
        items = r.json()["items"]
        assert any(p["name"] == "alice" for p in items)


class TestPreview:
    def test_requires_objects(self, client, seeded):
        r = client.post(
            "/api/v1/sharing/preview",
            json={"cluster_id": "c1", "org_id": 0, "principal_guids": ["g-finance"], "mode": "READ_ONLY"},
        )
        assert r.status_code == 422

    def test_happy_path(self, client, seeded, monkeypatch):
        # Stub the live preview to avoid the real TS call
        async def _fake(**kwargs):
            return {
                "items": [
                    {
                        "object_guid": "lb-1", "object_name": "Sales Liveboard",
                        "object_type": "LIVEBOARD",
                        "principal_guid": "g-finance", "principal_name": "Finance",
                        "principal_type": "USER_GROUP",
                        "previous_mode": "", "new_mode": "READ_ONLY", "will_change": True,
                    }
                ],
                "total": 1, "will_change_count": 1,
            }

        from ts_admin.services import bulk_sharing_service as svc
        monkeypatch.setattr(svc, "preview_share", _fake)

        r = client.post(
            "/api/v1/sharing/preview",
            json={
                "cluster_id": "c1", "org_id": 0,
                "object_guids": ["lb-1"],
                "principal_guids": ["g-finance"],
                "mode": "READ_ONLY",
            },
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 1
        assert body["will_change_count"] == 1

    def test_by_tag_resolves_guids(self, client, seeded, monkeypatch):
        async def _fake(**kwargs):
            assert kwargs["object_guids"] == ["lb-1"]  # resolved from finance tag
            return {"items": [], "total": 0, "will_change_count": 0}

        from ts_admin.services import bulk_sharing_service as svc
        monkeypatch.setattr(svc, "preview_share", _fake)

        r = client.post(
            "/api/v1/sharing/preview",
            json={
                "cluster_id": "c1", "org_id": 0,
                "tag_name": "finance",
                "principal_guids": ["g-finance"],
                "mode": "READ_ONLY",
            },
        )
        assert r.status_code == 200


class TestExecute:
    def test_returns_202(self, client, seeded, monkeypatch):
        async def _noop(*args, **kwargs):
            return None

        from ts_admin.services import bulk_sharing_service as svc
        monkeypatch.setattr(svc, "execute_share", _noop)

        r = client.post(
            "/api/v1/sharing/execute",
            json={
                "cluster_id": "c1", "org_id": 0,
                "object_guids": ["lb-1", "ans-1"],
                "principal_guids": ["g-finance"],
                "mode": "READ_ONLY",
            },
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["job_id"]
        # 2 objects × 1 principal
        assert body["total"] == 2


class TestDryRun:
    def test_returns_202(self, client, seeded, monkeypatch):
        async def _noop(*args, **kwargs):
            return None

        from ts_admin.services import bulk_sharing_service as svc
        monkeypatch.setattr(svc, "dryrun_share", _noop)

        r = client.post(
            "/api/v1/sharing/dryrun",
            json={
                "cluster_id": "c1", "org_id": 0,
                "object_guids": ["lb-1", "ans-1"],
                "principal_guids": ["g-finance"],
                "mode": "NO_ACCESS",
            },
        )
        assert r.status_code == 202, r.text
        assert r.json()["job_id"]

    def test_job_records_diff_without_writing(self, in_memory_db, seeded, monkeypatch):
        # The dryrun delegates to the live preview; stub that, then assert the
        # job result is populated and no ShareRecord/AuditLog rows were written.
        import asyncio
        import uuid

        from sqlmodel import select

        from ts_admin.models.audit_log import AuditLog
        from ts_admin.models.job import Job
        from ts_admin.services import bulk_sharing_service as svc

        async def _fake_preview(**kwargs):
            return {"items": [{"object_guid": "lb-1"}], "total": 1, "will_change_count": 1}

        monkeypatch.setattr(svc, "preview_share", _fake_preview)

        job_id = str(uuid.uuid4())
        with Session(in_memory_db) as s:
            job = Job(id=job_id, cluster_id="c1", job_type="bulk_share_dryrun", status="QUEUED")
            job.set_parameters({"cluster_id": "c1", "org_id": 0})
            s.add(job)
            s.commit()

        asyncio.run(
            svc.dryrun_share(
                job_id=job_id, cluster_id="c1", org_id=0,
                object_guids=["lb-1"], principal_guids=["g-finance"], mode="NO_ACCESS",
            )
        )

        with Session(in_memory_db) as s:
            job = s.get(Job, job_id)
            assert job.status == "COMPLETE"
            assert job.get_result()["will_change_count"] == 1
            assert s.exec(select(ShareRecord)).all() == []
            assert s.exec(select(AuditLog)).all() == []


class TestHistory:
    def test_aggregates_by_job(self, client, seeded, in_memory_db):
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as s:
            for status in ("SUCCESS", "SUCCESS", "FAILED"):
                s.add(
                    ShareRecord(
                        cluster_id="c1", job_id="job-1", org_id=0,
                        object_guid="lb-1", object_name="Sales",
                        object_type="LIVEBOARD",
                        principal_guid="g-finance", principal_name="Finance",
                        principal_type="USER_GROUP",
                        new_mode="READ_ONLY", status=status,
                        executed_at=now,
                    )
                )
            s.commit()

        r = client.get("/api/v1/sharing/history?cluster_id=c1")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1
        assert items[0]["job_id"] == "job-1"
        assert items[0]["succeeded"] == 2
        assert items[0]["failed"] == 1
        assert items[0]["status"] == "PARTIAL"
