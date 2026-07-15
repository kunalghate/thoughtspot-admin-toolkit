"""
lineage_service — builds and reads the Relationship Visualizer's lineage graph.

Two responsibilities, cleanly split by cost:

  BUILD (async, hits the TS API — behind an explicit sync gate, never auto-run):
    build_object_graph  — Phase 1: the cheap object tier. A batched dependency
                          sweep (metadata/search, NO TML) over the LOGICAL_TABLE
                          universe yields table→model and model→answer edges.
    build_column_map    — Phase 2 (added later): the 3-layer column map +
                          connection / liveboard edges from LOGICAL_TABLE +
                          LIVEBOARD TML.

  READ (sync, SQLite only — 0 API calls, react-query cached on the client):
    get_topology        — the left-list universe (logical tables / answers /
                          liveboards) from CachedMetadata.
    get_lineage_graph   — a neighborhood-scoped graph rooted at one object,
                          assembled by following indexed ts_dependencies edges.
    get_consumers       — paginated full consumer list (feeds the fan-out drawer).

Everything is cluster + org scoped. The build writes a "dependencies" SyncLog so
the existing staleness UI works unchanged.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from sqlmodel import col, func, select
from sqlmodel import delete as sql_delete

from ts_admin.database import get_session
from ts_admin.models.cache.ts_dependency import CachedDependency
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.services.job_service import mark_complete, mark_running, update_progress

logger = logging.getLogger(__name__)

# ── Type / layer mapping ────────────────────────────────────────────────────────
#
# CachedMetadata stores TS cache subtypes; the lineage graph uses six coarse node
# types. Map cache subtypes (and the API's dependent-object type variants) onto
# them. Physical tables (ONE_TO_ONE_LOGICAL) become DB_TABLE; worksheets/aggr
# worksheets become MODEL; SQL views / user-defined sources stay LOGICAL_TABLE.

_NODE_TYPE_BY_OBJECT_TYPE: dict[str, str] = {
    "LIVEBOARD": "LIVEBOARD",
    "PINBOARD_ANSWER_BOOK": "LIVEBOARD",
    "ANSWER": "ANSWER",
    "QUESTION_ANSWER_BOOK": "ANSWER",
    "WORKSHEET": "MODEL",
    "AGGR_WORKSHEET": "MODEL",
    "MODEL": "MODEL",
    "ONE_TO_ONE_LOGICAL": "DB_TABLE",
    "TABLE": "DB_TABLE",
    "SQL_VIEW": "LOGICAL_TABLE",
    "USER_DEFINED": "LOGICAL_TABLE",
    "VIEW": "LOGICAL_TABLE",
    "LOGICAL_TABLE": "LOGICAL_TABLE",
    "CONNECTION": "CONNECTION",
}

# Left-list subtype filter labels (AdminOps parity: Model / Table / Dataset / View).
_SUBTYPE_LABEL: dict[str, str] = {
    "WORKSHEET": "Model",
    "AGGR_WORKSHEET": "Model",
    "MODEL": "Model",
    "ONE_TO_ONE_LOGICAL": "Table",
    "TABLE": "Table",
    "USER_DEFINED": "Dataset",
    "SQL_VIEW": "View",
    "VIEW": "View",
    "LOGICAL_TABLE": "Table",
}

# L→R pipeline layer per node type (frontend lays out x by layer, y by index).
_LAYER_BY_NODE_TYPE: dict[str, int] = {
    "CONNECTION": 0,
    "DB_TABLE": 1,
    "LOGICAL_TABLE": 2,
    "MODEL": 3,
    "ANSWER": 4,
    "LIVEBOARD": 4,
}

# CachedMetadata object_types that make up the "logical tables" universe we sweep
# for dependents (everything that is neither an Answer nor a Liveboard).
LOGICAL_TABLE_TYPES: frozenset[str] = frozenset(
    {"WORKSHEET", "AGGR_WORKSHEET", "ONE_TO_ONE_LOGICAL", "SQL_VIEW", "USER_DEFINED", "LOGICAL_TABLE"}
)

# How many consumer nodes a graph response embeds before collapsing to a count
# node + drawer. Keeps the payload (and React Flow render) bounded.
CONSUMER_NODE_CAP = 50
# Bound the downstream impact BFS so a pathological graph can't run away.
IMPACT_BFS_CAP = 5000
# Concurrent metadata/search calls during the crawl (client retry handles 429s).
CRAWL_CONCURRENCY = 5


def _node_type(object_type: str | None) -> str:
    return _NODE_TYPE_BY_OBJECT_TYPE.get((object_type or "").upper(), "LOGICAL_TABLE")


def _subtype_label(object_type: str | None) -> str:
    return _SUBTYPE_LABEL.get((object_type or "").upper(), "Table")


def _layer(node_type: str) -> int:
    return _LAYER_BY_NODE_TYPE.get(node_type, 2)


def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


# ── BUILD: Phase 1 object tier (batched dependency sweep, no TML) ───────────────


async def build_object_graph(*, cluster_id: str, org_id: int, job_id: str, finalize: bool = True) -> int:
    """
    Rebuild the object-edge tier (table→model, model→answer) for one cluster+org
    from a batched metadata/search dependency sweep. Delete-before-insert so
    objects deleted in TS since the last build don't linger. Returns edge count.

    Connection→table and model→liveboard edges are NOT produced here — the
    dependency API does not reliably surface a liveboard's model links (they live
    in embedded-answer FQNs). Those arrive in Phase 2's TML pass.

    `finalize=True` marks the job COMPLETE. The dependencies sync handler passes
    `finalize=False` when a Phase 2 column pass follows, so the graph is queryable
    (SyncLog written, edges committed) while the longer TML tail runs, and the job
    is only marked complete once everything lands.
    """
    from ts_admin.services.archiver_service import _get_cluster
    from ts_admin.ts_client import ThoughtSpotClient

    mark_running(job_id, total=0)

    # 1. Read the GUID universe (cluster + org scoped) from CachedMetadata.
    with get_session() as session:
        meta_rows = session.exec(
            select(
                CachedMetadata.ts_guid,
                CachedMetadata.name,
                CachedMetadata.object_type,
            ).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
            )
        ).all()

    if not meta_rows:
        # Lineage is derived from the metadata cache — refuse to build on an empty
        # one rather than write a misleading empty graph.
        raise ValueError("No cached metadata for this cluster/org — sync metadata first, then build lineage.")

    # guid → (name, object_type) for resolving both edge endpoints.
    meta_by_guid: dict[str, tuple[str, str]] = {r[0]: (r[1], r[2]) for r in meta_rows}
    table_guids = [r[0] for r in meta_rows if (r[2] or "").upper() in LOGICAL_TABLE_TYPES]

    if not table_guids:
        # Metadata exists but no logical tables — nothing to trace. Still a valid
        # (empty) build; record it so the UI shows "synced, no lineage".
        edges: list[CachedDependency] = []
    else:
        cluster = _get_cluster(cluster_id)
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            dependents = await _sweep_dependents(client, table_guids, job_id)
        edges = _edges_from_dependents(dependents, meta_by_guid, cluster_id, org_id)

    # 2. Delete-before-insert the whole edge set for this cluster+org.
    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.exec(
            sql_delete(CachedDependency).where(
                CachedDependency.cluster_id == cluster_id,
                CachedDependency.org_id == org_id,
            )
        )
        for chunk in _chunks(edges, 500):
            for edge in chunk:
                edge.synced_at = now
                session.add(edge)
            session.commit()
        session.commit()

    _write_dependencies_sync_log(cluster_id, org_id, record_count=len(edges))
    if finalize:
        mark_complete(job_id, {"entity_type": "dependencies", "record_count": len(edges)})
    logger.info("Built %d object-graph edges for cluster=%s org=%s", len(edges), cluster_id, org_id)
    return len(edges)


async def _sweep_dependents(client, table_guids: list[str], job_id: str) -> dict[str, list[dict]]:
    """Batched, bounded-concurrency dependency sweep over the logical-table universe."""
    chunks = list(_chunks(table_guids, 100))
    sem = asyncio.Semaphore(CRAWL_CONCURRENCY)
    merged: dict[str, list[dict]] = {}
    done = 0

    async def _one(chunk: list[str]) -> dict[str, list[dict]]:
        async with sem:
            return await client.search_dependents(object_ids=chunk, object_type="LOGICAL_TABLE", batch_size=100)

    tasks = [asyncio.create_task(_one(c)) for c in chunks]
    for task in asyncio.as_completed(tasks):
        result = await task
        merged.update(result)
        done += len(result)  # count objects swept, not chunks (consistent with the column pass)
        update_progress(job_id, done)
    return merged


def _edges_from_dependents(
    dependents: dict[str, list[dict]],
    meta_by_guid: dict[str, tuple[str, str]],
    cluster_id: str,
    org_id: int,
) -> list[CachedDependency]:
    """
    Turn { producer_guid → [dependent objects] } into de-duplicated USES edges.

    Each dependent D of producer P means "D USES P": D is the consumer/downstream
    (source), P is the producer/upstream (target). Liveboard dependents are
    skipped here — their authoritative model links come from Phase 2's TML pass.
    """
    seen: set[tuple[str, str]] = set()
    edges: list[CachedDependency] = []

    for producer_guid, deps in dependents.items():
        p_meta = meta_by_guid.get(producer_guid)
        if not p_meta:
            continue
        p_name, p_type = p_meta
        p_node_type = _node_type(p_type)

        for dep in deps:
            dep_guid = dep.get("id") or dep.get("identifier") or dep.get("guid") or ""
            if not dep_guid or dep_guid == producer_guid:
                continue
            d_meta = meta_by_guid.get(dep_guid)
            if d_meta:
                d_name, d_type = d_meta
                d_node_type = _node_type(d_type)
            else:
                d_name = dep.get("name", "")
                d_node_type = _node_type(dep.get("type"))

            # Liveboard object edges are deferred to Phase 2 (TML) for fidelity.
            if d_node_type == "LIVEBOARD":
                continue

            key = (dep_guid, producer_guid)
            if key in seen:
                continue
            seen.add(key)
            edges.append(
                CachedDependency(
                    cluster_id=cluster_id,
                    org_id=org_id,
                    source_guid=dep_guid,
                    source_type=d_node_type,
                    source_name=d_name,
                    target_guid=producer_guid,
                    target_type=p_node_type,
                    target_name=p_name,
                    relation="USES",
                )
            )
    return edges


def _write_dependencies_sync_log(cluster_id: str, org_id: int, *, record_count: int) -> None:
    """Upsert the 'dependencies' SyncLog row (mirrors sync_service._write_sync_log)."""
    from ts_admin.models.sync_log import SyncLog

    with get_session() as session:
        existing = session.exec(
            select(SyncLog).where(
                SyncLog.cluster_id == cluster_id,
                SyncLog.org_id == org_id,
                SyncLog.entity_type == "dependencies",
            )
        ).first()
        if existing:
            existing.synced_at = datetime.now(timezone.utc)
            existing.record_count = record_count
            existing.status = "SUCCESS"
            existing.error = None
            session.add(existing)
        else:
            session.add(
                SyncLog(
                    cluster_id=cluster_id,
                    org_id=org_id,
                    entity_type="dependencies",
                    record_count=record_count,
                    status="SUCCESS",
                )
            )
        session.commit()


# ── BUILD: Phase 2 column map + connection / liveboard edges (TML) ──────────────
#
# The 3-layer column map and the two edge kinds the dependency API can't supply
# come from LOGICAL_TABLE + LIVEBOARD TML (JSON edoc). Parsers are pure functions
# (unit-tested against canned TML) so parser fidelity — THE risk — is exercisable
# without a live cluster; the Phase 3 debug endpoint validates against real data.

_MODEL_KINDS = ("worksheet", "model")
_SOURCE_KINDS = ("table", "view", "sql_view")


def _load_edoc(item: dict) -> dict | None:
    """Return the parsed TML dict for an export item, or None if inaccessible."""
    edoc = item.get("edoc")
    if isinstance(edoc, dict):
        return edoc
    if isinstance(edoc, str) and edoc.strip():
        import json

        try:
            return json.loads(edoc)
        except json.JSONDecodeError:
            return None
    return None


def _classify_edoc(edoc: dict) -> tuple[str, dict]:
    """(kind, body) for a TML edoc — the top-level key is the discriminator."""
    for key in (*_MODEL_KINDS, *_SOURCE_KINDS, "answer", "liveboard"):
        body = edoc.get(key)
        if isinstance(body, dict):
            return key, body
    return "", {}


def _parse_physical_source(body: dict) -> dict:
    """
    Parse a physical table / view TML body into the db layer:
    {db_table, connection_name, connection_guid, columns: {display_name → db_column}}.
    The column display `name` is what connected models reference in `column_id`.
    """
    conn = body.get("connection") or {}
    columns: dict[str, str] = {}
    for c in body.get("columns", []) or []:
        display = c.get("name")
        if display:
            columns[display] = c.get("db_column_name", "") or ""
    return {
        "db_table": body.get("db_table") or body.get("name", "") or "",
        "connection_name": conn.get("name", "") or "",
        "connection_guid": conn.get("fqn", "") or "",
        "columns": columns,
    }


def _model_alias_map(body: dict) -> dict[str, tuple[str, str]]:
    """
    Map every `column_id` prefix a model column can use → (table_name, table_guid).

    A prefix may be a table_paths id (worksheet), a model_tables id, or a table
    name directly. `tables`/`model_tables[].fqn` supplies the source GUID.
    """
    alias: dict[str, tuple[str, str]] = {}
    name_to_fqn: dict[str, str] = {}
    for tbl in (body.get("tables") or body.get("model_tables") or []):
        name = tbl.get("name") or tbl.get("id")
        fqn = tbl.get("fqn", "") or ""
        if not name:
            continue
        name_to_fqn[name] = fqn
        alias[name] = (name, fqn)
        if tbl.get("id"):
            alias[tbl["id"]] = (name, fqn)
    for path in body.get("table_paths", []) or []:
        pid, tname = path.get("id"), path.get("table")
        if pid and tname:
            alias[pid] = (tname, name_to_fqn.get(tname, ""))
    return alias


def _resolve_model_columns(
    *,
    guid: str,
    body: dict,
    physical_by_guid: dict[str, dict],
    physical_by_name: dict[str, dict],
    cluster_id: str,
    org_id: int,
):
    """
    3-layer resolution for one worksheet/model: model_column → table_column →
    db_column, joining source columns via the (already-exported) physical tables.
    """
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage

    alias = _model_alias_map(body)
    cols = body.get("worksheet_columns") or body.get("columns") or []
    rows: list[CachedColumnLineage] = []
    for c in cols:
        model_col = c.get("name")
        if not model_col:
            continue
        column_id = c.get("column_id", "") or ""
        table_name = table_guid = table_col = ""
        if "::" in column_id:
            prefix, table_col = column_id.split("::", 1)
            table_name, table_guid = alias.get(prefix, (prefix, ""))

        phys = None
        if table_guid:
            phys = physical_by_guid.get(table_guid)
        if phys is None and table_name:
            phys = physical_by_name.get(table_name)

        rows.append(
            CachedColumnLineage(
                cluster_id=cluster_id,
                org_id=org_id,
                model_guid=guid,
                model_column_name=model_col,
                table_guid=table_guid,
                table_column_name=table_col,
                db_table=phys["db_table"] if phys else "",
                db_column_name=(phys["columns"].get(table_col, "") if phys else ""),
                connection_name=phys["connection_name"] if phys else "",
            )
        )
    return rows


def _search_query_columns(search_query: str) -> set[str]:
    """Pull `[Column]` tokens out of a search_query string (one of 3 usage sources)."""
    import re

    if not search_query:
        return set()
    return {tok.strip() for tok in re.findall(r"\[([^\]]+)\]", search_query) if tok.strip()}


def _extract_col_usage_from_answer(answer: dict) -> tuple[list[str], set[str]]:
    """
    From an answer body (standalone or liveboard-embedded), return
    (referenced_model_guids, used_column_names) — the union of three sources:
    answer_columns, table.table_columns[].column_id, and search_query tokens.
    """
    model_guids = [t.get("fqn") for t in (answer.get("tables") or []) if t.get("fqn")]
    used: set[str] = set()
    for c in answer.get("answer_columns", []) or []:
        if c.get("name"):
            used.add(c["name"])
    table = answer.get("table") or {}
    for c in table.get("table_columns", []) or []:
        cid = c.get("column_id")
        if cid:
            used.add(cid)  # for answers, column_id is the display name
    used |= _search_query_columns(answer.get("search_query", "") or "")
    return model_guids, used


def _embedded_answers(liveboard_body: dict):
    """Yield each visualization's embedded answer body from a liveboard TML."""
    for viz in liveboard_body.get("visualizations", []) or []:
        answer = viz.get("answer")
        if isinstance(answer, dict):
            yield answer


async def build_column_map(*, cluster_id: str, org_id: int, job_id: str, incremental: bool = True) -> int:
    """
    Phase 2: build the 3-layer column map (LOGICAL_TABLE TML) plus the two edge
    kinds only TML supplies — table→connection (CONNECTS) and liveboard→model
    (USES) — from LIVEBOARD TML.

    Logical tables are always fully exported (they're the minority set and a
    model's column resolution needs its source tables present in the same batch).
    Liveboards are exported incrementally: unchanged ones (modified_at ≤ the last
    column build) keep their existing usage rows + edges. Returns rows written.
    """
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.services.archiver_service import _get_cluster
    from ts_admin.ts_client import ThoughtSpotClient

    # 1. Universe + last-build timestamp (for liveboard incrementality).
    with get_session() as session:
        meta_rows = session.exec(
            select(
                CachedMetadata.ts_guid,
                CachedMetadata.name,
                CachedMetadata.object_type,
                CachedMetadata.modified_at,
            ).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
            )
        ).all()
        last_built = session.exec(
            select(func.max(CachedColumnLineage.synced_at)).where(
                CachedColumnLineage.cluster_id == cluster_id,
                CachedColumnLineage.org_id == org_id,
            )
        ).first()

    meta_by_guid = {r[0]: (r[1], r[2]) for r in meta_rows}
    table_guids = [r[0] for r in meta_rows if (r[2] or "").upper() in LOGICAL_TABLE_TYPES]
    all_liveboards = [(r[0], r[3]) for r in meta_rows if _node_type(r[2]) == "LIVEBOARD"]

    def _changed(modified_at) -> bool:
        return not incremental or last_built is None or modified_at is None or modified_at > last_built

    lb_guids = [guid for guid, modified_at in all_liveboards if _changed(modified_at)]

    if not table_guids and not lb_guids:
        return 0

    cluster = _get_cluster(cluster_id)
    physical_by_guid: dict[str, dict] = {}
    physical_by_name: dict[str, dict] = {}
    model_specs: list[tuple[str, dict]] = []  # (guid, body)
    connect_edges: dict[str, dict] = {}       # table_guid → edge fields
    lineage_rows: list = []
    usage_rows: list = []
    lb_uses_edges: list = []
    inaccessible = 0
    progress = 0

    async with ThoughtSpotClient(
        url=cluster.url,
        auth=cluster.build_auth_strategy(org_id=org_id),
    ) as client:
        conn_name_to_guid = {c["name"]: c["id"] for c in await client.list_connections() if c.get("name")}

        # 2a. Logical tables → physical summaries + model specs (parse, don't hoard raw TML).
        for chunk in _chunks(table_guids, 50):
            items = await client.tml_export(object_ids=chunk, edoc_format="JSON")
            for item in items:
                edoc = _load_edoc(item)
                if edoc is None:
                    inaccessible += 1
                    continue
                guid = (edoc.get("guid") or (item.get("info") or {}).get("id") or "")
                kind, body = _classify_edoc(edoc)
                if kind in _SOURCE_KINDS:
                    summary = _parse_physical_source(body)
                    if guid:
                        physical_by_guid[guid] = summary
                    if body.get("name"):
                        physical_by_name[body["name"]] = summary
                    conn_guid = summary["connection_guid"] or conn_name_to_guid.get(summary["connection_name"], "")
                    if guid and summary["connection_name"]:
                        connect_edges[guid] = {
                            "source_guid": guid,
                            "source_type": "DB_TABLE",
                            "source_name": meta_by_guid.get(guid, (body.get("name", ""), ""))[0],
                            "target_guid": conn_guid or f"conn::{summary['connection_name']}",
                            "target_type": "CONNECTION",
                            "target_name": summary["connection_name"],
                            "relation": "CONNECTS",
                        }
                elif kind in _MODEL_KINDS and guid:
                    model_specs.append((guid, body))
            progress += len(chunk)
            update_progress(job_id, progress)

        # 2b. Resolve model column lineage now that all physical tables are known.
        for guid, body in model_specs:
            lineage_rows.extend(
                _resolve_model_columns(
                    guid=guid, body=body,
                    physical_by_guid=physical_by_guid, physical_by_name=physical_by_name,
                    cluster_id=cluster_id, org_id=org_id,
                )
            )

        # 2c. Liveboards → model→liveboard USES edges + per-column usage (attributed to the LB).
        for chunk in _chunks(lb_guids, 50):
            items = await client.tml_export(object_ids=chunk, edoc_format="JSON")
            for item in items:
                edoc = _load_edoc(item)
                if edoc is None:
                    inaccessible += 1
                    continue
                guid = (edoc.get("guid") or (item.get("info") or {}).get("id") or "")
                kind, body = _classify_edoc(edoc)
                if kind != "liveboard" or not guid:
                    continue
                lb_name = meta_by_guid.get(guid, (body.get("name", ""), ""))[0]
                seen_models: set[str] = set()
                for answer in _embedded_answers(body):
                    model_guids, used_cols = _extract_col_usage_from_answer(answer)
                    for model_guid in model_guids:
                        if model_guid not in seen_models:
                            seen_models.add(model_guid)
                            m_name, m_type = meta_by_guid.get(model_guid, ("", ""))
                            lb_uses_edges.append(
                                CachedDependency(
                                    cluster_id=cluster_id, org_id=org_id,
                                    source_guid=guid, source_type="LIVEBOARD", source_name=lb_name,
                                    target_guid=model_guid, target_type=_node_type(m_type),
                                    target_name=m_name,
                                    relation="USES",
                                )
                            )
                        for col in used_cols:
                            usage_rows.append(
                                CachedColumnUsage(
                                    cluster_id=cluster_id, org_id=org_id,
                                    model_guid=model_guid, model_column_name=col,
                                    consumer_guid=guid, consumer_type="LIVEBOARD", consumer_name=lb_name,
                                )
                            )
            progress += len(chunk)
            update_progress(job_id, progress)

    # 3. Persist (delete-before-insert, scoped for incremental liveboards).
    now = datetime.now(timezone.utc)
    written = _persist_column_map(
        cluster_id=cluster_id, org_id=org_id, now=now,
        lineage_rows=lineage_rows, connect_edges=list(connect_edges.values()),
        lb_guids=lb_guids, lb_uses_edges=lb_uses_edges, usage_rows=usage_rows,
    )
    if inaccessible:
        logger.info("Column map: %d inaccessible TML stub(s) for cluster=%s org=%s", inaccessible, cluster_id, org_id)
    logger.info("Built column map (%d rows) for cluster=%s org=%s", written, cluster_id, org_id)
    return written


def _persist_column_map(
    *, cluster_id, org_id, now, lineage_rows, connect_edges, lb_guids, lb_uses_edges, usage_rows
) -> int:
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage

    with get_session() as session:
        # Column lineage: full rebuild (all logical tables were re-exported).
        session.exec(
            sql_delete(CachedColumnLineage).where(
                CachedColumnLineage.cluster_id == cluster_id,
                CachedColumnLineage.org_id == org_id,
            )
        )
        # CONNECTS edges: full rebuild.
        session.exec(
            sql_delete(CachedDependency).where(
                CachedDependency.cluster_id == cluster_id,
                CachedDependency.org_id == org_id,
                CachedDependency.relation == "CONNECTS",
            )
        )
        # Liveboard USES edges + usage rows: scoped to the (re-exported) liveboards.
        if lb_guids:
            session.exec(
                sql_delete(CachedDependency).where(
                    CachedDependency.cluster_id == cluster_id,
                    CachedDependency.org_id == org_id,
                    CachedDependency.relation == "USES",
                    CachedDependency.source_type == "LIVEBOARD",
                    col(CachedDependency.source_guid).in_(lb_guids),
                )
            )
            session.exec(
                sql_delete(CachedColumnUsage).where(
                    CachedColumnUsage.cluster_id == cluster_id,
                    CachedColumnUsage.org_id == org_id,
                    CachedColumnUsage.consumer_type == "LIVEBOARD",
                    col(CachedColumnUsage.consumer_guid).in_(lb_guids),
                )
            )
        session.commit()

        count = 0
        for row in lineage_rows:
            row.synced_at = now
            session.add(row)
            count += 1
        for fields in connect_edges:
            session.add(CachedDependency(cluster_id=cluster_id, org_id=org_id, synced_at=now, **fields))
            count += 1
        for edge in lb_uses_edges:
            edge.synced_at = now
            session.add(edge)
            count += 1
        for row in usage_rows:
            row.synced_at = now
            session.add(row)
            count += 1
        session.commit()
    return count


# ── BUILD: Phase 3 saved-answer column usage (lazy / opt-in) + debug ────────────
#
# Saved-answer column usage is the largest TML set — never a mandatory crawl.
# index_answer exports one answer on open (memoized); run_deep_index is the
# opt-in "Build deep column index" that crawls all answers incrementally.


def _usage_rows_from_answer(*, guid: str, name: str, body: dict, cluster_id: str, org_id: int) -> list:
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage

    model_guids, used_cols = _extract_col_usage_from_answer(body)
    rows = []
    for model_guid in model_guids:
        for col_name in used_cols:
            rows.append(
                CachedColumnUsage(
                    cluster_id=cluster_id, org_id=org_id,
                    model_guid=model_guid, model_column_name=col_name,
                    consumer_guid=guid, consumer_type="ANSWER", consumer_name=name,
                )
            )
    return rows


async def index_answer(*, cluster_id: str, org_id: int, guid: str) -> int:
    """
    Lazily index one saved answer's column usage (1 TML export, memoized). Called
    when an answer is opened. Returns rows written (0 if already indexed, not an
    answer, or inaccessible).
    """
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.services.archiver_service import _get_cluster
    from ts_admin.ts_client import ThoughtSpotClient

    with get_session() as session:
        already = session.exec(
            select(CachedColumnUsage.id).where(
                CachedColumnUsage.cluster_id == cluster_id,
                CachedColumnUsage.org_id == org_id,
                CachedColumnUsage.consumer_guid == guid,
                CachedColumnUsage.consumer_type == "ANSWER",
            )
        ).first()
        if already is not None:
            return 0
        name_row = session.exec(
            select(CachedMetadata.name).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.ts_guid == guid,
            )
        ).first()

    cluster = _get_cluster(cluster_id)
    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy(org_id=org_id)) as client:
        items = await client.tml_export(object_ids=[guid], edoc_format="JSON")
    edoc = _load_edoc(items[0]) if items else None
    if edoc is None:
        return 0
    kind, body = _classify_edoc(edoc)
    if kind != "answer":
        return 0

    rows = _usage_rows_from_answer(guid=guid, name=name_row or "", body=body, cluster_id=cluster_id, org_id=org_id)
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    with get_session() as session:
        for row in rows:
            row.synced_at = now
            session.add(row)
        session.commit()
    return len(rows)


async def build_answer_index(*, cluster_id: str, org_id: int, job_id: str, incremental: bool = True) -> int:
    """
    Opt-in deep index: crawl ALL saved answers' column usage incrementally (the
    largest TML set). Same modified_at skip logic as the liveboard pass.
    """
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage
    from ts_admin.services.archiver_service import _get_cluster
    from ts_admin.ts_client import ThoughtSpotClient

    with get_session() as session:
        answer_rows = session.exec(
            select(CachedMetadata.ts_guid, CachedMetadata.name, CachedMetadata.modified_at).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.object_type == "ANSWER",
            )
        ).all()
        last_built = session.exec(
            select(func.max(CachedColumnUsage.synced_at)).where(
                CachedColumnUsage.cluster_id == cluster_id,
                CachedColumnUsage.org_id == org_id,
                CachedColumnUsage.consumer_type == "ANSWER",
            )
        ).first()

    def _changed(modified_at) -> bool:
        return not incremental or last_built is None or modified_at is None or modified_at > last_built

    names = {r[0]: r[1] for r in answer_rows}
    answer_guids = [r[0] for r in answer_rows if _changed(r[2])]
    if not answer_guids:
        mark_complete(job_id, {"entity_type": "answer_index", "record_count": 0})
        return 0

    mark_running(job_id, total=len(answer_guids))
    cluster = _get_cluster(cluster_id)
    all_rows: list = []
    progress = 0
    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy(org_id=org_id)) as client:
        for chunk in _chunks(answer_guids, 50):
            items = await client.tml_export(object_ids=chunk, edoc_format="JSON")
            for item in items:
                edoc = _load_edoc(item)
                if edoc is None:
                    continue
                guid = edoc.get("guid") or (item.get("info") or {}).get("id") or ""
                kind, body = _classify_edoc(edoc)
                if kind != "answer" or not guid:
                    continue
                all_rows.extend(
                    _usage_rows_from_answer(
                        guid=guid, name=names.get(guid, ""), body=body, cluster_id=cluster_id, org_id=org_id
                    )
                )
            progress += len(chunk)
            update_progress(job_id, progress)

    now = datetime.now(timezone.utc)
    with get_session() as session:
        session.exec(
            sql_delete(CachedColumnUsage).where(
                CachedColumnUsage.cluster_id == cluster_id,
                CachedColumnUsage.org_id == org_id,
                CachedColumnUsage.consumer_type == "ANSWER",
                col(CachedColumnUsage.consumer_guid).in_(answer_guids),
            )
        )
        session.commit()
        for row in all_rows:
            row.synced_at = now
            session.add(row)
        session.commit()
    mark_complete(job_id, {"entity_type": "answer_index", "record_count": len(all_rows)})
    logger.info("Deep answer index: %d usage rows for cluster=%s org=%s", len(all_rows), cluster_id, org_id)
    return len(all_rows)


async def run_deep_index(cluster_id: str, org_id: int, job_id: str) -> None:
    """Background-task wrapper for build_answer_index with job/error accounting."""
    from ts_admin.services.job_service import mark_failed

    try:
        await build_answer_index(cluster_id=cluster_id, org_id=org_id, job_id=job_id)
    except Exception as exc:
        logger.exception("Deep answer index failed: %s", exc)
        mark_failed(job_id, exc)


async def debug_tml(*, cluster_id: str, org_id: int, guid: str) -> dict:
    """
    Export one object's TML and return raw keys + the parsed result — the primary
    tool for validating parser fidelity against a real cluster (Phase 3 risk #1).
    """
    from ts_admin.services.archiver_service import _get_cluster
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _get_cluster(cluster_id)
    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy(org_id=org_id)) as client:
        items = await client.tml_export(object_ids=[guid], edoc_format="JSON")

    if not items:
        return {"guid": guid, "accessible": False, "edoc_keys": [], "kind": "", "parsed": None}
    item = items[0]
    edoc = _load_edoc(item)
    if edoc is None:
        return {"guid": guid, "accessible": False, "edoc_keys": [], "kind": "", "parsed": None}

    kind, body = _classify_edoc(edoc)
    parsed: dict | list | None = None
    if kind in _SOURCE_KINDS:
        parsed = _parse_physical_source(body)
    elif kind in _MODEL_KINDS:
        rows = _resolve_model_columns(
            guid=guid, body=body, physical_by_guid={}, physical_by_name={}, cluster_id=cluster_id, org_id=org_id
        )
        parsed = [
            {
                "model_column_name": r.model_column_name,
                "table_column_name": r.table_column_name,
                "table_guid": r.table_guid,
            }
            for r in rows
        ]
    elif kind == "answer":
        model_guids, used = _extract_col_usage_from_answer(body)
        parsed = {"model_guids": model_guids, "used_columns": sorted(used)}
    elif kind == "liveboard":
        embedded = []
        for answer in _embedded_answers(body):
            model_guids, used = _extract_col_usage_from_answer(answer)
            embedded.append(
                {"answer": answer.get("name", ""), "model_guids": model_guids, "used_columns": sorted(used)}
            )
        parsed = embedded

    return {
        "guid": guid,
        "accessible": True,
        "edoc_keys": sorted(edoc.keys()),
        "kind": kind,
        "parsed": parsed,
    }


# ── READ: topology (left-list universe) ─────────────────────────────────────────


def get_topology(*, cluster_id: str, org_id: int) -> dict:
    """
    Return the three left-list groups from CachedMetadata. Pure SQLite read.
    Each item carries node_type + subtype so the frontend can filter/colour.
    """
    with get_session() as session:
        rows = session.exec(
            select(
                CachedMetadata.ts_guid,
                CachedMetadata.name,
                CachedMetadata.object_type,
                CachedMetadata.owner_name,
            ).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
            )
        ).all()

    logical_tables, answers, liveboards = [], [], []
    for guid, name, object_type, owner_name in rows:
        node_type = _node_type(object_type)
        item = {
            "ts_guid": guid,
            "name": name,
            "object_type": object_type,
            "node_type": node_type,
            "subtype": _subtype_label(object_type),
            "owner_name": owner_name or "",
        }
        if node_type == "ANSWER":
            answers.append(item)
        elif node_type == "LIVEBOARD":
            liveboards.append(item)
        else:
            logical_tables.append(item)

    for group in (logical_tables, answers, liveboards):
        group.sort(key=lambda i: i["name"].lower())

    return {"logical_tables": logical_tables, "answers": answers, "liveboards": liveboards}


# ── READ: neighborhood-scoped lineage graph ─────────────────────────────────────


def _node_from_edge_endpoint(
    guid: str, name: str, node_type: str, owner_name: str = "", accessible: bool = True
) -> dict:
    return {
        "guid": guid,
        "name": name,
        "node_type": node_type,
        "layer": _layer(node_type),
        "owner_name": owner_name,
        "accessible": accessible,
    }


def get_lineage_graph(*, cluster_id: str, org_id: int, guid: str, root_kind: str) -> dict | None:
    """
    Assemble a neighborhood graph rooted at `guid`: the full upstream chain
    (Model → Tables → Connection) plus its direct downstream consumers, capped.

    Pure SQLite, indexed lookups only — 0 API calls. Returns None if the object
    isn't in the metadata cache (→ 404). `columns` is [] until Phase 2.
    """
    with get_session() as session:
        root_meta = session.exec(
            select(CachedMetadata.name, CachedMetadata.object_type, CachedMetadata.owner_name).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.ts_guid == guid,
            )
        ).first()
        if not root_meta:
            return None
        root_name, root_object_type, root_owner = root_meta
        root_node_type = _node_type(root_object_type)
        root = _node_from_edge_endpoint(guid, root_name, root_node_type, root_owner or "")

        nodes: dict[str, dict] = {guid: root}
        edges: list[dict] = []
        edge_keys: set[tuple[str, str, str]] = set()

        def _add_edge(e: CachedDependency) -> None:
            key = (e.source_guid, e.target_guid, e.relation)
            if key in edge_keys:
                return
            edge_keys.add(key)
            edges.append({"source": e.source_guid, "target": e.target_guid, "relation": e.relation})

        # ── Upstream: follow source==current → producers, up the whole chain ──
        frontier = [guid]
        visited_up: set[str] = {guid}
        while frontier:
            producer_edges = session.exec(
                select(CachedDependency).where(
                    CachedDependency.cluster_id == cluster_id,
                    CachedDependency.org_id == org_id,
                    col(CachedDependency.source_guid).in_(frontier),
                )
            ).all()
            next_frontier: list[str] = []
            for e in producer_edges:
                _add_edge(e)
                if e.target_guid not in nodes:
                    nodes[e.target_guid] = _node_from_edge_endpoint(e.target_guid, e.target_name, e.target_type)
                if e.target_guid not in visited_up:
                    visited_up.add(e.target_guid)
                    next_frontier.append(e.target_guid)
            frontier = next_frontier

        # ── Downstream: DIRECT consumers only (target==root), capped per type ──
        consumer_edges = session.exec(
            select(CachedDependency).where(
                CachedDependency.cluster_id == cluster_id,
                CachedDependency.org_id == org_id,
                CachedDependency.target_guid == guid,
            )
        ).all()

        consumer_totals: dict[str, int] = {}
        rendered_per_type: dict[str, int] = {}
        for e in sorted(consumer_edges, key=lambda x: (x.source_name or "").lower()):
            ctype = e.source_type
            consumer_totals[ctype] = consumer_totals.get(ctype, 0) + 1
            if rendered_per_type.get(ctype, 0) < CONSUMER_NODE_CAP:
                rendered_per_type[ctype] = rendered_per_type.get(ctype, 0) + 1
                if e.source_guid not in nodes:
                    nodes[e.source_guid] = _node_from_edge_endpoint(e.source_guid, e.source_name, e.source_type)
                _add_edge(e)

        # Enrich owner_name for consumer/root nodes that live in CachedMetadata.
        _enrich_owner_names(session, cluster_id, org_id, nodes)

        # ── Impact: transitive downstream closure size (bounded) ──
        downstream_count = _downstream_closure_count(session, cluster_id, org_id, guid)

        # ── Columns: the 3-layer map (Phase 2), scoped to the root ──
        columns = _columns_for_root(session, cluster_id, org_id, guid, root_node_type)

    capped = any(consumer_totals.get(t, 0) > rendered_per_type.get(t, 0) for t in consumer_totals)
    return {
        "root": root,
        "root_kind": root_kind,
        "nodes": list(nodes.values()),
        "edges": edges,
        "consumer_totals": consumer_totals,
        "capped": capped,
        "impact": {"downstream_count": downstream_count},
        "columns": columns,
    }


def _enrich_owner_names(session, cluster_id: str, org_id: int, nodes: dict[str, dict]) -> None:
    guids = [g for g, n in nodes.items() if not n.get("owner_name")]
    if not guids:
        return
    for chunk in _chunks(guids, 500):
        rows = session.exec(
            select(CachedMetadata.ts_guid, CachedMetadata.owner_name).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                col(CachedMetadata.ts_guid).in_(chunk),
            )
        ).all()
        for g, owner in rows:
            if g in nodes and owner:
                nodes[g]["owner_name"] = owner


def _downstream_closure_count(session, cluster_id: str, org_id: int, guid: str) -> int:
    """Count distinct transitive downstream objects (bounded BFS, indexed queries)."""
    visited: set[str] = set()
    frontier = [guid]
    while frontier and len(visited) < IMPACT_BFS_CAP:
        rows = session.exec(
            select(CachedDependency.source_guid).where(
                CachedDependency.cluster_id == cluster_id,
                CachedDependency.org_id == org_id,
                col(CachedDependency.target_guid).in_(frontier),
            )
        ).all()
        next_frontier = []
        for src in rows:
            if src not in visited and src != guid:
                visited.add(src)
                next_frontier.append(src)
        frontier = next_frontier
    return len(visited)


def _columns_for_root(session, cluster_id: str, org_id: int, guid: str, node_type: str) -> list[dict]:
    """
    The 3-layer column map for the selected root, scoped to it:

      - Model / logical-table root → its columns, each with the full "used by"
        consumer list from ts_column_usage.
      - Answer / Liveboard root → the columns *it* uses, resolved down through
        its model(s); "used by" is just this consumer.

    Returns [] when no column data exists yet (pre-Phase-2 build).
    """
    from ts_admin.models.cache.ts_column_lineage import CachedColumnLineage
    from ts_admin.models.cache.ts_column_usage import CachedColumnUsage

    def _usage_entry(u: CachedColumnUsage) -> dict:
        return {"guid": u.consumer_guid, "name": u.consumer_name, "node_type": u.consumer_type}

    if node_type in ("ANSWER", "LIVEBOARD"):
        usage = session.exec(
            select(CachedColumnUsage).where(
                CachedColumnUsage.cluster_id == cluster_id,
                CachedColumnUsage.org_id == org_id,
                CachedColumnUsage.consumer_guid == guid,
            )
        ).all()
        if not usage:
            return []
        model_guids = {u.model_guid for u in usage}
        lineage = session.exec(
            select(CachedColumnLineage).where(
                CachedColumnLineage.cluster_id == cluster_id,
                CachedColumnLineage.org_id == org_id,
                col(CachedColumnLineage.model_guid).in_(model_guids),
            )
        ).all()
        lineage_map = {(r.model_guid, r.model_column_name): r for r in lineage}
        out: list[dict] = []
        for u in sorted(usage, key=lambda x: x.model_column_name.lower()):
            r = lineage_map.get((u.model_guid, u.model_column_name))
            out.append(_column_row(r, u.model_guid, u.model_column_name, [_usage_entry(u)]))
        return out

    # Model / logical-table root.
    lineage = session.exec(
        select(CachedColumnLineage).where(
            CachedColumnLineage.cluster_id == cluster_id,
            CachedColumnLineage.org_id == org_id,
            CachedColumnLineage.model_guid == guid,
        )
    ).all()
    if not lineage:
        return []
    usage = session.exec(
        select(CachedColumnUsage).where(
            CachedColumnUsage.cluster_id == cluster_id,
            CachedColumnUsage.org_id == org_id,
            CachedColumnUsage.model_guid == guid,
        )
    ).all()
    used_by: dict[str, list[dict]] = {}
    for u in usage:
        used_by.setdefault(u.model_column_name, []).append(_usage_entry(u))
    return [
        _column_row(r, r.model_guid, r.model_column_name, used_by.get(r.model_column_name, []))
        for r in sorted(lineage, key=lambda x: x.model_column_name.lower())
    ]


def _column_row(r, model_guid: str, model_column_name: str, used_by: list[dict]) -> dict:
    return {
        "model_guid": model_guid,
        "model_column_name": model_column_name,
        "table_guid": r.table_guid if r else "",
        "table_column_name": r.table_column_name if r else "",
        "db_table": r.db_table if r else "",
        "db_column_name": r.db_column_name if r else "",
        "connection_name": r.connection_name if r else "",
        "used_by": used_by,
    }


# ── READ: paginated consumers (fan-out drawer) ──────────────────────────────────


def get_consumers(
    *,
    cluster_id: str,
    org_id: int,
    guid: str,
    consumer_type: str | None = None,
    offset: int = 0,
    limit: int = 100,
) -> tuple[list[dict], int]:
    """
    Paginated full list of direct consumers of `guid` (feeds the drawer/list),
    optionally filtered to one node type. Returns (items, total).
    """
    with get_session() as session:
        base = select(CachedDependency).where(
            CachedDependency.cluster_id == cluster_id,
            CachedDependency.org_id == org_id,
            CachedDependency.target_guid == guid,
        )
        if consumer_type:
            base = base.where(CachedDependency.source_type == consumer_type.upper())
        all_edges = session.exec(base).all()
        total = len(all_edges)
        page = sorted(all_edges, key=lambda x: (x.source_name or "").lower())[offset : offset + limit]
        items = [
            {
                "guid": e.source_guid,
                "name": e.source_name,
                "node_type": e.source_type,
                "owner_name": "",
            }
            for e in page
        ]
        _enrich_owner_names(session, cluster_id, org_id, {i["guid"]: i for i in items})
    return items, total
