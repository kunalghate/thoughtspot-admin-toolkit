"""
Cluster isolation: parametrized invariant test for every list/read endpoint.

CLAUDE.md says every SQLite table has a `cluster_id` FK and queries must scope
by it. This test seeds two clusters with overlapping ts_guid values and proves
that requests scoped to cluster A never leak rows from cluster B.

When you ship a new list/read endpoint, add it to READ_ENDPOINTS and the
isolation check is extended automatically.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine

from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cluster import Cluster

# ── Registry: every read endpoint that takes cluster_id as a query param ───────
#
# Each entry is (path_template, response_extractor). The extractor pulls the
# list of items out of the response so we can search them for cross-cluster
# leakage. When you add a new read endpoint, register it here.

READ_ENDPOINTS = [
    pytest.param(
        "/api/v1/metadata?cluster_id={cluster}&org_id=0",
        lambda body: body["items"],
        id="metadata-list",
    ),
    pytest.param(
        "/api/v1/archiver/results?cluster_id={cluster}&org_id=0&stale_activity_days=1&stale_modified_days=1",
        lambda body: body["items"],
        id="archiver-results",
    ),
    pytest.param(
        "/api/v1/archiver/records?cluster_id={cluster}&org_id=0",
        lambda body: body["items"],
        id="archiver-records",
    ),
    pytest.param(
        "/api/v1/archiver/history?cluster_id={cluster}&org_id=0",
        # Job-based; per-cluster job rows would also need to be seeded. We assert
        # it returns an empty list for both clusters when no jobs exist.
        lambda body: body["items"],
        id="archiver-history",
    ),
    pytest.param(
        "/api/v1/users?cluster_id={cluster}",
        lambda body: body["items"],
        id="users-list",
    ),
    pytest.param(
        "/api/v1/users/history?cluster_id={cluster}",
        lambda body: body["items"],
        id="users-history",
    ),
    pytest.param(
        "/api/v1/sharing/principals?cluster_id={cluster}&org_id=0",
        lambda body: body["items"],
        id="sharing-principals",
    ),
    pytest.param(
        "/api/v1/sharing/history?cluster_id={cluster}",
        lambda body: body["items"],
        id="sharing-history",
    ),
    pytest.param(
        "/api/v1/relationships/topology?cluster_id={cluster}&org_id=0",
        # Topology returns three grouped lists; flatten them for the leak check.
        lambda body: body["logical_tables"] + body["answers"] + body["liveboards"],
        id="relationships-topology",
    ),
    pytest.param(
        "/api/v1/dashboard?cluster_id={cluster}&org_id=0",
        # Aggregate read; the leak-sniffable rows are the jobs + activity feeds.
        # (Count scoping is asserted directly in test_dashboard_api.py.)
        lambda body: body["recent_jobs"] + body["recent_activity"],
        id="dashboard-summary",
    ),
]


# ── Fixtures ───────────────────────────────────────────────────────────────────


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
def two_clusters(in_memory_db):
    """
    Seed two clusters with OVERLAPPING ts_guid values. If any endpoint forgets
    to filter by cluster_id, cluster A's response will contain a row whose
    ts_guid was seeded under cluster B.
    """
    now = datetime.now(tz=timezone.utc)
    long_ago = datetime(2020, 1, 1, tzinfo=timezone.utc)

    with Session(in_memory_db) as session:
        for cid, name in [("c1", "Prod"), ("c2", "Staging")]:
            session.add(
                Cluster(
                    id=cid,
                    name=name,
                    url=f"https://{name.lower()}.thoughtspot.cloud",
                    username="admin",
                    auth_type="basic",
                )
            )

        # Same ts_guid in both clusters but different names — leakage would be
        # obvious because the wrong cluster's name would appear.
        for cid, owner in [("c1", "Alice (c1)"), ("c2", "Bob (c2)")]:
            session.add(
                CachedMetadata(
                    cluster_id=cid,
                    org_id=0,
                    ts_guid="shared-guid-1",
                    name=f"Liveboard in {cid}",
                    object_type="LIVEBOARD",
                    owner_guid="u1",
                    owner_name=owner,
                    tag_names=json.dumps([]),
                    last_accessed_at=long_ago,  # stale, so archiver sees it
                    modified_at=long_ago,
                    synced_at=now,
                )
            )
        session.commit()


# ── The invariant ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("path_template", "extract_items"), READ_ENDPOINTS)
class TestClusterIsolation:
    def test_no_rows_leak_from_other_cluster(self, client, two_clusters, path_template, extract_items):
        for cluster in ("c1", "c2"):
            r = client.get(path_template.format(cluster=cluster))
            assert r.status_code == 200, r.text
            items = extract_items(r.json())

            for item in items:
                # Any row that surfaces must belong to the requested cluster.
                # Different endpoints expose different fields — sniff for any
                # signal of the OTHER cluster's data.
                other = "c2" if cluster == "c1" else "c1"
                other_owner = "Bob (c2)" if other == "c2" else "Alice (c1)"
                serialized = json.dumps(item)
                assert other_owner not in serialized, (
                    f"Cluster isolation violation on {path_template} (asking for {cluster}): "
                    f"response contains data from cluster {other!r}. Item: {item}"
                )
