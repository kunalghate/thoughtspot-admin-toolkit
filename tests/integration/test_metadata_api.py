"""
Integration tests for GET /api/v1/metadata endpoints.

Uses FastAPI TestClient with an in-memory SQLite DB.
No ThoughtSpot API calls.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
    # StaticPool forces all sessions to reuse the same in-memory connection.
    # Without it, each Session() opens a NEW empty SQLite in-memory DB.
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
    app = create_app()
    return TestClient(app)


@pytest.fixture
def seeded(in_memory_db):
    """Seed the DB with a cluster and 3 metadata objects."""
    with Session(in_memory_db) as session:
        session.add(Cluster(id="c1", name="Prod", url="https://prod.thoughtspot.cloud", username="admin", auth_type="basic"))
        now = datetime.now(tz=timezone.utc)
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0, ts_guid="lb-1", name="Sales Dashboard",
            object_type="LIVEBOARD", owner_guid="u1", owner_name="Alice",
            tag_names=json.dumps(["Finance"]), last_accessed_at=now - timedelta(days=5), synced_at=now,
        ))
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0, ts_guid="ans-1", name="Revenue Answer",
            object_type="ANSWER", owner_guid="u2", owner_name="Bob",
            tag_names=json.dumps([]), last_accessed_at=now - timedelta(days=100), synced_at=now,
        ))
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0, ts_guid="lb-2", name="HR Overview",
            object_type="LIVEBOARD", owner_guid="u1", owner_name="Alice",
            tag_names=json.dumps(["HR"]), last_accessed_at=None, synced_at=now,
        ))
        session.commit()


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestListMetadata:

    def test_returns_all_objects(self, client, seeded):
        r = client.get("/api/v1/metadata?cluster_id=c1&org_id=0")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert len(data["items"]) == 3

    def test_filter_by_type(self, client, seeded):
        r = client.get("/api/v1/metadata?cluster_id=c1&org_id=0&types=ANSWER")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["object_type"] == "ANSWER"

    def test_filter_by_search(self, client, seeded):
        r = client.get("/api/v1/metadata?cluster_id=c1&org_id=0&search=sales")
        assert r.status_code == 200
        assert r.json()["total"] == 1
        assert r.json()["items"][0]["ts_guid"] == "lb-1"

    def test_filter_stale_90d(self, client, seeded):
        r = client.get("/api/v1/metadata?cluster_id=c1&org_id=0&stale_days=90")
        assert r.status_code == 200
        guids = {i["ts_guid"] for i in r.json()["items"]}
        assert "ans-1" in guids   # 100 days ago
        assert "lb-2" in guids    # never accessed
        assert "lb-1" not in guids  # only 5 days ago

    def test_empty_when_no_data(self, client):
        r = client.get("/api/v1/metadata?cluster_id=nobody&org_id=0")
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_requires_cluster_id(self, client):
        r = client.get("/api/v1/metadata?org_id=0")
        assert r.status_code == 422  # FastAPI validation error


class TestGetMetadata:

    def test_returns_object(self, client, seeded):
        r = client.get("/api/v1/metadata/lb-1?cluster_id=c1&org_id=0")
        assert r.status_code == 200
        assert r.json()["name"] == "Sales Dashboard"

    def test_404_if_not_found(self, client, seeded):
        r = client.get("/api/v1/metadata/does-not-exist?cluster_id=c1&org_id=0")
        assert r.status_code == 404

    def test_404_after_sync_message(self, client, seeded):
        r = client.get("/api/v1/metadata/does-not-exist?cluster_id=c1&org_id=0")
        assert "Sync first" in r.json()["detail"]


class TestMetadataStats:

    def test_returns_stats(self, client, seeded):
        r = client.get("/api/v1/metadata/stats?cluster_id=c1&org_id=0")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        assert data["by_type"]["LIVEBOARD"] == 2
        assert data["by_type"]["ANSWER"] == 1
        assert data["stale_90d"] == 2  # ans-1 (100d) + lb-2 (never)
