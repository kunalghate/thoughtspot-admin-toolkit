"""
CachedDependency — object-edge list for the Relationship Visualizer.

A rebuildable, cluster-scoped cache of the lineage graph's *edges*. Each row is
one directed edge between two ThoughtSpot objects (or an object and its
connection). Nodes are not stored separately — a node's identity/name lives on
the edges that reference it (`ts_metadata` supplies richer detail via a join on
`(cluster_id, ts_guid)`; connections and inaccessible stubs carry a denormalized
name on the row instead).

Edge modeling (see the plan / DECISIONS):
  - A saved Answer      → its models          (source=ANSWER,    relation=USES)
  - A Liveboard         → its models directly (source=LIVEBOARD, relation=USES)
    (liveboards embed answer TML inline, so embedded answers are NOT nodes)
  - A Model/Worksheet   → its source tables   (source=MODEL,     relation=USES)
  - A physical Table    → its connection      (source=DB_TABLE,  relation=CONNECTS)

`source` is the consumer/downstream side, `target` is the producer/upstream side.

SCHEMA BUMPS = DROP-AND-REBUILD via a fresh "dependencies" re-sync. This is a
cache, not a system of record — there is no Alembic migration for it (Alembic is
not wired into the runtime path; init_db() create_all is additive-only).
"""

from datetime import datetime

from sqlmodel import Field, Index, SQLModel

# Node types that appear as source_type / target_type on an edge.
NODE_TYPES = ("CONNECTION", "DB_TABLE", "LOGICAL_TABLE", "MODEL", "ANSWER", "LIVEBOARD")
# Edge relations.
RELATIONS = ("USES", "CONNECTS")


class CachedDependency(SQLModel, table=True):
    """One directed lineage edge: `source` (consumer) depends on `target` (producer)."""

    __tablename__ = "ts_dependencies"
    __table_args__ = (
        # Detail assembly walks edges by the selected object's GUID on either
        # end — index both directions so neither is a table scan.
        Index("ix_ts_dependencies_source", "cluster_id", "org_id", "source_guid"),
        Index("ix_ts_dependencies_target", "cluster_id", "org_id", "target_guid"),
    )

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id). Not DB-enforced.

    source_guid: str = Field(index=True)  # consumer / downstream object
    source_type: str  # one of NODE_TYPES
    source_name: str = ""  # denormalized (connections/stubs have no ts_metadata row)

    target_guid: str = Field(index=True)  # producer / upstream object
    target_type: str  # one of NODE_TYPES
    target_name: str = ""  # denormalized

    relation: str = "USES"  # one of RELATIONS
    synced_at: datetime | None = None
