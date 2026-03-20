from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


class Cluster(SQLModel, table=True):
    """A named ThoughtSpot cluster connection profile."""

    __tablename__ = "clusters"

    id: str = Field(primary_key=True)               # slug, e.g. "production"
    name: str                                        # display name
    url: str                                         # https://...
    username: str
    auth_type: str = "basic"                         # basic | trusted | bearer
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    last_used_at: datetime | None = None
