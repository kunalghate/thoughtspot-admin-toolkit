"""
Phase 1 unit tests for lineage_service.

Covers the object-tier build (batched dependency sweep → USES edges), the
cluster/org scoping invariant, and the SQLite-only read functions (topology,
neighborhood graph, paginated consumers). No real ThoughtSpot — a canned
_FakeClient stands in for search_dependents.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

CLUSTER_ID = "c1"


# ── Fixtures ────────────────────────────────────────────────────────────────────


@pytest.fixture
def in_memory_db(monkeypatch):
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
def patched_config(monkeypatch):
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cluster_cfg = ClusterConfig(
        id=CLUSTER_ID,
        name="Prod",
        url="https://prod.thoughtspot.cloud",
        username="admin",
        auth_type=AuthType.TRUSTED,
    )
    config = AppConfig(clusters={CLUSTER_ID: cluster_cfg}, active_cluster_id=CLUSTER_ID)
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    monkeypatch.setattr(
        "ts_admin.config.ClusterConfig.build_auth_strategy",
        lambda self, org_id=None: None,
    )
    return config


class _FakeClient:
    """Canned search_dependents: table-1 → model-1 → {answer-1, lb-1}."""

    def __init__(self, *args, **kwargs):
        self.calls: list[list[str]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def search_dependents(self, *, object_ids, object_type, batch_size=100):
        self.calls.append(list(object_ids))
        canned = {
            "table-1": [{"id": "model-1", "name": "Sales Model", "type": "LOGICAL_TABLE"}],
            "model-1": [
                {"id": "answer-1", "name": "Sales Answer", "type": "ANSWER"},
                {"id": "lb-1", "name": "Sales Liveboard", "type": "LIVEBOARD"},
            ],
        }
        return {guid: canned.get(guid, []) for guid in object_ids}


def _seed_metadata_sync_log(engine, *, cluster_id: str, org_id: int = 0, status: str = "SUCCESS") -> None:
    """Write the sync_log row that certifies (or fails to certify) the metadata
    cache for this scope.

    Upserts, mirroring `sync_service._write_sync_log` — there is exactly ONE row
    per (cluster, org, entity), so an interrupted re-sync flips the existing row
    to IN_PROGRESS rather than leaving a stale SUCCESS alongside it.
    """
    from ts_admin.models.sync_log import SyncLog

    with Session(engine) as session:
        row = session.exec(
            select(SyncLog).where(
                SyncLog.cluster_id == cluster_id,
                SyncLog.org_id == org_id,
                SyncLog.entity_type == "metadata",
            )
        ).first()
        if row is None:
            row = SyncLog(cluster_id=cluster_id, org_id=org_id, entity_type="metadata", record_count=4)
        row.status = status
        row.synced_at = datetime.now(tz=timezone.utc)
        session.add(row)
        session.commit()


def _seed_metadata(engine, cluster_id: str, org_id: int = 0, *, name_suffix: str = "") -> None:
    """Seed a healthy metadata cache: rows PLUS the SUCCESS sync_log that
    certifies them complete. Rows alone are not a healthy cache — a truncated
    sync also leaves rows behind, and `build_object_graph` fails closed on the
    log, not the row count."""
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.models.cluster import Cluster

    now = datetime.now(tz=timezone.utc)
    _seed_metadata_sync_log(engine, cluster_id=cluster_id, org_id=org_id, status="SUCCESS")
    with Session(engine) as session:
        if not session.get(Cluster, cluster_id):
            session.add(
                Cluster(
                    id=cluster_id,
                    name=cluster_id,
                    url=f"https://{cluster_id}.thoughtspot.cloud",
                    username="admin",
                    auth_type="trusted",
                )
            )
        rows = [
            ("table-1", "DB Table 1" + name_suffix, "ONE_TO_ONE_LOGICAL"),
            ("model-1", "Sales Model" + name_suffix, "WORKSHEET"),
            ("answer-1", "Sales Answer" + name_suffix, "ANSWER"),
            ("lb-1", "Sales Liveboard" + name_suffix, "LIVEBOARD"),
        ]
        for guid, name, otype in rows:
            session.add(
                CachedMetadata(
                    cluster_id=cluster_id,
                    org_id=org_id,
                    ts_guid=guid,
                    name=name,
                    object_type=otype,
                    owner_name="Alice",
                    synced_at=now,
                )
            )
        session.commit()


def _make_job() -> str:
    from ts_admin.services.job_service import create_job

    return create_job(
        job_type="sync:dependencies",
        parameters={"entity_type": "dependencies", "org_id": 0},
        cluster_id=CLUSTER_ID,
    )


# ── build_object_graph ──────────────────────────────────────────────────────────


async def test_build_object_graph_creates_uses_edges(monkeypatch, in_memory_db, patched_config):
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    count = await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert count == 2  # model→table, answer→model. lb→model is deferred to Phase 2.

    with Session(in_memory_db) as session:
        edges = session.exec(select(CachedDependency).where(CachedDependency.cluster_id == CLUSTER_ID)).all()
    pairs = {(e.source_guid, e.target_guid, e.relation, e.source_type, e.target_type) for e in edges}
    assert ("model-1", "table-1", "USES", "MODEL", "DB_TABLE") in pairs
    assert ("answer-1", "model-1", "USES", "ANSWER", "MODEL") in pairs
    # Liveboard object edges are NOT produced by the dependency sweep.
    assert not any(e.source_type == "LIVEBOARD" for e in edges)


def test_edges_from_dependents_types_by_group_key_and_drops_noncontent():
    """
    A dependent that isn't in the metadata cache is typed from the API's group
    key (stamped onto the item by the client). Answers are stored, Liveboards are
    deferred to Phase 2, and non-content deps (FEEDBACK alerts, untyped) are
    dropped — never mislabeled as LOGICAL_TABLE. Regression for the drawer that
    showed Liveboards/Answers under a "13 Logical Tables" count node.
    """
    from ts_admin.services import lineage_service

    meta_by_guid = {
        "model-1": ("Sales Model", "WORKSHEET"),
        "ans-known": ("Known Answer", "ANSWER"),  # in cache → typed from metadata
    }
    dependents = {
        "model-1": [
            {"id": "ans-x", "name": "Forecast", "type": "QUESTION_ANSWER_BOOK"},
            {"id": "lb-x", "name": "Sales Liveboard", "type": "PINBOARD_ANSWER_BOOK"},
            {"id": "fb-x", "name": "quota", "type": "FEEDBACK"},
            {"id": "unk-x", "name": "mystery", "type": None},
            {"id": "ans-known", "name": "Known Answer", "type": None},
        ]
    }
    edges = lineage_service._edges_from_dependents(dependents, meta_by_guid, CLUSTER_ID, 0)
    by_src = {e.source_guid: e.source_type for e in edges}

    assert by_src == {"ans-x": "ANSWER", "ans-known": "ANSWER"}
    assert "lb-x" not in by_src  # Liveboard deferred to Phase 2's TML pass
    assert "fb-x" not in by_src  # FEEDBACK alert is not lineage content
    assert "unk-x" not in by_src  # unknown/untyped dependent dropped, not defaulted
    assert all(e.target_type == "MODEL" for e in edges)


async def test_build_object_graph_writes_sync_log_and_deletes_stale(monkeypatch, in_memory_db, patched_config):
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.sync_log import SyncLog
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    # A stale edge that a rebuild must delete-before-insert away.
    with Session(in_memory_db) as session:
        session.add(
            CachedDependency(
                cluster_id=CLUSTER_ID,
                org_id=0,
                source_guid="ghost",
                source_type="ANSWER",
                target_guid="model-1",
                target_type="MODEL",
                relation="USES",
            )
        )
        session.commit()

    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        edges = session.exec(select(CachedDependency)).all()
        log = session.exec(select(SyncLog).where(SyncLog.entity_type == "dependencies")).one()
    assert not any(e.source_guid == "ghost" for e in edges)  # stale edge gone
    assert log.status == "SUCCESS"
    assert log.record_count == 2


async def test_build_object_graph_never_synced_metadata_raises(monkeypatch, in_memory_db, patched_config):
    """Building lineage before ANY metadata sync must fail closed with the typed
    StaleCacheError — not a bare ValueError, which error_formatter can only
    render as "Something went wrong" with no route back to the fix."""
    from ts_admin.models.cluster import Cluster
    from ts_admin.services import lineage_service
    from ts_admin.ts_client.exceptions import StaleCacheError

    with Session(in_memory_db) as session:
        session.add(Cluster(id=CLUSTER_ID, name="Prod", url="https://p", username="a", auth_type="trusted"))
        session.commit()

    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    with pytest.raises(StaleCacheError) as exc:
        await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert exc.value.status == "NOT_SYNCED"


async def test_build_object_graph_refuses_truncated_metadata_cache(monkeypatch, in_memory_db, patched_config):
    """REGRESSION. An interrupted metadata sync leaves rows behind, so the old
    `if not meta_rows` guard passed and lineage built from a partial GUID
    universe — publishing a SUCCESS graph that was missing whole branches."""
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service
    from ts_admin.ts_client.exceptions import StaleCacheError

    _seed_metadata(in_memory_db, CLUSTER_ID)
    # Sync was interrupted after the rows landed: newest log is IN_PROGRESS.
    _seed_metadata_sync_log(in_memory_db, cluster_id=CLUSTER_ID, org_id=0, status="IN_PROGRESS")

    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    with pytest.raises(StaleCacheError) as exc:
        await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert exc.value.status == "IN_PROGRESS"

    # And it refused BEFORE writing anything — no half-built graph left behind.
    with Session(in_memory_db) as session:
        assert session.exec(select(CachedDependency)).all() == []


async def test_build_object_graph_certified_empty_org_builds_empty_graph(monkeypatch, in_memory_db, patched_config):
    """A metadata sync that certified an org as genuinely empty is not an error
    — it's a valid empty lineage. Previously this raised."""
    from ts_admin.models.cluster import Cluster
    from ts_admin.services import lineage_service

    with Session(in_memory_db) as session:
        session.add(Cluster(id=CLUSTER_ID, name="Prod", url="https://p", username="a", auth_type="trusted"))
        session.commit()
    _seed_metadata_sync_log(in_memory_db, cluster_id=CLUSTER_ID, org_id=0, status="SUCCESS")

    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    assert await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job()) == 0


# ── Read functions ──────────────────────────────────────────────────────────────


async def test_topology_groups_and_reads(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    topo = lineage_service.get_topology(cluster_id=CLUSTER_ID, org_id=0)
    assert {i["ts_guid"] for i in topo["logical_tables"]} == {"table-1", "model-1"}
    assert [i["ts_guid"] for i in topo["answers"]] == ["answer-1"]
    assert [i["ts_guid"] for i in topo["liveboards"]] == ["lb-1"]
    # subtype labels feed the left-list filter and the detail header's type chip.
    subtypes = {i["ts_guid"]: i["subtype"] for i in topo["logical_tables"]}
    assert subtypes == {"table-1": "Table", "model-1": "Model"}
    assert topo["answers"][0]["subtype"] == "Answer"
    assert topo["liveboards"][0]["subtype"] == "Liveboard"


async def test_lineage_graph_neighborhood_and_impact(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="model-1", root_kind="model")
    node_guids = {n["guid"] for n in graph["nodes"]}
    assert node_guids == {"model-1", "table-1", "answer-1"}  # upstream table + downstream answer
    assert graph["consumer_totals"] == {"ANSWER": 1}
    assert graph["impact"]["downstream_count"] == 1
    assert graph["columns"] == []
    # root node carries a layer for the frontend's manual layout.
    assert graph["root"]["node_type"] == "MODEL"
    assert graph["root"]["layer"] == 3


async def test_lineage_graph_missing_object_returns_none(in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    assert lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="nope", root_kind="model") is None


async def test_consumers_pagination(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    items, total = lineage_service.get_consumers(cluster_id=CLUSTER_ID, org_id=0, guid="model-1", limit=10)
    assert total == 1
    assert items[0]["guid"] == "answer-1"
    assert items[0]["node_type"] == "ANSWER"
    # type filter that matches nothing returns empty.
    items2, total2 = lineage_service.get_consumers(
        cluster_id=CLUSTER_ID, org_id=0, guid="model-1", consumer_type="LIVEBOARD"
    )
    assert total2 == 0


async def test_build_is_cluster_scoped(monkeypatch, in_memory_db, patched_config):
    """A c1 build must never read c2 metadata or write c2 edges."""
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    _seed_metadata(in_memory_db, "c2", name_suffix=" (c2)")

    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        edges = session.exec(select(CachedDependency)).all()
    # Only c1 edges exist; none tagged c2.
    assert edges and all(e.cluster_id == CLUSTER_ID for e in edges)
    # c2 topology stays empty of lineage until its own build runs.
    graph_c2 = lineage_service.get_lineage_graph(cluster_id="c2", org_id=0, guid="model-1", root_kind="model")
    assert graph_c2 is not None
    assert graph_c2["nodes"] == [graph_c2["root"]]  # no edges → just the root node


# ── S22: inaccessible endpoints are flagged, never deleted ──────────────────────


async def test_lineage_graph_flags_endpoint_missing_from_metadata(monkeypatch, in_memory_db, patched_config):
    """
    A target deleted in TS leaves its edge behind (by design — the builder itself
    writes edges whose target isn't cached yet). The read path must mark that node
    `accessible=False` so it renders dashed, and must NOT delete the edge.
    """
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    # table-1 is deleted in TS: its metadata row goes, its edge stays.
    with Session(in_memory_db) as session:
        row = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == CLUSTER_ID,
                CachedMetadata.ts_guid == "table-1",
            )
        ).one()
        session.delete(row)
        session.commit()

    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="model-1", root_kind="model")
    by_guid = {n["guid"]: n for n in graph["nodes"]}
    assert by_guid["table-1"]["accessible"] is False  # ghost endpoint, dimmed
    assert by_guid["model-1"]["accessible"] is True  # root still cached
    assert by_guid["answer-1"]["accessible"] is True  # untouched consumer
    assert graph["root"]["accessible"] is True

    # The whole point of S22 over S6: the edge survives.
    with Session(in_memory_db) as session:
        edges = session.exec(select(CachedDependency).where(CachedDependency.cluster_id == CLUSTER_ID)).all()
    assert ("model-1", "table-1") in {(e.source_guid, e.target_guid) for e in edges}


async def test_lineage_graph_never_flags_connections_inaccessible(in_memory_db, patched_config):
    """CONNECTIONs come from TML, never from CachedMetadata — absence proves nothing."""
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    with Session(in_memory_db) as session:
        session.add(
            CachedDependency(
                cluster_id=CLUSTER_ID,
                org_id=0,
                source_guid="table-1",
                source_type="DB_TABLE",
                source_name="DB Table 1",
                target_guid="conn-1",
                target_type="CONNECTION",
                target_name="Snowflake Prod",
                relation="CONNECTS",
            )
        )
        session.commit()

    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="table-1", root_kind="table")
    conn = next(n for n in graph["nodes"] if n["guid"] == "conn-1")
    assert conn["accessible"] is True


async def test_lineage_graph_is_stable_across_repeated_reads(monkeypatch, in_memory_db, patched_config):
    """
    Regression for the rejected S6 purge: reading the graph must not mutate the
    cache, so two identical reads return identical node/edge sets.
    """
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed_metadata(in_memory_db, CLUSTER_ID)
    fake = _FakeClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    def _read():
        g = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="model-1", root_kind="model")
        return sorted((n["guid"], n["accessible"]) for n in g["nodes"])

    with Session(in_memory_db) as session:
        before = len(session.exec(select(CachedDependency)).all())
    assert _read() == _read()
    with Session(in_memory_db) as session:
        assert len(session.exec(select(CachedDependency)).all()) == before


# ── S25: composite index on the (cluster_id, org_id, ts_guid) identity key ──────


def test_ts_metadata_has_composite_identity_index(in_memory_db):
    from sqlalchemy import inspect

    indexes = {i["name"]: i["column_names"] for i in inspect(in_memory_db).get_indexes("ts_metadata")}
    assert indexes.get("ix_ts_metadata_cluster_org_guid") == ["cluster_id", "org_id", "ts_guid"]


def test_identity_lookup_uses_the_composite_index(in_memory_db):
    """Without it SQLite's no-stats heuristic picks ix_ts_metadata_cluster_id."""
    from sqlalchemy import text

    with Session(in_memory_db) as session:
        plan = " ".join(
            str(r)
            for r in session.exec(
                text(
                    "EXPLAIN QUERY PLAN SELECT ts_guid FROM ts_metadata "
                    "WHERE cluster_id = 'c1' AND org_id = 0 AND ts_guid = 'model-1'"
                )
            ).all()
        )
    assert "ix_ts_metadata_cluster_org_guid" in plan
    assert "COVERING INDEX" in plan.upper()


def test_missing_index_is_backfilled_on_an_existing_database(monkeypatch, tmp_path):
    """
    `create_all` never adds an index to a table that already exists, so an
    already-installed DB would otherwise never get it.
    """
    from sqlalchemy import create_engine as sa_create_engine
    from sqlalchemy import inspect, text

    import ts_admin.database as db_module

    db_file = tmp_path / "pre_existing.sqlite"
    engine = sa_create_engine(f"sqlite:///{db_file}", connect_args={"check_same_thread": False})
    monkeypatch.setattr(db_module, "get_engine", lambda: engine)

    # Stand up the schema, then drop the index to simulate a pre-S25 database.
    db_module.init_db()
    with engine.begin() as conn:
        conn.execute(text('DROP INDEX "ix_ts_metadata_cluster_org_guid"'))
    assert "ix_ts_metadata_cluster_org_guid" not in {i["name"] for i in inspect(engine).get_indexes("ts_metadata")}

    db_module.init_db()  # the backfill runs on every startup
    assert "ix_ts_metadata_cluster_org_guid" in {i["name"] for i in inspect(engine).get_indexes("ts_metadata")}
