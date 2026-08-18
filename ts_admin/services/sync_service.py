"""
Sync service — fetches data from ThoughtSpot and upserts into SQLite cache.

Each entity type has its own sync function. All follow the same pattern:
  1. Mark job as RUNNING
  2. Paginate through TS API, upsert rows into SQLite
  3. Write SyncLog entry
  4. Mark job as COMPLETE or FAILED
"""

import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from ts_admin.database import get_session
from ts_admin.models.sync_log import SyncLog
from ts_admin.services import connection_status
from ts_admin.services.job_service import mark_complete, mark_failed, mark_running, update_progress
from ts_admin.ts_client.exceptions import (
    TSAuthenticationError,
    TSInsufficientPrivilegesError,
)

logger = logging.getLogger(__name__)


def _resolve_cluster(cluster_id: str | None):
    """
    Cluster a sync must run against.

    The caller (the UI) names the cluster explicitly; ``None`` falls back to the
    configured active cluster for callers that predate the parameter. Resolving
    from the caller matters because the shell can be pointed at one cluster while
    another is marked active — syncing the active one then writes rows the user
    is not looking at, or fails against an org that only exists on the other side.
    """
    from ts_admin.config import load_config

    config = load_config()
    if cluster_id is None:
        return config.active_cluster
    cluster = config.clusters.get(cluster_id)
    if cluster is None:
        raise ValueError(f"Cluster {cluster_id!r} not found in config")
    return cluster


async def run_sync(*, entity_type: str, org_id: int, job_id: str, cluster_id: str | None = None) -> None:
    """
    Entry point for all sync operations. Dispatches to the correct handler.
    Called as a FastAPI BackgroundTask.
    """
    handler = sync_handlers().get(entity_type)
    if not handler:
        mark_failed(job_id, f"Unknown entity type: {entity_type!r}")
        return

    # Resolve once so the health/sync-log writes below name the cluster that was
    # actually synced, including when the caller left it implicit.
    from ts_admin.ts_client.exceptions import ConfigNotFoundError

    try:
        target_id: str | None = _resolve_cluster(cluster_id).id
    except (ConfigNotFoundError, ValueError) as exc:
        mark_failed(job_id, exc)
        return

    try:
        await handler(org_id=org_id, job_id=job_id, target_cluster_id=target_id)
    except TSAuthenticationError as exc:
        # A live sync just proved the session is dead — flip the cluster's
        # health so the "Connected" badge reflects reality instead of waiting
        # for the user to notice a buried FAILED job.
        if target_id:
            connection_status.mark_expired(target_id, detail=str(exc))
        logger.warning("Sync auth-failed for %s org=%s: %s", entity_type, org_id, exc)
        mark_failed(job_id, exc)
        _write_sync_log(entity_type, org_id, status="FAILED", error=str(exc), cluster_id=target_id)
    except TSInsufficientPrivilegesError as exc:
        # A privilege/org-access denial is NOT a dead session — the credentials
        # are valid, the account just can't reach this org (or lacks a privilege).
        # Do NOT flip the whole cluster to "expired": that sends the user into a
        # pointless reconnect loop. Fail just this job with an actionable message.
        logger.warning("Sync denied for %s org=%s: %s", entity_type, org_id, exc)
        mark_failed(job_id, exc)
        _write_sync_log(entity_type, org_id, status="FAILED", error=str(exc), cluster_id=target_id)
    except Exception as exc:
        logger.exception("Sync failed for %s org=%s: %s", entity_type, org_id, exc)
        mark_failed(job_id, exc)
        _write_sync_log(entity_type, org_id, status="FAILED", error=str(exc), cluster_id=target_id)
    else:
        # A successful sync confirms the session is live.
        if target_id:
            connection_status.mark_connected(target_id)


# ── Per-entity sync handlers ───────────────────────────────────────────────────


async def _sync_users(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
    from sqlmodel import select

    from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _resolve_cluster(target_cluster_id)
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
                        existing.created_at = user.created
                        existing.modified_at = user.modified
                        existing.synced_at = datetime.now(timezone.utc)
                        session.add(existing)
                    else:
                        session.add(
                            CachedUser(
                                cluster_id=cluster_id,
                                ts_guid=user.id,
                                username=user.name,
                                display_name=user.display_name,
                                email=user.email,
                                status=user.status.value,
                                created_at=user.created,
                                modified_at=user.modified,
                                synced_at=datetime.now(timezone.utc),
                            )
                        )

                    # Upsert org membership
                    membership = session.exec(
                        select(UserOrgMembership).where(
                            UserOrgMembership.cluster_id == cluster_id,
                            UserOrgMembership.ts_guid == user.id,
                            UserOrgMembership.org_id == org_id,
                        )
                    ).first()
                    if not membership:
                        session.add(
                            UserOrgMembership(
                                cluster_id=cluster_id,
                                ts_guid=user.id,
                                org_id=org_id,
                                synced_at=datetime.now(timezone.utc),
                            )
                        )

                    session.commit()
                    count += 1
            # Report progress after each page so the UI counter climbs live.
            # The TS search API doesn't return a grand total, so `total` stays 0
            # (indeterminate) — the frontend shows a running count instead.
            update_progress(job_id, count)

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log(
        "users", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms, cluster_id=cluster_id
    )
    mark_complete(job_id, {"entity_type": "users", "record_count": count})
    logger.info("Synced %d users for cluster=%s org=%s", count, cluster_id, org_id)


async def _sync_groups(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
    import json

    from sqlmodel import col, select
    from sqlmodel import delete as sql_delete

    from ts_admin.models.cache.ts_group import CachedGroup
    from ts_admin.models.cache.ts_user import UserGroupMembership
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _resolve_cluster(target_cluster_id)
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)
    count = 0
    seen_guids: set[str] = set()

    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy()) as client:
        async for page in client.search_groups(org_id=org_id):
            with get_session() as session:
                for group in page:
                    seen_guids.add(group.id)
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
                        existing.author_guid = group.author_id
                        existing.created_at = group.created
                        existing.modified_at = group.modified
                        existing.synced_at = datetime.now(timezone.utc)
                        session.add(existing)
                    else:
                        session.add(
                            CachedGroup(
                                cluster_id=cluster_id,
                                org_id=org_id,
                                ts_guid=group.id,
                                name=group.name,
                                display_name=group.display_name,
                                description=group.description,
                                privileges=json.dumps(group.privileges),
                                author_guid=group.author_id,
                                created_at=group.created,
                                modified_at=group.modified,
                                synced_at=datetime.now(timezone.utc),
                            )
                        )

                    # Rewrite this group's membership rows in the same commit,
                    # so a mid-sync failure never leaves a group half-written.
                    session.exec(
                        sql_delete(UserGroupMembership).where(
                            UserGroupMembership.cluster_id == cluster_id,
                            UserGroupMembership.org_id == org_id,
                            UserGroupMembership.group_guid == group.id,
                        )
                    )
                    for user_guid in group.member_users:
                        session.add(
                            UserGroupMembership(
                                cluster_id=cluster_id,
                                org_id=org_id,
                                user_guid=user_guid,
                                group_guid=group.id,
                                synced_at=datetime.now(timezone.utc),
                            )
                        )

                    session.commit()
                    count += 1
            update_progress(job_id, count)

    # Success path only: purge groups (and their memberships) that no longer
    # exist upstream, so deleted groups don't linger in the cache.
    #
    # Skipped when the sweep saw nothing: SQLAlchemy renders `not_in(<empty>)`
    # as `NOT IN (NULL) OR (1 = 1)` — always true — so an empty page would
    # delete every group and membership for the org. A zero-result response is
    # indistinguishable from "the org really has no groups", and the wrong
    # guess here is unrecoverable without a re-sync, so we keep the cache.
    if not seen_guids:
        logger.warning(
            "Groups sync for cluster=%s org=%s returned no groups; skipping purge to protect the cache",
            cluster_id,
            org_id,
        )
    else:
        with get_session() as session:
            session.exec(
                sql_delete(CachedGroup).where(
                    CachedGroup.cluster_id == cluster_id,
                    CachedGroup.org_id == org_id,
                    col(CachedGroup.ts_guid).not_in(seen_guids),
                )
            )
            session.exec(
                sql_delete(UserGroupMembership).where(
                    UserGroupMembership.cluster_id == cluster_id,
                    UserGroupMembership.org_id == org_id,
                    col(UserGroupMembership.group_guid).not_in(seen_guids),
                )
            )
            session.commit()

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log(
        "groups", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms, cluster_id=cluster_id
    )
    mark_complete(job_id, {"entity_type": "groups", "record_count": count})
    logger.info("Synced %d groups for cluster=%s org=%s", count, cluster_id, org_id)


async def _sync_metadata(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
    import json

    from ts_admin.models.cache.ts_metadata import CachedMetadata
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _resolve_cluster(target_cluster_id)
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)
    count = 0

    # Write-ahead invalidation. Everything below this line leaves the cache in a
    # non-empty but TRUNCATED shape if it is interrupted: we delete every row for
    # the org, then re-page in spec order (liveboards + answers first, models and
    # tables last), committing per page. A row count cannot distinguish "truncated"
    # from "healthy", so the sync_log row is the completeness signal — and it must
    # stop saying SUCCESS *before* the destruction starts, not after it finishes.
    #
    # This MUST stay in its own get_session() block, separate from the delete
    # below: merging them would put both in one transaction, so a crash mid-crawl
    # could roll the marker back and re-expose the stale SUCCESS. The ordering is
    # the whole mechanism.
    #
    # preserve_progress: this marker says "a sync is running", not "a sync
    # finished with 0 rows just now". Keeping the previous synced_at/record_count
    # is what lets the UI still say when the cache was last known complete.
    _write_sync_log("metadata", org_id, status="IN_PROGRESS", preserve_progress=True, cluster_id=cluster_id)

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
                    session.add(
                        CachedMetadata(
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
                        )
                    )
                    count += 1
                session.commit()
            update_progress(job_id, count)

    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log(
        "metadata", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms, cluster_id=cluster_id
    )
    mark_complete(job_id, {"entity_type": "metadata", "record_count": count})
    logger.info("Synced %d metadata objects for cluster=%s org=%s", count, cluster_id, org_id)


async def _sync_tags(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
    from sqlmodel import select

    from ts_admin.models.cache.ts_tag import CachedTag
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _resolve_cluster(target_cluster_id)
    cluster_id = cluster.id
    start = datetime.now(timezone.utc)

    mark_running(job_id, total=0)

    async with ThoughtSpotClient(url=cluster.url, auth=cluster.build_auth_strategy(org_id=org_id)) as client:
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
                session.add(
                    CachedTag(
                        cluster_id=cluster_id,
                        org_id=org_id,
                        ts_guid=tag.id,
                        name=tag.name,
                        color=tag.color,
                    )
                )
        session.commit()

    count = len(tags)
    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log(
        "tags", org_id, status="SUCCESS", record_count=count, duration_ms=duration_ms, cluster_id=cluster_id
    )
    mark_complete(job_id, {"entity_type": "tags", "record_count": count})


async def _sync_orgs(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
    from sqlmodel import select

    from ts_admin.models.cache.ts_org import CachedOrg
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _resolve_cluster(target_cluster_id)
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
                session.add(
                    CachedOrg(
                        cluster_id=cluster_id,
                        ts_org_id=org.id,
                        name=org.name,
                        description=org.description,
                        status=org.status.value,
                        is_primary=org.is_primary,
                    )
                )
        session.commit()

    count = len(orgs)
    duration_ms = int((datetime.now(timezone.utc) - start).total_seconds() * 1000)
    _write_sync_log(
        "orgs", org_id=0, status="SUCCESS", record_count=count, duration_ms=duration_ms, cluster_id=cluster_id
    )
    mark_complete(job_id, {"entity_type": "orgs", "record_count": count})


async def _sync_dependencies(*, org_id: int, job_id: str, target_cluster_id: str | None = None) -> None:
    """
    Build the Relationship Visualizer's lineage graph. Delegates to
    lineage_service; reuses run_sync's auth/privilege error handling verbatim.

    Runs the cheap object tier first and commits it (graph becomes queryable),
    then enriches with the column map + connection/liveboard edges from TML.
    Never part of trigger_sync_all — explicitly gated per ADR-005.
    """
    from ts_admin.services import lineage_service
    from ts_admin.services.job_service import mark_complete

    cluster_id = _resolve_cluster(target_cluster_id).id
    has_column_pass = hasattr(lineage_service, "build_column_map")

    # Phase 1: object tier — commits edges + writes the "dependencies" SyncLog so
    # the graph is queryable while the (longer) TML column pass runs. Defer job
    # completion to the end when a column pass follows.
    edge_count = await lineage_service.build_object_graph(
        cluster_id=cluster_id, org_id=org_id, job_id=job_id, finalize=not has_column_pass
    )

    if not has_column_pass:
        return

    # Phase 2: column map + connection/liveboard edges (best-effort enrichment;
    # a failure here must not undo the object tier already committed above).
    # Only a genuine session death (auth) propagates — so run_sync flips the
    # cluster to expired. A missing DATADOWNLOADING privilege (or any other error)
    # is swallowed: the object graph stands, columns just stay empty, job completes.
    column_count = 0
    column_error: str | None = None
    try:
        column_count = await lineage_service.build_column_map(cluster_id=cluster_id, org_id=org_id, job_id=job_id)
    except TSAuthenticationError:
        raise
    except Exception as exc:
        column_error = str(exc)
        logger.warning("Column-map pass failed (object tier kept): %s", exc)

    # A swallowed column failure used to be invisible: the job read COMPLETE and
    # the graph just silently had no connection nodes and no column map. Carry
    # the reason into the job result so the Jobs UI can show what was skipped.
    result: dict = {"entity_type": "dependencies", "record_count": edge_count, "column_count": column_count}
    if column_error:
        result["column_error"] = column_error[:500]
    mark_complete(job_id, result)


def sync_handlers() -> dict[str, Callable[..., Awaitable[None]]]:
    """
    Every entity `run_sync` can dispatch.

    Exposed (rather than inlined in `run_sync`) so the API allowlist can be
    checked against it: an entity in `api.sync.VALID_ENTITIES` with no handler
    here 200s with a job id and then fails the job before the response renders —
    which is exactly what "permissions" did for months.
    `tests/unit/test_sync_entities.py` pins the two together.

    A function, NOT a module-level dict: a dict built at import time binds the
    original function objects, so monkeypatching `sync_service._sync_metadata`
    would no longer swap the dispatch target and several existing tests would
    silently exercise the real handler (measured — 4 failures).
    """
    return {
        "users": _sync_users,
        "groups": _sync_groups,
        "metadata": _sync_metadata,
        "tags": _sync_tags,
        "orgs": _sync_orgs,
        "dependencies": _sync_dependencies,
    }


# ── Shared helpers ─────────────────────────────────────────────────────────────


def _write_sync_log(
    entity_type: str,
    org_id: int,
    *,
    status: str,
    record_count: int = 0,
    duration_ms: int = 0,
    error: str | None = None,
    preserve_progress: bool = False,
    cluster_id: str | None = None,
) -> None:
    """Upsert the single ``(cluster_id, org_id, entity_type)`` sync_log row.

    ``preserve_progress=True`` is for the *write-ahead* marker only: it flips
    ``status`` without touching ``synced_at`` / ``record_count`` /
    ``duration_ms``. Those three describe the last COMPLETED sync, and a
    write-ahead marker is written before any work happens — overwriting them
    with "now / 0" destroys the only record of when the cache was last known
    complete, and makes an in-flight sync indistinguishable from one that just
    finished with zero rows. SUCCESS/FAILED semantics are unchanged: those are
    terminal, and their counts and timestamp ARE the outcome.
    """
    from sqlmodel import select

    # The row must be keyed by the cluster that was synced, not by whichever
    # cluster happens to be marked active — otherwise a sync of cluster A
    # stamps its freshness onto cluster B's row.
    cluster_id = _resolve_cluster(cluster_id).id

    with get_session() as session:
        existing = session.exec(
            select(SyncLog).where(
                SyncLog.cluster_id == cluster_id,
                SyncLog.org_id == org_id,
                SyncLog.entity_type == entity_type,
            )
        ).first()

        if existing:
            if not preserve_progress:
                existing.synced_at = datetime.now(timezone.utc)
                existing.record_count = record_count
                existing.duration_ms = duration_ms
            existing.status = status
            existing.error = error
            session.add(existing)
        else:
            session.add(
                SyncLog(
                    cluster_id=cluster_id,
                    org_id=org_id,
                    entity_type=entity_type,
                    record_count=record_count,
                    duration_ms=duration_ms,
                    status=status,
                    error=error,
                )
            )
        session.commit()
