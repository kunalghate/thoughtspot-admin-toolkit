"""
Integration tests for the Group Management API (read-only v1).

Covers the list grid (envelope, search, sort, member counts), the
detail endpoint (members list, 404), and the CSV export.
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_user import CachedUser, UserGroupMembership
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


@pytest.fixture
def seeded(in_memory_db):
    now = datetime.now(tz=timezone.utc)
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
        for guid, name, privs in [
            ("g-admins", "Administrator", ["ADMINISTRATION"]),
            ("g-analysts", "analysts", []),
        ]:
            session.add(
                CachedGroup(
                    cluster_id="c1",
                    org_id=0,
                    ts_guid=guid,
                    name=name,
                    display_name=name.title(),
                    description=f"The {name} group",
                    privileges=json.dumps(privs),
                    synced_at=now,
                )
            )
        session.add(
            CachedUser(
                cluster_id="c1",
                ts_guid="u-alice",
                username="alice",
                display_name="Alice",
                email="alice@co.com",
                status="ACTIVE",
                synced_at=now,
            )
        )
        session.add(
            UserGroupMembership(
                cluster_id="c1",
                org_id=0,
                user_guid="u-alice",
                group_guid="g-admins",
                synced_at=now,
            )
        )
        session.commit()


class TestListGroups:
    def test_envelope_and_member_counts(self, client, seeded):
        r = client.get("/api/v1/groups?cluster_id=c1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["total"] == 2
        assert body["record_offset"] == 0
        assert body["page_size"] == 200
        counts = {g["ts_guid"]: g["member_count"] for g in body["items"]}
        assert counts == {"g-admins": 1, "g-analysts": 0}

    def test_search_narrows_results(self, client, seeded):
        r = client.get("/api/v1/groups?cluster_id=c1&search=analyst")
        assert r.status_code == 200
        assert r.json()["total"] == 1

    def test_sort_by_member_count(self, client, seeded):
        r = client.get("/api/v1/groups?cluster_id=c1&sort_field=member_count&sort_order=desc")
        assert r.status_code == 200
        items = r.json()["items"]
        assert [g["ts_guid"] for g in items] == ["g-admins", "g-analysts"]

    def test_privileges_are_lists(self, client, seeded):
        r = client.get("/api/v1/groups?cluster_id=c1&search=Administrator")
        assert r.json()["items"][0]["privileges"] == ["ADMINISTRATION"]


class TestGroupDetail:
    def test_returns_members(self, client, seeded):
        r = client.get("/api/v1/groups/g-admins?cluster_id=c1")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Administrator"
        assert body["member_count"] == 1
        assert body["members"] == [
            {
                "ts_guid": "u-alice",
                "username": "alice",
                "display_name": "Alice",
                "email": "alice@co.com",
                "status": "ACTIVE",
            }
        ]

    def test_404_for_unknown_guid(self, client, seeded):
        r = client.get("/api/v1/groups/g-ghost?cluster_id=c1")
        assert r.status_code == 404


class TestExportCsv:
    """
    The export must cover every matching row, not just the page the grid has
    loaded — that is the whole reason it is served from the backend instead of
    AG Grid's own exportDataAsCsv().
    """

    def test_streams_every_row_with_headers(self, client, seeded):
        r = client.get("/api/v1/groups/export.csv?cluster_id=c1")
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/csv")
        assert "attachment" in r.headers["content-disposition"]
        assert ".csv" in r.headers["content-disposition"]

        rows = list(csv.reader(io.StringIO(r.text)))
        assert rows[0] == [
            "Name",
            "Display name",
            "Description",
            "Privileges",
            "Members",
            "Created by",
            "Created",
            "Modified",
            "GUID",
            "Org ID",
        ]
        assert len(rows) == 3  # header + two groups
        by_name = {row[0]: row for row in rows[1:]}
        assert set(by_name) == {"Administrator", "analysts"}
        assert by_name["Administrator"][4] == "1"  # member_count
        assert by_name["Administrator"][8] == "g-admins"

    def test_list_privileges_render_as_one_cell(self, client, seeded):
        r = client.get("/api/v1/groups/export.csv?cluster_id=c1&search=Administrator")
        rows = list(csv.reader(io.StringIO(r.text)))
        assert len(rows) == 2
        assert rows[1][3] == "ADMINISTRATION"

    def test_honours_the_search_filter(self, client, seeded):
        r = client.get("/api/v1/groups/export.csv?cluster_id=c1&search=analyst")
        rows = list(csv.reader(io.StringIO(r.text)))
        assert [row[0] for row in rows[1:]] == ["analysts"]

    def test_pages_past_the_first_batch(self, client, in_memory_db, seeded, monkeypatch):
        """A cluster larger than one page must still export in full."""
        from ts_admin.api import csv_export

        monkeypatch.setattr(csv_export, "EXPORT_PAGE_SIZE", 1)
        r = client.get("/api/v1/groups/export.csv?cluster_id=c1")
        rows = list(csv.reader(io.StringIO(r.text)))
        assert len(rows) == 3, "paging must not truncate the export"

    def test_never_leaks_another_cluster(self, client, in_memory_db, seeded):
        now = datetime.now(tz=timezone.utc)
        with Session(in_memory_db) as session:
            session.add(
                Cluster(
                    id="c2",
                    name="Dev",
                    url="https://dev.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )
            session.add(
                CachedGroup(
                    cluster_id="c2",
                    org_id=0,
                    ts_guid="g-other",
                    name="other-cluster-group",
                    display_name="Other",
                    description="",
                    privileges=json.dumps([]),
                    synced_at=now,
                )
            )
            session.commit()

        body = client.get("/api/v1/groups/export.csv?cluster_id=c1").text
        assert "other-cluster-group" not in body
        assert "g-other" not in body
