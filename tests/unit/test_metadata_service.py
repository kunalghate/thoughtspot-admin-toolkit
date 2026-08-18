"""
Unit tests for MetadataService.

Uses an in-memory SQLite DB so tests are fast and isolated.
No ThoughtSpot API calls — service reads from the local cache only.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster
from ts_admin.models.sync_log import SyncLog
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
    c = Cluster(
        id="test-cluster", name="Test", url="https://test.thoughtspot.cloud", username="admin", auth_type="basic"
    )
    session.add(c)
    session.commit()
    return c


def _make_obj(
    session,
    *,
    cluster_id="test-cluster",
    org_id=0,
    guid,
    name,
    object_type="LIVEBOARD",
    owner_guid="owner-1",
    owner_name="Alice",
    tag_names=None,
    last_accessed_days_ago=10,
):
    last = (
        datetime.now(tz=timezone.utc) - timedelta(days=last_accessed_days_ago)
        if last_accessed_days_ago is not None
        else None
    )
    obj = CachedMetadata(
        cluster_id=cluster_id,
        org_id=org_id,
        ts_guid=guid,
        name=name,
        object_type=object_type,
        owner_guid=owner_guid,
        owner_name=owner_name,
        tag_names=json.dumps(tag_names or []),  # stored as JSON string
        last_accessed_at=last,
        synced_at=datetime.now(tz=timezone.utc),
    )
    session.add(obj)
    session.commit()
    return obj


def _sync_log(session, *, cluster_id="test-cluster", org_id=0, status="SUCCESS", entity_type="metadata"):
    row = SyncLog(cluster_id=cluster_id, org_id=org_id, entity_type=entity_type, status=status)
    session.add(row)
    session.commit()
    return row


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
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0, record_offset=0, page_size=3)
        assert total == 10
        assert len(items) == 3


class TestSystemUserContent:
    """A fresh ThoughtSpot org contains only built-in "System User" content.
    search() hides it (admins cannot act on it), so the count of what was hidden
    has to be reachable — otherwise that org renders as a blank grid that is
    indistinguishable from a failed sync."""

    def test_search_hides_system_user_content(self, session, cluster):
        _make_obj(session, guid="sys", name="TS: Embrace Query Worksheet", owner_name="System User")
        _make_obj(session, guid="mine", name="My Liveboard", owner_name="Alice")
        items, total = MetadataService.search(cluster_id="test-cluster", org_id=0)
        assert total == 1
        assert items[0].name == "My Liveboard"

    def test_hidden_count_matches_what_search_dropped(self, session, cluster):
        for i in range(3):
            _make_obj(session, guid=f"sys-{i}", name=f"TS: Builtin {i}", owner_name="System User")
        _make_obj(session, guid="mine", name="My Liveboard", owner_name="Alice")
        _, total = MetadataService.search(cluster_id="test-cluster", org_id=0)
        assert total == 1
        assert MetadataService.hidden_system_count(cluster_id="test-cluster", org_id=0) == 3

    def test_hidden_count_explains_an_all_system_org(self, session, cluster):
        """The reported bug: sync SUCCESS, 24 rows cached, grid shows zero."""
        for i in range(24):
            _make_obj(session, guid=f"sys-{i}", name=f"TS: Builtin {i}", owner_name="System User")
        _, total = MetadataService.search(cluster_id="test-cluster", org_id=0)
        assert total == 0
        assert MetadataService.hidden_system_count(cluster_id="test-cluster", org_id=0) == 24

    def test_hidden_count_is_zero_without_system_content(self, session, cluster):
        _make_obj(session, guid="mine", name="My Liveboard", owner_name="Alice")
        assert MetadataService.hidden_system_count(cluster_id="test-cluster", org_id=0) == 0

    def test_hidden_count_isolates_by_cluster_and_org(self, session, cluster):
        _make_obj(session, guid="a", name="Sys", owner_name="System User", cluster_id="test-cluster", org_id=0)
        _make_obj(session, guid="b", name="Sys", owner_name="System User", cluster_id="test-cluster", org_id=1)
        _make_obj(session, guid="c", name="Sys", owner_name="System User", cluster_id="other-cluster", org_id=0)
        assert MetadataService.hidden_system_count(cluster_id="test-cluster", org_id=0) == 1


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

    def test_staleness_excludes_types_the_archiver_cannot_touch(self, session, cluster):
        """
        Tables and worksheets carry no access telemetry, so a null
        `last_accessed_at` on them is missing data, not disuse. Counting them
        as stale inflated the dashboard tile with objects the Archiver — which
        that tile links to — can never act on.
        """
        _make_obj(session, guid="lb", name="LB", object_type="LIVEBOARD", last_accessed_days_ago=None)
        for i in range(5):
            _make_obj(
                session,
                guid=f"tbl-{i}",
                name=f"T{i}",
                object_type="ONE_TO_ONE_LOGICAL",
                last_accessed_days_ago=None,
            )
        stats = MetadataService.stats(cluster_id="test-cluster", org_id=0)
        assert stats["total"] == 6  # inventory still counts everything
        assert stats["archivable_total"] == 1
        assert stats["never_accessed"] == 1  # the liveboard only
        assert stats["stale_90d"] == 0  # no object has an aged-out real date

    def test_staleness_excludes_system_user_content(self, session, cluster):
        """Mirrors the Archiver's own scope — system content is not actionable."""
        _make_obj(
            session,
            guid="sys",
            name="System LB",
            owner_name="System User",
            last_accessed_days_ago=200,
        )
        _make_obj(session, guid="mine", name="My LB", owner_name="Alice", last_accessed_days_ago=200)
        stats = MetadataService.stats(cluster_id="test-cluster", org_id=0)
        assert stats["stale_90d"] == 1
        assert stats["archivable_total"] == 1


class TestMetadataCacheAuthoritative:
    """S23 — stats FLAG a truncated cache; they never refuse. `last_synced` and
    `cache_authoritative` both read the newest SUCCESS `sync_log` row, which is
    the only completeness signal that survives an interrupted sync."""

    def test_false_and_last_synced_none_when_never_synced(self, session, cluster):
        _make_obj(session, guid="a", name="A")
        stats = MetadataService.stats(cluster_id="test-cluster", org_id=0)
        assert stats["total"] == 1  # rows are still served
        assert stats["cache_authoritative"] is False
        assert stats["last_synced"] is None
        assert MetadataService.cache_authoritative(cluster_id="test-cluster", org_id=0) is False

    def test_true_after_a_successful_metadata_sync(self, session, cluster):
        _make_obj(session, guid="a", name="A")
        _sync_log(session, status="SUCCESS")
        stats = MetadataService.stats(cluster_id="test-cluster", org_id=0)
        assert stats["cache_authoritative"] is True
        assert stats["last_synced"] is not None
        assert MetadataService.cache_authoritative(cluster_id="test-cluster", org_id=0) is True

    def test_false_while_a_sync_is_in_progress(self, session, cluster):
        """The write-ahead marker: a sync that started and hasn't finished means
        the cache below it may be mid-rebuild and truncated."""
        _make_obj(session, guid="a", name="A")
        _sync_log(session, status="IN_PROGRESS")
        assert MetadataService.stats(cluster_id="test-cluster", org_id=0)["cache_authoritative"] is False

    def test_org_scoped_org_1_does_not_inherit_org_0s_sync(self, session, cluster):
        """The tightening this change makes. The copy of this query that lived in
        metadata_service filtered cluster_id + entity_type but NOT org_id, so a
        never-synced org reported org 0's `last_synced` and looked healthy."""
        _make_obj(session, guid="a", name="A", org_id=1)
        _sync_log(session, status="SUCCESS", org_id=0)

        stats = MetadataService.stats(cluster_id="test-cluster", org_id=1)
        assert stats["total"] == 1  # anti-vacuity: org 1 really does have rows
        assert stats["cache_authoritative"] is False
        assert stats["last_synced"] is None

        # ...and org 0 is unaffected, so this isn't "the query returns nothing".
        assert MetadataService.stats(cluster_id="test-cluster", org_id=0)["cache_authoritative"] is True

    def test_cluster_scoped(self, session, cluster):
        _make_obj(session, guid="a", name="A")
        _sync_log(session, status="SUCCESS", cluster_id="other-cluster")
        assert MetadataService.stats(cluster_id="test-cluster", org_id=0)["cache_authoritative"] is False
