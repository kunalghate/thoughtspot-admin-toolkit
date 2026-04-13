from datetime import datetime, UTC
from uuid import uuid4
from sqlmodel import SQLModel, Field


class ArchiveRecord(SQLModel, table=True):
    """
    One row per object deleted by the Content Archiver.

    Written at deletion time — serves as both the audit trail and the
    restore manifest. The TML file at tml_path allows the object to be
    re-imported into ThoughtSpot after deletion.

    Lifecycle:
        PENDING  → tml_export_status while TML export is queued
        SUCCESS  → TML written to disk, object included in delete batch
        FAILED   → TML export failed; object was NOT deleted
    """

    __tablename__ = "archive_records"

    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    cluster_id: str = Field(foreign_key="clusters.id", index=True)
    job_id: str = Field(index=True)        # FK to jobs.id — the archive session

    # Snapshot of the object at deletion time
    ts_guid: str = Field(index=True)
    name: str
    object_type: str                       # LIVEBOARD | ANSWER
    owner_guid: str
    owner_name: str
    org_id: int = Field(default=0)
    last_accessed_at: datetime | None = None
    days_unused: int = 0
    tags: str = Field(default="[]")        # JSON array of tag names at deletion time

    # TML backup
    tml_path: str | None = None            # absolute path to .tml file on disk
    tml_export_status: str = "PENDING"     # PENDING | SUCCESS | FAILED
    tml_export_error: str | None = None

    # Lifecycle
    archived_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    restored_at: datetime | None = None
    restored_as_guid: str | None = None    # NEW GUID assigned by TS after TML import
    restored_by_job_id: str | None = None  # job_id of the restore job
