from datetime import datetime

from sqlmodel import Field, SQLModel


class CachedConnection(SQLModel, table=True):
    """
    Local cache of a ThoughtSpot data connection (Snowflake, Databricks, …).

    Connections are the link between ThoughtSpot content and the warehouse it
    actually reads. ThoughtSpot's own UI makes it hard to see which connection
    a table sits on, and offers no way at all to spot connections nothing uses.

    Object counts are NOT stored here. They are computed at read time with a
    GROUP BY over ts_metadata.connection_guid (see connection_service), so a
    count can never drift from the metadata cache it describes — and a
    connection with zero objects, which is the one an admin is hunting for,
    falls out of the same query rather than needing its own bookkeeping.

    Org-scoped like the other content caches: `/connection/search` is answered
    in the caller's org context.
    """

    __tablename__ = "ts_connections"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id). Not DB-enforced
    # (composite key); the application syncs orgs before org-scoped data.
    ts_guid: str = Field(index=True)
    name: str
    description: str = ""
    # SNOWFLAKE | DATABRICKS | GOOGLE_BIGQUERY | … — the reason an admin scans
    # this list, so it is denormalized rather than derived.
    data_warehouse_type: str = ""
    synced_at: datetime | None = None
