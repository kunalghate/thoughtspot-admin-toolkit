"""
MetadataService — query the local SQLite cache for ThoughtSpot metadata objects.

All reads come from the cache (fast, <100ms).
Write operations (tag, delete) go to the live ThoughtSpot API.

Query pattern:
  Browser filter change
       │
       ▼
  GET /api/v1/metadata  (with filter params)
       │
       ▼
  MetadataService.search()  ← reads from SQLite cache
       │
       ▼
  AG Grid renders results instantly
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import asc, desc, func, nulls_last
from sqlmodel import Session, col, or_, select

import ts_admin.database as _db  # import module, not function — keeps monkeypatching working in tests
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.sync_log import SyncLog

logger = logging.getLogger(__name__)


class MetadataService:
    # ── Search / filter ────────────────────────────────────────────────────────

    @staticmethod
    def search(
        *,
        cluster_id: str,
        org_id: int,
        types: list[str] | None = None,
        owner_guid: str | None = None,
        tag_names: list[str] | None = None,
        search: str | None = None,
        stale_days: int | None = None,
        owner_name_search: str | None = None,
        tag_search: str | None = None,
        views_min: int | None = None,
        views_max: int | None = None,
        last_accessed_before: str | None = None,
        last_accessed_after: str | None = None,
        modified_before: str | None = None,
        modified_after: str | None = None,
        created_before: str | None = None,
        created_after: str | None = None,
        sort_field: str = "modified_at",
        sort_order: str = "desc",
        record_offset: int = 0,
        page_size: int = 200,
    ) -> tuple[list[CachedMetadata], int]:
        """
        Return a filtered, paginated page of metadata objects from the local cache.

        Returns (items, total_count).

        Filters:
          types       — object types to include (LIVEBOARD, ANSWER, etc.)
          owner_guid  — filter by owner GUID
          tag_names   — filter objects that have ALL of the given tags
          search      — substring match on object name (case-insensitive)
          stale_days  — objects not accessed in the last N days
          page        — 1-based page number
          page_size   — rows per page (default 500 — AG Grid handles client-side)
        """
        # Build conditions as a list so the same set applies to both
        # the total-count query and the paginated-fetch query.
        conditions: list[Any] = [
            CachedMetadata.cluster_id == cluster_id,
            CachedMetadata.org_id == org_id,
            # Hide objects owned by the built-in "System User" — not actionable
            # for admins (system-owned / internal content).
            col(CachedMetadata.owner_name) != "System User",
        ]

        if types:
            conditions.append(col(CachedMetadata.object_type).in_(types))

        if owner_guid:
            conditions.append(CachedMetadata.owner_guid == owner_guid)

        if search:
            conditions.append(col(CachedMetadata.name).ilike(f"%{search}%"))

        if stale_days is not None:
            # Use naive UTC — SQLite stores datetimes without tzinfo
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=stale_days)
            conditions.append(
                or_(
                    CachedMetadata.last_accessed_at == None,  # noqa: E711
                    col(CachedMetadata.last_accessed_at) < cutoff,
                )
            )

        if tag_names:
            # tag_names is stored as a JSON string e.g. '["Finance","HR"]'.
            # Use LIKE per tag so objects must have ALL requested tags.
            for tag in tag_names:
                conditions.append(col(CachedMetadata.tag_names).contains(f'"{tag}"'))

        if owner_name_search:
            conditions.append(col(CachedMetadata.owner_name).ilike(f"%{owner_name_search}%"))

        if tag_search:
            conditions.append(col(CachedMetadata.tag_names).ilike(f'%"%{tag_search}%"%'))

        if views_min is not None:
            conditions.append(col(CachedMetadata.view_count) >= views_min)
        if views_max is not None:
            conditions.append(col(CachedMetadata.view_count) <= views_max)

        def _parse_iso(s: str | None) -> datetime | None:
            if not s:
                return None
            try:
                return datetime.fromisoformat(s[:10])
            except ValueError:
                return None

        last_acc_before_dt = _parse_iso(last_accessed_before)
        last_acc_after_dt = _parse_iso(last_accessed_after)
        if last_acc_before_dt is not None:
            conditions.append(col(CachedMetadata.last_accessed_at) <= last_acc_before_dt + timedelta(days=1))
        if last_acc_after_dt is not None:
            conditions.append(col(CachedMetadata.last_accessed_at) >= last_acc_after_dt)

        modified_before_dt = _parse_iso(modified_before)
        modified_after_dt = _parse_iso(modified_after)
        if modified_before_dt is not None:
            conditions.append(col(CachedMetadata.modified_at) <= modified_before_dt + timedelta(days=1))
        if modified_after_dt is not None:
            conditions.append(col(CachedMetadata.modified_at) >= modified_after_dt)

        created_before_dt = _parse_iso(created_before)
        created_after_dt = _parse_iso(created_after)
        if created_before_dt is not None:
            conditions.append(col(CachedMetadata.created_at) <= created_before_dt + timedelta(days=1))
        if created_after_dt is not None:
            conditions.append(col(CachedMetadata.created_at) >= created_after_dt)

        # Map frontend field names to model columns
        _sortable: dict[str, Any] = {
            "name": CachedMetadata.name,
            "object_type": CachedMetadata.object_type,
            "owner_name": CachedMetadata.owner_name,
            "created_at": CachedMetadata.created_at,
            "modified_at": CachedMetadata.modified_at,
            "last_accessed_at": CachedMetadata.last_accessed_at,
            "view_count": CachedMetadata.view_count,
        }
        sort_col = _sortable.get(sort_field, CachedMetadata.name)
        direction = asc if sort_order.lower() == "asc" else desc
        order_expr = nulls_last(direction(sort_col))

        with Session(_db.get_engine()) as session:
            # Total count — use COUNT(*) not len(all()) to avoid loading all rows
            count_stmt = select(func.count()).select_from(CachedMetadata).where(*conditions)
            total = session.exec(count_stmt).one()

            # Paginated fetch
            base_stmt = select(CachedMetadata).where(*conditions).order_by(order_expr)
            items = session.exec(base_stmt.offset(record_offset).limit(page_size)).all()

            return list(items), total

    # ── Single object ──────────────────────────────────────────────────────────

    @staticmethod
    def get(*, cluster_id: str, org_id: int, ts_guid: str) -> CachedMetadata | None:
        """Return a single cached metadata object by GUID, or None if not found."""
        with Session(_db.get_engine()) as session:
            return session.exec(
                select(CachedMetadata).where(
                    CachedMetadata.cluster_id == cluster_id,
                    CachedMetadata.org_id == org_id,
                    CachedMetadata.ts_guid == ts_guid,
                )
            ).first()

    # ── Stats for dashboard ────────────────────────────────────────────────────

    @staticmethod
    def stats(*, cluster_id: str, org_id: int) -> dict:
        """
        Return aggregate stats for the dashboard health card.

        Returns:
            total         — total cached objects
            by_type       — count per object type
            stale_90d     — objects not accessed in 90+ days
            last_synced   — ISO timestamp of last successful metadata sync
        """
        # Use naive UTC datetime for comparison — SQLite stores datetimes without tzinfo
        cutoff_90d = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=90)

        with Session(_db.get_engine()) as session:
            all_items = session.exec(
                select(CachedMetadata).where(
                    CachedMetadata.cluster_id == cluster_id,
                    CachedMetadata.org_id == org_id,
                )
            ).all()

            by_type: dict[str, int] = {}
            stale = 0
            for item in all_items:
                by_type[item.object_type] = by_type.get(item.object_type, 0) + 1
                last = item.last_accessed_at
                if (
                    last is None
                    or (last.tzinfo is None and last < cutoff_90d)
                    or (last.tzinfo is not None and last.replace(tzinfo=None) < cutoff_90d)
                ):
                    stale += 1

            sync_log = session.exec(
                select(SyncLog)
                .where(
                    SyncLog.cluster_id == cluster_id,
                    SyncLog.entity_type == "metadata",
                    SyncLog.status == "SUCCESS",
                )
                .order_by(col(SyncLog.synced_at).desc())
            ).first()

            return {
                "total": len(all_items),
                "by_type": by_type,
                "stale_90d": stale,
                "last_synced": sync_log.synced_at.isoformat() if sync_log and sync_log.synced_at else None,
            }
