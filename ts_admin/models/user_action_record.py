"""
UserActionRecord — one row per user-management action (transfer / share-transfer / delete).

Lets the User History tab answer "what did we do to which user, when?". Parallels
ArchiveRecord but for user-centric actions instead of object-centric deletions.
"""

import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Field, SQLModel


class UserActionRecord(SQLModel, table=True):
    __tablename__ = "user_action_records"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    job_id: str = Field(index=True)
    org_id: int = Field(default=0)

    action_type: str  # "transfer" | "transfer_sharing" | "delete"

    # The user being acted upon
    from_user_guid: str = ""
    from_username: str = ""
    from_display_name: str = ""

    # Target for transfer / transfer_sharing (empty for delete)
    to_user_guid: str = ""
    to_username: str = ""
    to_display_name: str = ""

    items_total: int = 0
    items_succeeded: int = 0
    items_failed: int = 0

    # JSON-encoded list of affected object/share rows (capped — sample only)
    affected: str = "[]"

    status: str = "PENDING"  # PENDING | SUCCESS | PARTIAL | FAILED
    error: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def set_affected(self, items: list[dict]) -> None:
        self.affected = json.dumps(items)

    def get_affected(self) -> list[dict]:
        return json.loads(self.affected)
