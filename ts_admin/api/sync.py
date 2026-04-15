"""
Sync management endpoints.

GET  /api/v1/sync              — sync status for all entities (active cluster + org)
POST /api/v1/sync/{entity}     — trigger sync for a specific entity type
POST /api/v1/sync/all          — trigger sync for all entities
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/sync", tags=["sync"])

VALID_ENTITIES = {"users", "groups", "metadata", "tags", "orgs", "permissions"}


# ── Response models ────────────────────────────────────────────────────────────


class EntitySyncStatus(BaseModel):
    entity_type: str
    synced_at: datetime | None = None
    record_count: int = 0
    status: str = "NOT_SYNCED"  # NOT_SYNCED | SUCCESS | FAILED | IN_PROGRESS
    error: str | None = None
    age_minutes: float | None = None


class SyncTriggeredResponse(BaseModel):
    entity_type: str
    job_id: str


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("", response_model=list[EntitySyncStatus])
async def get_sync_status(org_id: int = 0) -> list[EntitySyncStatus]:
    """
    Return the sync status for every entity type for the active cluster + org.
    Used by the Settings → Sync page and the per-page sync indicators.
    """
    from sqlmodel import select

    from ts_admin.config import load_config
    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    config = load_config()
    cluster_id = config.active_cluster.id
    now = datetime.now(timezone.utc)

    with get_session() as session:
        rows = session.exec(select(SyncLog).where(SyncLog.cluster_id == cluster_id, SyncLog.org_id == org_id)).all()

    synced = {row.entity_type: row for row in rows}
    result = []

    for entity in VALID_ENTITIES:
        row = synced.get(entity)
        if not row:
            result.append(EntitySyncStatus(entity_type=entity))
            continue

        age = (now - row.synced_at.replace(tzinfo=timezone.utc)).total_seconds() / 60
        result.append(
            EntitySyncStatus(
                entity_type=entity,
                synced_at=row.synced_at,
                record_count=row.record_count,
                status=row.status,
                error=row.error,
                age_minutes=round(age, 1),
            )
        )

    return result


@router.post("/{entity_type}", response_model=SyncTriggeredResponse)
async def trigger_sync(
    entity_type: str,
    background_tasks: BackgroundTasks,
    org_id: int = 0,
) -> SyncTriggeredResponse:
    """
    Trigger a background sync for a specific entity type.
    Returns a job_id immediately — poll /api/v1/jobs/{job_id} for status.
    """
    if entity_type not in VALID_ENTITIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown entity type {entity_type!r}. Valid options: {sorted(VALID_ENTITIES)}",
        )

    from ts_admin.services.job_service import create_job
    from ts_admin.services.sync_service import run_sync

    job_id = create_job(
        job_type=f"sync:{entity_type}",
        parameters={
            "entity_type": entity_type,
            "org_id": org_id,
        },
    )

    background_tasks.add_task(run_sync, entity_type=entity_type, org_id=org_id, job_id=job_id)

    return SyncTriggeredResponse(entity_type=entity_type, job_id=job_id)


@router.post("/all", response_model=list[SyncTriggeredResponse])
async def trigger_sync_all(
    background_tasks: BackgroundTasks,
    org_id: int = 0,
) -> list[SyncTriggeredResponse]:
    """Trigger sync for all standard entities (excludes permissions — too heavy)."""
    from ts_admin.services.job_service import create_job
    from ts_admin.services.sync_service import run_sync

    standard_entities = {"users", "groups", "metadata", "tags", "orgs"}
    results = []

    for entity in standard_entities:
        job_id = create_job(
            job_type=f"sync:{entity}",
            parameters={
                "entity_type": entity,
                "org_id": org_id,
            },
        )
        background_tasks.add_task(run_sync, entity_type=entity, org_id=org_id, job_id=job_id)
        results.append(SyncTriggeredResponse(entity_type=entity, job_id=job_id))

    return results
