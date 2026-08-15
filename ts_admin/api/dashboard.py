"""Dashboard API — one aggregate read for the Dashboard page."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ts_admin.services.dashboard_service import DashboardService

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class DashboardCounts(BaseModel):
    users: int
    groups: int
    tags: int
    objects_total: int
    objects_by_type: dict[str, int]
    archivable_total: int
    stale_90d: int  # archivable objects last accessed 90+ days ago
    never_accessed: int  # archivable objects with no access date at all


class DashboardAttention(BaseModel):
    """Cached signals that map to an action the toolkit already supports."""

    inactive_users: int
    users_without_group: int
    empty_groups: int
    orphaned_content: int


class DashboardJob(BaseModel):
    id: str
    job_type: str
    status: str
    created_at: datetime | None
    error: str | None
    error_type: str | None


class DashboardRunningJob(BaseModel):
    id: str
    job_type: str
    status: str
    progress: int
    total: int


class DashboardActivity(BaseModel):
    kind: str  # "delete" | "share" | "user_action"
    label: str
    status: str
    timestamp: datetime | None
    count: int = 1  # identical adjacent entries folded into one row


class DashboardResponse(BaseModel):
    counts: DashboardCounts
    # Per-entity "has this ever synced?" — lets the UI distinguish a real zero
    # from a number we simply do not have yet.
    synced: dict[str, bool]
    # When each entity last synced successfully (null = never). Syncs are lazy
    # and per-entity, so cache freshness is only meaningful entity by entity.
    synced_at: dict[str, datetime | None]
    # Per-entity "is a sync running right now?". `synced` goes False for the
    # whole duration of a healthy sync (the write-ahead IN_PROGRESS marker
    # replaces the SUCCESS row), so without this the UI cannot tell an in-flight
    # sync apart from a cluster that has never synced.
    syncing: dict[str, bool] = {}
    # Change in record_count since the previous successful sync of each entity.
    deltas: dict[str, int]
    attention: DashboardAttention
    recent_jobs: list[DashboardJob]
    running_jobs: list[DashboardRunningJob]
    recent_activity: list[DashboardActivity]
    failed_jobs_7d: int


@router.get("", response_model=DashboardResponse)
def get_dashboard(
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> DashboardResponse:
    """Aggregate counts, recent jobs, and recent audit activity from SQLite."""
    if not cluster_id:
        from ts_admin.config import load_config

        cluster_id = load_config().active_cluster.id
    return DashboardResponse(**DashboardService.summary(cluster_id=cluster_id, org_id=org_id))
