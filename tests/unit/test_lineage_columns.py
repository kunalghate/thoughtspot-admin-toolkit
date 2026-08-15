"""
Phase 2 tests for the column map: TML parsers (the fidelity risk), the
build_column_map end-to-end (3-layer lineage + CONNECTS + liveboard USES edges +
per-column usage), the liveboard incremental-skip path, and the columns[]
population in the graph read.

Canned TML mirrors the authoritative structures from the TS docs (worksheet
column_id = "<table_path>::<column display name>", physical table columns with
db_column_name, liveboard.visualizations[].answer with fqn/search_query).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, create_engine, select

CLUSTER_ID = "c1"

# ── Canned TML edocs ─────────────────────────────────────────────────────────────

TABLE_EDOC = {
    "guid": "table-1",
    "table": {
        "name": "SALES",
        "db_table": "FACT_SALES",
        "connection": {"name": "Snowflake Prod", "fqn": "conn-1"},
        "columns": [
            {"name": "Revenue", "db_column_name": "REVENUE"},
            {"name": "Region", "db_column_name": "REGION"},
        ],
    },
}
MODEL_EDOC = {
    "guid": "model-1",
    "worksheet": {
        "name": "Sales Model",
        "tables": [{"name": "SALES", "fqn": "table-1"}],
        "table_paths": [{"id": "SALES_1", "table": "SALES", "join_path": [{"join": []}]}],
        "formulas": [{"id": "Formula_1", "name": "Margin %", "expr": "[Revenue] / 100"}],
        "worksheet_columns": [
            {"name": "Total Revenue", "column_id": "SALES_1::Revenue"},
            {"name": "Region", "column_id": "SALES_1::Region"},
            {"name": "Margin %", "formula_id": "Formula_1"},
        ],
    },
}
ANSWER_EDOC = {
    "guid": "answer-1",
    "answer": {
        "name": "Rev answer",
        "tables": [{"name": "Sales Model", "fqn": "model-1"}],
        "search_query": "[Total Revenue]",
        "answer_columns": [{"name": "Total Revenue"}],
        "table": {"table_columns": [{"column_id": "Total Revenue"}]},
    },
}
LIVEBOARD_EDOC = {
    "guid": "lb-1",
    "liveboard": {
        "name": "Sales LB",
        "visualizations": [
            {
                "id": "Viz_1",
                "answer": {
                    "name": "Rev by region",
                    "tables": [{"name": "Sales Model", "fqn": "model-1"}],
                    "search_query": "[Total Revenue] [Region]",
                    "answer_columns": [{"name": "Total Revenue"}, {"name": "Region"}],
                    "table": {"table_columns": [{"column_id": "Total Revenue"}, {"column_id": "Region"}]},
                },
            }
        ],
    },
}


# ── Pure parser tests (no DB, no client) ─────────────────────────────────────────


def test_resolve_model_columns_3_layer_chain():
    from ts_admin.services.lineage_service import _parse_physical_source, _resolve_model_columns

    phys = _parse_physical_source(TABLE_EDOC["table"])
    rows = _resolve_model_columns(
        guid="model-1",
        body=MODEL_EDOC["worksheet"],
        physical_by_guid={"table-1": phys},
        physical_by_name={"SALES": phys},
        cluster_id=CLUSTER_ID,
        org_id=0,
    )
    by_col = {r.model_column_name: r for r in rows}
    rev = by_col["Total Revenue"]
    assert rev.table_guid == "table-1"
    assert rev.table_column_name == "Revenue"
    assert rev.db_table == "FACT_SALES"
    assert rev.db_column_name == "REVENUE"
    assert rev.connection_name == "Snowflake Prod"
    assert rev.is_formula is False
    assert by_col["Region"].db_column_name == "REGION"
    # Formula column: flagged, with an intentionally empty physical chain.
    margin = by_col["Margin %"]
    assert margin.is_formula is True
    assert (margin.table_column_name, margin.db_table, margin.db_column_name) == ("", "", "")


def test_resolve_model_columns_tolerates_missing_source():
    """A formula/unknown-prefix column resolves with empty db layer, no crash."""
    from ts_admin.services.lineage_service import _resolve_model_columns

    body = {"worksheet_columns": [{"name": "Margin", "column_id": "formula_1"}]}
    rows = _resolve_model_columns(
        guid="m", body=body, physical_by_guid={}, physical_by_name={}, cluster_id=CLUSTER_ID, org_id=0
    )
    assert len(rows) == 1
    assert rows[0].model_column_name == "Margin"
    assert rows[0].db_column_name == ""
    # An unresolved column_id is NOT a formula — only formula_id marks one.
    assert rows[0].is_formula is False


def test_extract_col_usage_unions_three_sources():
    from ts_admin.services.lineage_service import _extract_col_usage_from_answer

    answer = LIVEBOARD_EDOC["liveboard"]["visualizations"][0]["answer"]
    model_guids, used = _extract_col_usage_from_answer(answer)
    assert model_guids == ["model-1"]
    assert used == {"Total Revenue", "Region"}


def test_load_edoc_parses_json_string_and_flags_inaccessible():
    from ts_admin.services.lineage_service import _load_edoc

    assert _load_edoc({"edoc": json.dumps(TABLE_EDOC)})["guid"] == "table-1"
    assert _load_edoc({"edoc": ""}) is None  # inaccessible stub
    assert _load_edoc({"info": {"name": "x"}}) is None


# ── Fixtures for the end-to-end build ────────────────────────────────────────────


@pytest.fixture
def in_memory_db(monkeypatch):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    import ts_admin.database as db_module

    monkeypatch.setattr(db_module, "get_engine", lambda: engine)
    db_module.init_db()
    return engine


@pytest.fixture
def patched_config(monkeypatch):
    from ts_admin.config import AppConfig, ClusterConfig
    from ts_admin.ts_client.models import AuthType

    cfg = ClusterConfig(id=CLUSTER_ID, name="Prod", url="https://p", username="admin", auth_type=AuthType.TRUSTED)
    config = AppConfig(clusters={CLUSTER_ID: cfg}, active_cluster_id=CLUSTER_ID)
    monkeypatch.setattr("ts_admin.config.load_config", lambda: config)
    monkeypatch.setattr("ts_admin.config.ClusterConfig.build_auth_strategy", lambda self, org_id=None: None)
    return config


class _FakeTMLClient:
    def __init__(self):
        self.exported: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def list_connections(self):
        return [{"id": "conn-1", "name": "Snowflake Prod"}]

    async def tml_export(self, *, object_ids, edoc_format=None):
        self.exported.extend(object_ids)
        by_guid = {"table-1": TABLE_EDOC, "model-1": MODEL_EDOC, "lb-1": LIVEBOARD_EDOC, "answer-1": ANSWER_EDOC}
        out = []
        for guid in object_ids:
            edoc = by_guid.get(guid)
            if edoc:
                out.append({"info": {"id": guid, "name": edoc.get("guid")}, "edoc": edoc})
        return out

    async def search_dependents(self, *, object_ids, object_type, batch_size=100):
        # Object-tier sweep: the model depends on the table (model USES table).
        return {"table-1": [{"id": "model-1", "name": "Sales Model", "type": "WORKSHEET"}]}


class _FakeTMLClientUnresolvedConnection(_FakeTMLClient):
    """Connection GUID resolvable from neither the TML fqn nor the connections list."""

    async def list_connections(self):
        return []

    async def tml_export(self, *, object_ids, edoc_format=None):
        items = await super().tml_export(object_ids=object_ids, edoc_format=edoc_format)
        for item in items:
            if item["info"]["id"] == "table-1":
                edoc = json.loads(json.dumps(item["edoc"]))
                edoc["table"]["connection"] = {"name": "Snowflake Prod"}
                item["edoc"] = edoc
        return items


def _seed(engine, *, lb_modified: datetime | None = None):
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.models.cluster import Cluster

    now = datetime.now(tz=timezone.utc)
    with Session(engine) as session:
        session.add(Cluster(id=CLUSTER_ID, name="Prod", url="https://p", username="a", auth_type="trusted"))
        rows = [
            ("table-1", "SALES", "ONE_TO_ONE_LOGICAL", now),
            ("model-1", "Sales Model", "WORKSHEET", now),
            ("lb-1", "Sales LB", "LIVEBOARD", lb_modified or now),
            ("answer-1", "Rev answer", "ANSWER", now),
        ]
        for guid, name, otype, modified in rows:
            session.add(
                CachedMetadata(
                    cluster_id=CLUSTER_ID,
                    org_id=0,
                    ts_guid=guid,
                    name=name,
                    object_type=otype,
                    owner_name="Alice",
                    modified_at=modified,
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


# ── End-to-end build ─────────────────────────────────────────────────────────────


async def test_build_column_map_end_to_end(monkeypatch, in_memory_db, patched_config):
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    written = await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert written > 0

    with Session(in_memory_db) as session:
        lineage = session.exec(select(CachedColumnLineage)).all()
        edges = session.exec(select(CachedDependency)).all()
        usage = session.exec(select(CachedColumnUsage)).all()

    # 3-layer lineage for the model (including the flagged formula column).
    assert {r.model_column_name for r in lineage} == {"Total Revenue", "Region", "Margin %"}
    rev = next(r for r in lineage if r.model_column_name == "Total Revenue")
    assert (rev.db_table, rev.db_column_name, rev.connection_name) == ("FACT_SALES", "REVENUE", "Snowflake Prod")
    margin = next(r for r in lineage if r.model_column_name == "Margin %")
    assert margin.is_formula is True and margin.db_column_name == ""

    # CONNECTS edge (table→connection) and liveboard USES edge (lb→model).
    connects = [e for e in edges if e.relation == "CONNECTS"]
    assert connects and connects[0].source_guid == "table-1" and connects[0].target_guid == "conn-1"
    lb_uses = [e for e in edges if e.relation == "USES" and e.source_type == "LIVEBOARD"]
    assert lb_uses and lb_uses[0].source_guid == "lb-1" and lb_uses[0].target_guid == "model-1"

    # Per-column usage attributed to the liveboard.
    assert {u.model_column_name for u in usage} == {"Total Revenue", "Region"}
    assert all(u.consumer_guid == "lb-1" and u.consumer_type == "LIVEBOARD" for u in usage)


async def test_build_column_map_incremental_skips_unchanged_liveboards(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    # Liveboard last modified long ago → unchanged relative to the first build.
    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _seed(in_memory_db, lb_modified=old)

    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported  # first build exports everything

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    # Unchanged liveboard skipped; logical tables always re-exported for resolution.
    assert "lb-1" not in fake.exported
    assert "model-1" in fake.exported and "table-1" in fake.exported


async def test_object_graph_rebuild_preserves_phase2_edges(monkeypatch, in_memory_db, patched_config):
    """
    Regression: Phase 1's delete-before-insert must not wipe the edges only
    Phase 2 produces (liveboard USES + CONNECTS) — the incremental liveboard
    pass skips unchanged liveboards and would never re-create them.
    """
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    await lineage_service.build_object_graph(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        edges = session.exec(select(CachedDependency)).all()
    lb_uses = [e for e in edges if e.relation == "USES" and e.source_type == "LIVEBOARD"]
    connects = [e for e in edges if e.relation == "CONNECTS"]
    assert lb_uses and lb_uses[0].source_guid == "lb-1"
    assert connects and connects[0].target_guid == "conn-1"
    # And the object tier itself landed (model USES table from the sweep).
    assert any(e.source_guid == "model-1" and e.target_guid == "table-1" for e in edges)


async def test_column_map_self_heals_missing_liveboard_edges(monkeypatch, in_memory_db, patched_config):
    """
    Regression: a DB whose liveboard edges were wiped (pre-fix object-tier
    build) recovers on the next column build even when no liveboard changed.
    """
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _seed(in_memory_db, lb_modified=old)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    with Session(in_memory_db) as session:
        session.exec(sql_delete(CachedDependency).where(CachedDependency.source_type == "LIVEBOARD"))
        session.commit()

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported  # unchanged, but re-exported to heal
    with Session(in_memory_db) as session:
        lb_uses = session.exec(select(CachedDependency).where(CachedDependency.source_type == "LIVEBOARD")).all()
    assert lb_uses and lb_uses[0].target_guid == "model-1"


async def test_column_map_purges_deleted_liveboards(monkeypatch, in_memory_db, patched_config):
    """A liveboard deleted in TS loses its USES edges + usage rows on the next build."""
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        session.exec(sql_delete(CachedMetadata).where(CachedMetadata.ts_guid == "lb-1"))
        session.commit()

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    with Session(in_memory_db) as session:
        lb_edges = session.exec(select(CachedDependency).where(CachedDependency.source_type == "LIVEBOARD")).all()
        lb_usage = session.exec(select(CachedColumnUsage).where(CachedColumnUsage.consumer_type == "LIVEBOARD")).all()
    assert lb_edges == []
    assert lb_usage == []


async def test_column_map_purges_edges_to_deleted_target(monkeypatch, in_memory_db, patched_config):
    """
    A model deleted in TS must not survive as a ghost node reachable from an
    UNCHANGED liveboard. The liveboard is skipped by the incremental pass, so
    nothing rebuilds its edge — only a target-keyed purge can remove it.
    """
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _seed(in_memory_db, lb_modified=old)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        assert session.exec(
            select(CachedDependency).where(
                CachedDependency.source_guid == "lb-1", CachedDependency.target_guid == "model-1"
            )
        ).all()
        assert session.exec(select(CachedColumnUsage).where(CachedColumnUsage.model_guid == "model-1")).all()
        session.exec(sql_delete(CachedMetadata).where(CachedMetadata.ts_guid == "model-1"))
        session.commit()

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" not in fake.exported  # unchanged liveboard: nothing rebuilds its edge

    with Session(in_memory_db) as session:
        orphan_edges = session.exec(
            select(CachedDependency).where(
                CachedDependency.relation == "USES", CachedDependency.target_guid == "model-1"
            )
        ).all()
        orphan_usage = session.exec(select(CachedColumnUsage).where(CachedColumnUsage.model_guid == "model-1")).all()
    assert orphan_edges == []
    assert orphan_usage == []

    # Feature-level closure: the ghost node is gone from the user-visible graph.
    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="lb-1", root_kind="liveboard")
    assert not [n for n in graph["nodes"] if n["guid"] == "model-1"]


async def test_orphan_purge_spares_connects_edges(monkeypatch, in_memory_db, patched_config):
    """
    Regression guard against a future over-broad purge: CONNECTS targets are
    connection GUIDs (often synthetic `conn::<name>`) that search_metadata never
    syncs, so they are PERMANENTLY absent from CachedMetadata. This passes
    pre-fix by design — it exists to fail loudly if the purge ever widens beyond
    relation == "USES".
    """
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        session.exec(sql_delete(CachedMetadata).where(CachedMetadata.ts_guid == "model-1"))
        session.commit()

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    with Session(in_memory_db) as session:
        connects = session.exec(select(CachedDependency).where(CachedDependency.relation == "CONNECTS")).all()
    assert [e.target_guid for e in connects] == ["conn-1"]

    # Same guard for the synthetic `conn::<name>` fallback (no connection GUID
    # resolvable from the TML fqn or the connections list).
    fake_no_guid = _FakeTMLClientUnresolvedConnection()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake_no_guid)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    with Session(in_memory_db) as session:
        connects = session.exec(select(CachedDependency).where(CachedDependency.relation == "CONNECTS")).all()
    assert [e.target_guid for e in connects] == ["conn::Snowflake Prod"]


async def test_orphan_purge_skipped_when_metadata_cache_empty(monkeypatch, in_memory_db, patched_config):
    """An unsynced (empty) metadata cache must not be read as 'everything was deleted'."""
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        before_edges = len(session.exec(select(CachedDependency)).all())
        before_usage = len(session.exec(select(CachedColumnUsage)).all())
        session.exec(sql_delete(CachedMetadata))
        session.commit()
    assert before_edges and before_usage

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    with Session(in_memory_db) as session:
        after_edges = len(session.exec(select(CachedDependency)).all())
        after_usage = len(session.exec(select(CachedColumnUsage)).all())
    assert (after_edges, after_usage) == (before_edges, before_usage)


async def test_orphan_purge_spares_edges_rebuilt_this_run(monkeypatch, in_memory_db, patched_config):
    """
    Pins delete-before-insert ordering: a CHANGED liveboard is re-exported in the
    same run, so its edge must survive even though the target model is missing
    from the metadata cache (it lands with an empty target_name).
    """
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    # modified_at AFTER the build we are about to run → the incremental pass
    # always sees the liveboard as changed and re-exports it.
    _seed(in_memory_db, lb_modified=datetime.now(tz=timezone.utc) + timedelta(days=1))
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        session.exec(sql_delete(CachedMetadata).where(CachedMetadata.ts_guid == "model-1"))
        session.commit()

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported
    with Session(in_memory_db) as session:
        edges = session.exec(
            select(CachedDependency).where(
                CachedDependency.source_guid == "lb-1", CachedDependency.target_guid == "model-1"
            )
        ).all()
    assert len(edges) == 1
    assert edges[0].target_name == ""


async def test_graph_columns_populated_after_build(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    # Model root: columns carry the 3-layer chain + "used by" consumers.
    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="model-1", root_kind="model")
    cols = {c["model_column_name"]: c for c in graph["columns"]}
    assert set(cols) == {"Total Revenue", "Region", "Margin %"}
    assert cols["Total Revenue"]["db_column_name"] == "REVENUE"
    assert cols["Total Revenue"]["is_formula"] is False
    assert cols["Total Revenue"]["used_by"][0]["guid"] == "lb-1"
    assert cols["Margin %"]["is_formula"] is True

    # Liveboard root: the columns IT uses, resolved through the model.
    lb_graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="lb-1", root_kind="liveboard")
    lb_cols = {c["model_column_name"]: c for c in lb_graph["columns"]}
    assert set(lb_cols) == {"Total Revenue", "Region"}
    assert lb_cols["Region"]["db_column_name"] == "REGION"


# ── Phase 3: lazy answer indexing + debug ────────────────────────────────────────


async def test_index_answer_is_lazy_and_memoized(monkeypatch, in_memory_db, patched_config):
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    n1 = await lineage_service.index_answer(cluster_id=CLUSTER_ID, org_id=0, guid="answer-1")
    assert n1 == 1  # one used column → one usage row
    assert fake.exported == ["answer-1"]

    with Session(in_memory_db) as session:
        usage = session.exec(select(CachedColumnUsage).where(CachedColumnUsage.consumer_guid == "answer-1")).all()
    assert usage[0].consumer_type == "ANSWER"
    assert usage[0].model_guid == "model-1"

    # Second call is memoized: no re-export, no new rows.
    fake.exported.clear()
    n2 = await lineage_service.index_answer(cluster_id=CLUSTER_ID, org_id=0, guid="answer-1")
    assert n2 == 0
    assert fake.exported == []


async def test_answer_columns_show_after_lazy_index(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    # Column map first (builds model lineage), then lazily index the answer.
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    await lineage_service.index_answer(cluster_id=CLUSTER_ID, org_id=0, guid="answer-1")

    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="answer-1", root_kind="answer")
    cols = {c["model_column_name"]: c for c in graph["columns"]}
    assert "Total Revenue" in cols
    assert cols["Total Revenue"]["db_column_name"] == "REVENUE"  # resolved through the model


async def test_debug_tml_reports_kind_and_parsed(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    dbg = await lineage_service.debug_tml(cluster_id=CLUSTER_ID, org_id=0, guid="model-1")
    assert dbg["accessible"] is True
    assert dbg["kind"] == "worksheet"
    assert "worksheet" in dbg["edoc_keys"]
    assert any(row["model_column_name"] == "Total Revenue" for row in dbg["parsed"])
