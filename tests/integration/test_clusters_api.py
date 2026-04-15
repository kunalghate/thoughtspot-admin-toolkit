"""
Integration tests for cluster-related endpoints.

Tests:
  - GET /clusters/{id}/orgs/cached  — offline fallback reads from ts_orgs
  - GET /clusters/{id}/orgs         — live fetch writes to ts_orgs cache
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_org import CachedOrg
from ts_admin.models.cluster import Cluster

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
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
def app_client(in_memory_db):
    """TestClient with no config patching — for endpoints that only touch SQLite."""
    from ts_admin.main import create_app

    return TestClient(create_app())


@pytest.fixture
def live_client(in_memory_db, monkeypatch):
    """TestClient with load_config + keychain patched — for endpoints that call TS live."""
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
    # load_config is lazily imported inside each route handler, so patch at source
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    # Avoid keychain access
    monkeypatch.setattr("ts_admin.config._load_secret", lambda cluster_id, field: "fake-secret")

    from ts_admin.main import create_app

    return TestClient(create_app())


@pytest.fixture
def cluster_row(in_memory_db):
    """Insert the cluster FK row so ts_orgs inserts don't violate FK constraints."""
    with Session(in_memory_db) as session:
        session.add(
            Cluster(id="c1", name="Prod", url="https://prod.thoughtspot.cloud", username="admin", auth_type="basic")
        )
        session.commit()


# ── GET /clusters/{id}/orgs/cached ────────────────────────────────────────────


class TestListClusterOrgsCached:
    def test_returns_empty_when_no_cache(self, app_client, cluster_row):
        r = app_client.get("/api/v1/clusters/c1/orgs/cached")
        assert r.status_code == 200
        assert r.json() == []

    def test_returns_cached_orgs(self, app_client, in_memory_db, cluster_row):
        with Session(in_memory_db) as session:
            session.add(CachedOrg(cluster_id="c1", ts_org_id=0, name="Primary", status="ACTIVE", is_primary=True))
            session.add(CachedOrg(cluster_id="c1", ts_org_id=42, name="Staging", status="ACTIVE", is_primary=False))
            session.add(CachedOrg(cluster_id="c1", ts_org_id=999, name="Dev", status="ACTIVE", is_primary=False))
            session.commit()

        r = app_client.get("/api/v1/clusters/c1/orgs/cached")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 3
        assert {o["org_id"] for o in data} == {0, 42, 999}

    def test_isolates_by_cluster(self, app_client, in_memory_db, cluster_row):
        """Cached orgs for a different cluster must not appear."""
        with Session(in_memory_db) as session:
            session.add(
                Cluster(
                    id="c2", name="Other", url="https://other.thoughtspot.cloud", username="admin", auth_type="basic"
                )
            )
            session.add(CachedOrg(cluster_id="c1", ts_org_id=1, name="C1 Org", status="ACTIVE", is_primary=False))
            session.add(CachedOrg(cluster_id="c2", ts_org_id=1, name="C2 Org", status="ACTIVE", is_primary=False))
            session.commit()

        r = app_client.get("/api/v1/clusters/c1/orgs/cached")
        assert r.status_code == 200
        assert len(r.json()) == 1
        assert r.json()[0]["name"] == "C1 Org"

    def test_returns_empty_for_unknown_cluster(self, app_client):
        """No 404 — just an empty list for a cluster with no cached rows."""
        r = app_client.get("/api/v1/clusters/does-not-exist/orgs/cached")
        assert r.status_code == 200
        assert r.json() == []


# ── GET /clusters/{id}/orgs — cache write side effect ─────────────────────────


class TestListClusterOrgsLiveCacheWrite:
    @respx.mock
    def test_writes_orgs_to_cache_on_live_fetch(self, live_client, in_memory_db, cluster_row):
        """
        When the live fetch succeeds, ts_orgs must be populated so that
        GET /orgs/cached can serve the offline fallback.
        """
        respx.post("https://prod.thoughtspot.cloud/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json={"token": "tok"})
        )
        respx.post("https://prod.thoughtspot.cloud/api/rest/2.0/orgs/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 0, "name": "Primary", "description": "", "status": "ACTIVE", "is_primary": True},
                    {"id": 77, "name": "TeamA", "description": "", "status": "ACTIVE", "is_primary": False},
                ],
            )
        )

        r = live_client.get("/api/v1/clusters/c1/orgs")
        assert r.status_code == 200
        assert len(r.json()) == 2

        with Session(in_memory_db) as session:
            from sqlmodel import select

            rows = session.exec(select(CachedOrg).where(CachedOrg.cluster_id == "c1")).all()
        assert len(rows) == 2
        assert {row.ts_org_id for row in rows} == {0, 77}

    @respx.mock
    def test_cache_write_replaces_stale_orgs(self, live_client, in_memory_db, cluster_row):
        """
        Org deleted in TS must not linger in the cache after a live fetch.
        """
        with Session(in_memory_db) as session:
            session.add(CachedOrg(cluster_id="c1", ts_org_id=999, name="OldOrg", status="ACTIVE", is_primary=False))
            session.commit()

        respx.post("https://prod.thoughtspot.cloud/api/rest/2.0/auth/token/full").mock(
            return_value=httpx.Response(200, json={"token": "tok"})
        )
        respx.post("https://prod.thoughtspot.cloud/api/rest/2.0/orgs/search").mock(
            return_value=httpx.Response(
                200,
                json=[
                    {"id": 0, "name": "Primary", "description": "", "status": "ACTIVE", "is_primary": True},
                ],
            )
        )

        live_client.get("/api/v1/clusters/c1/orgs")

        with Session(in_memory_db) as session:
            from sqlmodel import select

            rows = session.exec(select(CachedOrg).where(CachedOrg.cluster_id == "c1")).all()
        assert len(rows) == 1
        assert rows[0].ts_org_id == 0  # stale 999 is gone
