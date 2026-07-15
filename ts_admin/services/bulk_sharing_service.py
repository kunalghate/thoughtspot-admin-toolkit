"""
bulk_sharing_service — share many objects with many principals in one job.

Two intake modes wrap the same write pipeline:
  - object_guids list  (selection intake — caller provides explicit GUIDs)
  - tag_name           (by-tag intake — resolve to GUIDs from CachedMetadata)

Pipeline:
  preview_share() — for each (object × principal), look up current ACL via
                    ts_client.fetch_permissions and diff against the proposed
                    mode. Returns a row per (object × principal) pair.
  execute_share() — bucket calls by object_type, issue one share_objects
                    request per (type, principal-set, mode) tuple, write a
                    ShareRecord per pair + a single AuditLog at the end.
"""

from __future__ import annotations

import asyncio
import logging

from sqlmodel import Session, col, func, select

import ts_admin.database as _db
from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
from ts_admin.models.share_record import ShareRecord

logger = logging.getLogger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────


def _chunks(lst: list, size: int):
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _get_cluster(cluster_id: str):
    from ts_admin.config import load_config

    config = load_config()
    cluster = config.clusters.get(cluster_id)
    if cluster is None:
        raise ValueError(f"Cluster {cluster_id!r} not found in config")
    return cluster


# ── Principal picker ──────────────────────────────────────────────────────────


def list_principals(
    *,
    cluster_id: str,
    org_id: int,
    search: str | None,
    include_users: bool,
    include_groups: bool,
    limit: int,
) -> list[dict]:
    """Combined users + groups list for the principal picker."""
    items: list[dict] = []
    with Session(_db.get_engine()) as session:
        if include_users:
            uq = (
                select(CachedUser)
                .join(
                    UserOrgMembership,
                    (UserOrgMembership.ts_guid == CachedUser.ts_guid)
                    & (UserOrgMembership.cluster_id == CachedUser.cluster_id),
                )
                .where(
                    CachedUser.cluster_id == cluster_id,
                    UserOrgMembership.org_id == org_id,
                )
            )
            if search:
                pattern = f"%{search}%"
                uq = uq.where(col(CachedUser.username).ilike(pattern) | col(CachedUser.display_name).ilike(pattern))
            uq = uq.limit(limit)
            for u in session.exec(uq).all():
                items.append(
                    {
                        "ts_guid": u.ts_guid,
                        "name": u.username,
                        "display_name": u.display_name or u.username,
                        "principal_type": "USER",
                    }
                )

        if include_groups:
            gq = select(CachedGroup).where(
                CachedGroup.cluster_id == cluster_id,
                CachedGroup.org_id == org_id,
            )
            if search:
                pattern = f"%{search}%"
                gq = gq.where(col(CachedGroup.name).ilike(pattern) | col(CachedGroup.display_name).ilike(pattern))
            gq = gq.limit(limit)
            for g in session.exec(gq).all():
                items.append(
                    {
                        "ts_guid": g.ts_guid,
                        "name": g.name,
                        "display_name": g.display_name or g.name,
                        "principal_type": "USER_GROUP",
                    }
                )

    items.sort(key=lambda x: (x["principal_type"], x["display_name"].lower()))
    return items[:limit]


# ── Tag intake ─────────────────────────────────────────────────────────────────


def resolve_tag_to_guids(*, cluster_id: str, org_id: int, tag_name: str) -> list[str]:
    """Return CachedMetadata GUIDs that carry the given tag name."""
    with Session(_db.get_engine()) as session:
        rows = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
            )
        ).all()
    return [r.ts_guid for r in rows if tag_name in r.get_tag_names()]


# ── Preview ────────────────────────────────────────────────────────────────────


async def preview_share(
    *,
    cluster_id: str,
    org_id: int,
    object_guids: list[str],
    principal_guids: list[str],
    mode: str,
) -> dict:
    """
    For each (object × principal) pair, return the current ACL vs proposed mode.

    Live call to fetch_permissions per object (cached objects aren't enough —
    permissions are not in the local cache by default for fresh-enough data).
    Concurrency capped to 10 to avoid hammering the cluster.
    """
    from ts_admin.ts_client import ThoughtSpotClient

    with Session(_db.get_engine()) as session:
        objs = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                col(CachedMetadata.ts_guid).in_(object_guids),
            )
        ).all()
        obj_map = {o.ts_guid: o for o in objs}

        # Principal display names for the preview
        user_map = {
            u.ts_guid: u
            for u in session.exec(
                select(CachedUser).where(
                    CachedUser.cluster_id == cluster_id,
                    col(CachedUser.ts_guid).in_(principal_guids),
                )
            ).all()
        }
        group_map = {
            g.ts_guid: g
            for g in session.exec(
                select(CachedGroup).where(
                    CachedGroup.cluster_id == cluster_id,
                    CachedGroup.org_id == org_id,
                    col(CachedGroup.ts_guid).in_(principal_guids),
                )
            ).all()
        }

    cluster = _get_cluster(cluster_id)
    sem = asyncio.Semaphore(10)

    async with ThoughtSpotClient(
        url=cluster.url,
        auth=cluster.build_auth_strategy(org_id=org_id),
    ) as client:

        async def _fetch(guid: str, obj_type: str):
            async with sem:
                try:
                    perms = await client.fetch_permissions(ts_guid=guid, object_type=obj_type)
                    return guid, perms, None
                except Exception as exc:
                    logger.warning("fetch_permissions failed for %s: %s", guid, exc)
                    return guid, [], exc

        tasks = [_fetch(g, obj_map[g].object_type) for g in object_guids if g in obj_map]
        results = await asyncio.gather(*tasks)

    # Build a (guid, principal) → current mode map
    current_acl: dict[tuple[str, str], str] = {}
    for guid, perms, _err in results:
        for p in perms:
            current_acl[(guid, p.principal_id)] = str(p.share_mode)

    rows: list[dict] = []
    will_change = 0
    for guid in object_guids:
        obj = obj_map.get(guid)
        if obj is None:
            continue
        for pid in principal_guids:
            previous = current_acl.get((guid, pid), "")
            changing = previous != mode
            if changing:
                will_change += 1
            principal_name = ""
            principal_type = "USER"
            if pid in user_map:
                principal_name = user_map[pid].display_name or user_map[pid].username
                principal_type = "USER"
            elif pid in group_map:
                principal_name = group_map[pid].display_name or group_map[pid].name
                principal_type = "USER_GROUP"
            else:
                principal_name = pid
            rows.append(
                {
                    "object_guid": guid,
                    "object_name": obj.name,
                    "object_type": obj.object_type,
                    "principal_guid": pid,
                    "principal_name": principal_name,
                    "principal_type": principal_type,
                    "previous_mode": previous,
                    "new_mode": mode,
                    "will_change": changing,
                }
            )

    return {"items": rows, "total": len(rows), "will_change_count": will_change}


# ── Dry run ──────────────────────────────────────────────────────────────────


async def dryrun_share(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_guids: list[str],
    principal_guids: list[str],
    mode: str,
) -> None:
    """
    Live, no-write impact check for a proposed share.

    Runs the same live ACL diff as :func:`preview_share` but as a background job,
    storing the summary in ``Job.result`` and writing nothing. This is the
    dry-run that gates the destructive ``execute_share`` (especially ``NO_ACCESS``
    revokes), satisfying the dry-run safety contract.
    """
    from ts_admin.services.job_service import mark_complete, mark_failed, mark_running

    mark_running(job_id, len(object_guids) * len(principal_guids))
    try:
        preview = await preview_share(
            cluster_id=cluster_id,
            org_id=org_id,
            object_guids=object_guids,
            principal_guids=principal_guids,
            mode=mode,
        )
        result = {
            "total": preview["total"],
            "will_change_count": preview["will_change_count"],
            "items": preview["items"][:500],  # cap the stored sample
        }
        mark_complete(job_id, result)
        logger.info(
            "dryrun_share job=%s cluster=%s total=%d will_change=%d",
            job_id,
            cluster_id,
            result["total"],
            result["will_change_count"],
        )
    except Exception as exc:
        logger.exception("dryrun_share job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


# ── Execute ────────────────────────────────────────────────────────────────────


async def execute_share(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_guids: list[str],
    principal_guids: list[str],
    mode: str,
    notify: bool = False,
) -> None:
    """
    Share `object_guids` with `principal_guids` at `mode`. One share_objects
    call per object_type group (the API requires a single type per call).
    """
    from ts_admin.services.job_service import (
        is_cancelled,
        mark_complete,
        mark_failed,
        mark_partial,
        mark_running,
        update_progress,
    )
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.ts_client.models import SharePermission

    total_pairs = len(object_guids) * len(principal_guids)
    mark_running(job_id, total_pairs)

    # Resolve to display data for ShareRecord
    with Session(_db.get_engine()) as session:
        objs = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                col(CachedMetadata.ts_guid).in_(object_guids),
            )
        ).all()
        obj_map = {o.ts_guid: o for o in objs}
        users = session.exec(
            select(CachedUser).where(
                CachedUser.cluster_id == cluster_id,
                col(CachedUser.ts_guid).in_(principal_guids),
            )
        ).all()
        groups = session.exec(
            select(CachedGroup).where(
                CachedGroup.cluster_id == cluster_id,
                CachedGroup.org_id == org_id,
                col(CachedGroup.ts_guid).in_(principal_guids),
            )
        ).all()

    principal_meta: dict[str, dict] = {}
    for u in users:
        principal_meta[u.ts_guid] = {
            "name": u.display_name or u.username,
            "type": "USER",
        }
    for g in groups:
        principal_meta[g.ts_guid] = {
            "name": g.display_name or g.name,
            "type": "USER_GROUP",
        }
    for pid in principal_guids:
        principal_meta.setdefault(pid, {"name": pid, "type": "USER"})

    succeeded_pairs = 0
    failed_records: list[dict] = []
    cancelled = False

    try:
        enum_mode = SharePermission(mode)
    except ValueError:
        mark_failed(job_id, f"Invalid share mode {mode!r}")
        return

    try:
        cluster = _get_cluster(cluster_id)
        # Bucket objects by type — one share_objects call per (type, principals, mode)
        type_groups: dict[str, list[str]] = {}
        for guid in object_guids:
            t = obj_map.get(guid).object_type if guid in obj_map else "LIVEBOARD"
            type_groups.setdefault(t, []).append(guid)

        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            for obj_type, guids in type_groups.items():
                if cancelled:
                    break
                for chunk in _chunks(guids, 50):
                    if is_cancelled(job_id):
                        cancelled = True
                        break
                    try:
                        await client.share_objects(
                            object_ids=chunk,
                            principal_ids=principal_guids,
                            permission=enum_mode,
                        )
                        pair_count = len(chunk) * len(principal_guids)
                        succeeded_pairs += pair_count

                        # ShareRecord per (object × principal)
                        with Session(_db.get_engine()) as session:
                            for guid in chunk:
                                for pid in principal_guids:
                                    obj = obj_map.get(guid)
                                    meta = principal_meta.get(pid, {"name": pid, "type": "USER"})
                                    session.add(
                                        ShareRecord(
                                            cluster_id=cluster_id,
                                            job_id=job_id,
                                            org_id=org_id,
                                            object_guid=guid,
                                            object_name=obj.name if obj else "",
                                            object_type=obj_type,
                                            principal_guid=pid,
                                            principal_name=meta["name"],
                                            principal_type=meta["type"],
                                            new_mode=mode,
                                            status="SUCCESS",
                                        )
                                    )
                            session.commit()
                    except Exception as exc:
                        logger.warning("share_objects chunk failed (%s): %s", obj_type, exc)
                        failed_records.append({"object_type": obj_type, "guids": chunk, "error": str(exc)[:300]})
                        with Session(_db.get_engine()) as session:
                            for guid in chunk:
                                for pid in principal_guids:
                                    obj = obj_map.get(guid)
                                    meta = principal_meta.get(pid, {"name": pid, "type": "USER"})
                                    session.add(
                                        ShareRecord(
                                            cluster_id=cluster_id,
                                            job_id=job_id,
                                            org_id=org_id,
                                            object_guid=guid,
                                            object_name=obj.name if obj else "",
                                            object_type=obj_type,
                                            principal_guid=pid,
                                            principal_name=meta["name"],
                                            principal_type=meta["type"],
                                            new_mode=mode,
                                            status="FAILED",
                                            error=str(exc)[:300],
                                        )
                                    )
                            session.commit()
                    update_progress(job_id, succeeded_pairs)

        status = "PARTIAL" if (failed_records or cancelled) else "SUCCESS"
        with Session(_db.get_engine()) as session:
            audit = AuditLog(
                cluster_id=cluster_id,
                action_type="bulk_share",
                entity_type="metadata",
                items_affected=succeeded_pairs,
                status=status if status != "SUCCESS" else "SUCCESS",
            )
            audit.set_parameters(
                {
                    "object_guids": object_guids,
                    "principal_guids": principal_guids,
                    "mode": mode,
                    "notify": notify,
                    "succeeded_pairs": succeeded_pairs,
                    "total_pairs": total_pairs,
                    "failed": failed_records,
                    "cancelled": cancelled,
                }
            )
            session.add(audit)
            session.commit()

        result = {
            "succeeded_pairs": succeeded_pairs,
            "failed_pairs": total_pairs - succeeded_pairs,
            "total_pairs": total_pairs,
            "cancelled": cancelled,
        }
        if status == "PARTIAL":
            mark_partial(job_id, result)
        elif succeeded_pairs == 0 and not cancelled:
            mark_failed(job_id, "0 share operations succeeded")
        else:
            mark_complete(job_id, result)
        logger.info(
            "bulk_share job=%s cluster=%s succeeded=%d failed=%d cancelled=%s",
            job_id,
            cluster_id,
            succeeded_pairs,
            total_pairs - succeeded_pairs,
            cancelled,
        )
    except Exception as exc:
        logger.exception("execute_share job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


# ── History ────────────────────────────────────────────────────────────────────


def list_history(
    *,
    cluster_id: str,
    org_id: int | None,
    record_offset: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """Aggregate ShareRecord by job_id, newest first."""
    with Session(_db.get_engine()) as session:
        base = select(
            ShareRecord.job_id,
            func.min(ShareRecord.executed_at).label("executed_at"),
            func.count(func.distinct(ShareRecord.object_guid)).label("object_count"),
            func.count(func.distinct(ShareRecord.principal_guid)).label("principal_count"),
            func.sum(
                func.iif(ShareRecord.status == "SUCCESS", 1, 0)  # SQLite-friendly
            ).label("succeeded"),
            func.sum(func.iif(ShareRecord.status == "FAILED", 1, 0)).label("failed"),
        ).where(ShareRecord.cluster_id == cluster_id)
        if org_id is not None:
            base = base.where(ShareRecord.org_id == org_id)
        base = base.group_by(ShareRecord.job_id).order_by(func.min(ShareRecord.executed_at).desc())

        all_rows = session.exec(base).all()
        total = len(all_rows)
        page = all_rows[record_offset : record_offset + page_size]

        items = []
        for r in page:
            failed = r.failed or 0
            items.append(
                {
                    "job_id": r.job_id,
                    "executed_at": r.executed_at.isoformat() if r.executed_at else "",
                    "object_count": r.object_count or 0,
                    "principal_count": r.principal_count or 0,
                    "succeeded": r.succeeded or 0,
                    "failed": failed,
                    "status": "PARTIAL" if failed and r.succeeded else ("FAILED" if failed else "SUCCESS"),
                }
            )
        return items, total
