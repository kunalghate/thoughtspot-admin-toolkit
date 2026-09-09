"""
Dashboard aggregates — one read that powers the whole Dashboard page.

Everything comes from the local SQLite cache (counts, jobs, audit history);
nothing here touches the live ThoughtSpot cluster, so the dashboard renders
instantly even when the cluster is offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import case, desc, distinct, func
from sqlmodel import Session, col, select

from ts_admin import database as _db
from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.cache.ts_connection import CachedConnection
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_tag import CachedTag
from ts_admin.models.cache.ts_user import CachedUser, UserGroupMembership, UserOrgMembership
from ts_admin.models.job import Job
from ts_admin.models.share_record import ShareRecord
from ts_admin.models.sync_log import SyncLog
from ts_admin.models.user_action_record import UserActionRecord
from ts_admin.services.metadata_service import MetadataService

RECENT_JOBS_LIMIT = 5
RECENT_ACTIVITY_LIMIT = 8
# How many raw audit rows to scan for the UserActionRecord feed. That source is
# ALREADY one row per action, so a row limit drops whole entries and distorts
# nothing — each surviving row still carries its complete state.
_ACTIVITY_SCAN_ROWS = 300
# How many grouped sessions to scan for the archive/share feeds. Those two are
# aggregated in SQL, so the per-session counts are exact: truncation here drops
# whole sessions only, and no entry can ever make a terminal claim ("FAILED",
# "Deleted N of M") from a partial view of its own rows.
_ACTIVITY_SCAN_SESSIONS = 50
# Activity older than this is history, not news — the feed hides it so a
# months-old bulk delete cannot masquerade as "recent".
ACTIVITY_MAX_AGE_DAYS = 30
# Entities whose freshness the dashboard reports.
#
# It reports NO record-count trend: `sync_log` keeps no time series. Every
# writer (`sync_service._write_sync_log`, `lineage_service._write_dependencies_sync_log`)
# UPSERTS the single (cluster_id, org_id, entity_type) row and none append, so
# there is never a prior row to diff against. A "change since the last sync"
# needs a second stored number, not a second query.
_TRACKED_ENTITIES = ("metadata", "users", "groups", "tags", "connections", "dependencies")
_IN_FLIGHT_STATUSES = ("QUEUED", "PENDING", "RUNNING")


def _naive(dt: datetime | None) -> datetime | None:
    """SQLite stores naive datetimes; normalize both kinds for comparison."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


# Worst-wins ordering for `_collapse`. An UNKNOWN status ranks 1 — above
# SUCCESS, so a status this code has never heard of can never be swallowed by a
# green one, and below PARTIAL, so it can never be promoted into a terminal
# claim about what happened.
_STATUS_SEVERITY = {"SUCCESS": 0, "PENDING": 1, "PARTIAL": 2, "FAILED": 3}
_UNKNOWN_STATUS_SEVERITY = 1


def _severity(status: str | None) -> int:
    return _STATUS_SEVERITY.get(status or "", _UNKNOWN_STATUS_SEVERITY)


def _plural(n: int, noun: str) -> str:
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _delete_entry(
    *,
    total: int,
    deleted: int,
    exported: int,
    failed: int,
    job_type: str | None,
    job_status: str | None,
    action: str | None,
) -> tuple[str, str]:
    """
    Render one archive session as (label, status).

    `deleted` counts rows with `deleted_confirmed_at IS NOT NULL`, which per
    `ts_admin/models/archive_record.py:46-55` is the ONLY predicate that means
    "this object is really gone from ThoughtSpot". `tml_export_status` says
    nothing about it: `_execute_delete` exports every object (Phase A) before
    deleting any (Phase B), so a crash between the phases leaves a whole session
    SUCCESS-exported and fully alive.

    The archive rows alone cannot distinguish an export-only run from a delete
    job that crashed in Phase B — both leave every row SUCCESS-exported with no
    delete confirmation — so the owning `Job` supplies the *intent*. A confirmed
    delete is still evaluated FIRST: real evidence outranks job metadata, so a
    mislabelled job can never hide a delete that actually happened.

    Module-level and pure so every branch is unit-testable without HTTP.
    """
    intent: str | None = None
    if job_type == "bulk_delete" or action == "delete":
        intent = "delete"
    elif action == "export":
        intent = "export"

    if total > 0 and deleted == total:
        return f"Deleted {_plural(total, 'object')} (TML backed up)", "SUCCESS"
    if deleted > 0:
        return f"Deleted {deleted} of {_plural(total, 'object')} (TML backed up)", "PARTIAL"
    if job_status in _IN_FLIGHT_STATUSES:
        return f"Exporting/Deleting {_plural(total, 'object')}…", "PENDING"
    if intent == "export":
        if failed == 0:
            return f"Exported {_plural(total, 'object')} to TML (not deleted)", "SUCCESS"
        if exported > 0:
            return f"Exported {exported} of {_plural(total, 'object')} to TML (not deleted)", "PARTIAL"
        return f"TML export failed for {_plural(total, 'object')}", "FAILED"
    if intent == "delete":
        return f"Delete failed — 0 of {_plural(total, 'object')} deleted", "FAILED"
    # No jobs row at all (purged history, or a hand-written record): we know
    # what was archived and that nothing is confirmed deleted, and nothing else.
    return f"Archived {_plural(total, 'object')} — none deleted, {exported} backed up", "PENDING"


class DashboardService:
    @staticmethod
    def summary(*, cluster_id: str, org_id: int) -> dict:
        meta = MetadataService.stats(cluster_id=cluster_id, org_id=org_id)

        with Session(_db.get_engine()) as session:
            users = len(
                session.exec(
                    select(UserOrgMembership.ts_guid).where(
                        UserOrgMembership.cluster_id == cluster_id,
                        UserOrgMembership.org_id == org_id,
                    )
                ).all()
            )
            groups = len(
                session.exec(
                    select(CachedGroup.ts_guid).where(
                        CachedGroup.cluster_id == cluster_id,
                        CachedGroup.org_id == org_id,
                    )
                ).all()
            )
            tags = len(
                session.exec(
                    select(CachedTag.ts_guid).where(
                        CachedTag.cluster_id == cluster_id,
                        CachedTag.org_id == org_id,
                    )
                ).all()
            )
            connections = len(
                session.exec(
                    select(CachedConnection.ts_guid).where(
                        CachedConnection.cluster_id == cluster_id,
                        CachedConnection.org_id == org_id,
                    )
                ).all()
            )

            recent_jobs = [
                {
                    "id": j.id,
                    "job_type": j.job_type,
                    "status": j.status,
                    "created_at": _naive(j.created_at),
                    "error": j.error,
                    "error_type": j.error_type,
                }
                for j in session.exec(
                    select(Job)
                    .where(Job.cluster_id == cluster_id)
                    .order_by(col(Job.created_at).desc())
                    .limit(RECENT_JOBS_LIMIT)
                ).all()
            ]

            # COUNT over the whole window, not a slice of the newest N jobs —
            # a busy cluster can push failures out of any fixed-size page.
            week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
            failed_jobs_7d = session.exec(
                select(func.count())
                .select_from(Job)
                .where(
                    Job.cluster_id == cluster_id,
                    Job.status == "FAILED",
                    col(Job.created_at).is_not(None),
                    col(Job.created_at) >= week_ago,
                )
            ).one()

            running_jobs = [
                {
                    "id": j.id,
                    "job_type": j.job_type,
                    "status": j.status,
                    "progress": j.progress,
                    "total": j.total,
                }
                for j in session.exec(
                    select(Job)
                    .where(
                        Job.cluster_id == cluster_id,
                        col(Job.status).in_(_IN_FLIGHT_STATUSES),
                    )
                    # Oldest first: the sync the user started first sits on top
                    # of the running-jobs bar, matching the order they acted in.
                    .order_by(col(Job.created_at).asc())
                    .limit(RECENT_JOBS_LIMIT)
                ).all()
            ]

            activity = DashboardService._recent_activity(session, cluster_id=cluster_id, org_id=org_id)
            synced, synced_at, syncing = DashboardService._sync_state(session, cluster_id=cluster_id, org_id=org_id)
            attention = DashboardService._attention(session, cluster_id=cluster_id, org_id=org_id, synced=synced)

        return {
            "counts": {
                "users": users,
                "groups": groups,
                "tags": tags,
                "connections": connections,
                "objects_total": meta["total"],
                "objects_by_type": meta["by_type"],
                "archivable_total": meta["archivable_total"],
                "stale_90d": meta["stale_90d"],
                "never_accessed": meta["never_accessed"],
            },
            "synced": synced,
            "synced_at": synced_at,
            "syncing": syncing,
            "attention": attention,
            "recent_jobs": recent_jobs,
            "running_jobs": running_jobs,
            "recent_activity": activity,
            "failed_jobs_7d": failed_jobs_7d,
        }

    @staticmethod
    def _sync_state(
        session: Session, *, cluster_id: str, org_id: int
    ) -> tuple[dict[str, bool], dict[str, datetime | None], dict[str, bool]]:
        """
        Per-entity "has this ever synced?" flags, the timestamp of the last
        successful sync, and "is a sync running now?".

        The flags exist so the UI can tell a real zero apart from a number we
        simply do not have yet — rendering "0 tags" for a cluster that has
        never run a tag sync is a lie, not a measurement.

        `synced_at` is per-entity on purpose: syncs are lazy and independent
        (ADR-005), so "when was this cluster last synced?" has no single answer.
        It reports the last SUCCESS only — a failed attempt does not make the
        cached data any newer than the successful sync before it.

        `in_flight` exists because `synced` alone cannot tell "never synced"
        apart from "syncing right now". `_sync_metadata` writes an IN_PROGRESS
        marker before it deletes the cache, and `_write_sync_log` UPSERTS the
        single (cluster, org, entity) row — so for the whole duration of an
        ordinary, healthy sync there is no SUCCESS row and `synced[entity]` is
        False. Without this flag the dashboard tells the admin their content was
        "Never synced" mid-sync and invites a second concurrent one.
        """
        synced: dict[str, bool] = {}
        synced_at: dict[str, datetime | None] = {}
        in_flight: dict[str, bool] = {}
        for entity in _TRACKED_ENTITIES:
            # `.first()` is ordered `synced_at DESC` because `sync_log` has no
            # unique constraint on (cluster_id, org_id, entity_type).
            last_success = session.exec(
                select(SyncLog)
                .where(
                    SyncLog.cluster_id == cluster_id,
                    SyncLog.org_id == org_id,
                    SyncLog.entity_type == entity,
                    SyncLog.status == "SUCCESS",
                )
                .order_by(col(SyncLog.synced_at).desc())
            ).first()
            synced[entity] = last_success is not None
            synced_at[entity] = _naive(last_success.synced_at) if last_success else None

            newest = session.exec(
                select(SyncLog)
                .where(
                    SyncLog.cluster_id == cluster_id,
                    SyncLog.org_id == org_id,
                    SyncLog.entity_type == entity,
                )
                .order_by(col(SyncLog.synced_at).desc())
            ).first()
            in_flight[entity] = newest is not None and newest.status == "IN_PROGRESS"
        return synced, synced_at, in_flight

    @staticmethod
    def _attention(session: Session, *, cluster_id: str, org_id: int, synced: dict[str, bool]) -> dict[str, int]:
        """
        Counts of things an admin probably needs to act on.

        Every one of these is already in the cache — they are cheap aggregate
        queries, not live calls — and each maps to a tool the toolkit already
        ships (deactivate/delete users, group management, transfer ownership).

        Each signal is a join across two entities, so it is only meaningful
        once BOTH have synced: without a user sync every object looks orphaned,
        and without a group sync every user looks ungrouped. Unmet
        prerequisites report 0 rather than a fabricated alarm.
        """
        users_ready = synced.get("users", False)
        groups_ready = synced.get("groups", False)
        metadata_ready = synced.get("metadata", False)

        if not users_ready:
            return {
                "inactive_users": 0,
                "users_without_group": 0,
                "empty_groups": 0,
                "orphaned_content": 0,
            }

        org_user_guids = select(UserOrgMembership.ts_guid).where(
            UserOrgMembership.cluster_id == cluster_id,
            UserOrgMembership.org_id == org_id,
        )

        inactive_users = session.exec(
            select(func.count())
            .select_from(CachedUser)
            .where(
                CachedUser.cluster_id == cluster_id,
                CachedUser.status != "ACTIVE",
                col(CachedUser.ts_guid).in_(org_user_guids),
            )
        ).one()

        users_without_group = 0
        empty_groups = 0
        if groups_ready:
            grouped_users = select(UserGroupMembership.user_guid).where(
                UserGroupMembership.cluster_id == cluster_id,
                UserGroupMembership.org_id == org_id,
            )
            users_without_group = session.exec(
                select(func.count())
                .select_from(UserOrgMembership)
                .where(
                    UserOrgMembership.cluster_id == cluster_id,
                    UserOrgMembership.org_id == org_id,
                    col(UserOrgMembership.ts_guid).not_in(grouped_users),
                )
            ).one()

            populated_groups = select(UserGroupMembership.group_guid).where(
                UserGroupMembership.cluster_id == cluster_id,
                UserGroupMembership.org_id == org_id,
            )
            empty_groups = session.exec(
                select(func.count())
                .select_from(CachedGroup)
                .where(
                    CachedGroup.cluster_id == cluster_id,
                    CachedGroup.org_id == org_id,
                    col(CachedGroup.ts_guid).not_in(populated_groups),
                )
            ).one()

        # Content whose owner is no longer a known user on this cluster — the
        # trigger for Users → Transfer ownership.
        orphaned_content = 0
        if metadata_ready:
            known_owners = select(CachedUser.ts_guid).where(CachedUser.cluster_id == cluster_id)
            orphaned_content = session.exec(
                select(func.count())
                .select_from(CachedMetadata)
                .where(
                    CachedMetadata.cluster_id == cluster_id,
                    CachedMetadata.org_id == org_id,
                    CachedMetadata.owner_guid != "",
                    col(CachedMetadata.owner_name) != "System User",
                    col(CachedMetadata.owner_guid).not_in(known_owners),
                )
            ).one()

        return {
            "inactive_users": inactive_users,
            "users_without_group": users_without_group,
            "empty_groups": empty_groups,
            "orphaned_content": orphaned_content,
        }

    @staticmethod
    def _recent_activity(session: Session, *, cluster_id: str, org_id: int) -> list[dict]:
        """
        Merge the three audit trails into one feed, newest first.

        Bounded by `ACTIVITY_MAX_AGE_DAYS` — a card titled "recent" that shows
        months-old rows reads as current activity when it is really an empty
        state — and identical adjacent entries are collapsed, so four
        single-object deletes become one "×4" line instead of four.
        """
        cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=ACTIVITY_MAX_AGE_DAYS)
        items: list[dict] = []

        # Content deletions (Archiver / Bulk Delete) — one row per object,
        # aggregated in SQL into one feed entry per session (job). One query,
        # no per-session follow-up read.
        #
        # `cluster_id` AND `org_id` stay in the WHERE clause (pre-aggregation):
        # they select which rows belong to this feed at all. The AGE cutoff, by
        # contrast, is a HAVING on max(archived_at) — a row-level cutoff would
        # re-introduce exactly the defect this query exists to remove, letting a
        # session straddling the boundary be counted from a partial view of its
        # own rows and report "Deleted 3 of 3" about a 400-row session. Do not
        # "optimize" it back into the WHERE.
        #
        # The join is an OUTER join so a session whose `Job` row has been purged
        # still renders (as "archived, intent unknown") rather than vanishing.
        # We `json_extract` the one key we need instead of selecting
        # `Job.parameters`: that column holds the full `object_ids` list
        # (`ts_admin/api/archiver.py:429`), so selecting it for 50 sessions on
        # every dashboard poll could pull ~10MB. `json_extract` is SQLite's
        # JSON1 extension — compiled in by default since 3.38; a build without
        # it raises OperationalError loudly rather than degrading silently.
        archived_at = func.max(ArchiveRecord.archived_at).label("archived_at")
        action = func.json_extract(Job.parameters, "$.action")
        for row in session.exec(
            select(
                ArchiveRecord.job_id,
                archived_at,
                func.count().label("total"),
                func.count(case((col(ArchiveRecord.deleted_confirmed_at).is_not(None), 1))).label("deleted"),
                func.count(case((ArchiveRecord.tml_export_status == "SUCCESS", 1))).label("exported"),
                func.count(case((ArchiveRecord.tml_export_status == "FAILED", 1))).label("failed"),
                Job.job_type,
                Job.status,
                action.label("action"),
            )
            .select_from(ArchiveRecord)
            .join(
                Job,
                onclause=(Job.id == ArchiveRecord.job_id) & (Job.cluster_id == ArchiveRecord.cluster_id),
                isouter=True,
            )
            .where(
                ArchiveRecord.cluster_id == cluster_id,
                ArchiveRecord.org_id == org_id,
            )
            .group_by(col(ArchiveRecord.job_id), col(Job.job_type), col(Job.status), action)
            .having(func.max(ArchiveRecord.archived_at) >= cutoff)
            .order_by(desc("archived_at"))
            .limit(_ACTIVITY_SCAN_SESSIONS)
        ).all():
            label, status = _delete_entry(
                total=row.total,
                deleted=row.deleted,
                exported=row.exported,
                failed=row.failed,
                job_type=row.job_type,
                job_status=row.status,
                action=row.action,
            )
            items.append(
                {
                    "kind": "delete",
                    "label": label,
                    "status": status,
                    "timestamp": _naive(row.archived_at),
                }
            )

        # Sharing changes — one row per object × principal; grouped per session
        # in SQL, same shape as above. No `Job` join here: a share row's own
        # `status` already says what happened to it, and a session still in
        # flight simply reads as PENDING until its rows land, self-correcting on
        # the next poll (the dashboard polls; there is no cached verdict).
        #
        # NOTE: COUNT(DISTINCT x) is single-column only on SQLite — there is no
        # COUNT(DISTINCT a, b), so these must stay two separate counts.
        executed_at = func.max(ShareRecord.executed_at).label("executed_at")
        for row in session.exec(
            select(
                ShareRecord.job_id,
                executed_at,
                func.count(distinct(col(ShareRecord.object_guid))).label("objects"),
                func.count(distinct(col(ShareRecord.principal_guid))).label("principals"),
                func.count(case((ShareRecord.status == "SUCCESS", 1))).label("succeeded"),
                func.count(case((ShareRecord.status == "FAILED", 1))).label("failed"),
            )
            .where(
                ShareRecord.cluster_id == cluster_id,
                ShareRecord.org_id == org_id,
            )
            .group_by(col(ShareRecord.job_id))
            .having(func.max(ShareRecord.executed_at) >= cutoff)
            .order_by(desc("executed_at"))
            .limit(_ACTIVITY_SCAN_SESSIONS)
        ).all():
            n, m = row.objects, row.principals
            # Three-way, with the zero-success limb first: a session where every
            # row failed is FAILED, and a session where no row has reached a
            # terminal state yet is PENDING — never a silent SUCCESS.
            if row.succeeded and row.failed:
                status = "PARTIAL"
            elif row.failed:
                status = "FAILED"
            elif row.succeeded:
                status = "SUCCESS"
            else:
                status = "PENDING"
            if status == "FAILED":
                label = f"Sharing update failed for {_plural(n, 'object')} ({_plural(m, 'principal')})"
            elif status == "PENDING":
                label = f"Sharing update pending on {_plural(n, 'object')} for {_plural(m, 'principal')}"
            else:
                label = f"Updated sharing on {_plural(n, 'object')} for {_plural(m, 'principal')}"
            items.append(
                {
                    "kind": "share",
                    "label": label,
                    "status": status,
                    "timestamp": _naive(row.executed_at),
                }
            )

        # User-management actions — already one row per action.
        labels = {
            "transfer": "Transferred ownership",
            "transfer_sharing": "Transferred sharing",
            "delete": "Deleted user",
        }
        for rec in session.exec(
            select(UserActionRecord)
            .where(
                UserActionRecord.cluster_id == cluster_id,
                UserActionRecord.org_id == org_id,
                col(UserActionRecord.executed_at) >= cutoff,
            )
            .order_by(col(UserActionRecord.executed_at).desc())
            .limit(_ACTIVITY_SCAN_ROWS)
        ).all():
            base = labels.get(rec.action_type, rec.action_type)
            if rec.action_type == "delete":
                label = f"{base} {rec.from_username}"
            else:
                label = f"{base}: {rec.from_username} → {rec.to_username}"
            items.append(
                {
                    "kind": "user_action",
                    "label": label,
                    "status": rec.status,
                    "timestamp": _naive(rec.executed_at),
                }
            )

        items.sort(key=lambda x: x["timestamp"] or datetime.min, reverse=True)
        return DashboardService._collapse(items)[:RECENT_ACTIVITY_LIMIT]

    @staticmethod
    def _collapse(items: list[dict]) -> list[dict]:
        """
        Fold runs of identical adjacent entries into one row with a count.

        The merged status is the WORST of the run by explicit rank
        (`_STATUS_SEVERITY`), not "whatever the first writer put there". The
        previous form only overwrote a SUCCESS, so a FAILED entry followed by a
        PARTIAL one stayed FAILED (correct) while the same pair in the other
        order reported PARTIAL about a run containing a total failure. The rank
        also covers `kind == "user_action"`, whose status comes straight off the
        record and can be FAILED.
        """
        collapsed: list[dict] = []
        for item in items:
            prev = collapsed[-1] if collapsed else None
            if prev and prev["kind"] == item["kind"] and prev["label"] == item["label"]:
                prev["count"] += 1
                if _severity(item["status"]) > _severity(prev["status"]):
                    prev["status"] = item["status"]
                continue
            collapsed.append({**item, "count": 1})
        return collapsed
