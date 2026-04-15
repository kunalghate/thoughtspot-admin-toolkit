import json
from datetime import datetime

from sqlmodel import Field, SQLModel


class CachedGroup(SQLModel, table=True):
    """
    Local cache of a ThoughtSpot user group.

    Groups are org-scoped — a group exists in exactly one org.
    org_id is stored directly on the group row (no junction table needed).

    User membership is NOT stored here. Query user_group_memberships instead:
      SELECT user_guid FROM user_group_memberships
      WHERE cluster_id=:c AND org_id=:o AND group_guid=:g
    """

    __tablename__ = "ts_groups"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)
    # Logical FK → (ts_orgs.cluster_id, ts_orgs.ts_org_id).
    # Not enforced at DB level because the FK is composite.
    # Application layer ensures orgs are synced before org-scoped data.
    ts_guid: str = Field(index=True)
    name: str
    display_name: str = ""
    description: str = ""
    privileges: str = "[]"  # JSON list of privilege strings
    created_at: datetime | None = None
    modified_at: datetime | None = None
    synced_at: datetime | None = None

    def get_privileges(self) -> list[str]:
        return json.loads(self.privileges)
