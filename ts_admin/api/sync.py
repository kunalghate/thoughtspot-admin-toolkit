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

# The entities a caller may sync. This is BOTH the trigger allowlist and the
# render set for GET /sync, so an entry with no handler in
# `sync_service.sync_handlers()` is a double lie: POST /sync/{entity} returns a
# job id for a job that has already failed, and the status list carries a row
# that can never leave NOT_SYNCED. `tests/unit/test_sync_entities.py` asserts
# the two sets agree in both directions — "permissions" was listed here for
# months with no handler behind it.
#
# "dependencies" (lineage graph) is a valid explicit sync target but is
# deliberately excluded from trigger_sync_all — it is heavy and gated per ADR-005.
VALID_ENTITIES = {"users", "groups", "metadata", "tags", "orgs", "dependencies"}
# Everything POST /sync/all fans out to. A strict subset of VALID_ENTITIES.
STANDARD_ENTITIES = {"users", "groups", "metadata", "tags", "orgs"}


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


# ── Helpers ────────────────────────────────────────────────────────────────────


def _resolve_cluster_id(cluster_id: str | None) -> str:
    """The caller's cluster, falling back to the active one.

    Mirrors ``users.py::_resolve_cluster_id`` — cluster identity lives in config
    while callers address clusters by id, so the id is taken at face value here.
    An id that names no configured cluster is caught by ``run_sync``, which fails
    the job rather than syncing the wrong cluster.
    """
    from ts_admin.config import load_config

    if cluster_id:
        return cluster_id
    return load_config().active_cluster.id


def _in_flight_sync_job(cluster_id: str, org_id: int, entity_type: str):
    """The QUEUED/RUNNING sync job for (cluster, org, entity), or None.

    Repeated Sync clicks used to start duplicate concurrent syncs of the same
    type (S24/S34): each is a full delete-and-rebuild so the data survives, but
    concurrent TML crawls have been observed live tripling each other's
    wall-clock via API timeouts.
    """
    from ts_admin.services.job_service import find_in_flight_sync

    return find_in_flight_sync(cluster_id=cluster_id, org_id=org_id, entity_type=entity_type)


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("", response_model=list[EntitySyncStatus])
async def get_sync_status(org_id: int = 0, cluster_id: str | None = None) -> list[EntitySyncStatus]:
    """
    Return the sync status for every entity type for a cluster + org.
    Used by the Settings → Sync page and the per-page sync indicators.

    ``cluster_id`` defaults to the active cluster, but callers should send the
    cluster they are actually displaying — the UI can be pointed at one cluster
    while another is marked active.
    """
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.sync_log import SyncLog

    cluster_id = _resolve_cluster_id(cluster_id)
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


# NOTE: registered BEFORE /{entity_type} — FastAPI matches routes in
# registration order, so declaring the dynamic route first shadowed "/all"
# into a 400 ("Unknown entity type 'all'") and left this endpoint unreachable.
@router.post("/all", response_model=list[SyncTriggeredResponse])
async def trigger_sync_all(
    background_tasks: BackgroundTasks,
    org_id: int = 0,
    cluster_id: str | None = None,
) -> list[SyncTriggeredResponse]:
    """Trigger sync for all standard entities (excludes dependencies — too heavy)."""
    from ts_admin.services.job_service import create_job
    from ts_admin.services.sync_service import run_sync

    cluster_id = _resolve_cluster_id(cluster_id)
    results = []

    for entity in sorted(STANDARD_ENTITIES):
        # "Sync all" attaches to work already in flight rather than duplicating
        # it — the caller's intent is "everything fresh", and an in-flight job
        # is already delivering that for its entity.
        in_flight = _in_flight_sync_job(cluster_id, org_id, entity)
        if in_flight:
            results.append(SyncTriggeredResponse(entity_type=entity, job_id=in_flight.id))
            continue
        job_id = create_job(
            job_type=f"sync:{entity}",
            parameters={
                "entity_type": entity,
                "org_id": org_id,
                "cluster_id": cluster_id,
            },
        )
        background_tasks.add_task(run_sync, entity_type=entity, org_id=org_id, job_id=job_id, cluster_id=cluster_id)
        results.append(SyncTriggeredResponse(entity_type=entity, job_id=job_id))

    return results


@router.post("/{entity_type}", response_model=SyncTriggeredResponse)
async def trigger_sync(
    entity_type: str,
    background_tasks: BackgroundTasks,
    org_id: int = 0,
    cluster_id: str | None = None,
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

    cluster_id = _resolve_cluster_id(cluster_id)

    in_flight = _in_flight_sync_job(cluster_id, org_id, entity_type)
    if in_flight:
        raise HTTPException(
            status_code=409,
            detail=(
                f"A {entity_type} sync is already {in_flight.status.lower()} for this "
                f"cluster and org (job {in_flight.id}). Wait for it to finish before "
                "starting another."
            ),
        )

    job_id = create_job(
        job_type=f"sync:{entity_type}",
        parameters={
            "entity_type": entity_type,
            "org_id": org_id,
            "cluster_id": cluster_id,
        },
    )

    background_tasks.add_task(run_sync, entity_type=entity_type, org_id=org_id, job_id=job_id, cluster_id=cluster_id)

    return SyncTriggeredResponse(entity_type=entity_type, job_id=job_id)
