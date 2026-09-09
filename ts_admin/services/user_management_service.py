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
  - delete refuses admins without an explicit confirmation, and ALWAYS refuses
    the cluster's own configured service account.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Literal

import httpx
from sqlalchemy import distinct
from sqlalchemy.orm import aliased
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
from ts_admin.services.sync_status import last_successful_sync
from ts_admin.ts_client.exceptions import StaleCacheError, TSAdminError

logger = logging.getLogger(__name__)

# Hard-coded admin group names (cluster admins). Matches CS Tools behavior of
# refusing to push sharing onto admins who already see everything.
ADMIN_GROUP_NAMES = {"Administrator", "System User"}

# Delete retry loop. Rounds are retried with exponential backoff — the loop used
# to fire all ten rounds back to back with no sleep, which turns a rate-limited
# or briefly-unavailable cluster into ten hammering rounds and then a giving-up
# "failed" that a single pause would have avoided. Module-level so tests can
# collapse the delays.
DELETE_MAX_ATTEMPTS = 10
DELETE_RETRY_BASE_DELAY = 0.5  # seconds, doubled each round
DELETE_RETRY_MAX_DELAY = 8.0  # seconds, ceiling per round

# `security/metadata/share` marks `message` REQUIRED — omitting the key is a 400.
TRANSFER_SHARE_MESSAGE = "Shared with you as part of a ThoughtSpot access handover."

# `principals/fetch-permissions` returns one row per accessible object of EVERY
# type, and on a real cluster the overwhelming majority are LOGICAL_COLUMNs
# (measured on ps-internal-prod: 20,425 of 23,197 rows for a single user). A
# column is not shareable content on its own — it is reachable only through the
# table that owns it, which transfer-sharing already re-shares. Including them
# inflated the job total ~8x, made the dry-run count meaningless, and spent the
# whole run issuing shares that change nothing a user can see.
#
# Preview and execute MUST apply this identically, or the dry-run stops
# predicting the execute — hence one helper, used by both.
TRANSFER_SHARING_EXCLUDED_TYPES = frozenset({"LOGICAL_COLUMN"})


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


def _nothing_succeeded_reason(*, noun: str, total: int, failures: list[str], cancelled: bool) -> str:
    """Compose the failure message for a job in which nothing succeeded.

    A bare count ("0 objects transferred") is not a reason — it is the number the
    admin can already see. What they need is the error ThoughtSpot actually
    returned, which is what tells them whether to retry, fix a privilege, or
    stop. Three of this module's write paths reported PARTIAL with zero items
    affected for the life of the project against endpoints that 404'd; the
    status is only half the fix, the message is the other half.
    """
    if total == 0:
        return f"0 {noun} — the request resolved nothing to act on."
    parts: list[str] = []
    if failures:
        parts.append(f"{len(failures)} call(s) failed — first error: {failures[0] or 'unknown error'}")
    if cancelled:
        parts.append("the job was cancelled")
    if not parts:
        parts.append("no call was attempted")
    return f"0 of {total} {noun}: " + "; ".join(parts) + "."


def _user_row_to_dict(u: CachedUser, created_by: str | None = None) -> dict:
    return {
        "ts_guid": u.ts_guid,
        "username": u.username,
        "display_name": u.display_name,
        "email": u.email,
        "status": u.status,
        # Display name of the creating user; falls back to the raw GUID when
        # that user is no longer in the cache (deleted upstream), and is None
        # when ThoughtSpot reported no author at all.
        "created_by": created_by or (u.author_guid or None),
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


def _delete_refusal(
    *,
    cluster_id: str,
    service_username: str,
    user_guids: list[str],
    identifiers: list[str],
    confirm_admin_delete: bool,
) -> str | None:
    """
    Return an actionable refusal message for a proposed user delete, or None.

    Both the GUIDs and the identifiers actually sent to ThoughtSpot are checked:
    the caller may pass either form, and the self-delete guard in particular has
    to catch a raw username string that may have no CachedUser row at all.
    """
    service = (service_username or "").strip().lower()

    with Session(_db.get_engine()) as session:
        targets: dict[str, CachedUser | None] = {}
        for ident in [*user_guids, *identifiers]:
            if ident not in targets:
                targets[ident] = _resolve_user(session, cluster_id, ident)

        # Self-delete: refused unconditionally, no override flag. This is the
        # account the toolkit authenticates as — deleting it 401s the next
        # operation and every credential in the keychain is dead.
        for ident, user in targets.items():
            names = {ident.strip().lower()}
            if user is not None:
                names.add((user.username or "").strip().lower())
            if service and service in names:
                return (
                    f"Refusing to delete {service_username!r}: it is the account this toolkit "
                    f"authenticates as on cluster {cluster_id!r}, so deleting it would break every "
                    "stored credential. Remove it from the selection and retry."
                )

        if confirm_admin_delete:
            return None

        # preview_delete/dryrun_delete already compute is_admin and admin_count;
        # execute has to enforce it, not just report it.
        admins = sorted(
            {
                user.username or guid
                for guid, user in targets.items()
                if user is not None and _is_admin(session, cluster_id, user.ts_guid)
            }
        )

    if admins:
        return (
            f"Refusing to delete cluster admin(s): {', '.join(admins)}. "
            "Re-run with confirm_admin_delete=true if this is intended."
        )
    return None


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
        # Users are cluster-scoped, not org-scoped, so — as in group_service —
        # the join is on cluster_id + GUID only. LEFT JOIN on purpose: a user
        # whose creator has been deleted upstream must still list.
        creator = aliased(CachedUser)

        base = (
            select(CachedUser, col(creator.display_name))
            .outerjoin(
                creator,
                (col(creator.cluster_id) == CachedUser.cluster_id) & (col(creator.ts_guid) == CachedUser.author_guid),
            )
            .where(CachedUser.cluster_id == cluster_id)
        )
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
            "username": col(CachedUser.username),
            "display_name": col(CachedUser.display_name),
            "email": col(CachedUser.email),
            "status": col(CachedUser.status),
            "created_by": col(creator.display_name),
            "created_at": col(CachedUser.created_at),
            "modified_at": col(CachedUser.modified_at),
        }.get(sort_field, col(CachedUser.username))
        base = base.order_by(sort_col.desc() if sort_order == "desc" else sort_col.asc())
        base = base.offset(record_offset).limit(page_size)

        rows = session.exec(base).all()
        return [_user_row_to_dict(u, created_by) for u, created_by in rows], total


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

        # DISTINCT for the same reason preview_delete counts distinct GUIDs: an
        # object visible in two orgs has a cache row per org, and this count is
        # "objects owned by this user", not "cache rows".
        owned_count = session.exec(
            select(func.count(distinct(col(CachedMetadata.ts_guid))))
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
    from_user_guid: str | None = None,
    object_types: list[str] | None = None,
    tag_names: list[str] | None = None,
    explicit_guids: list[str] | None = None,
) -> dict:
    """
    Return the objects that will be reassigned.

    With `from_user_guid`, that is everything that user owns — the Users page
    offboarding flow. With `from_user_guid=None` and `explicit_guids`, it is
    exactly those objects whoever owns them, which is what a selection made on
    the Metadata page means.

    Filters narrow the set:
      - object_types: only these CachedMetadata.object_type values
      - tag_names:    only objects whose tag_names JSON contains every name listed
      - explicit_guids: only these GUIDs (intersected with the owner filter)

    Refuses on a non-authoritative metadata cache: the whole result set IS the
    cache query, so a truncated cache silently under-reports what the user is
    about to transfer — objects would be left behind on a departing user.
    """
    from ts_admin.services.sync_status import require_authoritative_metadata

    require_authoritative_metadata(cluster_id=cluster_id, org_id=org_id)

    if not from_user_guid and not explicit_guids:
        # Neither a source user nor a selection: that would preview every object
        # on the cluster and offer to reassign it. Refuse rather than guess.
        raise ValueError("preview_transfer needs either from_user_guid or explicit_guids")

    with Session(_db.get_engine()) as session:
        q = select(CachedMetadata).where(
            CachedMetadata.cluster_id == cluster_id,
            CachedMetadata.org_id == org_id,
        )
        if from_user_guid:
            q = q.where(CachedMetadata.owner_guid == from_user_guid)
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
        # A Metadata-page selection can span owners, and the confirmation step
        # needs to say whose objects are about to move — "12 objects from 4
        # owners" is a different decision from "12 objects from Alice".
        owners: dict[str, dict] = {}
        for r in rows:
            entry = owners.setdefault(
                r.owner_guid,
                {"owner_guid": r.owner_guid, "owner_name": r.owner_name or r.owner_guid, "count": 0},
            )
            entry["count"] += 1
        return {
            "items": items,
            "total": len(items),
            "by_type": by_type,
            "owners": sorted(owners.values(), key=lambda o: -o["count"]),
        }


async def dryrun_transfer(
    job_id: str,
    cluster_id: str,
    org_id: int,
    to_user_identifier: str,
    object_ids: list[str],
    *,
    from_user_guid: str | None = None,
) -> None:
    """
    Pre-flight a transfer against ThoughtSpot LIVE. Changes nothing.

    The preview that precedes this is a cache query, and the cache is exactly
    what goes stale between a sync and a transfer. Two failure modes only a
    live check can see:

      - **The recipient is gone.** A user deactivated or renamed upstream still
        looks fine locally, and the transfer would discover it only after every
        chunk had failed.
      - **A poison GUID.** `assign_metadata_owner` takes 50 ids per call and the
        executor buckets the whole chunk on failure, so one object deleted
        upstream fails 49 healthy transfers alongside it. There is no bisection
        on this path (unlike the TML export), so the pre-flight is what keeps
        one stale row from costing an admin the other 49.

    Result shape mirrors the deleter's dry-run: counts, plus the specific GUIDs
    that would fail, so the modal can name them rather than warning in general.
    """
    from ts_admin.services.job_service import mark_complete, mark_failed, mark_running
    from ts_admin.services.sync_status import require_authoritative_metadata
    from ts_admin.ts_client import ThoughtSpotClient

    # Same fail-closed guard as the executor, for the same reason, and it must
    # not raise out of a background task.
    try:
        require_authoritative_metadata(cluster_id=cluster_id, org_id=org_id)
    except StaleCacheError as exc:
        mark_failed(job_id, exc)
        return

    total = len(object_ids)
    mark_running(job_id, total)

    try:
        # Cache side: what we believe we are about to move, and from whom.
        preview = preview_transfer(
            cluster_id=cluster_id,
            org_id=org_id,
            from_user_guid=from_user_guid,
            explicit_guids=None if from_user_guid else object_ids,
        )
        known = {i["ts_guid"] for i in preview["items"]}

        cluster = _get_cluster(cluster_id)
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            target = await client.find_user(identifier=to_user_identifier)
            live_guids = await client.verify_metadata_exists(object_ids=object_ids)

        missing = [guid for guid in object_ids if guid not in live_guids]
        # Objects the cache does not know either — selected from a stale grid.
        uncached = [guid for guid in object_ids if guid not in known]

        result = {
            "requested": total,
            "transferable": total - len(missing),
            "missing_count": len(missing),
            # Capped like every other job result; the counts above are exact.
            "missing_guids": missing[:200],
            "uncached_guids": uncached[:200],
            "target_found": target is not None,
            "target_name": (target.display_name or target.name) if target else "",
            "target_identifier": to_user_identifier,
            "by_type": preview["by_type"],
            "owners": preview["owners"],
        }
        mark_complete(job_id, result)
        logger.info(
            "transfer dryrun job=%s cluster=%s requested=%d missing=%d target_found=%s",
            job_id,
            cluster_id,
            total,
            len(missing),
            target is not None,
        )
    # Background task: an escaping exception is invisible to the caller and
    # would strand the job at RUNNING. Logged with a traceback, reported FAILED.
    except Exception as exc:
        logger.exception("dryrun_transfer job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


async def execute_transfer(
    job_id: str,
    cluster_id: str,
    org_id: int,
    from_user_guid: str | None,
    to_user_identifier: str,
    object_ids: list[str],
) -> None:
    """Reassign ownership of `object_ids` to `to_user_identifier`. Chunked at 50.

    `from_user_guid` names a single source owner — the Users page flow, where
    every object belongs to the user being offboarded. Pass None for a
    selection made on the Metadata page, which can span many owners; the source
    owner of each object is then read from the cache and one UserActionRecord
    is written per distinct owner, so the History tab still files each transfer
    under the user it took objects away from.

    Chunks are built WITHIN an owner group rather than across the whole
    selection. Chunking across owners would make per-owner success counts a
    guess whenever a chunk straddling two owners failed; this way every chunk
    belongs to exactly one owner and the accounting is exact.

    Refuses on a non-authoritative metadata cache — `object_ids` was produced by
    `preview_transfer` against that cache, and executing a transfer the preview
    understated is exactly the failure mode this guard exists for.

    Terminal status: zero transfers is FAILED, never PARTIAL, and the message
    names the error ThoughtSpot returned rather than only counting failures.
    """
    from ts_admin.services.job_service import (
        is_cancelled,
        mark_complete,
        mark_failed,
        mark_partial,
        mark_running,
        update_progress,
    )
    from ts_admin.services.sync_status import require_authoritative_metadata
    from ts_admin.ts_client import ThoughtSpotClient

    # Defense in depth. The REAL refusal is in the router, which returns 409
    # before any Job row exists — this function only ever runs as a background
    # task, so it is unreachable in practice. It must NOT raise: an exception
    # escaping a background task is invisible to the caller and would leave the
    # job QUEUED forever. Mark the job FAILED instead, BEFORE mark_running and
    # before any UserActionRecord is written.
    try:
        require_authoritative_metadata(cluster_id=cluster_id, org_id=org_id)
    except StaleCacheError as exc:
        mark_failed(job_id, exc)
        return

    total = len(object_ids)
    mark_running(job_id, total)

    succeeded = 0
    failed_chunks: list[dict] = []
    cancelled = False

    # ── Group the selection by its source owner ──────────────────────────────
    # One group for the Users-page flow (every object is the named user's, by
    # construction of the preview). For a Metadata-page selection the owner is
    # read from the cache; an object with no cached row groups under "" rather
    # than being dropped, so it is still transferred and still accounted for.
    with Session(_db.get_engine()) as session:
        if from_user_guid:
            owner_groups: dict[str, list[str]] = {from_user_guid: list(object_ids)}
        else:
            owner_of: dict[str, str] = {}
            for chunk in _chunks(object_ids, 500):  # SQLite caps IN() at 999
                rows = session.exec(
                    select(CachedMetadata).where(
                        CachedMetadata.cluster_id == cluster_id,
                        CachedMetadata.org_id == org_id,
                        col(CachedMetadata.ts_guid).in_(chunk),
                    )
                ).all()
                for row in rows:
                    owner_of[row.ts_guid] = row.owner_guid
            owner_groups = {}
            for guid in object_ids:
                owner_groups.setdefault(owner_of.get(guid, ""), []).append(guid)

        source_users = (
            {
                u.ts_guid: u
                for u in session.exec(
                    select(CachedUser).where(
                        CachedUser.cluster_id == cluster_id,
                        col(CachedUser.ts_guid).in_([g for g in owner_groups if g]),
                    )
                ).all()
            }
            if any(owner_groups)
            else {}
        )
        to_user = _resolve_user(session, cluster_id, to_user_identifier)

    # ── One record per source owner ──────────────────────────────────────────
    record_ids: dict[str, str] = {}
    with Session(_db.get_engine(), expire_on_commit=False) as session:
        for owner_guid, guids in owner_groups.items():
            from_user = source_users.get(owner_guid)
            record = UserActionRecord(
                cluster_id=cluster_id,
                job_id=job_id,
                org_id=org_id,
                action_type="transfer",
                from_user_guid=owner_guid,
                from_username=from_user.username if from_user else "",
                from_display_name=from_user.display_name if from_user else "",
                to_user_guid=to_user.ts_guid if to_user else "",
                to_username=to_user.username if to_user else to_user_identifier,
                to_display_name=to_user.display_name if to_user else "",
                items_total=len(guids),
                status="PENDING",
            )
            record.set_affected([{"ts_guid": oid} for oid in guids[:200]])
            session.add(record)
            session.commit()
            record_ids[owner_guid] = record.id

    per_owner_succeeded: dict[str, int] = {owner: 0 for owner in owner_groups}

    try:
        cluster = _get_cluster(cluster_id)
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            for owner_guid, guids in owner_groups.items():
                if cancelled:
                    break
                for chunk in _chunks(guids, 50):
                    if is_cancelled(job_id):
                        cancelled = True
                        break
                    try:
                        await client.assign_metadata_owner(
                            object_ids=chunk,
                            new_owner_identifier=to_user_identifier,
                        )
                    # ONLY the live call is guarded, and only against the two
                    # families it can raise (`_request` maps every HTTP outcome
                    # onto TSAdminError; httpx.HTTPError covers the transport).
                    # The blanket `except Exception` that used to wrap the cache
                    # update as well turned a bug in our own code into "this
                    # chunk failed upstream" — anything else must reach the
                    # outer handler.
                    except (TSAdminError, httpx.HTTPError) as exc:
                        logger.warning("assign_metadata_owner chunk failed: %s", exc)
                        failed_chunks.append({"guids": chunk, "error": str(exc)[:300]})
                        update_progress(job_id, succeeded)
                        continue

                    succeeded += len(chunk)
                    per_owner_succeeded[owner_guid] += len(chunk)
                    # Update CachedMetadata so the UI reflects new ownership
                    # immediately. Org-scoped: the same GUID can hold a row per
                    # org, and this transfer ran against exactly one org — a
                    # cluster-only .first() rewrote whichever org came back
                    # first, so the wrong org's cache showed the new owner and
                    # the right one kept the old.
                    with Session(_db.get_engine()) as session:
                        for guid in chunk:
                            obj = session.exec(
                                select(CachedMetadata).where(
                                    CachedMetadata.cluster_id == cluster_id,
                                    CachedMetadata.org_id == org_id,
                                    CachedMetadata.ts_guid == guid,
                                )
                            ).first()
                            if obj and to_user:
                                obj.owner_guid = to_user.ts_guid
                                obj.owner_name = to_user.display_name or to_user.username
                                session.add(obj)
                        session.commit()
                    update_progress(job_id, succeeded)

        # Zero successes is FAILED, never PARTIAL — see the note in
        # `bulk_sharing_service.execute_share`. The succeeded == 0 branch is
        # evaluated first and the message names the cause, not just a count.
        if succeeded == 0:
            status = "FAILED"
        elif failed_chunks or cancelled:
            status = "PARTIAL"
        else:
            status = "SUCCESS"
        failure_reason = _nothing_succeeded_reason(
            noun="objects transferred",
            total=total,
            failures=[c.get("error", "") for c in failed_chunks],
            cancelled=cancelled,
        )

        with Session(_db.get_engine()) as session:
            for owner_guid, record_id in record_ids.items():
                rec = session.get(UserActionRecord, record_id)
                if not rec:
                    continue
                owner_ok = per_owner_succeeded[owner_guid]
                owner_total = len(owner_groups[owner_guid])
                rec.items_succeeded = owner_ok
                rec.items_failed = owner_total - owner_ok
                # Per-owner status: one owner's objects failing must not mark
                # another owner's record FAILED.
                if owner_ok == 0:
                    rec.status = "FAILED"
                    rec.error = failure_reason[:500]
                elif owner_ok < owner_total:
                    rec.status = "PARTIAL"
                else:
                    rec.status = "SUCCESS"
                session.add(rec)

            audit = AuditLog(
                cluster_id=cluster_id,
                action_type="transfer_ownership",
                # A Users-page transfer is an action ON a user (offboarding);
                # a Metadata-page transfer is an action on content that happens
                # to change its owner. The audit trail should not conflate them.
                entity_type="user" if from_user_guid else "metadata",
                items_affected=succeeded,
                # Same terminal status as the job — the audit row used to be
                # computed from an expression that could not say FAILED.
                status=status,
            )
            audit.set_parameters(
                {
                    "from_user_guid": from_user_guid or "",
                    "from_owner_guids": sorted(owner_groups),
                    "to_user_identifier": to_user_identifier,
                    "object_ids": object_ids,
                    "succeeded": succeeded,
                    "failed_chunks": failed_chunks,
                    "cancelled": cancelled,
                    "error": failure_reason if status == "FAILED" else "",
                }
            )
            session.add(audit)
            session.commit()

        result = {
            "succeeded": succeeded,
            "failed": total - succeeded,
            "cancelled": cancelled,
            "record_id": next(iter(record_ids.values()), None),
            "record_ids": list(record_ids.values()),
        }
        if status == "FAILED":
            mark_failed(job_id, failure_reason)
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)
        logger.info(
            "transfer job=%s cluster=%s status=%s from=%s to=%s succeeded=%d failed=%d owners=%d",
            job_id,
            cluster_id,
            status,
            from_user_guid or "<multiple>",
            to_user_identifier,
            succeeded,
            total - succeeded,
            len(owner_groups),
        )
    # Last-resort handler for a background task: this runs after the 202 is on
    # the wire, so an exception that escapes is invisible to the caller and
    # strands the Job row at RUNNING. Permitted to swallow ANY exception —
    # nothing is swallowed silently; each one is logged with a traceback, marks
    # every UserActionRecord FAILED and re-reported as a FAILED job.
    except Exception as exc:
        logger.exception("execute_transfer job %s failed: %s", job_id, exc)
        with Session(_db.get_engine()) as session:
            for record_id in record_ids.values():
                rec = session.get(UserActionRecord, record_id)
                if rec:
                    rec.status = "FAILED"
                    rec.error = str(exc)[:500]
                    session.add(rec)
            session.commit()
        mark_failed(job_id, exc)


# ── Transfer sharing (re-share what the source user can see) ──────────────────


def _transferable_rows(rows: list[dict]) -> list[dict]:
    """Drop rows that are not shareable content in their own right.

    See TRANSFER_SHARING_EXCLUDED_TYPES. Applied by both the preview and the
    execute so the two always agree.
    """
    return [r for r in rows if r.get("metadata_type") not in TRANSFER_SHARING_EXCLUDED_TYPES]


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
        # This endpoint has no `permission_type` key at all — it always returns
        # EFFECTIVE access, group inheritance included. That happens to be the
        # right answer for an audit view ("what can this user see"), but it was
        # arrived at by accident: the `permission_type="EFFECTIVE"` we used to
        # send was silently discarded, exactly like the `"DEFINED"` the transfer
        # path sent. Each row now carries `is_direct_share` so the two cases are
        # distinguishable at the row level.
        rows = await client.principal_permissions(principal_identifier=ts_guid)

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
        rows = _transferable_rows(await client.principal_permissions(principal_identifier=from_user_guid))

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
    drop the types that are not shareable content (see
    TRANSFER_SHARING_EXCLUDED_TYPES), bucket by share_mode, issue one
    share_objects call per bucket.

    `notify` is forwarded to the API as `notify_on_share`. It used to be written
    to the audit log and then dropped, and the wire default is TRUE.

    NOTE: the source set is the source user's EFFECTIVE access — group-inherited
    included — because `principals/fetch-permissions` has no way to ask for
    anything narrower (the `permission_type: "DEFINED"` we used to send was not
    a key on that endpoint and was discarded). Rows now carry `is_direct_share`,
    so narrowing to direct shares only is a one-line filter here; it is
    deliberately NOT applied yet — see the hand-back note.

    Terminal status: zero successes is FAILED, never PARTIAL — including the
    `total == 0` case, which used to report COMPLETE. See the comment at the
    status computation for why an empty source set cannot be trusted.
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
            rows = _transferable_rows(await client.principal_permissions(principal_identifier=from_user_guid))

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
                            message=TRANSFER_SHARE_MESSAGE,
                            notify=notify,
                        )
                        succeeded += len(chunk)
                    # Only the two families a live call can raise; a bug in our
                    # own code is not "this bucket failed upstream" and must
                    # reach the outer handler instead of becoming a PARTIAL.
                    except (TSAdminError, httpx.HTTPError) as exc:
                        logger.warning("share_objects chunk failed (%s): %s", mode, exc)
                        failed_buckets.append({"mode": mode, "guids": chunk, "error": str(exc)[:300]})
                    update_progress(job_id, succeeded)

        # Zero successes is FAILED, never PARTIAL — see the note in
        # `bulk_sharing_service.execute_share`. `total == 0` is FAILED too, and
        # deliberately so: `principals/fetch-permissions` degrades to an empty
        # list on a response-shape mismatch rather than raising, so "the source
        # user can see nothing" and "we could not read what the source user can
        # see" are indistinguishable here. Reporting COMPLETE told an admin the
        # handover was done when it may never have started; the message names
        # both possibilities.
        if succeeded == 0:
            status = "FAILED"
        elif failed_buckets or cancelled:
            status = "PARTIAL"
        else:
            status = "SUCCESS"
        if total == 0:
            failure_reason = (
                f"0 objects were re-shared to {to_user_identifier!r}: the source user's accessible-object "
                "list came back empty. Either they have no shareable content, or "
                "`security/principals/fetch-permissions` returned a shape this build could not read — "
                "check the user in ThoughtSpot before treating the handover as done."
            )
        else:
            failure_reason = _nothing_succeeded_reason(
                noun="objects re-shared",
                total=total,
                failures=[b.get("error", "") for b in failed_buckets],
                cancelled=cancelled,
            )

        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.items_total = total
                rec.items_succeeded = succeeded
                rec.items_failed = total - succeeded
                rec.status = status
                rec.set_affected(rows[:200])
                if status == "FAILED":
                    rec.error = failure_reason[:500]
                session.add(rec)
                audit = AuditLog(
                    cluster_id=cluster_id,
                    action_type="transfer_sharing",
                    entity_type="user",
                    items_affected=succeeded,
                    # Same terminal status as the job.
                    status=status,
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
                        "error": failure_reason if status == "FAILED" else "",
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
        if status == "FAILED":
            mark_failed(job_id, failure_reason)
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)
        logger.info(
            "transfer_sharing job=%s cluster=%s status=%s from=%s to=%s succeeded=%d failed=%d",
            job_id,
            cluster_id,
            status,
            from_user_guid,
            to_user_identifier,
            succeeded,
            total - succeeded,
        )
    # Last-resort handler for a background task — see the note on the matching
    # handler in `execute_transfer`. Permitted to swallow ANY exception because
    # an escape here strands the Job row at RUNNING; nothing is silent.
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


def _metadata_marker(session: Session, *, cluster_id: str, org_id: int | None) -> tuple[int | None, datetime] | None:
    """IDENTITY of the SUCCESS ``metadata`` sync marker for this scope, or None.

    Returns ``(row.id, row.synced_at)`` — deliberately not a bool. Presence
    ("*a* marker exists") is not enough for the before/after comparison in
    :func:`preview_delete`: a whole sync cycle can complete inside the count
    window, so *a* SUCCESS marker can be present at both reads while the counts
    taken in between are certified by neither. The caller compares identities,
    which is only sound because ``sync_service._write_sync_log`` upserts the
    one row in place and refreshes ``synced_at`` on every non-``preserve_progress``
    write: same ``(id, synced_at)`` before and after ⇔ no sync completed in the
    window. (The write-ahead IN_PROGRESS marker does not bump ``synced_at`` —
    it does not need to, since it is not SUCCESS and so is not a marker at all
    as far as this function is concerned.)

    ``session.expire_all()`` is not hygiene — it removes a GC dependency. Both
    reads run on ONE session, and re-``SELECT``ing a row that is still in the
    identity map returns the loaded instance with its ORIGINAL attribute
    values: the after-read would then report the *before* ``synced_at`` for an
    in-place upsert and the identity check would silently degrade back into a
    presence check. Measured (SQLAlchemy 2.x, pysqlite): with a strong
    reference held, the re-read IS stale; with none held it is fresh, because
    the identity map is weak-referencing and this function drops the row as
    soon as it has the tuple. So today's behaviour happens to be correct *by
    garbage-collection timing* — which no test can pin and any future refactor
    that keeps the row alive would flip. Expiring first makes it unconditional.

    Says NOTHING about groups/`UserGroupMembership` (and so nothing about
    ``is_admin``), nor about any other entity — see :func:`preview_delete`.

    The ``org_id is None`` guard is load-bearing and is NOT redundant with the
    query it guards. ``last_successful_sync`` filters ``SyncLog.org_id ==
    org_id``, and SQLAlchemy compiles ``== None`` to SQL ``org_id IS NULL`` —
    a perfectly matchable predicate. No writer produces a NULL-org
    ``sync_log`` row *today*, so removing the guard is a silent no-op right
    now; the day anything writes a cluster-wide/NULL-org marker, a bare
    ``last_successful_sync`` would match it and certify a **cluster-wide**
    count from a single marker. `org_id is None` means the caller asked for a
    cluster-wide count (the preview endpoint's request carries no org), which
    no single marker can certify. Fail closed.
    """
    if org_id is None:
        return None
    session.expire_all()
    row = last_successful_sync(session, cluster_id=cluster_id, org_id=org_id, entity_type="metadata")
    if row is None:
        return None
    return (row.id, row.synced_at)


def preview_delete(*, cluster_id: str, user_guids: list[str], org_id: int | None = None) -> dict:
    """
    Snapshot users + owned-object counts so the UI can warn before delete.

    ``org_id`` scopes the owned-object count to the org the operation runs in.
    It was cluster-scoped only, so an object present in two orgs was counted
    twice and the warning over-stated the blast radius. When no org is given
    (the cache-only preview endpoint, which has no org in its request) the count
    falls back to distinct GUIDs across the cluster — still not double-counted,
    just cluster-wide. The count is DISTINCT either way; scoping is what makes
    it the number for *this* operation.

    Completeness (``metadata_cache_authoritative``)
    -----------------------------------------------
    The owned-object count is read from ``CachedMetadata``, which an interrupted
    metadata sync leaves non-empty but truncated — so a **zero is not evidence
    of zero**. ``metadata_cache_authoritative`` says whether the *metadata* sync
    for this scope is certified complete. It is deliberately a flag, not a
    refusal — the dry-run still runs and still reports ``missing_live`` /
    ``admin_count``.

    **The flag is named for the entity it covers, and it covers only that.**
    What it says: the ``metadata`` sync for this (cluster, org) had the SAME
    SUCCESS marker — same row, same ``synced_at`` — both before and after the
    counts were read, so no sync completed underneath the loop and the
    owned-object counts are read from one certified-complete metadata cache.
    What it does NOT say — and no caller may present it as saying:

    * Nothing about **group membership**. ``is_admin`` is derived from
      ``UserGroupMembership``, which only the *groups* sync writes and which
      this flag never consults. A True flag on a cluster that has never run a
      groups sync still means every ``is_admin`` is an unverified False.
    * Nothing about any other entity (users, tags, dependencies).
    * Not "this is everything the user owns" even for metadata: only the seven
      synced specs live in ``CachedMetadata``; CONNECTIONs, for instance, never
      do (see the lineage notes in ``docs/org-memory/codebase.md``).
    * When ``org_id`` is None (the cache-only preview endpoint, whose request
      carries no org) the count is cluster-wide, and one org's sync marker
      cannot certify a cluster-wide count — so the flag is hardcoded False.

    User-facing copy built on this flag must therefore be scoped strictly to
    owned-object counts.
    """
    with Session(_db.get_engine()) as session:
        # Certification is read in THIS session (no second connection on a
        # destructive-preview path) and TWICE — once before the count loop and
        # once after — because a metadata sync that overlaps the counts is
        # dangerous in BOTH directions:
        #
        #   * sync STARTS in the window  → the marker flips SUCCESS→IN_PROGRESS
        #     (write-ahead, before `_sync_metadata`'s DELETE-all). Read-after
        #     alone catches this.
        #   * sync FINISHES in the window → the counts were already read from
        #     the mid-sync truncated cache (post-DELETE-all, pre-repopulate: a
        #     genuine bare 0), and only then does the marker flip to SUCCESS.
        #     Read-after alone certifies that 0. Read-BEFORE catches it.
        #
        #   * a whole sync CYCLE fits in the window → SUCCESS marker N, then
        #     IN_PROGRESS, DELETE-all, repopulate, SUCCESS marker N+1. Marker N
        #     certifies only the reads before the DELETE-all and marker N+1 only
        #     the reads after it; the counts in between are certified by
        #     neither. Merely asking "was *a* SUCCESS marker present?" at both
        #     instants answers yes here and ships a bare 0 as certified.
        #
        # The window is seconds wide, not microseconds: the loop below issues
        # one COUNT per user (measured ~3.4s for 500 users), so the first count
        # can be read from a cache that the last count no longer sees.
        #
        # What makes the flag sound is therefore comparing the marker's
        # IDENTITY, not its presence: certify only when both reads return the
        # SAME `(id, synced_at)`. `_write_sync_log` upserts the row in place and
        # bumps `synced_at` on every completed sync, so an unchanged identity is
        # exactly "no sync completed in this window". None before, None after,
        # or a mismatch ⇒ not certified.
        #
        # INVARIANT — THIS SESSION MUST STAY READ-ONLY. Under pysqlite a session
        # that has only issued SELECTs never opens a real transaction, which is
        # why the second read sees commits made by other connections during the
        # loop. Adding ANY DML before the second read opens one, freezes this
        # session's snapshot, and silently reduces the after-read to a re-read
        # of the before-read — the guarantee evaporates and no test goes red.
        # (Under the current rollback journal it can instead surface as
        # "database is locked".) If this function ever needs to write, take a
        # separate short-lived session for the write.
        #
        # Test-fixture caveat, so nobody over-reads the suite: the race tests
        # in tests/unit/test_stale_cache_guard.py run on a StaticPool
        # in-memory engine — ONE shared DBAPI connection — so they are
        # structurally incapable of exercising the cross-connection visibility
        # production depends on. They pass because that behaviour was measured
        # separately, not because the fixture proves it.
        marker_before = _metadata_marker(session, cluster_id=cluster_id, org_id=org_id)

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
            owned_conditions = [
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.owner_guid == u.ts_guid,
            ]
            if org_id is not None:
                owned_conditions.append(CachedMetadata.org_id == org_id)
            owned = session.exec(
                select(func.count(distinct(col(CachedMetadata.ts_guid))))
                .select_from(CachedMetadata)
                .where(*owned_conditions)
            ).one()
            items.append(
                {
                    **_user_row_to_dict(u),
                    "owned_object_count": owned,
                    "is_admin": _is_admin(session, cluster_id, u.ts_guid),
                }
            )

        marker_after = _metadata_marker(session, cluster_id=cluster_id, org_id=org_id)

    return {
        "items": items,
        "total": len(items),
        "unrecognized": unrecognized,
        "metadata_cache_authoritative": marker_before is not None and marker_before == marker_after,
    }


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
        snapshot = preview_delete(cluster_id=cluster_id, user_guids=user_guids, org_id=org_id)

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
            "metadata_cache_authoritative": snapshot["metadata_cache_authoritative"],
        }
        mark_complete(job_id, result)
        logger.info(
            "dryrun_delete job=%s cluster=%s total=%d missing_live=%d authoritative=%s",
            job_id,
            cluster_id,
            result["total"],
            len(missing_live),
            result["metadata_cache_authoritative"],
        )
    # Last-resort handler for a background task — see the note on the matching
    # handler in `execute_transfer`. Permitted to swallow ANY exception because
    # an escape here strands the Job row at RUNNING; nothing is silent.
    except Exception as exc:
        logger.exception("dryrun_delete job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


async def execute_delete(
    job_id: str,
    cluster_id: str,
    org_id: int,
    user_guids: list[str],
    user_identifiers: list[str] | None = None,
    confirm_admin_delete: bool = False,
) -> None:
    """
    Retry-to-10 delete loop with backoff, concurrency capped at 15.
    `user_identifiers` is the list of usernames/GUIDs to send to the TS API —
    defaults to user_guids if not provided (the caller is expected to pass
    either form).

    Two refusals fire BEFORE any live call, mirroring the admin-target refusal
    in :func:`execute_transfer_sharing`:

      - deleting a cluster admin requires ``confirm_admin_delete=True``;
      - deleting the cluster's own configured ``username`` is refused
        unconditionally. That account is what the toolkit authenticates as, so
        deleting it 401s every subsequent operation and leaves every stored
        credential dead. There is deliberately no override.

    A refusal marks the job FAILED and returns — it never raises. The task runs
    as a Starlette background task, i.e. after the 202 is on the wire, where a
    raise is fail-silent and strands the Job row (S23).

    Terminal status: zero deletions is FAILED, never PARTIAL, and the message
    names the error ThoughtSpot returned rather than only counting failures.
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

    # ── Refusals (before any live call, before any record is written) ─────────
    try:
        cluster = _get_cluster(cluster_id)
    except ValueError as exc:
        mark_failed(job_id, exc)
        return

    refusal = _delete_refusal(
        cluster_id=cluster_id,
        service_username=cluster.username,
        user_guids=user_guids,
        identifiers=identifiers,
        confirm_admin_delete=confirm_admin_delete,
    )
    if refusal:
        logger.warning("execute_delete job %s refused: %s", job_id, refusal)
        mark_failed(job_id, refusal)
        return

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
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            pending: dict[str, int] = {ident: 0 for ident in identifiers}
            round_index = 0

            while pending and not cancelled:
                if is_cancelled(job_id):
                    cancelled = True
                    break

                # Back off before every round after the first. Without this the
                # ten rounds fired back to back in milliseconds, so a transient
                # 429/503 burned every attempt before the cluster recovered.
                if round_index:
                    delay = min(DELETE_RETRY_BASE_DELAY * (2 ** (round_index - 1)), DELETE_RETRY_MAX_DELAY)
                    await asyncio.sleep(delay)
                round_index += 1

                # One identifier per call so per-user retries stay isolated.
                # Concurrency cap of 15 mirrors the deleter pattern.
                sem = asyncio.Semaphore(15)

                async def _delete_one(ident: str) -> tuple[str, Exception | None]:
                    async with sem:
                        try:
                            await client.delete_user(user_identifier=ident)
                            return ident, None
                        # Only the two families a live call can raise. This
                        # per-identifier catch feeds the retry loop, so a bug in
                        # our own code would otherwise be retried ten times and
                        # then reported as a cluster-side failure.
                        except (TSAdminError, httpx.HTTPError) as exc:
                            return ident, exc

                results = await asyncio.gather(*[_delete_one(i) for i in list(pending.keys())])
                for ident, err in results:
                    if err is None:
                        succeeded += 1
                        succeeded_identifiers.add(ident)
                        pending.pop(ident, None)
                    else:
                        pending[ident] = pending.get(ident, 0) + 1
                        if pending[ident] >= DELETE_MAX_ATTEMPTS:
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

        # Zero successes is FAILED, never PARTIAL — see the note in
        # `bulk_sharing_service.execute_share`. `/users/delete` 404'd for the
        # life of the project and every run of it reported PARTIAL with zero
        # users affected, which reads as "some of them went, retry the rest".
        if succeeded == 0:
            status = "FAILED"
        elif failed or cancelled:
            status = "PARTIAL"
        else:
            status = "SUCCESS"
        failure_reason = _nothing_succeeded_reason(
            noun="users deleted",
            total=total,
            failures=list(failed.values()),
            cancelled=cancelled,
        )

        with Session(_db.get_engine()) as session:
            rec = session.get(UserActionRecord, record_id)
            if rec:
                rec.items_succeeded = succeeded
                rec.items_failed = total - succeeded
                rec.status = status
                if failed:
                    rec.error = json.dumps(failed)[:500]
                elif status == "FAILED":
                    rec.error = failure_reason[:500]
                session.add(rec)
                audit = AuditLog(
                    cluster_id=cluster_id,
                    action_type="delete_users",
                    entity_type="user",
                    items_affected=succeeded,
                    # Same terminal status as the job.
                    status=status,
                )
                audit.set_parameters(
                    {
                        "user_guids": user_guids,
                        "identifiers": identifiers,
                        "succeeded": succeeded,
                        "failed": failed,
                        "cancelled": cancelled,
                        "error": failure_reason if status == "FAILED" else "",
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
        if status == "FAILED":
            mark_failed(job_id, failure_reason)
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)
        logger.info(
            "delete_users job=%s cluster=%s status=%s succeeded=%d failed=%d cancelled=%s",
            job_id,
            cluster_id,
            status,
            succeeded,
            len(failed),
            cancelled,
        )
    # Last-resort handler for a background task — see the note on the matching
    # handler in `execute_transfer`. Permitted to swallow ANY exception because
    # an escape here strands the Job row at RUNNING; nothing is silent.
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
