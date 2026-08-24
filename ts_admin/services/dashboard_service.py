"""
Dashboard aggregates — one read that powers the whole Dashboard page.

Everything comes from the local SQLite cache (counts, jobs, audit history);
nothing here touches the live ThoughtSpot cluster, so the dashboard renders
instantly even when the cluster is offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlmodel import Session, col, select

from ts_admin import database as _db
from ts_admin.models.archive_record import ArchiveRecord
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
# How many raw audit rows to scan per source when building the activity feed.
# Bulk operations write one row per object/principal, so grouping needs a
# window of raw rows, not a LIMIT on the grouped result.
_ACTIVITY_SCAN_ROWS = 300
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
_TRACKED_ENTITIES = ("metadata", "users", "groups", "tags", "dependencies")
_IN_FLIGHT_STATUSES = ("QUEUED", "PENDING", "RUNNING")


def _naive(dt: datetime | None) -> datetime | None:
    """SQLite stores naive datetimes; normalize both kinds for comparison."""
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


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

        # Content deletions (Archiver / Bulk Delete) — one row per object;
        # group into one feed entry per session (job).
        deletions: dict[str, dict] = {}
        for rec in session.exec(
            select(ArchiveRecord)
            .where(
                ArchiveRecord.cluster_id == cluster_id,
                ArchiveRecord.org_id == org_id,
                col(ArchiveRecord.archived_at) >= cutoff,
            )
            .order_by(col(ArchiveRecord.archived_at).desc())
            .limit(_ACTIVITY_SCAN_ROWS)
        ).all():
            entry = deletions.setdefault(
                rec.job_id,
                {"kind": "delete", "count": 0, "failed": 0, "timestamp": _naive(rec.archived_at)},
            )
            entry["count"] += 1
            if rec.tml_export_status == "FAILED":
                entry["failed"] += 1
            ts = _naive(rec.archived_at)
            if ts and entry["timestamp"] and ts > entry["timestamp"]:
                entry["timestamp"] = ts
        for entry in deletions.values():
            n = entry["count"]
            items.append(
                {
                    "kind": "delete",
                    "label": f"Deleted {n} object{'s' if n != 1 else ''} (TML backed up)",
                    "status": "PARTIAL" if entry["failed"] else "SUCCESS",
                    "timestamp": entry["timestamp"],
                }
            )

        # Sharing changes — one row per object × principal; group per session.
        shares: dict[str, dict] = {}
        for rec in session.exec(
            select(ShareRecord)
            .where(
                ShareRecord.cluster_id == cluster_id,
                ShareRecord.org_id == org_id,
                col(ShareRecord.executed_at) >= cutoff,
            )
            .order_by(col(ShareRecord.executed_at).desc())
            .limit(_ACTIVITY_SCAN_ROWS)
        ).all():
            entry = shares.setdefault(
                rec.job_id,
                {
                    "objects": set(),
                    "principals": set(),
                    "failed": 0,
                    "timestamp": _naive(rec.executed_at),
                },
            )
            entry["objects"].add(rec.object_guid)
            entry["principals"].add(rec.principal_guid)
            if rec.status == "FAILED":
                entry["failed"] += 1
            ts = _naive(rec.executed_at)
            if ts and entry["timestamp"] and ts > entry["timestamp"]:
                entry["timestamp"] = ts
        for entry in shares.values():
            n, m = len(entry["objects"]), len(entry["principals"])
            items.append(
                {
                    "kind": "share",
                    "label": (
                        f"Updated sharing on {n} object{'s' if n != 1 else ''} for {m} principal{'s' if m != 1 else ''}"
                    ),
                    "status": "PARTIAL" if entry["failed"] else "SUCCESS",
                    "timestamp": entry["timestamp"],
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
        """Fold runs of identical adjacent entries into one row with a count."""
        collapsed: list[dict] = []
        for item in items:
            prev = collapsed[-1] if collapsed else None
            if prev and prev["kind"] == item["kind"] and prev["label"] == item["label"]:
                prev["count"] += 1
                if prev["status"] == "SUCCESS" and item["status"] != "SUCCESS":
                    prev["status"] = item["status"]
                continue
            collapsed.append({**item, "count": 1})
        return collapsed
