"""
Integration tests for /api/v1/deleter/* endpoints.

Covers the synchronous resolve modes (tag, list, root search, available tags)
and shape-checks the async dryrun/execute endpoints (Job is created with the
expected job_type; we don't assert on background-task completion here —
that's the deletion_service unit-test territory).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

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

    app = create_app()
    return TestClient(app)


@pytest.fixture
def seeded(in_memory_db):
    """One cluster + four metadata rows covering all the deleter modes."""
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
        rows = [
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="ws-1",
                name="Sales Worksheet",
                object_type="WORKSHEET",
                owner_guid="u1",
                owner_name="Alice",
                tag_names=json.dumps([]),
                synced_at=now,
            ),
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="lb-1",
                name="Sales Dashboard",
                object_type="LIVEBOARD",
                owner_guid="u1",
                owner_name="Alice",
                tag_names=json.dumps(["TODO_DELETE", "Finance"]),
                synced_at=now,
            ),
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="ans-1",
                name="Revenue Answer",
                object_type="ANSWER",
                owner_guid="u2",
                owner_name="Bob",
                tag_names=json.dumps(["TODO_DELETE"]),
                synced_at=now,
            ),
            CachedMetadata(
                cluster_id="c1",
                org_id=0,
                ts_guid="sys-1",
                name="System Built-in",
                object_type="LIVEBOARD",
                owner_guid="sys",
                owner_name="System User",  # must be filtered out
                tag_names=json.dumps(["TODO_DELETE"]),
                synced_at=now,
            ),
        ]
        for r in rows:
            session.add(r)
        session.commit()


# ── Resolve: From Tag ──────────────────────────────────────────────────────────


class TestResolveTag:
    def test_returns_only_matching_tag(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/tag",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "TODO_DELETE"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        guids = {i["ts_guid"] for i in body["items"]}
        # lb-1 and ans-1 carry the tag; sys-1 is filtered (System User);
        # ws-1 has no tags.
        assert guids == {"lb-1", "ans-1"}
        assert body["total"] == 2
        assert body["by_type"] == {"LIVEBOARD": 1, "ANSWER": 1}
        assert body["tag_name"] == "TODO_DELETE"

    def test_excludes_system_user(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/tag",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "TODO_DELETE"},
        )
        guids = {i["ts_guid"] for i in r.json()["items"]}
        assert "sys-1" not in guids

    def test_unknown_tag_returns_empty(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/tag",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "NOPE"},
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0

    def test_missing_tag_name_is_422(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/tag",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": ""},
        )
        assert r.status_code == 422


# ── Resolve: From List ─────────────────────────────────────────────────────────


class TestResolveList:
    def test_resolves_known_guids(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/list",
            json={"cluster_id": "c1", "org_id": 0, "guids": ["lb-1", "ans-1"]},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        assert body["unrecognized"] == []

    def test_reports_unrecognized(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/list",
            json={"cluster_id": "c1", "org_id": 0, "guids": ["lb-1", "bogus-xyz"]},
        )
        body = r.json()
        assert body["total"] == 1
        assert body["unrecognized"] == ["bogus-xyz"]

    def test_dedupes_input(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/list",
            json={"cluster_id": "c1", "org_id": 0, "guids": ["lb-1", "lb-1", "lb-1"]},
        )
        assert r.json()["total"] == 1

    def test_empty_list_is_422(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/list",
            json={"cluster_id": "c1", "org_id": 0, "guids": []},
        )
        assert r.status_code == 422

    def test_excludes_system_user(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/resolve/list",
            json={"cluster_id": "c1", "org_id": 0, "guids": ["sys-1"]},
        )
        body = r.json()
        # sys-1 *is* recognized (it's in the cache) but filtered for System User.
        assert body["total"] == 0
        assert body["unrecognized"] == []  # not unrecognized — just filtered


# ── UI helpers ────────────────────────────────────────────────────────────────


class TestTagsList:
    def test_returns_distinct_tags_sorted(self, client, seeded):
        r = client.get("/api/v1/deleter/tags?cluster_id=c1&org_id=0")
        assert r.status_code == 200
        # System-user-tagged tags ("TODO_DELETE" on sys-1) excluded by owner filter,
        # but lb-1 and ans-1 still carry it.
        assert r.json() == ["Finance", "TODO_DELETE"]


class TestRootSearch:
    def test_finds_worksheets_by_substring(self, client, seeded):
        r = client.get("/api/v1/deleter/roots/search?cluster_id=c1&org_id=0&query=sales")
        assert r.status_code == 200
        items = r.json()
        # Only ws-1 matches the default Worksheet/Table/Model type filter.
        assert len(items) == 1
        assert items[0]["ts_guid"] == "ws-1"

    def test_excludes_non_root_types(self, client, seeded):
        r = client.get("/api/v1/deleter/roots/search?cluster_id=c1&org_id=0&query=dashboard")
        # "Sales Dashboard" is a LIVEBOARD — not a valid root type.
        assert r.json() == []


# ── Async endpoints (shape-only) ──────────────────────────────────────────────


class TestDryRunEndpoint:
    def test_creates_job_with_bulk_delete_dryrun_type(self, client, seeded, monkeypatch):
        # Block the background task so we don't try to call ThoughtSpot.
        from ts_admin.services import deletion_service

        async def _noop(**kwargs):
            return None

        monkeypatch.setattr(deletion_service, "dryrun", _noop)

        r = client.post(
            "/api/v1/deleter/dryrun",
            json={"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1", "ans-1"]},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["total"] == 2
        assert body["job_id"]

        # Job row exists with the right job_type
        from ts_admin.database import get_session
        from ts_admin.models.job import Job

        with get_session() as s:
            job = s.get(Job, body["job_id"])
        assert job is not None
        assert job.job_type == "bulk_delete_dryrun"

    def test_empty_object_ids_is_422(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/dryrun",
            json={"cluster_id": "c1", "org_id": 0, "object_ids": []},
        )
        assert r.status_code == 422


class TestExecuteEndpoint:
    def test_creates_job_with_bulk_delete_type(self, client, seeded, monkeypatch):
        from ts_admin.services import deletion_service

        async def _noop(**kwargs):
            return None

        monkeypatch.setattr(deletion_service, "_execute_delete", _noop)

        r = client.post(
            "/api/v1/deleter/execute",
            json={"cluster_id": "c1", "org_id": 0, "object_ids": ["lb-1"]},
        )
        assert r.status_code == 202, r.text
        body = r.json()
        assert body["total"] == 1

        from ts_admin.database import get_session
        from ts_admin.models.job import Job

        with get_session() as s:
            job = s.get(Job, body["job_id"])
        assert job is not None
        assert job.job_type == "bulk_delete"

    def test_empty_object_ids_is_422(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/execute",
            json={"cluster_id": "c1", "org_id": 0, "object_ids": []},
        )
        assert r.status_code == 422
