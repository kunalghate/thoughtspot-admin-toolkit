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


def test_resolve_model_columns_handles_model_kind_alias_prefix():
    """MODEL TML keys columns off `model_tables[].alias` — the lowercased table name."""
    from ts_admin.services.lineage_service import _parse_physical_source, _resolve_model_columns

    # Real shape from a v10 cluster: alias-prefixed column_id, no table_paths.
    model_body = {
        "name": "Avnet",
        "model_tables": [
            {"name": "Dim_Date_Blitz", "alias": "dim_date_blitz", "fqn": "table-1"},
        ],
        "columns": [{"name": "Date Fk", "column_id": "dim_date_blitz::date_fk"}],
    }
    phys = _parse_physical_source(
        {
            "name": "Dim_Date_Blitz",
            "db_table": "DIM_DATE_BLITZ",
            "connection": {"name": "BLITZ_DEMOS", "fqn": "conn-9"},
            "columns": [{"name": "date_fk", "db_column_name": "DATE_FK"}],
        }
    )
    rows = _resolve_model_columns(
        guid="model-9",
        body=model_body,
        physical_by_guid={"table-1": phys},
        physical_by_name={"Dim_Date_Blitz": phys},
        cluster_id=CLUSTER_ID,
        org_id=0,
    )

    assert len(rows) == 1
    row = rows[0]
    assert (row.table_guid, row.table_column_name) == ("table-1", "date_fk")
    assert (row.db_table, row.db_column_name, row.connection_name) == ("DIM_DATE_BLITZ", "DATE_FK", "BLITZ_DEMOS")


def test_resolve_model_columns_resolves_alias_by_name_when_fqn_is_absent():
    """Without an fqn the lowercased alias must still find the physical table."""
    from ts_admin.services.lineage_service import _parse_physical_source, _resolve_model_columns

    model_body = {
        "name": "Avnet",
        "model_tables": [{"name": "Dim_Date_Blitz", "alias": "dim_date_blitz"}],
        "columns": [{"name": "Date Fk", "column_id": "dim_date_blitz::date_fk"}],
    }
    phys = _parse_physical_source(
        {
            "name": "Dim_Date_Blitz",
            "db_table": "DIM_DATE_BLITZ",
            "connection": {"name": "BLITZ_DEMOS", "fqn": "conn-9"},
            "columns": [{"name": "date_fk", "db_column_name": "DATE_FK"}],
        }
    )
    rows = _resolve_model_columns(
        guid="model-9",
        body=model_body,
        physical_by_guid={},
        physical_by_name={"Dim_Date_Blitz": phys, "dim_date_blitz": phys},
        cluster_id=CLUSTER_ID,
        org_id=0,
    )

    assert rows[0].db_column_name == "DATE_FK"


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


def _seed(engine, *, lb_modified: datetime | None = None):
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.models.cluster import Cluster
    from ts_admin.models.sync_log import SyncLog

    now = datetime.now(tz=timezone.utc)
    with Session(engine) as session:
        session.add(Cluster(id=CLUSTER_ID, name="Prod", url="https://p", username="a", auth_type="trusted"))
        # The SUCCESS metadata log is part of a healthy cache, not decoration:
        # `build_object_graph` fails closed without it (a truncated sync leaves
        # rows behind too, so rows alone certify nothing).
        session.add(
            SyncLog(
                cluster_id=CLUSTER_ID,
                org_id=0,
                entity_type="metadata",
                status="SUCCESS",
                record_count=4,
                synced_at=now,
            )
        )
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


async def test_build_column_map_reexports_changed_liveboard(monkeypatch, in_memory_db, patched_config):
    """
    The falsifier for the incremental watermark: a liveboard that genuinely
    changed since the last build MUST be re-exported.

    The timestamp has to be in the FUTURE, not `now` — `last_built` is the first
    build's `synced_at`, which is strictly later than any `modified_at` seeded at
    seed time, so `lb_modified=now` still reads as unchanged.
    """
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.services import lineage_service

    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _seed(in_memory_db, lb_modified=old)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported  # first build exports everything

    future = datetime.now(tz=timezone.utc) + timedelta(days=365)
    with Session(in_memory_db) as session:
        row = session.exec(select(CachedMetadata).where(CachedMetadata.ts_guid == "lb-1")).one()
        row.modified_at = future
        session.add(row)
        session.commit()

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported


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


async def test_total_liveboard_edge_loss_recovers_on_next_build(monkeypatch, in_memory_db, patched_config):
    """
    Pins the accidental self-heal for total lineage loss.

    Two independent mechanisms recover from this state today, and each one alone
    is sufficient on current code (verified by mutation, 2026-08-15):

    1. `max(CachedColumnLineage.synced_at)` goes NULL once every lineage row for
       the scope is deleted, which makes `_changed()` treat every liveboard as
       changed. This is accidental — it falls out of `_persist_column_map`
       delete-and-rebuilding the lineage table every run, and is named by no
       other test.
    2. The explicit `has_lb_edges` probe (`lineage_service.py:541,564`).

    Because they are redundant, this test only goes red when BOTH are gone. Any
    change that persists the liveboard watermark independently of the lineage
    rows (e.g. a dedicated build-timestamp row surviving the delete-and-rebuild)
    removes mechanism 1 silently — mechanism 2 must then stay, or be replaced
    explicitly.
    """
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    old = datetime(2020, 1, 1, tzinfo=timezone.utc)
    _seed(in_memory_db, lb_modified=old)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    with Session(in_memory_db) as session:
        session.exec(sql_delete(CachedDependency).where(CachedDependency.source_type == "LIVEBOARD"))
        session.exec(
            sql_delete(CachedColumnLineage).where(
                CachedColumnLineage.cluster_id == CLUSTER_ID,
                CachedColumnLineage.org_id == 0,
            )
        )
        session.commit()

    fake.exported.clear()
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())
    assert "lb-1" in fake.exported
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


async def test_graph_columns_for_physical_table_root(monkeypatch, in_memory_db, patched_config):
    """A DB_TABLE root is a lineage row's `table_guid`, never its `model_guid` —
    it must still get a column map (with the consuming model's "used by")."""
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="table-1", root_kind="table")
    cols = {c["model_column_name"]: c for c in graph["columns"]}
    assert set(cols) == {"Total Revenue", "Region"}  # formula columns have no table
    assert cols["Total Revenue"]["db_column_name"] == "REVENUE"
    assert cols["Total Revenue"]["table_guid"] == "table-1"
    assert cols["Total Revenue"]["used_by"][0]["guid"] == "lb-1"


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


# ── Un-exportable objects: a poisoned batch must not sink the whole pass ─────────
#
# `metadata/tml/export` is all-or-nothing per request: one object the server
# cannot serialize 500s the entire batch. That used to abort build_column_map,
# so a single broken object on a big cluster left it with NO connection edges
# and NO column map at all.


class _PoisonTMLClient(_FakeTMLClient):
    """Fails any batch containing `poison` wholesale — exactly like the real 500."""

    def __init__(self, poison: str):
        super().__init__()
        self.poison = poison
        self.calls: list[list[str]] = []

    async def tml_export(self, *, object_ids, edoc_format=None):
        from ts_admin.ts_client.exceptions import TSServerError

        self.calls.append(list(object_ids))
        if self.poison in object_ids:
            raise TSServerError(status_code=500, body='{"error":{"debug":["No value present"]}}')
        return await super().tml_export(object_ids=object_ids, edoc_format=edoc_format)


def _add_metadata(engine, guid: str, name: str, object_type: str) -> None:
    from ts_admin.models.cache.ts_metadata import CachedMetadata

    now = datetime.now(tz=timezone.utc)
    with Session(engine) as session:
        session.add(
            CachedMetadata(
                cluster_id=CLUSTER_ID,
                org_id=0,
                ts_guid=guid,
                name=name,
                object_type=object_type,
                owner_name="Alice",
                modified_at=now,
                synced_at=now,
            )
        )
        session.commit()


async def test_column_map_survives_an_unexportable_table(monkeypatch, in_memory_db, patched_config):
    """One table the server refuses to export is isolated; everything else still builds."""
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    _add_metadata(in_memory_db, "bad-table", "BROKEN", "ONE_TO_ONE_LOGICAL")
    fake = _PoisonTMLClient("bad-table")
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)

    written = await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    assert written > 0
    with Session(in_memory_db) as session:
        lineage = session.exec(select(CachedColumnLineage)).all()
        connects = session.exec(select(CachedDependency).where(CachedDependency.relation == "CONNECTS")).all()
    assert {r.model_column_name for r in lineage} == {"Total Revenue", "Region", "Margin %"}
    assert connects and connects[0].target_guid == "conn-1"
    # The bad object was bisected down to its own batch and skipped there.
    assert ["bad-table"] in fake.calls


async def test_unexportable_liveboard_keeps_its_existing_edges(monkeypatch, in_memory_db, patched_config):
    """A transient export failure must not delete edges nothing will rebuild."""
    from ts_admin.models.cache.ts_dependency import CachedDependency
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    healthy = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: healthy)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    poisoned = _PoisonTMLClient("lb-1")
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: poisoned)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job(), incremental=False)

    with Session(in_memory_db) as session:
        lb_edges = session.exec(select(CachedDependency).where(CachedDependency.source_type == "LIVEBOARD")).all()
    assert [e.target_guid for e in lb_edges] == ["model-1"]


async def test_export_bisect_isolates_a_rejected_identifier(monkeypatch):
    """A 400 ("Invalid parameter values: metadata_identifiers") is a bad object, not a bad request."""
    from ts_admin.services import lineage_service
    from ts_admin.ts_client.exceptions import TSInvalidParametersError

    class _RejectsOne:
        async def tml_export(self, *, object_ids, edoc_format=None):
            if "stale-guid" in object_ids:
                raise TSInvalidParametersError("Invalid parameter values: metadata_identifiers")
            return [{"info": {"id": g}, "edoc": {"guid": g}} for g in object_ids]

    failed: list[str] = []
    items = await lineage_service._export_tml_resilient(_RejectsOne(), ["a", "stale-guid", "b"], failed)

    assert failed == ["stale-guid"]
    assert {i["info"]["id"] for i in items} == {"a", "b"}


async def test_export_bisect_gives_up_once_the_failure_budget_is_spent(monkeypatch):
    """A cluster failing everything is not worth bisecting object by object."""
    from ts_admin.services import lineage_service
    from ts_admin.ts_client.exceptions import TSServerError

    class _AlwaysFails:
        def __init__(self):
            self.calls = 0

        async def tml_export(self, *, object_ids, edoc_format=None):
            self.calls += 1
            raise TSServerError(status_code=500, body="down")

    client = _AlwaysFails()
    monkeypatch.setattr(lineage_service, "TML_EXPORT_FAILURE_BUDGET", 2)
    failed: list[str] = []
    with pytest.raises(TSServerError):
        await lineage_service._export_tml_resilient(client, [f"g{i}" for i in range(50)], failed)
    assert len(failed) <= 2


# ── ThoughtSpot Views (TML `view` / AGGR_WORKSHEET) ──────────────────────────────
#
# A View is derived from another logical object, not from physical tables. It was
# classified as a physical source, which meant every View on a cluster produced no
# column rows at all AND registered a fake physical entry under its own name.
# These are built from the real TML shape ps-internal-prod returns.

_VIEW_TML = {
    "guid": "view-1",
    "view": {
        "name": "Test View",
        "tables": [{"id": "Sample Retail - WH", "name": "Sample Retail - WH", "fqn": "ws-1"}],
        "search_query": "[Region] [Sales Amt]",
        "view_columns": [
            {"name": "Region", "search_output_column": "Region"},
            {"name": "Total Sales Amt", "search_output_column": "Total Sales Amt"},
        ],
    },
}


def test_view_is_classified_as_a_model_not_a_physical_source():
    """A View has no connection and no db_table — parsing it as one poisoned the db layer."""
    from ts_admin.services import lineage_service

    kind, body = lineage_service._classify_edoc(_VIEW_TML)
    assert kind == "view"
    assert kind in lineage_service._MODEL_KINDS
    assert kind not in lineage_service._SOURCE_KINDS


def test_view_columns_resolve_to_their_upstream_object():
    from ts_admin.services import lineage_service

    _, body = lineage_service._classify_edoc(_VIEW_TML)
    rows = lineage_service._resolve_model_columns(
        guid="view-1",
        body=body,
        physical_by_guid={},
        physical_by_name={},
        cluster_id=CLUSTER_ID,
        org_id=0,
    )

    assert {r.model_column_name for r in rows} == {"Region", "Total Sales Amt"}
    region = next(r for r in rows if r.model_column_name == "Region")
    # One layer resolves: view column → upstream worksheet column. The db layer
    # stays blank because a View genuinely has none.
    assert (region.table_guid, region.table_column_name) == ("ws-1", "Region")
    assert (region.db_table, region.connection_name) == ("", "")


def test_view_with_several_sources_stops_at_the_column_name():
    """The TML does not say which source an output column came from — do not guess."""
    from ts_admin.services import lineage_service

    body = {
        "name": "Joined View",
        "tables": [{"name": "A", "fqn": "a-1"}, {"name": "B", "fqn": "b-1"}],
        "view_columns": [{"name": "Region", "search_output_column": "Region"}],
    }
    rows = lineage_service._resolve_model_columns(
        guid="view-2", body=body, physical_by_guid={}, physical_by_name={}, cluster_id=CLUSTER_ID, org_id=0
    )

    assert len(rows) == 1
    assert rows[0].table_column_name == "Region"
    assert rows[0].table_guid == ""


# ── Connections as a first-class root ────────────────────────────────────────────
#
# Connections are never CachedMetadata rows — they exist only as the target of a
# CONNECTS edge. The left list and the graph both read from metadata, so a
# connection was unreachable: it rendered in the graph but resolved to nothing,
# and clicking it silently did nothing.


async def test_topology_lists_connections_from_edges(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    topo = lineage_service.get_topology(cluster_id=CLUSTER_ID, org_id=0)
    assert [c["ts_guid"] for c in topo["connections"]] == ["conn-1"]
    assert topo["connections"][0]["node_type"] == "CONNECTION"
    # And it is not double-counted into the object groups.
    assert all(i["node_type"] != "CONNECTION" for i in topo["logical_tables"])


async def test_connection_can_be_a_lineage_root(monkeypatch, in_memory_db, patched_config):
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    fake = _FakeTMLClient()
    monkeypatch.setattr("ts_admin.ts_client.ThoughtSpotClient", lambda *a, **k: fake)
    await lineage_service.build_column_map(cluster_id=CLUSTER_ID, org_id=0, job_id=_make_job())

    graph = lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="conn-1", root_kind="connection")
    assert graph is not None
    assert (graph["root"]["name"], graph["root"]["node_type"]) == ("Snowflake Prod", "CONNECTION")
    # Its tables are its consumers, and a connection has no column map of its own.
    assert graph["consumer_totals"] == {"DB_TABLE": 1}
    assert "table-1" in {n["guid"] for n in graph["nodes"]}
    assert graph["columns"] == []


def test_a_guid_that_is_neither_object_nor_connection_is_still_404(in_memory_db, patched_config):
    """The connection fallback must not turn every unknown GUID into an empty graph."""
    from ts_admin.services import lineage_service

    _seed(in_memory_db)
    assert (
        lineage_service.get_lineage_graph(cluster_id=CLUSTER_ID, org_id=0, guid="no-such-guid", root_kind="connection")
        is None
    )
