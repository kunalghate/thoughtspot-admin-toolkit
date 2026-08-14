"""
Unit tests for group_service — the read-only query layer behind the Groups page.

Covers the list grid (search/org filters, member-count sort with empty groups,
pagination, sort-field whitelist fallback) and the detail view (membership join,
privileges JSON round-trip, missing GUID).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_user import CachedUser, UserGroupMembership
from ts_admin.models.cluster import Cluster
from ts_admin.services import group_service

CLUSTER_ID = "c1"


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
def seeded(in_memory_db):
    """Three groups: admins (2 members), analysts (1 member), empty (0 members)."""
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as session:
        session.add(
            Cluster(
                id=CLUSTER_ID,
                name="Prod",
                url="https://prod.thoughtspot.cloud",
                username="admin",
                auth_type="basic",
            )
        )
        groups = [
            ("g-admins", "Administrator", "Admins", "Cluster admins", ["ADMINISTRATION"]),
            ("g-analysts", "analysts", "Analysts", "Finance analysts", ["DATADOWNLOADING"]),
            ("g-empty", "empty-group", "Empty", "No members here", []),
        ]
        for guid, name, display, desc, privs in groups:
            session.add(
                CachedGroup(
                    cluster_id=CLUSTER_ID,
                    org_id=0,
                    ts_guid=guid,
                    name=name,
                    display_name=display,
                    description=desc,
                    privileges=json.dumps(privs),
                    synced_at=now,
                )
            )
        for guid, name in [("u-alice", "alice"), ("u-bob", "bob")]:
            session.add(
                CachedUser(
                    cluster_id=CLUSTER_ID,
                    ts_guid=guid,
                    username=name,
                    display_name=name.title(),
                    email=f"{name}@co.com",
                    status="ACTIVE",
                    synced_at=now,
                )
            )
        memberships = [
            ("u-alice", "g-admins"),
            ("u-bob", "g-admins"),
            ("u-alice", "g-analysts"),
        ]
        for user_guid, group_guid in memberships:
            session.add(
                UserGroupMembership(
                    cluster_id=CLUSTER_ID,
                    org_id=0,
                    user_guid=user_guid,
                    group_guid=group_guid,
                    synced_at=now,
                )
            )
        session.commit()


class TestListGroups:
    def test_returns_all_with_member_counts(self, seeded):
        items, total = group_service.list_groups(cluster_id=CLUSTER_ID)
        assert total == 3
        counts = {i["ts_guid"]: i["member_count"] for i in items}
        assert counts == {"g-admins": 2, "g-analysts": 1, "g-empty": 0}

    def test_search_matches_description(self, seeded):
        items, total = group_service.list_groups(cluster_id=CLUSTER_ID, search="finance")
        assert total == 1
        assert items[0]["ts_guid"] == "g-analysts"

    def test_org_filter(self, seeded):
        _, total = group_service.list_groups(cluster_id=CLUSTER_ID, org_id=0)
        assert total == 3
        _, total = group_service.list_groups(cluster_id=CLUSTER_ID, org_id=7)
        assert total == 0

    def test_sort_by_member_count_desc_and_asc(self, seeded):
        items, _ = group_service.list_groups(cluster_id=CLUSTER_ID, sort_field="member_count", sort_order="desc")
        assert [i["ts_guid"] for i in items] == ["g-admins", "g-analysts", "g-empty"]
        items, _ = group_service.list_groups(cluster_id=CLUSTER_ID, sort_field="member_count", sort_order="asc")
        # coalesce: the zero-member group sorts as 0, not NULL-last.
        assert [i["ts_guid"] for i in items] == ["g-empty", "g-analysts", "g-admins"]

    def test_unknown_sort_field_falls_back_to_name(self, seeded):
        items, _ = group_service.list_groups(cluster_id=CLUSTER_ID, sort_field="nope")
        assert [i["name"] for i in items] == ["Administrator", "analysts", "empty-group"]

    def test_pagination(self, seeded):
        items, total = group_service.list_groups(cluster_id=CLUSTER_ID, record_offset=1, page_size=1)
        assert total == 3
        assert len(items) == 1

    def test_privileges_round_trip(self, seeded):
        items, _ = group_service.list_groups(cluster_id=CLUSTER_ID, search="Administrator")
        assert items[0]["privileges"] == ["ADMINISTRATION"]

    def test_other_cluster_is_empty(self, seeded):
        items, total = group_service.list_groups(cluster_id="c2")
        assert total == 0
        assert items == []


class TestGetGroupDetail:
    def test_members_join(self, seeded):
        detail = group_service.get_group_detail(cluster_id=CLUSTER_ID, ts_guid="g-admins")
        assert detail is not None
        assert detail["member_count"] == 2
        assert [m["username"] for m in detail["members"]] == ["alice", "bob"]
        assert detail["members"][0]["email"] == "alice@co.com"

    def test_empty_group(self, seeded):
        detail = group_service.get_group_detail(cluster_id=CLUSTER_ID, ts_guid="g-empty")
        assert detail is not None
        assert detail["member_count"] == 0
        assert detail["members"] == []

    def test_missing_guid_returns_none(self, seeded):
        assert group_service.get_group_detail(cluster_id=CLUSTER_ID, ts_guid="g-ghost") is None

    def test_membership_without_synced_user_still_counts(self, seeded, in_memory_db):
        """Member count comes from the junction table, so it must not drop when
        the member's CachedUser row hasn't been synced yet."""
        with Session(in_memory_db) as session:
            session.add(
                UserGroupMembership(
                    cluster_id=CLUSTER_ID,
                    org_id=0,
                    user_guid="u-unsynced",
                    group_guid="g-empty",
                    synced_at=datetime.now(tz=timezone.utc),
                )
            )
            session.commit()
        detail = group_service.get_group_detail(cluster_id=CLUSTER_ID, ts_guid="g-empty")
        assert detail["member_count"] == 1
        assert detail["members"] == []  # user row absent from cache
