from sqlmodel import Field, SQLModel


class CachedTag(SQLModel, table=True):
    """Local cache of a ThoughtSpot tag."""

    __tablename__ = "ts_tags"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id). Not DB-enforced (composite key).
    ts_guid: str = Field(index=True)
    name: str
    color: str = ""
