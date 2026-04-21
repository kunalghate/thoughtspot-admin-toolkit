import json
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class AuditLog(SQLModel, table=True):
    """
    Immutable record of every admin action executed via this app.
    Written after every successful write operation.
    """

    __tablename__ = "audit_log"

    id: int | None = Field(default=None, primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    action_type: str  # "archive" | "share" | "delete" | "tag" | ...
    entity_type: str  # "metadata" | "user" | "group"
    items_affected: int = 0
    parameters: str = "{}"  # JSON string of operation parameters
    status: str = "SUCCESS"  # SUCCESS | FAILED | PARTIAL
    error: str | None = None
    executed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def set_parameters(self, params: dict) -> None:
        self.parameters = json.dumps(params)

    def get_parameters(self) -> dict:
        return json.loads(self.parameters)
