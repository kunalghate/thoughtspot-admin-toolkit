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
from sqlmodel import Session, create_engine

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
        session.add(
            Cluster(id="c1", name="Prod", url="https://prod.thoughtspot.cloud", username="admin", auth_type="basic")
        )
        now = datetime.now(tz=timezone.utc)
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="lb-1",
                name="Sales Dashboard",
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="Alice",
                tag_names=json.dumps(["Finance"]),
                last_accessed_at=now - timedelta(days=5),
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
                owner_guid="u2",
                owner_name="Bob",
                tag_names=json.dumps([]),
                last_accessed_at=now - timedelta(days=100),
                synced_at=now,
            )
        )
        session.add(
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="lb-2",
                name="HR Overview",
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="Alice",
                tag_names=json.dumps(["HR"]),
                last_accessed_at=None,
                synced_at=now,
            )
        )
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
        assert "ans-1" in guids  # 100 days ago
        assert "lb-2" in guids  # never accessed
        assert "lb-1" not in guids  # only 5 days ago

    def test_empty_when_no_data(self, client):
        r = client.get("/api/v1/metadata?cluster_id=nobody&org_id=0")
        assert r.status_code == 200
        assert r.json()["total"] == 0


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
        # "Aged out" and "no evidence" are separate counts, not one blended
        # number: ans-1 has a real access date 100d old, lb-2 has none at all.
        assert data["stale_90d"] == 1
        assert data["never_accessed"] == 1
        assert data["archivable_total"] == 3


class TestGetPermissions:
    """GET /{ts_guid}/permissions — live call, so the TS client is faked."""

    @pytest.fixture
    def fake_ts(self, in_memory_db, monkeypatch):
        """Patch load_config + ThoughtSpotClient; record the org_id the auth was built for."""
        from types import SimpleNamespace

        calls = SimpleNamespace(auth_org_ids=[], fetch_kwargs=[])

        class FakeCluster:
            id = "c1"
            url = "https://prod.thoughtspot.cloud"

            def build_auth_strategy(self, org_id=None):
                calls.auth_org_ids.append(org_id)
                return object()

        class FakeClient:
            def __init__(self, *, url, auth):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *exc):
                return None

            async def fetch_permissions(self, **kwargs):
                calls.fetch_kwargs.append(kwargs)
                return [
                    SimpleNamespace(
                        principal_id="u1",
                        principal_name="Alice",
                        principal_type="USER",
                        share_mode="MODIFY",
                    )
                ]

        import ts_admin.config as config_module
        import ts_admin.ts_client as ts_client_module

        monkeypatch.setattr(config_module, "load_config", lambda: SimpleNamespace(active_cluster=FakeCluster()))
        monkeypatch.setattr(ts_client_module, "ThoughtSpotClient", FakeClient)
        return calls

    @pytest.fixture
    def seeded_org_table(self, in_memory_db):
        """A table living in a non-default org."""
        with Session(in_memory_db) as session:
            session.add(
                CachedMetadata(
                    cluster_id="c1",
                    org_id=42,
                    ts_guid="tbl-42",
                    name="DIM_PRODUCTS",
                    object_type="ONE_TO_ONE_LOGICAL",
                    owner_guid="u1",
                    owner_name="Alice",
                    tag_names=json.dumps([]),
                    last_accessed_at=None,
                    synced_at=datetime.now(tz=timezone.utc),
                )
            )
            session.commit()

    def test_returns_permissions(self, client, seeded, fake_ts):
        r = client.get("/api/v1/metadata/lb-1/permissions?cluster_id=c1&org_id=0")
        assert r.status_code == 200
        data = r.json()
        assert data["object_name"] == "Sales Dashboard"
        assert data["permissions"] == [
            {
                "principal_id": "u1",
                "principal_name": "Alice",
                "principal_type": "USER",
                "share_mode": "MODIFY",
            }
        ]

    def test_auth_scoped_to_requested_org(self, client, seeded_org_table, fake_ts):
        # Regression: objects in a non-default org 400 on fetch-permissions
        # unless the auth token is built for that org's context.
        r = client.get("/api/v1/metadata/tbl-42/permissions?cluster_id=c1&org_id=42")
        assert r.status_code == 200
        assert fake_ts.auth_org_ids == [42]

    def test_404_if_not_in_cache(self, client, seeded, fake_ts):
        r = client.get("/api/v1/metadata/nope/permissions?cluster_id=c1&org_id=0")
        assert r.status_code == 404
