"""
ShareRecord — one row per (object × principal) share change executed by Bulk Sharing.

Powers the Sharing History tab so admins can answer "what did we change last Tuesday?".
"""

from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


class ShareRecord(SQLModel, table=True):
    __tablename__ = "share_records"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    job_id: str = Field(index=True)
    org_id: int = Field(default=0)

    object_guid: str = Field(index=True)
    object_name: str = ""
    object_type: str = ""

    principal_guid: str = Field(index=True)
    principal_name: str = ""
    principal_type: str = "USER"  # USER | USER_GROUP

    previous_mode: str = ""  # "" | READ_ONLY | MODIFY | NO_ACCESS
    new_mode: str  # READ_ONLY | MODIFY | NO_ACCESS

    status: str = "SUCCESS"  # SUCCESS | FAILED
    error: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
