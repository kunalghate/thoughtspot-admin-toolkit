"""
Relationship Visualizer API — data-lineage reads (all SQLite, 0 API calls).

  GET /relationships/topology
      → the left-list universe: { logical_tables, answers, liveboards }
  GET /relationships/{root_kind}/{guid}
      → a neighborhood-scoped LineageGraphResponse (root + capped nodes + edges)
  GET /relationships/{root_kind}/{guid}/consumers?type=&offset=&limit=
      → paginated full consumer list (feeds the fan-out drawer)

Building the graph is NOT a route here — it routes through the existing
POST /api/v1/sync/dependencies (gated background job, ADR-005). These endpoints
only read the cache the build populated.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ts_admin.services import lineage_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/relationships", tags=["Relationships"])

_ROOT_KINDS = {"model", "answer", "liveboard"}


def _resolve_cluster_id(cluster_id: str | None) -> str:
    if cluster_id:
        return cluster_id
    from ts_admin.config import load_config

    return load_config().active_cluster.id


# ── Schemas ────────────────────────────────────────────────────────────────────


class TopologyItem(BaseModel):
    ts_guid: str
    name: str
    object_type: str
    node_type: str
    subtype: str
    owner_name: str


class TopologyResponse(BaseModel):
    logical_tables: list[TopologyItem]
    answers: list[TopologyItem]
    liveboards: list[TopologyItem]


class LineageNode(BaseModel):
    guid: str
    name: str
    node_type: str
    layer: int
    owner_name: str = ""
    accessible: bool = True


class LineageEdge(BaseModel):
    source: str
    target: str
    relation: str


class ColumnLineageRow(BaseModel):
    model_guid: str
    model_column_name: str
    table_guid: str = ""
    table_column_name: str = ""
    db_table: str = ""
    db_column_name: str = ""
    connection_name: str = ""
    used_by: list[dict] = []


class LineageImpact(BaseModel):
    downstream_count: int


class LineageGraphResponse(BaseModel):
    root: LineageNode
    root_kind: str
    nodes: list[LineageNode]
    edges: list[LineageEdge]
    consumer_totals: dict[str, int]
    capped: bool
    impact: LineageImpact
    columns: list[ColumnLineageRow] = []


class ConsumerItem(BaseModel):
    guid: str
    name: str
    node_type: str
    owner_name: str = ""


class ConsumersResponse(BaseModel):
    items: list[ConsumerItem]
    total: int
    offset: int
    limit: int


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("/topology", response_model=TopologyResponse)
def get_topology(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> TopologyResponse:
    """The searchable left-list universe, grouped into the three explorer tabs."""
    result = lineage_service.get_topology(cluster_id=_resolve_cluster_id(cluster_id), org_id=org_id)
    return TopologyResponse(**result)


@router.get("/{root_kind}/{guid}", response_model=LineageGraphResponse)
def get_lineage_graph(
    root_kind: str,
    guid: str,
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> LineageGraphResponse:
    """Neighborhood lineage graph rooted at `guid`. 404 if the object isn't cached."""
    if root_kind not in _ROOT_KINDS:
        raise HTTPException(status_code=422, detail=f"root_kind must be one of {sorted(_ROOT_KINDS)}")

    result = lineage_service.get_lineage_graph(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        guid=guid,
        root_kind=root_kind,
    )
    if result is None:
        raise HTTPException(
            status_code=404,
            detail=f"Object {guid!r} not found in the metadata cache for this cluster/org.",
        )
    return LineageGraphResponse(**result)


@router.get("/{root_kind}/{guid}/consumers", response_model=ConsumersResponse)
def get_consumers(
    root_kind: str,
    guid: str,
    type: str | None = Query(default=None),  # noqa: A002 — matches the query-param name
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
) -> ConsumersResponse:
    """Paginated full downstream-consumer list for the fan-out drawer."""
    if root_kind not in _ROOT_KINDS:
        raise HTTPException(status_code=422, detail=f"root_kind must be one of {sorted(_ROOT_KINDS)}")

    items, total = lineage_service.get_consumers(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        guid=guid,
        consumer_type=type,
        offset=offset,
        limit=limit,
    )
    return ConsumersResponse(items=[ConsumerItem(**i) for i in items], total=total, offset=offset, limit=limit)
