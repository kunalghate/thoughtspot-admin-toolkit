"""
CachedColumnUsage — per-column consumer index for the Relationship Visualizer.

One row per (model column, consumer) pair: which saved Answer or Liveboard
references a given model column. Powers the Columns grid's "Used by" cell and
column-level impact analysis.

Populated by `_extract_col_usage_from_answer` for both saved answers (ANSWER
consumer) and liveboard-embedded answers (attributed to the LIVEBOARD).
Cluster-scoped, rebuildable cache — SCHEMA BUMPS = DROP-AND-REBUILD via re-sync.
"""

from datetime import datetime

from sqlmodel import Field, Index, SQLModel


class CachedColumnUsage(SQLModel, table=True):
    """A single 'model column X is used by consumer Y' fact."""

    __tablename__ = "ts_column_usage"
    __table_args__ = (
        # "Which consumers use this model's columns?" (Columns grid)
        Index("ix_ts_column_usage_model", "cluster_id", "org_id", "model_guid"),
        # "Which model columns does this answer/liveboard use?" (consumer detail)
        Index("ix_ts_column_usage_consumer", "cluster_id", "org_id", "consumer_guid"),
    )

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)

    model_guid: str = Field(index=True)
    model_column_name: str

    consumer_guid: str = Field(index=True)
    consumer_type: str  # ANSWER | LIVEBOARD
    consumer_name: str = ""  # denormalized
    synced_at: datetime | None = None
