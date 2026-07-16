"""
Integration tests for the Relationship Visualizer read API.

Seeds two clusters that share GUIDs + a lineage edge set for c1 only, then
exercises topology / graph / consumers through the FastAPI app and proves c1
requests never surface c2 data (the multi-cluster isolation rule).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_dependency import CachedDependency
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


@pytest.fixture
def seeded(in_memory_db):
    """
    Both clusters get a model/table/answer/liveboard universe with the SAME
    GUIDs but cluster-distinct names/owners. Only c1 gets lineage edges.
    """
    now = datetime.now(tz=timezone.utc)
    with Session(in_memory_db) as session:
        for cid in ("c1", "c2"):
            session.add(
                Cluster(id=cid, name=cid, url=f"https://{cid}.thoughtspot.cloud", username="admin", auth_type="basic")
            )
            owner = "Alice (c1)" if cid == "c1" else "Bob (c2)"
            for guid, name, otype in [
                ("table-1", "DB Table", "ONE_TO_ONE_LOGICAL"),
                ("model-1", "Sales Model", "WORKSHEET"),
                ("answer-1", "Sales Answer", "ANSWER"),
                ("lb-1", "Sales Liveboard", "LIVEBOARD"),
            ]:
                session.add(
                    CachedMetadata(
                        cluster_id=cid,
                        org_id=0,
                        ts_guid=guid,
                        name=f"{name} in {cid}",
                        object_type=otype,
                        owner_name=owner,
                        tag_names=json.dumps([]),
                        synced_at=now,
                    )
                )
        # Lineage edges for c1 only: model→table, answer→model.
        session.add(
            CachedDependency(
                cluster_id="c1",
                org_id=0,
                source_guid="model-1",
                source_type="MODEL",
                source_name="Sales Model in c1",
                target_guid="table-1",
                target_type="DB_TABLE",
                target_name="DB Table in c1",
                relation="USES",
                synced_at=now,
            )
        )
        session.add(
            CachedDependency(
                cluster_id="c1",
                org_id=0,
                source_guid="answer-1",
                source_type="ANSWER",
                source_name="Sales Answer in c1",
                target_guid="model-1",
                target_type="MODEL",
                target_name="Sales Model in c1",
                relation="USES",
                synced_at=now,
            )
        )
        session.commit()


def test_topology_returns_grouped_universe(client, seeded):
    r = client.get("/api/v1/relationships/topology?cluster_id=c1&org_id=0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert {i["ts_guid"] for i in body["logical_tables"]} == {"table-1", "model-1"}
    assert [i["ts_guid"] for i in body["answers"]] == ["answer-1"]
    assert [i["ts_guid"] for i in body["liveboards"]] == ["lb-1"]


def test_graph_assembles_neighborhood(client, seeded):
    r = client.get("/api/v1/relationships/model/model-1?cluster_id=c1&org_id=0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["root"]["guid"] == "model-1"
    assert {n["guid"] for n in body["nodes"]} == {"model-1", "table-1", "answer-1"}
    assert body["consumer_totals"] == {"ANSWER": 1}
    assert body["impact"]["downstream_count"] == 1
    assert body["columns"] == []


def test_graph_404_for_unknown_object(client, seeded):
    r = client.get("/api/v1/relationships/model/does-not-exist?cluster_id=c1&org_id=0")
    assert r.status_code == 404


def test_graph_422_for_bad_root_kind(client, seeded):
    r = client.get("/api/v1/relationships/widget/model-1?cluster_id=c1&org_id=0")
    assert r.status_code == 422


def test_consumers_paginated(client, seeded):
    r = client.get("/api/v1/relationships/model/model-1/consumers?cluster_id=c1&org_id=0&type=ANSWER")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total"] == 1
    assert body["items"][0]["guid"] == "answer-1"


def test_graph_is_cluster_isolated(client, seeded):
    """c2 has no edges — its graph is just the root, never c1's neighborhood."""
    r = client.get("/api/v1/relationships/model/model-1?cluster_id=c2&org_id=0")
    assert r.status_code == 200, r.text
    body = r.json()
    assert [n["guid"] for n in body["nodes"]] == ["model-1"]
    assert body["impact"]["downstream_count"] == 0
    # No c1 owner name leaks into c2's response.
    assert "Alice (c1)" not in json.dumps(body)
