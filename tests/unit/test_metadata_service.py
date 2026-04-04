"""
Unit tests for MetadataService.

Uses an in-memory SQLite DB so tests are fast and isolated.
No ThoughtSpot API calls — service reads from the local cache only.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, SQLModel, create_engine

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.services.metadata_service import MetadataService


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def in_memory_db(monkeypatch):
    """Patch get_engine() to use an in-memory SQLite DB for each test."""
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
def session(in_memory_db):
    with Session(in_memory_db) as s:
        yield s


@pytest.fixture
def cluster(session):
    c = Cluster(id="test-cluster", name="Test", url="https://test.thoughtspot.cloud", username="admin", auth_type="basic")
    session.add(c)
    session.commit()
    return c


def _make_obj(session, *, cluster_id="test-cluster", org_id=0, guid, name, object_type="LIVEBOARD",
              owner_guid="owner-1", owner_name="Alice", tag_names=None, last_accessed_days_ago=10):
    last = datetime.now(tz=timezone.utc) - timedelta(days=last_accessed_days_ago) if last_accessed_days_ago is not None else None
    obj = CachedMetadata(
        cluster_id=cluster_id, org_id=org_id, ts_guid=guid, name=name,
        object_type=object_type, owner_guid=owner_guid, owner_name=owner_name,
        tag_names=json.dumps(tag_names or []),   # stored as JSON string
        last_accessed_at=last, synced_at=datetime.now(tz=timezone.utc),
    )
    session.add(obj)
    session.commit()
    return obj


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestMetadataServiceSearch:

    def test_returns_all_for_cluster_org(self, session, cluster):
        _make_obj(session, guid="a", name="Sales Dashboard")
        _make_obj(session, guid="b", name="Revenue Report")
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0)
        assert total == 2
        assert len(items) == 2

    def test_filters_by_type(self, session, cluster):
        _make_obj(session, guid="a", name="A Liveboard", object_type="LIVEBOARD")
        _make_obj(session, guid="b", name="An Answer", object_type="ANSWER")
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0, types=["ANSWER"])
        assert total == 1
        assert items[0].object_type == "ANSWER"

    def test_filters_by_search(self, session, cluster):
        _make_obj(session, guid="a", name="Sales Dashboard")
        _make_obj(session, guid="b", name="Revenue Report")
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0, search="sales")
        assert total == 1
        assert items[0].name == "Sales Dashboard"

    def test_search_is_case_insensitive(self, session, cluster):
        _make_obj(session, guid="a", name="Sales Dashboard")
        items, _ = MetadataService.search(cluster_id="test-cluster", org_id=0, search="SALES")
        assert len(items) == 1

    def test_filters_stale_objects(self, session, cluster):
        _make_obj(session, guid="a", name="Recent", last_accessed_days_ago=5)
        _make_obj(session, guid="b", name="Stale", last_accessed_days_ago=100)
        _make_obj(session, guid="c", name="Never Accessed", last_accessed_days_ago=None)
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0, stale_days=90)
        assert total == 2
        names = {i.name for i in items}
        assert "Stale" in names
        assert "Never Accessed" in names
        assert "Recent" not in names

    def test_isolates_by_cluster(self, session, cluster):
        _make_obj(session, guid="a", name="Cluster 1 obj", cluster_id="test-cluster")
        _make_obj(session, guid="b", name="Cluster 2 obj", cluster_id="other-cluster")
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0)
        assert total == 1
        assert items[0].cluster_id == "test-cluster"

    def test_isolates_by_org(self, session, cluster):
        _make_obj(session, guid="a", name="Org 0 obj", org_id=0)
        _make_obj(session, guid="b", name="Org 1 obj", org_id=1)
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0)
        assert total == 1
        assert items[0].org_id == 0

    def test_returns_empty_when_nothing_synced(self, session, cluster):
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0)
        assert total == 0
        assert items == []

    def test_pagination(self, session, cluster):
        for i in range(10):
            _make_obj(session, guid=f"guid-{i}", name=f"Object {i}")
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0, page=1, page_size=3)
        assert total == 10
        assert len(items) == 3


class TestMetadataServiceGet:

    def test_returns_object_by_guid(self, session, cluster):
        _make_obj(session, guid="abc-123", name="My Liveboard")
        obj = MetadataService.get(cluster_id="test-cluster", org_id=0, ts_guid="abc-123")
        assert obj is not None
        assert obj.name == "My Liveboard"

    def test_returns_none_if_not_found(self, session, cluster):
        obj = MetadataService.get(cluster_id="test-cluster", org_id=0, ts_guid="does-not-exist")
        assert obj is None


class TestMetadataServiceStats:

    def test_returns_correct_totals(self, session, cluster):
        _make_obj(session, guid="a", name="A", object_type="LIVEBOARD", last_accessed_days_ago=10)
        _make_obj(session, guid="b", name="B", object_type="ANSWER", last_accessed_days_ago=10)
        _make_obj(session, guid="c", name="C", object_type="LIVEBOARD", last_accessed_days_ago=100)
        stats = MetadataService.stats(cluster_id="test-cluster", org_id=0)
        assert stats["total"] == 3
        assert stats["by_type"]["LIVEBOARD"] == 2
        assert stats["by_type"]["ANSWER"] == 1
        assert stats["stale_90d"] == 1
