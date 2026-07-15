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

from sqlmodel import col, select
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
        done += 1
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

    capped = any(consumer_totals.get(t, 0) > rendered_per_type.get(t, 0) for t in consumer_totals)
    return {
        "root": root,
        "root_kind": root_kind,
        "nodes": list(nodes.values()),
        "edges": edges,
        "consumer_totals": consumer_totals,
        "capped": capped,
        "impact": {"downstream_count": downstream_count},
        "columns": [],
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
