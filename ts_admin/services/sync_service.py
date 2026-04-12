"""
Sync service — fetches data from ThoughtSpot and upserts into SQLite cache.

Each entity type has its own sync function. All follow the same pattern:
  1. Mark job as RUNNING
  2. Paginate through TS API, upsert rows into SQLite
  3. Write SyncLog entry
  4. Mark job as COMPLETE or FAILED
"""

import logging
from datetime import datetime, timezone

from ts_admin.database import get_session
from ts_admin.models.sync_log import SyncLog
from ts_admin.services.job_service import mark_complete, mark_failed, mark_running

logger = logging.getLogger(__name__)


async def run_sync(*, entity_type: str, org_id: int, job_id: str) -> None:
    """
    Entry point for all sync operations. Dispatches to the correct handler.
    Called as a FastAPI BackgroundTask.
    """
    handlers = {
        "users":    _sync_users,
        "groups":   _sync_groups,
        "metadata": _sync_metadata,
        "tags":     _sync_tags,
        "orgs":     _sync_orgs,
    }

    handler = handlers.get(entity_type)
    if not handler:
        mark_failed(job_id, f"Unknown entity type: {entity_type!r}")
        return

    try:
        await handler(org_id=org_id, job_id=job_id)
    except Exception as exc:
        logger.exception("Sync failed for %s org=%s: %s", entity_type, org_id, exc)
        mark_failed(job_id, str(exc))
        _write_sync_log(entity_type, org_id, status="FAILED", error=str(exc))


# ── Per-entity sync handlers ───────────────────────────────────────────────────

async def _sync_users(*, org_id: int, job_id: str) -> None:
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
    from sqlmodel import select

    config = load_config()
    cluster = config.active_cluster
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)
    count = 0

    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy()) as client:
        async for page in client.search_users(org_id=org_id):
            with get_session() as session:
                for user in page:
                    # Upsert user profile (cluster-scoped, not org-scoped)
                    existing = session.exec(
                        select(CachedUser).where(
                            CachedUser.cluster_id == cluster_id,
                            CachedUser.ts_guid == user.id,
                        )
                    ).first()

                    if existing:
                        existing.username = user.name
                        existing.display_name = user.display_name
                        existing.email = user.email
                        existing.status = user.status.value
                        existing.synced_at = datetime.now(timezone.utc)
                        session.add(existing)
                    else:
                        session.add(CachedUser(
                            cluster_id=cluster_id,
                            ts_guid=user.id,
                            username=user.name,
                            display_name=user.display_name,
                            email=user.email,
                            status=user.status.value,
                            created_at=user.created,
                            modified_at=user.modified,
                            synced_at=datetime.now(timezone.utc),
                        ))

                    # Upsert org membership
                    membership = session.exec(
                        select(UserOrgMembership).where(
                            UserOrgMembership.cluster_id == cluster_id,
                            UserOrgMembership.ts_guid == user.id,
                            UserOrgMembership.org_id == org_id,
                        )
                    ).first()
                    if not membership:
                        session.add(UserOrgMembership(
                            cluster_id=cluster_id,
                            ts_guid=user.id,
                            org_id=org_id,
                            synced_at=datetime.now(timezone.utc),
                        ))

                    session.commit()
                    count += 1

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log("users", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms)
    mark_complete(job_id, {"entity_type": "users", "record_count": count})
    logger.info("Synced %d users for cluster=%s org=%s", count, cluster_id, org_id)


async def _sync_groups(*, org_id: int, job_id: str) -> None:
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.models.cache.ts_group import CachedGroup
    from sqlmodel import select
    import json

    config = load_config()
    cluster = config.active_cluster
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)
    count = 0

    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy()) as client:
        async for page in client.search_groups(org_id=org_id):
            with get_session() as session:
                for group in page:
                    existing = session.exec(
                        select(CachedGroup).where(
                            CachedGroup.cluster_id == cluster_id,
                            CachedGroup.org_id == org_id,
                            CachedGroup.ts_guid == group.id,
                        )
                    ).first()

                    if existing:
                        existing.name = group.name
                        existing.display_name = group.display_name
                        existing.description = group.description
                        existing.privileges = json.dumps(group.privileges)
                        existing.synced_at = datetime.now(timezone.utc)
                        session.add(existing)
                    else:
                        session.add(CachedGroup(
                            cluster_id=cluster_id,
                            org_id=org_id,
                            ts_guid=group.id,
                            name=group.name,
                            display_name=group.display_name,
                            description=group.description,
                            privileges=json.dumps(group.privileges),
                            created_at=group.created,
                            modified_at=group.modified,
                            synced_at=datetime.now(timezone.utc),
                        ))

                    session.commit()
                    count += 1

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log("groups", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms)
    mark_complete(job_id, {"entity_type": "groups", "record_count": count})
    logger.info("Synced %d groups for cluster=%s org=%s", count, cluster_id, org_id)


async def _sync_metadata(*, org_id: int, job_id: str) -> None:
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.models.cache.ts_metadata import CachedMetadata
    import json

    config = load_config()
    cluster = config.active_cluster
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)
    count = 0

    # Delete all existing rows for this org before re-syncing so stale objects
    # (deleted in TS since last sync) don't linger in the cache.
    from sqlmodel import delete as sql_delete
    with get_session() as session:
        session.exec(
            sql_delete(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
            )
        )
        session.commit()

    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy(org_id=org_id)) as client:
        async for page in client.search_metadata():
            with get_session() as session:
                for obj in page:
                    tag_names = json.dumps([t.name for t in obj.tags])
                    session.add(CachedMetadata(
                        cluster_id=cluster_id,
                        org_id=org_id,
                        ts_guid=obj.id,
                        name=obj.name,
                        object_type=obj.type.value,
                        owner_guid=obj.owner_id,
                        owner_name=obj.author_name,
                        tag_names=tag_names,
                        created_at=obj.created,
                        modified_at=obj.modified,
                        last_accessed_at=obj.last_accessed,
                        view_count=obj.view_count,
                        synced_at=datetime.now(timezone.utc),
                    ))
                    count += 1
                session.commit()

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log("metadata", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms)
    mark_complete(job_id, {"entity_type": "metadata", "record_count": count})
    logger.info("Synced %d metadata objects for cluster=%s org=%s", count, cluster_id, org_id)


async def _sync_tags(*, org_id: int, job_id: str) -> None:
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.models.cache.ts_tag import CachedTag
    from sqlmodel import select

    config = load_config()
    cluster = config.active_cluster
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)

    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy()) as client:
        tags = await client.search_tags()

    with get_session() as session:
        for tag in tags:
            existing = session.exec(
                select(CachedTag).where(
                    CachedTag.cluster_id == cluster_id,
                    CachedTag.org_id == org_id,
                    CachedTag.ts_guid == tag.id,
                )
            ).first()

            if existing:
                existing.name = tag.name
                existing.color = tag.color
                session.add(existing)
            else:
                session.add(CachedTag(
                    cluster_id=cluster_id,
                    org_id=org_id,
                    ts_guid=tag.id,
                    name=tag.name,
                    color=tag.color,
                ))
        session.commit()

    count = len(tags)
    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log("tags", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms)
    mark_complete(job_id, {"entity_type": "tags", "record_count": count})


async def _sync_orgs(*, org_id: int, job_id: str) -> None:
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.models.cache.ts_org import CachedOrg
    from sqlmodel import select

    config = load_config()
    cluster = config.active_cluster
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)

    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy()) as client:
        orgs = await client.search_orgs()

    with get_session() as session:
        for org in orgs:
            existing = session.exec(
                select(CachedOrg).where(
                    CachedOrg.cluster_id == cluster_id,
                    CachedOrg.ts_org_id == org.id,
                )
            ).first()

            if existing:
                existing.name = org.name
                existing.description = org.description
                existing.status = org.status.value
                existing.is_primary = org.is_primary
                session.add(existing)
            else:
                session.add(CachedOrg(
                    cluster_id=cluster_id,
                    ts_org_id=org.id,
                    name=org.name,
                    description=org.description,
                    status=org.status.value,
                    is_primary=org.is_primary,
                ))
        session.commit()

    count = len(orgs)
    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log("orgs", org_id=0, status="SUCCESS", record_count=count, duration_ms=duration_ms)
    mark_complete(job_id, {"entity_type": "orgs", "record_count": count})


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _write_sync_log(
    entity_type: str,
    org_id: int,
    *,
    status: str,
    record_count: int = 0,
    duration_ms: int = 0,
    error: str | None = None,
) -> None:
    from ts_admin.config import load_config
    from sqlmodel import select

    config = load_config()
    cluster_id = config.active_cluster.id

    with get_session() as session:
        existing = session.exec(
            select(SyncLog).where(
                SyncLog.cluster_id == cluster_id,
                SyncLog.org_id == org_id,
                SyncLog.entity_type == entity_type,
            )
        ).first()

        if existing:
            existing.synced_at = datetime.now(timezone.utc)
            existing.record_count = record_count
            existing.duration_ms = duration_ms
            existing.status = status
            existing.error = error
            session.add(existing)
        else:
            session.add(SyncLog(
                cluster_id=cluster_id,
                org_id=org_id,
                entity_type=entity_type,
                record_count=record_count,
                duration_ms=duration_ms,
                status=status,
                error=error,
            ))
        session.commit()
