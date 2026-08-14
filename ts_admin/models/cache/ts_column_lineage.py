"""
CachedColumnLineage — the 3-layer column map for the Relationship Visualizer.

One row per model column, tracing it down through the physical stack:

    model_column  →  table_column  →  db_column
    (Model/Worksheet) (Logical Table)  (physical DB table on the connection)

Populated by lineage_service.build_column_map from LOGICAL_TABLE TML
(`_build_resolved_model`). Cluster-scoped, rebuildable cache — SCHEMA BUMPS =
DROP-AND-REBUILD via re-sync (no Alembic migration).
"""

from datetime import datetime

from sqlmodel import Field, Index, SQLModel


class CachedColumnLineage(SQLModel, table=True):
    """A single resolved column chain: model column → table column → db column."""

    __tablename__ = "ts_column_lineage"
    __table_args__ = (
        # Detail assembly reads all columns for the selected model at once.
        Index("ix_ts_column_lineage_model", "cluster_id", "org_id", "model_guid"),
    )

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)

    model_guid: str = Field(index=True)  # the Model/Worksheet this column belongs to
    model_column_name: str

    table_guid: str = ""  # the source Logical Table's GUID (may be a stub)
    table_column_name: str = ""

    db_table: str = ""  # physical DB table name (from TML)
    db_column_name: str = ""

    connection_name: str = ""  # denormalized connection name (from TML)
    # True for computed columns (TML `formula_id`, no `column_id`) — they have no
    # physical table/db chain by design, not because resolution failed.
    is_formula: bool = False
    synced_at: datetime | None = None
