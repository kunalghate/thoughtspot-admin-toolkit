"""
Integration tests for POST /api/v1/deleter/delete-tag-only.

Patches ThoughtSpotClient at the import boundary so search_tags + delete_tag
are simulated; verifies that the local CachedMetadata cache gets the tag
stripped and an AuditLog row is written with action_type=bulk_delete_tag.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select

from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.ts_client.models import TSTag

# ── Fake TS client ─────────────────────────────────────────────────────────────


class FakeTSClient:
    """Minimal stand-in for ThoughtSpotClient used as `async with FakeTSClient(...) as c`."""

    delete_tag_calls: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def search_tags(self) -> list[TSTag]:
        return [
            TSTag(id="tag-todo", name="TODO_DELETE", color=""),
            TSTag(id="tag-fin",  name="Finance",     color=""),
        ]

    async def delete_tag(self, *, tag_id: str) -> None:
        FakeTSClient.delete_tag_calls.append(tag_id)


@pytest.fixture(autouse=True)
def reset_fake():
    FakeTSClient.delete_tag_calls = []


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
def patched_client(monkeypatch):
    """Patch every import path the deleter service may resolve ThoughtSpotClient through."""
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", FakeTSClient)
    monkeypatch.setattr("ts_admin.services.deleter_service.ThoughtSpotClient", FakeTSClient, raising=False)


@pytest.fixture
def patched_config(monkeypatch):
    """Patch load_config so _get_cluster returns a cluster without hitting the keychain."""
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


@pytest.fixture
def client(in_memory_db, patched_client, patched_config):
    from ts_admin.main import create_app

    return TestClient(create_app())


@pytest.fixture
def seeded(in_memory_db):
    """Cluster + 3 metadata rows; two carry TODO_DELETE."""
    with Session(in_memory_db) as session:
        session.add(
            Cluster(
                id="c1", name="Prod",
                url="https://prod.thoughtspot.cloud",
                username="admin", auth_type="basic",
            )
        )
        now = datetime.now(tz=timezone.utc)
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0,
            ts_guid="lb-1", name="Sales", object_type="LIVEBOARD",
            owner_guid="u1", owner_name="Alice",
            tag_names=json.dumps(["TODO_DELETE", "Finance"]),
            synced_at=now,
        ))
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0,
            ts_guid="ans-1", name="Revenue", object_type="ANSWER",
            owner_guid="u2", owner_name="Bob",
            tag_names=json.dumps(["TODO_DELETE"]),
            synced_at=now,
        ))
        session.add(CachedMetadata(
            cluster_id="c1", org_id=0,
            ts_guid="lb-2", name="Untouched", object_type="LIVEBOARD",
            owner_guid="u1", owner_name="Alice",
            tag_names=json.dumps(["Finance"]),
            synced_at=now,
        ))
        session.commit()


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestDeleteTagOnly:
    def test_calls_ts_delete_tag_with_resolved_id(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/delete-tag-only",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "TODO_DELETE"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["tag_id"] == "tag-todo"
        assert body["tag_name"] == "TODO_DELETE"
        # Two cached rows carried the tag.
        assert body["removed_from"] == 2
        assert FakeTSClient.delete_tag_calls == ["tag-todo"]

    def test_strips_tag_from_cached_rows(self, client, seeded, in_memory_db):
        client.post(
            "/api/v1/deleter/delete-tag-only",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "TODO_DELETE"},
        )
        with Session(in_memory_db) as session:
            rows = session.exec(select(CachedMetadata)).all()
        for r in rows:
            assert "TODO_DELETE" not in r.get_tag_names()
        # Other tags survive.
        finance = next(r for r in rows if r.ts_guid == "lb-1")
        assert "Finance" in finance.get_tag_names()

    def test_writes_audit_log(self, client, seeded, in_memory_db):
        client.post(
            "/api/v1/deleter/delete-tag-only",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "TODO_DELETE"},
        )
        with Session(in_memory_db) as session:
            audits = session.exec(select(AuditLog)).all()
        assert len(audits) == 1
        a = audits[0]
        assert a.action_type == "bulk_delete_tag"
        assert a.entity_type == "tag"
        assert a.status == "COMPLETE"
        params = a.get_parameters()
        assert params["tag_id"] == "tag-todo"
        assert params["removed_from"] == 2

    def test_unknown_tag_returns_404(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/delete-tag-only",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "NOPE"},
        )
        assert r.status_code == 404
        assert "not found" in r.json()["detail"].lower()
        # No TS call was made.
        assert FakeTSClient.delete_tag_calls == []

    def test_case_insensitive_match(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/delete-tag-only",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": "todo_delete"},
        )
        assert r.status_code == 200
        # Resolved to the canonical tag id.
        assert FakeTSClient.delete_tag_calls == ["tag-todo"]

    def test_empty_tag_name_is_422(self, client, seeded):
        r = client.post(
            "/api/v1/deleter/delete-tag-only",
            json={"cluster_id": "c1", "org_id": 0, "tag_name": ""},
        )
        assert r.status_code == 422
