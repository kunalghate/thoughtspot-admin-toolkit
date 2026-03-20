from datetime import datetime
from sqlmodel import Field, SQLModel


class ContentPermission(SQLModel, table=True):
    """
    Cache of who has access to each piece of ThoughtSpot content.

    Covers both user-level and group-level sharing in a single table,
    mirroring how ThoughtSpot's sharing API returns permissions — a list
    of principals (users or groups) with a permission level per object.

    This is a heavy table (potentially millions of rows on large instances).
    It is NOT synced automatically. Sync is triggered only when the admin
    navigates to a feature that requires it (Archiver dry-run, Metadata
    Explorer sharing view) and the admin explicitly confirms the sync.

    Query patterns:
      "Who has access to object X?"
        → WHERE cluster_id=C AND org_id=O AND metadata_guid=X

      "What content is user U directly shared on?"
        → WHERE cluster_id=C AND org_id=O
          AND principal_type='user' AND principal_guid=U

      "What content is group G shared on?"
        → WHERE cluster_id=C AND org_id=O
          AND principal_type='group' AND principal_guid=G

      "Is object X shared with any active users?" (used by Archiver dry-run)
        → JOIN ts_users ON principal_guid = ts_users.ts_guid
          WHERE metadata_guid=X AND principal_type='user'
          AND ts_users.status='ACTIVE'
    """

    __tablename__ = "content_permissions"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id). Not DB-enforced (composite key).
    metadata_guid: str = Field(index=True)      # FK → ts_metadata.ts_guid
    principal_type: str                          # "user" | "group"
    principal_guid: str = Field(index=True)      # FK → ts_users.ts_guid or ts_groups.ts_guid
    permission: str                              # "READ_ONLY" | "MODIFY"
    synced_at: datetime | None = None
