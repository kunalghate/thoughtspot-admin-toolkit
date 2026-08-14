"""
user_management_service — read/list users and execute the three offboarding actions.

Reads:
  - list_users()                  — paginated user grid from CachedUser
  - get_user(ts_guid)             — profile + group/org membership counts
  - preview_transfer()            — objects currently owned by a user (from cache)
  - preview_transfer_sharing()    — live API: what the source user can see
  - preview_delete()              — basic snapshot before delete

Writes (all run as background jobs):
  - execute_transfer()            — chunked reassign_metadata_owner
  - execute_transfer_sharing()    — re-share every visible object to the target
  - execute_delete()              — retry-to-10 user delete loop

Rules:
  - SQLite-only methods are sync; TS API methods are async.
  - Every write records a UserActionRecord row + AuditLog entry.
  - transfer-sharing refuses to target an admin (mirrors CS Tools behavior).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Literal

from sqlmodel import Session, col, func, select

import ts_admin.database as _db
from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import (
    CachedUser,
    UserGroupMembership,
    UserOrgMembership,
)
from ts_admin.models.user_action_record import UserActionRecord

logger = logging.getLogger(__name__)

# Hard-coded admin group names (cluster admins). Matches CS Tools behavior of
# refusing to push sharing onto admins who already see everything.
ADMIN_GROUP_NAMES = {"Administrator", "System User"}


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


def _user_row_to_dict(u: CachedUser) -> dict:
    return {
        "ts_guid": u.ts_guid,
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "status": u.status,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "modified_at": u.modified_at.isoformat() if u.modified_at else None,
        "synced_at": u.synced_at.isoformat() if u.synced_at else None,
    }


def _resolve_user(session: Session, cluster_id: str, identifier: str) -> CachedUser | None:
    """Look up a user by GUID or username (cluster-scoped)."""
    return session.exec(
        select(CachedUser).where(
            CachedUser.cluster_id == cluster_id,
            (CachedUser.ts_guid == identifier) | (CachedUser.username == identifier),
        )
    ).first()


def _is_admin(session: Session, cluster_id: str, user_guid: str) -> bool:
    """True if the user is a member of any admin-marker group."""
    rows = session.exec(
        select(CachedGroup.name)
        .join(
            UserGroupMembership,
            (UserGroupMembership.group_guid == CachedGroup.ts_guid)
            & (UserGroupMembership.cluster_id == CachedGroup.cluster_id),
        )
        .where(
            UserGroupMembership.cluster_id == cluster_id,
            UserGroupMembership.user_guid == user_guid,
        )
    ).all()
    return any(name in ADMIN_GROUP_NAMES for name in rows)


# ── List / detail (sync, SQLite) ──────────────────────────────────────────────


def list_users(
    *,
    cluster_id: str,
    org_id: int | None = None,
    status: str | None = None,
    search: str | None = None,
    sort_field: str = "username",
    sort_order: Literal["asc", "desc"] = "asc",
    record_offset: int = 0,
    page_size: int = 200,
) -> tuple[list[dict], int]:
    """Paginated user grid. If org_id is provided, joins through UserOrgMembership."""
    with Session(_db.get_engine()) as session:
        base = select(CachedUser).where(CachedUser.cluster_id == cluster_id)
        if org_id is not None:
            base = base.join(
                UserOrgMembership,
                (UserOrgMembership.ts_guid == CachedUser.ts_guid)
                & (UserOrgMembership.cluster_id == CachedUser.cluster_id),
            ).where(UserOrgMembership.org_id == org_id)
        if status:
            base = base.where(CachedUser.status == status.upper())
        if search:
            pattern = f"%{search}%"
            base = base.where(
                col(CachedUser.username).ilike(pattern)
                | col(CachedUser.display_name).ilike(pattern)
                | col(CachedUser.email).ilike(pattern)
            )

        # Total
        count_q = select(func.count()).select_from(base.subquery())
        total = session.exec(count_q).one()

        # Order
        sort_col = {
            "username": CachedUser.username,
            "display_name": CachedUser.display_name,
            "email": CachedUser.email,
            "status": CachedUser.status,
            "created_at": CachedUser.created_at,
            "modified_at": CachedUser.modified_at,
        }.get(sort_field, CachedUser.username)
        base = base.order_by(sort_col.desc() if sort_order == "desc" else sort_col.asc())
        base = base.offset(record_offset).limit(page_size)

        rows = session.exec(base).all()
        return [_user_row_to_dict(u) for u in rows], total


def get_user_detail(*, cluster_id: str, ts_guid: str) -> dict | None:
    """Single user + owned-object count + org/group memberships."""
    with Session(_db.get_engine()) as session:
        user = session.exec(
            select(CachedUser).where(
                CachedUser.cluster_id == cluster_id,
                CachedUser.ts_guid == ts_guid,
            )
        ).first()
        if user is None:
            return None

        owned_count = session.exec(
            select(func.count())
            .select_from(CachedMetadata)
            .where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.owner_guid == ts_guid,
            )
        ).one()

        org_ids = session.exec(
            select(UserOrgMembership.org_id).where(
                UserOrgMembership.cluster_id == cluster_id,
                UserOrgMembership.ts_guid == ts_guid,
            )
        ).all()

        group_rows = session.exec(
            select(CachedGroup)
            .join(
                UserGroupMembership,
                (UserGroupMembership.group_guid == CachedGroup.ts_guid)
                & (UserGroupMembership.cluster_id == CachedGroup.cluster_id),
            )
            .where(
                UserGroupMembership.cluster_id == cluster_id,
                UserGroupMembership.user_guid == ts_guid,
            )
            .order_by(col(CachedGroup.name).asc())
        ).all()

        out = _user_row_to_dict(user)
        out["owned_object_count"] = owned_count
        out["org_ids"] = list(org_ids)
        out["groups"] = [g.name for g in group_rows]
        out["group_details"] = [
            {
                "ts_guid": g.ts_guid,
                "name": g.name,
                "display_name": g.display_name,
                "privileges": g.get_privileges(),
            }
            for g in group_rows
        ]
        # Effective privileges: union of every group's privileges — "what this
        # user can do", since TS privileges are only granted through groups.
        out["privileges"] = sorted({p for g in group_rows for p in g.get_privileges()})
        out["is_admin"] = any(g.name in ADMIN_GROUP_NAMES for g in group_rows)
        return out


# ── Transfer ownership ─────────────────────────────────────────────────────────


def preview_transfer(
    *,
    cluster_id: str,
    org_id: int,
    from_user_guid: str,
    object_types: list[str] | None = None,
    tag_names: list[str] | None = None,
    explicit_guids: list[str] | None = None,
) -> dict:
    """
    Return objects currently owned by `from_user_guid` that will be reassigned.

    Filters narrow the set:
      - object_types: only these CachedMetadata.object_type values
      - tag_names:    only objects whose tag_names JSON contains every name listed
      - explicit_guids: only these GUIDs (intersected with the owner filter)
    """
    with Session(_db.get_engine()) as session:
        q = select(CachedMetadata).where(
            CachedMetadata.cluster_id == cluster_id,
            CachedMetadata.org_id == org_id,
            CachedMetadata.owner_guid == from_user_guid,
        )
        if object_types:
            q = q.where(col(CachedMetadata.object_type).in_(object_types))
        if explicit_guids:
            q = q.where(col(CachedMetadata.ts_guid).in_(explicit_guids))

        rows = session.exec(q).all()

        if tag_names:
            rows = [r for r in rows if all(t in r.get_tag_names() for t in tag_names)]

        items = [
            {
                "ts_guid": r.ts_guid,
                "name": r.name,
                "object_type": r.object_type,
                "owner_guid": r.owner_guid,
                "owner_name": r.owner_name,
                "modified_at": r.modified_at.isoformat() if r.modified_at else None,
                "tags": r.get_tag_names(),
            }
            for r in rows
        ]
        by_type: dict[str, int] = {}
        for r in rows:
            by_type[r.object_type] = by_type.get(r.object_type, 0) + 1
        return {"items": items, "total": len(items), "by_type": by_type}


async def execute_transfer(
    job_id: str,
    cluster_id: str,
    org_id: int,
    from_user_guid: str,
    to_user_identifier: str,
    object_ids: list[str],
) -> None:
    """Reassign ownership of `object_ids` to `to_user_identifier`. Chunked at 50."""
    from ts_admin.services.job_service import (
        is_cancelled,
        mark_complete,
        mark_failed,
        mark_partial,
        mark_running,
        update_progress,
    )
    from ts_admin.ts_client import ThoughtSpotClient

    total = len(object_ids)
    mark_running(job_id, total)

    succeeded = 0
    failed_chunks: list[dict] = []
    cancelled = False

    # Snapshot from / to identities for the record
    with Session(_db.get_engine()) as session:
        from_user = session.exec(
            select(CachedUser).where(
                CachedUser.cluster_id == cluster_id,
                CachedUser.ts_guid == from_user_guid,
            )
        ).first()
        to_user = _resolve_user(session, cluster_id, to_user_identifier)

    record = UserActionRecord(
        cluster_id=cluster_id,
        job_id=job_id,
        org_id=org_id,
        action_type="transfer",
        from_user_guid=from_user_guid,
        from_username=from_user.username if from_user else "",
        from_display_name=from_user.display_name if from_user else "",
        to_user_guid=to_user.ts_guid if to_user else "",
        to_username=to_user.username if to_user else to_user_identifier,
        to_display_name=to_user.display_name if to_user else "",
        items_total=total,
        status="PENDING",
    )
    record.set_affected([{"ts_guid": oid} for oid in object_ids[:200]])

    with Session(_db.get_engine(), expire_on_commit=False) as session:
        session.add(record)
        session.commit()
        record_id = record.id

    try:
        cluster = _get_cluster(cluster_id)
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            for chunk in _chunks(object_ids, 50):
                if is_cancelled(job_id):
                    cancelled = True
                    break
                try:
                    await client.assign_metadata_owner(
                        object_ids=chunk,
                        new_owner_identifier=to_user_identifier,
                    )
                    succeeded += len(chunk)
                    # Update CachedMetadata so the UI reflects new ownership immediately
                    with Session(_db.get_engine()) as session:
                        for guid in chunk:
                            obj = session.exec(
                                select(CachedMetadata).where(
                                    CachedMetadata.cluster_id == cluster_id,
                                    CachedMetadata.ts_guid == guid,
                                )
                            ).first()
                            if obj and to_user:
                                obj.owner_guid = to_user.ts_guid
                                obj.owner_name = to_user.display_name or to_user.username
                                session.add(obj)
                        session.commit()
                except Exception as exc:
                    logger.warning("assign_metadata_owner chunk failed: %s", exc)
                    failed_chunks.append({"guids": chunk, "error": str(exc)[:300]})
                update_progress(job_id, succeeded)

        status = "PARTIAL" if (failed_chunks or cancelled) else "SUCCESS"
        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.items_succeeded = succeeded
                rec.items_failed = total - succeeded
                rec.status = status
                session.add(rec)
                audit = AuditLog(
                    cluster_id=cluster_id,
                    action_type="transfer_ownership",
                    entity_type="user",
                    items_affected=succeeded,
                    status=status if status != "SUCCESS" else "SUCCESS",
                )
                audit.set_parameters(
                    {
                        "from_user_guid": from_user_guid,
                        "to_user_identifier": to_user_identifier,
                        "object_ids": object_ids,
                        "succeeded": succeeded,
                        "failed_chunks": failed_chunks,
                        "cancelled": cancelled,
                    }
                )
                session.add(audit)
                session.commit()

        result = {
            "succeeded": succeeded,
            "failed": total - succeeded,
            "cancelled": cancelled,
            "record_id": record_id,
        }
        if status == "PARTIAL":
            mark_partial(job_id, result)
        elif succeeded == 0:
            mark_failed(job_id, "0 objects transferred")
        else:
            mark_complete(job_id, result)
        logger.info(
            "transfer job=%s cluster=%s from=%s to=%s succeeded=%d failed=%d",
            job_id,
            cluster_id,
            from_user_guid,
            to_user_identifier,
            succeeded,
            total - succeeded,
        )
    except Exception as exc:
        logger.exception("execute_transfer job %s failed: %s", job_id, exc)
        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.status = "FAILED"
                rec.error = str(exc)[:500]
                session.add(rec)
                session.commit()
        mark_failed(job_id, exc)


# ── Transfer sharing (re-share what the source user can see) ──────────────────


async def get_user_access(*, cluster_id: str, org_id: int, ts_guid: str) -> dict:
    """
    Live API call: everything the user can currently see (defined permissions).

    Powers the audit section of the user detail drawer. Same fetch as the
    transfer-sharing preview, without the target-user validation.
    """
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _get_cluster(cluster_id)
    # fetch-permissions walks every ACL for the principal — routinely slower
    # than the default 30s window on content-heavy orgs.
    async with ThoughtSpotClient(
        url=cluster.url,
        auth=cluster.build_auth_strategy(org_id=org_id),
        timeout=120.0,
    ) as client:
        # EFFECTIVE resolves group-inherited access too — the audit answer to
        # "what can this user see", not just what was shared to them directly.
        rows = await client.principal_permissions(
            principal_identifier=ts_guid,
            permission_type="EFFECTIVE",
        )

    by_type: dict[str, int] = {}
    for r in rows:
        t = r.get("metadata_type", "")
        by_type[t] = by_type.get(t, 0) + 1

    return {"items": rows, "total": len(rows), "by_type": by_type}


async def preview_transfer_sharing(
    *,
    cluster_id: str,
    org_id: int,
    from_user_guid: str,
    to_user_identifier: str,
) -> dict:
    """
    Live API call: fetch everything the source user can see.

    Returns the row list and refuses (HTTP 422 from the router) if the
    target user is an admin in our cache.
    """
    from ts_admin.ts_client import ThoughtSpotClient

    # Refuse admin targets up-front
    with Session(_db.get_engine()) as session:
        target = _resolve_user(session, cluster_id, to_user_identifier)
        if target is not None and _is_admin(session, cluster_id, target.ts_guid):
            raise ValueError(
                f"Refusing to share with {target.username!r}: target is a cluster admin and already sees everything"
            )

    cluster = _get_cluster(cluster_id)
    async with ThoughtSpotClient(
        url=cluster.url,
        auth=cluster.build_auth_strategy(org_id=org_id),
    ) as client:
        rows = await client.principal_permissions(
            principal_identifier=from_user_guid,
        )

    by_type: dict[str, int] = {}
    for r in rows:
        t = r.get("metadata_type", "")
        by_type[t] = by_type.get(t, 0) + 1

    return {"items": rows, "total": len(rows), "by_type": by_type}


async def execute_transfer_sharing(
    job_id: str,
    cluster_id: str,
    org_id: int,
    from_user_guid: str,
    to_user_identifier: str,
    notify: bool = False,
) -> None:
    """
    Re-share every object the source user can see with the target, at the
    same access level. Implementation: fetch principal_permissions once,
    bucket by share_mode, issue one share_objects call per bucket.
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

    mark_running(job_id, 0)

    with Session(_db.get_engine()) as session:
        from_user = session.exec(
            select(CachedUser).where(
                CachedUser.cluster_id == cluster_id,
                CachedUser.ts_guid == from_user_guid,
            )
        ).first()
        to_user = _resolve_user(session, cluster_id, to_user_identifier)
        if to_user is not None and _is_admin(session, cluster_id, to_user.ts_guid):
            mark_failed(
                job_id,
                f"Refusing to share with {to_user.username!r}: target is a cluster admin",
            )
            return

    record = UserActionRecord(
        cluster_id=cluster_id,
        job_id=job_id,
        org_id=org_id,
        action_type="transfer_sharing",
        from_user_guid=from_user_guid,
        from_username=from_user.username if from_user else "",
        from_display_name=from_user.display_name if from_user else "",
        to_user_guid=to_user.ts_guid if to_user else "",
        to_username=to_user.username if to_user else to_user_identifier,
        to_display_name=to_user.display_name if to_user else "",
        status="PENDING",
    )
    with Session(_db.get_engine(), expire_on_commit=False) as session:
        session.add(record)
        session.commit()
        record_id = record.id

    try:
        cluster = _get_cluster(cluster_id)
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            rows = await client.principal_permissions(principal_identifier=from_user_guid)

            # Bucket by share_mode
            buckets: dict[str, list[str]] = {}
            for r in rows:
                mode = r.get("share_mode") or "READ_ONLY"
                buckets.setdefault(mode, []).append(r["metadata_id"])

            total = sum(len(v) for v in buckets.values())
            from ts_admin.services.job_service import mark_running as _mr

            _mr(job_id, total)

            succeeded = 0
            failed_buckets: list[dict] = []
            cancelled = False

            for mode, guids in buckets.items():
                if cancelled:
                    break
                try:
                    enum_mode = SharePermission(mode)
                except ValueError:
                    enum_mode = SharePermission.READ_ONLY

                for chunk in _chunks(guids, 50):
                    if is_cancelled(job_id):
                        cancelled = True
                        break
                    try:
                        await client.share_objects(
                            object_ids=chunk,
                            principal_ids=[to_user_identifier],
                            permission=enum_mode,
                        )
                        succeeded += len(chunk)
                    except Exception as exc:
                        logger.warning("share_objects chunk failed (%s): %s", mode, exc)
                        failed_buckets.append({"mode": mode, "guids": chunk, "error": str(exc)[:300]})
                    update_progress(job_id, succeeded)

        status = "PARTIAL" if (failed_buckets or cancelled) else "SUCCESS"
        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.items_total = total
                rec.items_succeeded = succeeded
                rec.items_failed = total - succeeded
                rec.status = status
                rec.set_affected(rows[:200])
                session.add(rec)
                audit = AuditLog(
                    cluster_id=cluster_id,
                    action_type="transfer_sharing",
                    entity_type="user",
                    items_affected=succeeded,
                    status=status if status != "SUCCESS" else "SUCCESS",
                )
                audit.set_parameters(
                    {
                        "from_user_guid": from_user_guid,
                        "to_user_identifier": to_user_identifier,
                        "notify": notify,
                        "total": total,
                        "succeeded": succeeded,
                        "failed_buckets": failed_buckets,
                        "cancelled": cancelled,
                    }
                )
                session.add(audit)
                session.commit()

        result = {
            "total": total,
            "succeeded": succeeded,
            "failed": total - succeeded,
            "cancelled": cancelled,
            "record_id": record_id,
        }
        if total == 0:
            mark_complete(job_id, result)
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        elif succeeded == 0:
            mark_failed(job_id, "0 shares applied")
        else:
            mark_complete(job_id, result)
        logger.info(
            "transfer_sharing job=%s cluster=%s from=%s to=%s succeeded=%d failed=%d",
            job_id,
            cluster_id,
            from_user_guid,
            to_user_identifier,
            succeeded,
            total - succeeded,
        )
    except Exception as exc:
        logger.exception("execute_transfer_sharing job %s failed: %s", job_id, exc)
        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.status = "FAILED"
                rec.error = str(exc)[:500]
                session.add(rec)
                session.commit()
        mark_failed(job_id, exc)


# ── Delete users ──────────────────────────────────────────────────────────────


def preview_delete(*, cluster_id: str, user_guids: list[str]) -> dict:
    """Snapshot users + owned-object counts so the UI can warn before delete."""
    with Session(_db.get_engine()) as session:
        users = session.exec(
            select(CachedUser).where(
                CachedUser.cluster_id == cluster_id,
                col(CachedUser.ts_guid).in_(user_guids),
            )
        ).all()
        found_guids = {u.ts_guid for u in users}
        unrecognized = [g for g in user_guids if g not in found_guids]

        items = []
        for u in users:
            owned = session.exec(
                select(func.count())
                .select_from(CachedMetadata)
                .where(
                    CachedMetadata.cluster_id == cluster_id,
                    CachedMetadata.owner_guid == u.ts_guid,
                )
            ).one()
            items.append(
                {
                    **_user_row_to_dict(u),
                    "owned_object_count": owned,
                    "is_admin": _is_admin(session, cluster_id, u.ts_guid),
                }
            )
        return {"items": items, "total": len(items), "unrecognized": unrecognized}


async def dryrun_delete(
    job_id: str,
    cluster_id: str,
    org_id: int,
    user_guids: list[str],
    user_identifiers: list[str] | None = None,
) -> None:
    """
    Live, no-write impact check for a proposed user deletion.

    Unlike :func:`preview_delete` (cache-only), this confirms against the live
    cluster which selected users still exist — catching cache drift where a user
    was already deleted upstream — and reports cached owned-object counts + admin
    flags. The summary lands in ``Job.result``; nothing is written to the DB.

    Mirrors the Deleter's job-based dryrun so it satisfies the dry-run safety
    contract (see ``tests/integration/test_dryrun_safety.py``).
    """
    from ts_admin.services.job_service import mark_complete, mark_failed, mark_running
    from ts_admin.ts_client import ThoughtSpotClient

    total = len(user_guids)
    mark_running(job_id, total)

    try:
        # Cache snapshot: owned-object counts + admin flags (no live call needed).
        snapshot = preview_delete(cluster_id=cluster_id, user_guids=user_guids)

        # Live existence check: page the org's users and build a lookup of what
        # actually exists upstream right now.
        cluster = _get_cluster(cluster_id)
        live_guids: set[str] = set()
        live_usernames: set[str] = set()
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            async for page in client.search_users(org_id=org_id):
                for u in page:
                    live_guids.add(u.id)
                    if u.name:
                        live_usernames.add(u.name)

        missing_live: list[str] = []
        for item in snapshot["items"]:
            exists = item["ts_guid"] in live_guids or item["username"] in live_usernames
            item["exists_live"] = exists
            if not exists:
                missing_live.append(item["username"] or item["ts_guid"])

        result = {
            "total": snapshot["total"],
            "items": snapshot["items"],
            "unrecognized": snapshot["unrecognized"],
            "missing_live": missing_live,
            "admin_count": sum(1 for i in snapshot["items"] if i["is_admin"]),
            "owned_total": sum(i["owned_object_count"] for i in snapshot["items"]),
        }
        mark_complete(job_id, result)
        logger.info(
            "dryrun_delete job=%s cluster=%s total=%d missing_live=%d",
            job_id,
            cluster_id,
            result["total"],
            len(missing_live),
        )
    except Exception as exc:
        logger.exception("dryrun_delete job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


async def execute_delete(
    job_id: str,
    cluster_id: str,
    org_id: int,
    user_guids: list[str],
    user_identifiers: list[str] | None = None,
) -> None:
    """
    Retry-to-10 delete loop, concurrency capped at 15. `user_identifiers`
    is the list of usernames/GUIDs to send to the TS API — defaults to
    user_guids if not provided (the caller is expected to pass either form).
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

    identifiers = list(user_identifiers or user_guids)
    total = len(identifiers)
    mark_running(job_id, total)

    # Snapshot identities for the record
    with Session(_db.get_engine()) as session:
        users = session.exec(
            select(CachedUser).where(
                CachedUser.cluster_id == cluster_id,
                col(CachedUser.ts_guid).in_(user_guids),
            )
        ).all()
    snapshot = [{"ts_guid": u.ts_guid, "username": u.username, "display_name": u.display_name} for u in users]

    record = UserActionRecord(
        cluster_id=cluster_id,
        job_id=job_id,
        org_id=org_id,
        action_type="delete",
        items_total=total,
        status="PENDING",
    )
    record.set_affected(snapshot)
    with Session(_db.get_engine(), expire_on_commit=False) as session:
        session.add(record)
        session.commit()
        record_id = record.id

    succeeded = 0
    succeeded_identifiers: set[str] = set()
    failed: dict[str, str] = {}  # identifier → last error
    cancelled = False

    try:
        cluster = _get_cluster(cluster_id)
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            pending: dict[str, int] = {ident: 0 for ident in identifiers}

            while pending and not cancelled:
                if is_cancelled(job_id):
                    cancelled = True
                    break

                # One identifier per call so per-user retries stay isolated.
                # Concurrency cap of 15 mirrors the deleter pattern.
                sem = asyncio.Semaphore(15)

                async def _delete_one(ident: str) -> tuple[str, Exception | None]:
                    async with sem:
                        try:
                            await client.delete_users(user_identifiers=[ident])
                            return ident, None
                        except Exception as exc:
                            return ident, exc

                results = await asyncio.gather(*[_delete_one(i) for i in list(pending.keys())])
                for ident, err in results:
                    if err is None:
                        succeeded += 1
                        succeeded_identifiers.add(ident)
                        pending.pop(ident, None)
                    else:
                        pending[ident] = pending.get(ident, 0) + 1
                        if pending[ident] >= 10:
                            failed[ident] = str(err)[:300]
                            pending.pop(ident, None)
                update_progress(job_id, succeeded)

        # Remove only users that actually got deleted upstream from the cache
        deleted_guids = [
            u["ts_guid"]
            for u in snapshot
            if u["ts_guid"] in succeeded_identifiers or u["username"] in succeeded_identifiers
        ]
        if deleted_guids:
            with Session(_db.get_engine()) as session:
                from sqlmodel import delete as sql_delete

                session.exec(
                    sql_delete(CachedUser).where(
                        CachedUser.cluster_id == cluster_id,
                        col(CachedUser.ts_guid).in_(deleted_guids),
                    )
                )
                session.exec(
                    sql_delete(UserOrgMembership).where(
                        UserOrgMembership.cluster_id == cluster_id,
                        col(UserOrgMembership.ts_guid).in_(deleted_guids),
                    )
                )
                session.exec(
                    sql_delete(UserGroupMembership).where(
                        UserGroupMembership.cluster_id == cluster_id,
                        col(UserGroupMembership.user_guid).in_(deleted_guids),
                    )
                )
                session.commit()

        status = "PARTIAL" if (failed or cancelled) else "SUCCESS"
        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.items_succeeded = succeeded
                rec.items_failed = total - succeeded
                rec.status = status
                if failed:
                    rec.error = json.dumps(failed)[:500]
                session.add(rec)
                audit = AuditLog(
                    cluster_id=cluster_id,
                    action_type="delete_users",
                    entity_type="user",
                    items_affected=succeeded,
                    status=status if status != "SUCCESS" else "SUCCESS",
                )
                audit.set_parameters(
                    {
                        "user_guids": user_guids,
                        "identifiers": identifiers,
                        "succeeded": succeeded,
                        "failed": failed,
                        "cancelled": cancelled,
                    }
                )
                session.add(audit)
                session.commit()

        result = {
            "succeeded": succeeded,
            "failed": list(failed.keys()),
            "cancelled": cancelled,
            "record_id": record_id,
        }
        if status == "PARTIAL":
            mark_partial(job_id, result)
        elif succeeded == 0 and not cancelled:
            mark_failed(job_id, f"0 users deleted — {len(failed)} failures")
        else:
            mark_complete(job_id, result)
        logger.info(
            "delete_users job=%s cluster=%s succeeded=%d failed=%d cancelled=%s",
            job_id,
            cluster_id,
            succeeded,
            len(failed),
            cancelled,
        )
    except Exception as exc:
        logger.exception("execute_delete job %s failed: %s", job_id, exc)
        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.status = "FAILED"
                rec.error = str(exc)[:500]
                session.add(rec)
                session.commit()
        mark_failed(job_id, exc)


# ── History ────────────────────────────────────────────────────────────────────


def list_history(
    *,
    cluster_id: str,
    org_id: int | None = None,
    action_type: str | None = None,
    record_offset: int = 0,
    page_size: int = 50,
) -> tuple[list[dict], int]:
    """Paginated user-action history, newest first."""
    with Session(_db.get_engine()) as session:
        q = select(UserActionRecord).where(UserActionRecord.cluster_id == cluster_id)
        if org_id is not None:
            q = q.where(UserActionRecord.org_id == org_id)
        if action_type:
            q = q.where(UserActionRecord.action_type == action_type)

        total = session.exec(select(func.count()).select_from(q.subquery())).one()
        q = q.order_by(UserActionRecord.executed_at.desc()).offset(record_offset).limit(page_size)
        rows = session.exec(q).all()
        items = [
            {
                "id": r.id,
                "job_id": r.job_id,
                "action_type": r.action_type,
                "from_username": r.from_username,
                "from_display_name": r.from_display_name,
                "to_username": r.to_username,
                "to_display_name": r.to_display_name,
                "items_total": r.items_total,
                "items_succeeded": r.items_succeeded,
                "items_failed": r.items_failed,
                "status": r.status,
                "error": r.error,
                "executed_at": r.executed_at.isoformat(),
            }
            for r in rows
        ]
        return items, total
