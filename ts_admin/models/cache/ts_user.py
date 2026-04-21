from datetime import datetime

from sqlmodel import Field, SQLModel


class CachedUser(SQLModel, table=True):
    """
    Local cache of a ThoughtSpot user profile.
    Scoped to cluster only — NOT org-scoped.

    A user can belong to multiple orgs on the same cluster. Org membership
    is tracked separately in UserOrgMembership, which is the join table between
    users and orgs. This avoids duplicating profile data across orgs.

    To query users in a specific org:
        SELECT u.* FROM ts_users u
        JOIN user_org_memberships m ON m.ts_guid = u.ts_guid
        WHERE u.cluster_id = :cluster_id AND m.org_id = :org_id
    """

    __tablename__ = "ts_users"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    ts_guid: str = Field(index=True)  # ThoughtSpot's GUID for this user
    username: str
    display_name: str = ""
    email: str = ""
    status: str = "ACTIVE"  # ACTIVE | INACTIVE
    created_at: datetime | None = None
    modified_at: datetime | None = None
    synced_at: datetime | None = None


class UserOrgMembership(SQLModel, table=True):
    """
    Junction table: which orgs does each user belong to on a given cluster.

    One row per (cluster_id, ts_guid, org_id) combination.
    Updated during user sync for each org.
    """

    __tablename__ = "user_org_memberships"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    ts_guid: str = Field(index=True)  # FK to ts_users.ts_guid
    org_id: int = Field(index=True)  # ThoughtSpot org ID (0 = primary org)
    synced_at: datetime | None = None


class UserGroupMembership(SQLModel, table=True):
    """
    Junction table: which groups does each user belong to, per org.

    Groups are org-scoped, so membership is tracked per (cluster, org, user, group).

    Query patterns:
      "All users in group G in org O"
        → WHERE cluster_id=C AND org_id=O AND group_guid=G

      "All groups user U belongs to in org O"
        → WHERE cluster_id=C AND org_id=O AND user_guid=U

      "All groups user U belongs to across all orgs"
        → WHERE cluster_id=C AND user_guid=U
    """

    __tablename__ = "user_group_memberships"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    org_id: int = Field(index=True)  # ThoughtSpot org ID (0 = primary org)
    user_guid: str = Field(index=True)  # FK to ts_users.ts_guid
    group_guid: str = Field(index=True)  # FK to ts_groups.ts_guid
    synced_at: datetime | None = None
