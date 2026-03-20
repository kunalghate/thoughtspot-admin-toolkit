import json
from datetime import datetime
from sqlmodel import Field, SQLModel


class CachedMetadata(SQLModel, table=True):
    """
    Local cache of a ThoughtSpot content object.
    Covers: Liveboards, Answers, Worksheets, Tables.
    """

    __tablename__ = "ts_metadata"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id). Not DB-enforced (composite key).
    ts_guid: str = Field(index=True)
    name: str
    object_type: str                    # LIVEBOARD | ANSWER | LOGICAL_TABLE | ...
    owner_id: str = ""                  # GUID of the owning user
    author_name: str = ""
    tag_ids: str = "[]"                # JSON list of tag GUIDs
    created_at: datetime | None = None
    modified_at: datetime | None = None
    last_accessed: datetime | None = None
    view_count: int = 0
    synced_at: datetime | None = None

    def get_tag_ids(self) -> list[str]:
        return json.loads(self.tag_ids)

    def set_tag_ids(self, ids: list[str]) -> None:
        self.tag_ids = json.dumps(ids)
