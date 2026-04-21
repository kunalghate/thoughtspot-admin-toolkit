from sqlmodel import Field, SQLModel, UniqueConstraint


class CachedOrg(SQLModel, table=True):
    """
    Local cache of a ThoughtSpot org.

    ts_org_id is ThoughtSpot's numeric org ID (0 = primary org).
    The (cluster_id, ts_org_id) pair is unique and used as a logical FK
    by all org-scoped tables (ts_groups, ts_metadata, ts_tags, etc.).
    """

    __tablename__ = "ts_orgs"
    __table_args__ = (UniqueConstraint("cluster_id", "ts_org_id", name="uq_ts_orgs_cluster_org"),)

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    ts_org_id: int = Field(index=True)  # ThoughtSpot's numeric org ID (0 = primary)
    name: str
    description: str = ""
    status: str = "ACTIVE"
    is_primary: bool = False
