"""
Integration tests for GET /api/v1/connections.

The page exists to answer two questions ThoughtSpot's own UI does not:
what does this connection point at, and which connections have nothing on them.
The second one is why object counts are computed from the metadata cache with a
GROUP BY rather than stored on the connection row — a stored counter drifts the
moment a metadata sync runs, and a zero that is actually staleness is the worst
possible answer here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_connection import CachedConnection
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


def _conn(session, *, cluster_id: str, guid: str, name: str, warehouse: str = "SNOWFLAKE", org_id: int = 0):
    session.add(
        CachedConnection(
            cluster_id=cluster_id,
            org_id=org_id,
            ts_guid=guid,
            name=name,
            description=f"{name} description",
            data_warehouse_type=warehouse,
            synced_at=datetime.now(tz=timezone.utc),
        )
    )


def _table(session, *, cluster_id: str, guid: str, connection_guid: str, otype: str = "ONE_TO_ONE_LOGICAL"):
    session.add(
        CachedMetadata(
            cluster_id=cluster_id,
            org_id=0,
            ts_guid=guid,
            name=f"obj-{guid}",
            object_type=otype,
            owner_guid="u1",
            owner_name="Alice",
            tag_names=json.dumps([]),
            connection_guid=connection_guid,
            db_name="DB",
            db_schema="PUBLIC",
            db_table=guid.upper(),
            synced_at=datetime.now(tz=timezone.utc),
        )
    )


@pytest.fixture
def seeded(in_memory_db):
    with Session(in_memory_db) as session:
        for cid, name in [("c1", "Prod"), ("c2", "Dev")]:
            session.add(Cluster(id=cid, name=name, url=f"https://{cid}.ts.cloud", username="admin", auth_type="basic"))
        _conn(session, cluster_id="c1", guid="conn-busy", name="Snowflake Prod")
        _conn(session, cluster_id="c1", guid="conn-empty", name="Databricks Old", warehouse="DATABRICKS")
        _conn(session, cluster_id="c2", guid="conn-other", name="Other Cluster Conn")

        _table(session, cluster_id="c1", guid="t-1", connection_guid="conn-busy")
        _table(session, cluster_id="c1", guid="t-2", connection_guid="conn-busy")
        _table(session, cluster_id="c1", guid="w-1", connection_guid="conn-busy", otype="WORKSHEET")
        # An object on a connection that is not cached (Analyst Studio, DEFAULT).
        _table(session, cluster_id="c1", guid="t-orphan", connection_guid="conn-unknown")
        session.commit()


class TestListConnections:
    def test_counts_objects_per_connection(self, client, seeded):
        r = client.get("/api/v1/connections?cluster_id=c1&org_id=0")
        assert r.status_code == 200, r.text
        body = r.json()
        by_guid = {c["ts_guid"]: c for c in body["items"]}
        assert by_guid["conn-busy"]["object_count"] == 3
        assert by_guid["conn-busy"]["counts_by_type"] == {"ONE_TO_ONE_LOGICAL": 2, "WORKSHEET": 1}

    def test_a_connection_with_nothing_on_it_still_lists(self, client, seeded):
        """The row an admin came here to find must not be filtered out by the join."""
        r = client.get("/api/v1/connections?cluster_id=c1&org_id=0")
        by_guid = {c["ts_guid"]: c for c in r.json()["items"]}
        assert "conn-empty" in by_guid
        assert by_guid["conn-empty"]["object_count"] == 0
        assert by_guid["conn-empty"]["counts_by_type"] == {}

    def test_reports_the_warehouse_type(self, client, seeded):
        r = client.get("/api/v1/connections?cluster_id=c1&org_id=0")
        by_guid = {c["ts_guid"]: c for c in r.json()["items"]}
        assert by_guid["conn-empty"]["data_warehouse_type"] == "DATABRICKS"

    def test_search_matches_name_and_type(self, client, seeded):
        assert [
            c["ts_guid"]
            for c in client.get("/api/v1/connections?cluster_id=c1&org_id=0&search=databricks").json()["items"]
        ] == ["conn-empty"]

    def test_sort_by_object_count(self, client, seeded):
        r = client.get("/api/v1/connections?cluster_id=c1&org_id=0&sort_field=object_count&sort_order=desc")
        assert [c["ts_guid"] for c in r.json()["items"]] == ["conn-busy", "conn-empty"]

    def test_linked_rows_distinguishes_stale_cache_from_genuinely_unused(self, client, seeded):
        """Zero linked rows over a non-empty cache means 're-sync', not 'unused'."""
        body = client.get("/api/v1/connections?cluster_id=c1&org_id=0").json()
        # Includes the orphan pointing at an uncached connection.
        assert body["linked_rows"] == 4
        assert body["metadata_rows"] == 4

    def test_never_lists_another_clusters_connections(self, client, seeded):
        guids = [c["ts_guid"] for c in client.get("/api/v1/connections?cluster_id=c1&org_id=0").json()["items"]]
        assert "conn-other" not in guids
        assert [c["ts_guid"] for c in client.get("/api/v1/connections?cluster_id=c2&org_id=0").json()["items"]] == [
            "conn-other"
        ]


class TestMetadataConnectionColumns:
    def test_list_resolves_the_connection_name(self, client, seeded):
        r = client.get("/api/v1/metadata?cluster_id=c1&org_id=0")
        by_guid = {o["ts_guid"]: o for o in r.json()["items"]}
        assert by_guid["t-1"]["connection_name"] == "Snowflake Prod"
        assert by_guid["t-1"]["db_name"] == "DB"
        assert by_guid["t-1"]["db_schema"] == "PUBLIC"
        assert by_guid["t-1"]["db_table"] == "T-1"

    def test_falls_back_to_the_guid_for_an_uncached_connection(self, client, seeded):
        """Analyst Studio and DEFAULT are not returned by /connection/search."""
        r = client.get("/api/v1/metadata?cluster_id=c1&org_id=0")
        by_guid = {o["ts_guid"]: o for o in r.json()["items"]}
        assert by_guid["t-orphan"]["connection_name"] == "conn-unknown"

    def test_filters_by_connection(self, client, seeded):
        r = client.get("/api/v1/metadata?cluster_id=c1&org_id=0&connection_guid=conn-busy")
        assert sorted(o["ts_guid"] for o in r.json()["items"]) == ["t-1", "t-2", "w-1"]
