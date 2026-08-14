"""
Dashboard aggregates — one read that powers the whole Dashboard page.

Everything comes from the local SQLite cache (counts, jobs, audit history);
nothing here touches the live ThoughtSpot cluster, so the dashboard renders
instantly even when the cluster is offline.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import Session, col, select

from ts_admin import database as _db
from ts_admin.models.archive_record import ArchiveRecord
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_tag import CachedTag
from ts_admin.models.cache.ts_user import UserOrgMembership
from ts_admin.models.job import Job
from ts_admin.models.share_record import ShareRecord
from ts_admin.models.user_action_record import UserActionRecord
from ts_admin.services.metadata_service import MetadataService

RECENT_JOBS_LIMIT = 5
RECENT_ACTIVITY_LIMIT = 8
# How many raw audit rows to scan per source when building the activity feed.
# Bulk operations write one row per object/principal, so grouping needs a
# window of raw rows, not a LIMIT on the grouped result.
_ACTIVITY_SCAN_ROWS = 300


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

            jobs = session.exec(
                select(Job).where(Job.cluster_id == cluster_id).order_by(col(Job.created_at).desc()).limit(200)
            ).all()

            week_ago = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=7)
            failed_jobs_7d = sum(
                1 for j in jobs if j.status == "FAILED" and (_naive(j.created_at) or week_ago) >= week_ago
            )
            recent_jobs = [
                {
                    "id": j.id,
                    "job_type": j.job_type,
                    "status": j.status,
                    "created_at": _naive(j.created_at),
                    "error": j.error,
                }
                for j in jobs[:RECENT_JOBS_LIMIT]
            ]

            activity = DashboardService._recent_activity(session, cluster_id=cluster_id, org_id=org_id)

        return {
            "counts": {
                "users": users,
                "groups": groups,
                "tags": tags,
                "objects_total": meta["total"],
                "objects_by_type": meta["by_type"],
                "stale_90d": meta["stale_90d"],
            },
            "recent_jobs": recent_jobs,
            "recent_activity": activity,
            "failed_jobs_7d": failed_jobs_7d,
        }

    @staticmethod
    def _recent_activity(session: Session, *, cluster_id: str, org_id: int) -> list[dict]:
        """Merge the three audit trails into one feed, newest first."""
        items: list[dict] = []

        # Content deletions (Archiver / Bulk Delete) — one row per object;
        # group into one feed entry per session (job).
        deletions: dict[str, dict] = {}
        for rec in session.exec(
            select(ArchiveRecord)
            .where(ArchiveRecord.cluster_id == cluster_id, ArchiveRecord.org_id == org_id)
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
            .where(ShareRecord.cluster_id == cluster_id, ShareRecord.org_id == org_id)
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
        return items[:RECENT_ACTIVITY_LIMIT]
