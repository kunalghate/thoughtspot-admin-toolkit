from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class SyncLog(SQLModel, table=True):
    """
    Records when each entity type was last synced per cluster.
    One row per (cluster_id, entity_type) pair.
    """

    __tablename__ = "sync_log"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id). Not DB-enforced (composite key).
    entity_type: str  # "users" | "groups" | "metadata" | "tags" | "orgs" | "dependencies"
    synced_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    record_count: int = 0
    duration_ms: int = 0
    status: str = "SUCCESS"  # SUCCESS | FAILED | IN_PROGRESS
    error: str | None = None
