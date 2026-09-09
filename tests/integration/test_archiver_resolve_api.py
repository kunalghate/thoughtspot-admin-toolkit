"""
Integration tests for POST /api/v1/archiver/resolve.

This backs "select all N matching" on the archiver grid. AG Grid's infinite row
model has no working header checkbox, so the server expands the current filters
into the full GUID list; that list then feeds the UNCHANGED dryrun/execute
endpoints, which is what keeps the dry-run guard and the audit trail working on
an explicit set of objects.

The properties that matter:
  - resolve returns exactly what the grid would page through (no more, no less)
  - exclusions are honoured ("select all, untick three")
  - it is bounded, and refuses rather than truncating
  - it never reaches across clusters
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster


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


def _stale(session, *, cluster_id: str, guid: str, name: str, otype: str = "LIVEBOARD", owner: str = "Alice"):
    old = datetime.now(tz=timezone.utc) - timedelta(days=400)
    session.add(
        CachedMetadata(
            cluster_id=cluster_id,
            org_id=0,
            ts_guid=guid,
            name=name,
            object_type=otype,
            owner_guid=f"u-{owner.lower()}",
            owner_name=owner,
            tag_names=json.dumps([]),
            last_accessed_at=old,
            modified_at=old,
            created_at=old,
            synced_at=datetime.now(tz=timezone.utc),
        )
    )


@pytest.fixture
def seeded(in_memory_db):
    with Session(in_memory_db) as session:
        for cid, name in [("c1", "Prod"), ("c2", "Dev")]:
            session.add(
                Cluster(
                    id=cid,
                    name=name,
                    url=f"https://{cid}.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )
        for i in range(5):
            _stale(session, cluster_id="c1", guid=f"lb-{i}", name=f"Board {i}")
        _stale(session, cluster_id="c1", guid="ans-1", name="An Answer", otype="ANSWER", owner="Bob")
        _stale(session, cluster_id="c2", guid="other-1", name="Other Cluster Board")
        session.commit()


def _resolve(client, **body):
    return client.post("/api/v1/archiver/resolve", json={"cluster_id": "c1", "org_id": 0, **body})


class TestResolve:
    def test_returns_every_matching_guid(self, client, seeded):
        r = _resolve(client)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 6
        assert sorted(body["guids"]) == ["ans-1", "lb-0", "lb-1", "lb-2", "lb-3", "lb-4"]

    def test_agrees_with_what_the_grid_pages_through(self, client, seeded):
        """The whole point: resolve must not mean something different from the grid."""
        grid = client.get("/api/v1/archiver/results?cluster_id=c1&org_id=0&page_size=1000")
        grid_guids = sorted(i["ts_guid"] for i in grid.json()["items"])

        resolved = sorted(_resolve(client).json()["guids"])
        assert resolved == grid_guids

    def test_honours_a_type_filter(self, client, seeded):
        r = _resolve(client, types=["ANSWER"])
        assert r.json()["guids"] == ["ans-1"]

    def test_honours_an_owner_name_filter(self, client, seeded):
        r = _resolve(client, owner_name_search="Bob")
        assert r.json()["guids"] == ["ans-1"]

    def test_honours_exclusions(self, client, seeded):
        """ "Select all, then untick three."""
        r = _resolve(client, excluded_guids=["lb-0", "lb-1", "ans-1"])
        body = r.json()
        assert sorted(body["guids"]) == ["lb-2", "lb-3", "lb-4"]
        assert body["total"] == 3, "the count must reflect exclusions, not the pre-exclusion total"

    def test_empty_result_is_not_an_error(self, client, seeded):
        r = _resolve(client, search="nothing-matches-this")
        assert r.status_code == 200
        assert r.json() == {"guids": [], "total": 0}

    def test_refuses_rather_than_truncating_above_the_cap(self, client, seeded, monkeypatch):
        """A truncated list would be silently acted on."""
        import ts_admin.services.archiver_service as svc

        monkeypatch.setattr(svc, "RESOLVE_MAX", 2)
        r = _resolve(client)
        assert r.status_code == 413
        assert "narrow the criteria" in r.json()["detail"].lower()

    def test_never_reaches_into_another_cluster(self, client, seeded):
        guids = _resolve(client).json()["guids"]
        assert "other-1" not in guids

        # ...and the other cluster sees only its own.
        r = client.post("/api/v1/archiver/resolve", json={"cluster_id": "c2", "org_id": 0})
        assert r.json()["guids"] == ["other-1"]
