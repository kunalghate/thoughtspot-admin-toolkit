import json
from datetime import datetime

from sqlmodel import Field, Index, SQLModel


class CachedMetadata(SQLModel, table=True):
    """
    Local cache of a ThoughtSpot content object.
    Covers: Liveboards, Answers, Worksheets, Tables.

    Field naming conventions (consistent across all cache models):
      owner_guid     — ThoughtSpot GUID of the owning user
      owner_name     — display name of the owner (denormalized for fast display)
      tag_names      — JSON list of tag names (not GUIDs — admins see names)
      last_accessed_at — when the object was last accessed (UTC)
    """

    __tablename__ = "ts_metadata"

    # Existence/lookup by the full identity key. Without this, SQLite's no-stats
    # heuristic picks the single-column ix_ts_metadata_cluster_id — the least
    # selective of the three — and a per-object lookup degrades to a scan.
    # create_all does NOT add indexes to an already-existing table, so
    # database.init_db() also issues this as an explicit CREATE INDEX IF NOT EXISTS.
    __table_args__ = (Index("ix_ts_metadata_cluster_org_guid", "cluster_id", "org_id", "ts_guid"),)

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id). Not DB-enforced (composite key).
    ts_guid: str = Field(index=True)
    name: str
    object_type: str  # LIVEBOARD | ANSWER | LOGICAL_TABLE | ...
    owner_guid: str = ""  # GUID of the owning user
    owner_name: str = ""  # display name of owner (denormalized)
    tag_names: str = "[]"  # JSON list of tag names
    created_at: datetime | None = None
    modified_at: datetime | None = None
    last_accessed_at: datetime | None = None
    view_count: int = 0
    synced_at: datetime | None = None

    def get_tag_names(self) -> list[str]:
        return json.loads(self.tag_names)

    def set_tag_names(self, names: list[str]) -> None:
        self.tag_names = json.dumps(names)
