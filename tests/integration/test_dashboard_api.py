"""
Integration tests for the Dashboard API.

The dashboard is one aggregate read over the SQLite cache: entity counts,
recent jobs, and a merged audit-activity feed. These tests seed two clusters
to prove counts are scoped, and seed each audit source to prove the feed
groups bulk rows into per-session entries.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_tag import CachedTag
from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
from ts_admin.models.cluster import Cluster
from ts_admin.models.job import Job
from ts_admin.models.share_record import ShareRecord
from ts_admin.models.user_action_record import UserActionRecord


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

        # Two users in c1/org0, one in c2 — counts must not bleed across.
        for cid, guid in [("c1", "u1"), ("c1", "u2"), ("c2", "u9")]:
            session.add(
                CachedUser(
                    cluster_id=cid,
                    ts_guid=guid,
                    username=f"user-{guid}",
                    display_name=guid,
                    email=f"{guid}@x.io",
                    status="ACTIVE",
                    synced_at=now,
                )
            )
            session.add(UserOrgMembership(cluster_id=cid, ts_guid=guid, org_id=0, synced_at=now))

        session.add(
            CachedGroup(
                cluster_id="c1",
                org_id=0,
                ts_guid="g1",
                name="admins",
                display_name="Admins",
                description="",
                privileges="[]",
                synced_at=now,
            )
        )
        session.add(CachedTag(cluster_id="c1", org_id=0, ts_guid="t1", name="Stale"))
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="m1",
                name="LB",
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="user-u1",
                tag_names=json.dumps([]),
                last_accessed_at=now,
                modified_at=now,
                synced_at=now,
            )
        )

        # Jobs: one recent failure, one old failure (outside the 7d window).
        session.add(Job(id="j-new", cluster_id="c1", job_type="sync:users", status="FAILED", error="boom"))
        old = Job(id="j-old", cluster_id="c1", job_type="sync:users", status="FAILED")
        old.created_at = now - timedelta(days=30)
        session.add(old)

        # Audit rows: a 2-object deletion session, a share session, a transfer.
        for guid in ("m1", "m2"):
            session.add(
                ArchiveRecord(
                    cluster_id="c1",
                    job_id="del-1",
                    ts_guid=guid,
                    name=guid,
                    object_type="LIVEBOARD",
                    owner_guid="u1",
                    owner_name="user-u1",
                    org_id=0,
                    tml_export_status="SUCCESS",
                )
            )
        session.add(
            ShareRecord(
                cluster_id="c1",
                job_id="share-1",
                org_id=0,
                object_guid="m1",
                object_name="LB",
                object_type="LIVEBOARD",
                principal_guid="g1",
                principal_name="Admins",
                principal_type="USER_GROUP",
                new_mode="READ_ONLY",
            )
        )
        session.add(
            UserActionRecord(
                cluster_id="c1",
                job_id="xfer-1",
                org_id=0,
                action_type="transfer",
                from_username="alice",
                to_username="bob",
                status="SUCCESS",
            )
        )
        session.commit()


class TestDashboard:
    def test_counts_are_cluster_scoped(self, client, seeded):
        r = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0")
        assert r.status_code == 200, r.text
        counts = r.json()["counts"]
        assert counts["users"] == 2
        assert counts["groups"] == 1
        assert counts["tags"] == 1
        assert counts["objects_total"] == 1
        assert counts["objects_by_type"] == {"LIVEBOARD": 1}

        r2 = client.get("/api/v1/dashboard?cluster_id=c2&org_id=0")
        assert r2.json()["counts"]["users"] == 1
        assert r2.json()["counts"]["groups"] == 0

    def test_failed_jobs_window_and_recent_jobs(self, client, seeded):
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        # Only the recent failure counts toward the 7-day window.
        assert body["failed_jobs_7d"] == 1
        ids = [j["id"] for j in body["recent_jobs"]]
        assert "j-new" in ids

    def test_activity_groups_bulk_rows_per_session(self, client, seeded):
        body = client.get("/api/v1/dashboard?cluster_id=c1&org_id=0").json()
        labels = [a["label"] for a in body["recent_activity"]]
        # 2 ArchiveRecord rows → ONE grouped deletion entry.
        assert "Deleted 2 objects (TML backed up)" in labels
        assert "Updated sharing on 1 object for 1 principal" in labels
        assert "Transferred ownership: alice → bob" in labels
        assert len(body["recent_activity"]) == 3

    def test_empty_cluster_returns_zeroes(self, client, seeded):
        body = client.get("/api/v1/dashboard?cluster_id=c2&org_id=99").json()
        assert body["counts"]["objects_total"] == 0
        assert body["recent_activity"] == []
