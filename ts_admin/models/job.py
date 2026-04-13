import json
from datetime import datetime, timezone
from sqlmodel import Field, SQLModel


class Job(SQLModel, table=True):
    """Background job record — queue, status tracking, and history."""

    __tablename__ = "jobs"

    id: str = Field(primary_key=True)       # UUID assigned at creation
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    job_type: str                           # "archive" | "share" | "sync" | ...
    status: str = "QUEUED"                  # QUEUED | RUNNING | COMPLETE | FAILED | PARTIAL
    progress: int = 0                       # items processed so far
    total: int = 0                          # total items to process
    parameters: str = "{}"                 # JSON string of job input params
    result: str | None = None              # JSON string of result summary
    error: str | None = None
    is_cancelled: bool = False             # set True by DELETE /jobs/{id}/cancel
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @property
    def is_done(self) -> bool:
        return self.status in ("COMPLETE", "FAILED", "PARTIAL")

    @property
    def progress_pct(self) -> float:
        if self.total == 0:
            return 0.0
        return round(self.progress / self.total * 100, 1)

    def set_parameters(self, params: dict) -> None:
        self.parameters = json.dumps(params)

    def get_parameters(self) -> dict:
        return json.loads(self.parameters)

    def set_result(self, result: dict) -> None:
        self.result = json.dumps(result)

    def get_result(self) -> dict | None:
        return json.loads(self.result) if self.result else None
