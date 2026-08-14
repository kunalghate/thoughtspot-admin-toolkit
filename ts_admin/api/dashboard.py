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
    stale_90d: int


class DashboardJob(BaseModel):
    id: str
    job_type: str
    status: str
    created_at: datetime | None
    error: str | None


class DashboardActivity(BaseModel):
    kind: str  # "delete" | "share" | "user_action"
    label: str
    status: str
    timestamp: datetime | None


class DashboardResponse(BaseModel):
    counts: DashboardCounts
    recent_jobs: list[DashboardJob]
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
