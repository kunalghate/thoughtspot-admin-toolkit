"""
ArchiverService — Content Archiver business logic.

Rules:
  - SQLite-only methods are sync.
  - Methods that call the ThoughtSpot API are async.
  - No business logic in ts_client — all orchestration lives here.
  - Every write operation: TML export first, delete second, audit log third.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import asc, desc, func, nulls_last
from sqlmodel import Session, col, or_, select

import ts_admin.database as _db
from ts_admin.models.cache.ts_metadata import CachedMetadata

logger = logging.getLogger(__name__)

# TML backup directory: ~/.ts-admin/tml-exports/{job_id}/{guid}.tml
TML_EXPORT_DIR = Path.home() / ".ts-admin" / "tml-exports"


# ── Generic helpers ────────────────────────────────────────────────────────────


def _chunks(lst: list, size: int):
    """Yield successive chunks of `size` from `lst`."""
    for i in range(0, len(lst), size):
        yield lst[i : i + size]


def _fetch_objects_by_guids(
    session,
    guids: list[str],
    cluster_id: str,
    org_id: int,
):
    """
    Fetch CachedMetadata rows for the given GUIDs in one chunked query.

    SQLite limits IN (...) to 999 parameters — chunk at 500 to stay safe.
    Returns a list of CachedMetadata objects in unspecified order.
    """
    from sqlmodel import col, select

    from ts_admin.models.cache.ts_metadata import CachedMetadata

    results = []
    for chunk in _chunks(guids, 500):
        rows = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                col(CachedMetadata.ts_guid).in_(chunk),
            )
        ).all()
        results.extend(rows)
    return results


# ── Tag-name JSON helpers ──────────────────────────────────────────────────────


def _add_tag(tag_names_json: str, tag: str) -> str:
    """Return updated JSON array with `tag` appended (idempotent)."""
    names: list[str] = json.loads(tag_names_json)
    if tag not in names:
        names.append(tag)
    return json.dumps(names)


def _remove_tag(tag_names_json: str, tag: str) -> str:
    """Return updated JSON array with `tag` removed."""
    names: list[str] = json.loads(tag_names_json)
    return json.dumps([n for n in names if n != tag])


def _has_tag(tag_names_json: str, tag: str) -> bool:
    """Return True if `tag` is present in the JSON array."""
    return tag in json.loads(tag_names_json)


# ── Stale-query helpers ────────────────────────────────────────────────────────

# Archiver only targets these two object types.
_ARCHIVABLE_TYPES = ("LIVEBOARD", "ANSWER")


def _parse_iso_date(s: str | None) -> datetime | None:
    """Parse YYYY-MM-DD into a naive UTC datetime at start-of-day."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:10])
    except ValueError:
        return None


def _stale_conditions(
    cluster_id: str,
    org_id: int,
    stale_activity_days: int,
    stale_modified_days: int,
    types: list[str] | None,
    exclude_tags: list[str] | None,
    owner_guid: str | None,
    exclude_owner_guids: list[str] | None,
    filter_tags: list[str] | None = None,
    search: str | None = None,
    stale_operator: str = "AND",
    owner_name_search: str | None = None,
    tag_search: str | None = None,
    days_unused_min: int | None = None,
    days_unused_max: int | None = None,
    views_min: int | None = None,
    views_max: int | None = None,
    last_accessed_before: str | None = None,
    last_accessed_after: str | None = None,
    modified_before: str | None = None,
    modified_after: str | None = None,
    created_before: str | None = None,
    created_after: str | None = None,
) -> list[Any]:
    """
    Build the WHERE conditions shared by preview() and search().

    An object is considered stale when BOTH of the following are true:
      - last_accessed_at < cutoff_activity  (or NULL — never accessed)
      - modified_at      < cutoff_modified  (or NULL — never modified)
    """
    # Use naive UTC datetimes — SQLite stores datetimes without tzinfo
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff_activity = now - timedelta(days=stale_activity_days)
    cutoff_modified = now - timedelta(days=stale_modified_days)

    allowed_types = list(types) if types else list(_ARCHIVABLE_TYPES)

    stale_activity = or_(
        CachedMetadata.last_accessed_at == None,  # noqa: E711
        col(CachedMetadata.last_accessed_at) < cutoff_activity,
    )
    stale_modified = or_(
        CachedMetadata.modified_at == None,  # noqa: E711
        col(CachedMetadata.modified_at) < cutoff_modified,
    )

    # AND: object must satisfy both thresholds (default — more conservative)
    # OR:  object satisfies either threshold (broader — catches everything stale on any axis)
    stale_expr = (
        or_(stale_activity, stale_modified)
        if stale_operator.upper() == "OR"
        else stale_activity.__and__(stale_modified)
    )

    conditions: list[Any] = [
        CachedMetadata.cluster_id == cluster_id,
        CachedMetadata.org_id == org_id,
        col(CachedMetadata.object_type).in_(allowed_types),
        stale_expr,
        # Hide objects owned by the built-in "System User" — not actionable
        # for admins (system-owned / internal content).
        col(CachedMetadata.owner_name) != "System User",
    ]

    if owner_guid:
        conditions.append(CachedMetadata.owner_guid == owner_guid)

    if exclude_owner_guids:
        for chunk in _chunks(exclude_owner_guids, 500):
            conditions.append(col(CachedMetadata.owner_guid).not_in(chunk))

    if exclude_tags:
        # Exclude objects that have ANY of the excluded tags.
        # tag_names is stored as JSON e.g. '["Finance","HR"]'; use LIKE per tag.
        for tag in exclude_tags:
            conditions.append(col(CachedMetadata.tag_names).not_like(f'%"{tag}"%'))

    if filter_tags:
        # Include objects that have ANY of the filter tags (OR logic).
        conditions.append(or_(*[col(CachedMetadata.tag_names).contains(f'"{tag}"') for tag in filter_tags]))

    if search:
        conditions.append(col(CachedMetadata.name).ilike(f"%{search}%"))

    if owner_name_search:
        conditions.append(col(CachedMetadata.owner_name).ilike(f"%{owner_name_search}%"))

    if tag_search:
        # Substring against the JSON tag array (e.g. '["Stale","Finance"]').
        conditions.append(col(CachedMetadata.tag_names).ilike(f'%"%{tag_search}%"%'))

    # days_unused is computed off last_accessed_at — invert min/max
    # last_accessed_at <= now - days_unused_min  →  unused for AT LEAST that long
    # last_accessed_at >= now - days_unused_max  →  unused for AT MOST that long
    if days_unused_min is not None:
        conditions.append(col(CachedMetadata.last_accessed_at) <= now - timedelta(days=days_unused_min))
    if days_unused_max is not None:
        conditions.append(col(CachedMetadata.last_accessed_at) >= now - timedelta(days=days_unused_max))

    if views_min is not None:
        conditions.append(col(CachedMetadata.view_count) >= views_min)
    if views_max is not None:
        conditions.append(col(CachedMetadata.view_count) <= views_max)

    last_acc_before_dt = _parse_iso_date(last_accessed_before)
    last_acc_after_dt = _parse_iso_date(last_accessed_after)
    if last_acc_before_dt is not None:
        conditions.append(col(CachedMetadata.last_accessed_at) <= last_acc_before_dt + timedelta(days=1))
    if last_acc_after_dt is not None:
        conditions.append(col(CachedMetadata.last_accessed_at) >= last_acc_after_dt)

    modified_before_dt = _parse_iso_date(modified_before)
    modified_after_dt = _parse_iso_date(modified_after)
    if modified_before_dt is not None:
        conditions.append(col(CachedMetadata.modified_at) <= modified_before_dt + timedelta(days=1))
    if modified_after_dt is not None:
        conditions.append(col(CachedMetadata.modified_at) >= modified_after_dt)

    created_before_dt = _parse_iso_date(created_before)
    created_after_dt = _parse_iso_date(created_after)
    if created_before_dt is not None:
        conditions.append(col(CachedMetadata.created_at) <= created_before_dt + timedelta(days=1))
    if created_after_dt is not None:
        conditions.append(col(CachedMetadata.created_at) >= created_after_dt)

    return conditions


def _compute_days_unused(obj: CachedMetadata) -> int:
    """
    Compute days_unused from last_accessed_at (falling back to created_at).

    days_unused is a derived value — it is not stored in the DB.
    """
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    ref = obj.last_accessed_at or obj.created_at
    if ref is None:
        return 9999
    ref_naive = ref.replace(tzinfo=None) if ref.tzinfo is not None else ref
    return max(0, (now - ref_naive).days)


# ── Phase 2: Read-only queries ─────────────────────────────────────────────────


class ArchiverService:
    @staticmethod
    def preview(
        *,
        cluster_id: str,
        org_id: int,
        stale_activity_days: int = 90,
        stale_modified_days: int = 90,
        types: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        stale_operator: str = "AND",
        owner_guid: str | None = None,
        exclude_owner_guids: list[str] | None = None,
    ) -> dict:
        """
        Return total count + per-type breakdown of stale objects matching the criteria.

        Does NOT return individual objects — use search() for that.
        """
        conditions = _stale_conditions(
            cluster_id,
            org_id,
            stale_activity_days,
            stale_modified_days,
            types,
            exclude_tags,
            owner_guid,
            exclude_owner_guids,
            stale_operator=stale_operator,
        )

        with Session(_db.get_engine()) as session:
            rows = session.exec(
                select(CachedMetadata.object_type, func.count()).where(*conditions).group_by(CachedMetadata.object_type)
            ).all()

        by_type: dict[str, int] = {r[0]: r[1] for r in rows}
        total = sum(by_type.values())

        # Human-readable criteria summary for the badge tooltip
        type_labels = {"LIVEBOARD": "Liveboard", "ANSWER": "Answer"}
        type_str = " & ".join(type_labels.get(t, t) for t in sorted(by_type))
        criteria_summary = f"Unused {stale_activity_days}+ days · Unmodified {stale_modified_days}+ days" + (
            f" · {type_str}" if type_str else ""
        )

        return {"total": total, "by_type": by_type, "criteria_summary": criteria_summary}

    @staticmethod
    def search(
        *,
        cluster_id: str,
        org_id: int,
        stale_activity_days: int = 90,
        stale_modified_days: int = 90,
        types: list[str] | None = None,
        exclude_tags: list[str] | None = None,
        filter_tags: list[str] | None = None,
        search: str | None = None,
        stale_operator: str = "AND",
        owner_guid: str | None = None,
        exclude_owner_guids: list[str] | None = None,
        owner_name_search: str | None = None,
        tag_search: str | None = None,
        days_unused_min: int | None = None,
        days_unused_max: int | None = None,
        views_min: int | None = None,
        views_max: int | None = None,
        last_accessed_before: str | None = None,
        last_accessed_after: str | None = None,
        modified_before: str | None = None,
        modified_after: str | None = None,
        created_before: str | None = None,
        created_after: str | None = None,
        sort_field: str = "days_unused",
        sort_order: str = "desc",
        record_offset: int = 0,
        page_size: int = 200,
    ) -> tuple[list[dict], int]:
        """
        Return a paginated list of stale objects with days_unused computed in Python.

        stale_operator — "AND" (default): both thresholds must be met
                         "OR": either threshold is sufficient
        """
        conditions = _stale_conditions(
            cluster_id,
            org_id,
            stale_activity_days,
            stale_modified_days,
            types,
            exclude_tags,
            owner_guid,
            exclude_owner_guids,
            filter_tags=filter_tags,
            search=search,
            stale_operator=stale_operator,
            owner_name_search=owner_name_search,
            tag_search=tag_search,
            days_unused_min=days_unused_min,
            days_unused_max=days_unused_max,
            views_min=views_min,
            views_max=views_max,
            last_accessed_before=last_accessed_before,
            last_accessed_after=last_accessed_after,
            modified_before=modified_before,
            modified_after=modified_after,
            created_before=created_before,
            created_after=created_after,
        )

        # Sort mapping — days_unused proxied via last_accessed_at
        _sortable: dict[str, Any] = {
            "name": CachedMetadata.name,
            "object_type": CachedMetadata.object_type,
            "owner_name": CachedMetadata.owner_name,
            "created_at": CachedMetadata.created_at,
            "modified_at": CachedMetadata.modified_at,
            "last_accessed_at": CachedMetadata.last_accessed_at,
            "days_unused": CachedMetadata.last_accessed_at,  # proxy
            "view_count": CachedMetadata.view_count,
        }
        sort_col = _sortable.get(sort_field, CachedMetadata.last_accessed_at)
        # For staleness columns, NULL = most stale. ASC puts NULLs first (correct for
        # "most stale first" when sort_order=asc). Invert for desc: nulls_last(desc).
        if sort_order.lower() == "asc":
            order_expr = asc(sort_col)  # NULLs sort first naturally in SQLite
        else:
            order_expr = nulls_last(desc(sort_col))

        with Session(_db.get_engine()) as session:
            total = session.exec(select(func.count()).select_from(CachedMetadata).where(*conditions)).one()

            rows = session.exec(
                select(CachedMetadata).where(*conditions).order_by(order_expr).offset(record_offset).limit(page_size)
            ).all()

        items = [
            {
                "ts_guid": r.ts_guid,
                "name": r.name,
                "object_type": r.object_type,
                "owner_guid": r.owner_guid,
                "owner_name": r.owner_name,
                "org_id": r.org_id,
                "last_accessed_at": r.last_accessed_at.isoformat() if r.last_accessed_at else None,
                "modified_at": r.modified_at.isoformat() if r.modified_at else None,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "view_count": r.view_count,
                "days_unused": _compute_days_unused(r),
                "tags": r.get_tag_names(),
            }
            for r in rows
        ]
        return items, total

    @staticmethod
    def list_tags(
        *,
        cluster_id: str,
        org_id: int,
        stale_activity_days: int = 90,
        stale_modified_days: int = 90,
        types: list[str] | None = None,
    ) -> list[dict]:
        """
        Return distinct tag names found on stale objects matching the given criteria.

        Scoped to the same stale threshold + type filter the user has set, so
        the dropdown only offers tags that will actually return results when selected.
        """
        conditions = _stale_conditions(
            cluster_id,
            org_id,
            stale_activity_days,
            stale_modified_days,
            types,
            None,
            None,
            None,
        )
        conditions.append(CachedMetadata.tag_names != "[]")

        with Session(_db.get_engine()) as session:
            tag_name_jsons = session.exec(select(CachedMetadata.tag_names).where(*conditions)).all()

        seen: set[str] = set()
        for tag_names_json in tag_name_jsons:
            for name in json.loads(tag_names_json):
                seen.add(name)

        return [{"ts_guid": name, "name": name, "color": ""} for name in sorted(seen)]


# ── Phase 3: Tag / Untag ───────────────────────────────────────────────────────


def _get_cluster(cluster_id: str):
    """Look up a ClusterConfig by ID from the loaded config."""
    from ts_admin.config import load_config

    config = load_config()
    cluster = config.clusters.get(cluster_id)
    if cluster is None:
        raise ValueError(f"Cluster {cluster_id!r} not found in config")
    return cluster


async def execute(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_ids: list[str],
    action: str,
    tag_name: str = "INACTIVE",
    create_tag_if_missing: bool = True,
) -> None:
    """
    Background task: tag, untag, or delete a set of metadata objects.

    action="tag"    — assign `tag_name` to all objects; create the tag if missing
    action="untag"  — remove `tag_name` from all objects
    action="delete" — TML-backup every object then permanently delete it (cancel-aware)
    """
    if action == "delete":
        await _execute_delete(job_id, cluster_id, org_id, object_ids)
        return

    from ts_admin.models.audit_log import AuditLog
    from ts_admin.services.job_service import (
        mark_complete,
        mark_failed,
        mark_partial,
        mark_running,
        update_progress,
    )
    from ts_admin.ts_client import ThoughtSpotClient

    total = len(object_ids)
    mark_running(job_id, total)

    try:
        cluster = _get_cluster(cluster_id)
        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id if org_id != 0 else None),
        ) as client:
            # Resolve or create the tag
            tags = await client.search_tags()
            tag = next((t for t in tags if t.name == tag_name), None)

            if tag is None:
                if not create_tag_if_missing:
                    mark_failed(job_id, f"Tag {tag_name!r} not found on cluster")
                    return
                tag = await client.create_tag(name=tag_name)
                logger.info("Created tag %r on cluster %s", tag_name, cluster_id)

            succeeded = 0
            failed_ids: list[str] = []

            for chunk in _chunks(object_ids, 50):
                try:
                    if action == "tag":
                        await client.assign_tag(object_ids=chunk, tag_id=tag.id)
                    else:
                        await client.unassign_tag(object_ids=chunk, tag_id=tag.id)

                    # Mirror the change in the SQLite cache
                    with Session(_db.get_engine()) as session:
                        cached = _fetch_objects_by_guids(session, chunk, cluster_id, org_id)
                        for obj in cached:
                            if action == "tag":
                                obj.tag_names = _add_tag(obj.tag_names, tag_name)
                            else:
                                obj.tag_names = _remove_tag(obj.tag_names, tag_name)
                            session.add(obj)
                        session.commit()

                    succeeded += len(chunk)
                except Exception as exc:
                    logger.warning("execute %s chunk failed: %s", action, exc)
                    failed_ids.extend(chunk)

                update_progress(job_id, succeeded)

        # Audit log
        with Session(_db.get_engine()) as session:
            entry = AuditLog(
                cluster_id=cluster_id,
                action_type=action,
                entity_type="metadata",
                items_affected=succeeded,
                status="COMPLETE" if not failed_ids else "PARTIAL",
            )
            entry.set_parameters({"tag_name": tag_name, "object_ids": object_ids, "errors": failed_ids})
            session.add(entry)
            session.commit()

        result = {"succeeded": succeeded, "failed": len(failed_ids), "tag_name": tag_name}
        if failed_ids:
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)

        logger.info(
            "archive.%s job=%s cluster=%s tag=%r succeeded=%d failed=%d",
            action,
            job_id,
            cluster_id,
            tag_name,
            succeeded,
            len(failed_ids),
        )

    except Exception as exc:
        logger.exception("execute job %s failed: %s", job_id, exc)
        mark_failed(job_id, str(exc))


# ── Phase 5: Delete with mandatory TML safety net ─────────────────────────────


async def _execute_delete(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_ids: list[str],
) -> None:
    """
    Permanently delete objects with a mandatory TML backup before each deletion.

    Phase A — TML Export:
      Export TML for every object. Objects whose export fails are excluded from
      deletion and marked FAILED in ArchiveRecord — they are never deleted.

    Phase B — Delete (cancel-aware):
      Delete only objects whose TML export succeeded. Checks job.is_cancelled
      before each chunk and stops cleanly if set.

    Phase C — Audit:
      Write AuditLog + structured log line.
    """
    from sqlmodel import col
    from sqlmodel import delete as sql_delete

    from ts_admin.models.archive_record import ArchiveRecord
    from ts_admin.models.audit_log import AuditLog
    from ts_admin.services.job_service import (
        is_cancelled,
        mark_complete,
        mark_failed,
        mark_partial,
        mark_running,
        update_progress,
    )
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.ts_client.models import MetadataType

    total = len(object_ids)
    mark_running(job_id, total)

    try:
        # ── Fetch object metadata early (before any concurrent sync can wipe rows)
        with Session(_db.get_engine()) as session:
            cached_objs = _fetch_objects_by_guids(session, object_ids, cluster_id, org_id)
        obj_map = {o.ts_guid: o for o in cached_objs}

        # ── Create TML export directory for this job
        job_tml_dir = TML_EXPORT_DIR / job_id
        job_tml_dir.mkdir(parents=True, exist_ok=True)

        # ── Batch-insert ArchiveRecord rows as PENDING ────────────────────────
        now = datetime.now(timezone.utc)
        archive_rows: list[ArchiveRecord] = []
        for guid in object_ids:
            obj = obj_map.get(guid)
            archive_rows.append(
                ArchiveRecord(
                    cluster_id=cluster_id,
                    job_id=job_id,
                    ts_guid=guid,
                    name=obj.name if obj else guid,
                    object_type=obj.object_type if obj else "UNKNOWN",
                    owner_guid=obj.owner_guid if obj else "",
                    owner_name=obj.owner_name if obj else "",
                    org_id=org_id,
                    last_accessed_at=obj.last_accessed_at if obj else None,
                    days_unused=_compute_days_unused(obj) if obj else 0,
                    tags=obj.tag_names if obj else "[]",
                    tml_export_status="PENDING",
                    archived_at=now,
                )
            )
        # expire_on_commit=False keeps id/ts_guid readable after the session closes
        with Session(_db.get_engine(), expire_on_commit=False) as session:
            for row in archive_rows:
                session.add(row)
            session.commit()

        # Index archive record primary keys by GUID for updates
        rec_id_map = {r.ts_guid: r.id for r in archive_rows}

        def _update_record(guid: str, **kwargs) -> None:
            """Update a single ArchiveRecord field in its own session."""
            rec_id = rec_id_map.get(guid)
            if not rec_id:
                return
            with Session(_db.get_engine()) as s:
                rec = s.get(ArchiveRecord, rec_id)
                if rec:
                    for k, v in kwargs.items():
                        setattr(rec, k, v)
                    s.add(rec)
                    s.commit()

        # ── Phase A: TML Export ───────────────────────────────────────────────
        delete_batch: list[str] = []  # GUIDs successfully exported
        failed_tml: list[str] = []

        cluster = _get_cluster(cluster_id)

        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id if org_id != 0 else None),
        ) as client:
            for chunk in _chunks(object_ids, 50):
                try:
                    tml_results = await client.tml_export(object_ids=chunk)
                except Exception as exc:
                    logger.warning("TML export chunk failed: %s", exc)
                    for guid in chunk:
                        failed_tml.append(guid)
                        _update_record(guid, tml_export_status="FAILED", tml_export_error=str(exc)[:500])
                    continue

                for item in tml_results:
                    info = item.get("info") or {}
                    guid = info.get("id") or info.get("identifier", "")
                    edoc = item.get("edoc") or ""

                    if not guid:
                        continue

                    if edoc:
                        tml_path = job_tml_dir / f"{guid}.tml"
                        tml_path.write_text(edoc, encoding="utf-8")
                        delete_batch.append(guid)
                        _update_record(
                            guid,
                            tml_export_status="SUCCESS",
                            tml_path=str(tml_path),
                        )
                    else:
                        error = info.get("error_message") or "TML export returned empty content"
                        failed_tml.append(guid)
                        _update_record(
                            guid,
                            tml_export_status="FAILED",
                            tml_export_error=error[:500],
                        )

            logger.info(
                "archive.delete job=%s TML export done: %d ok, %d failed",
                job_id,
                len(delete_batch),
                len(failed_tml),
            )

            # ── Phase B: Delete (cancel-aware) ───────────────────────────────
            succeeded = 0
            failed_delete: list[str] = []
            cancelled = False

            # Group by object_type for delete_metadata (requires a single type per call)
            type_groups: dict[str, list[str]] = {}
            for guid in delete_batch:
                obj = obj_map.get(guid)
                type_groups.setdefault(obj.object_type if obj else "LIVEBOARD", []).append(guid)

            for obj_type, guids in type_groups.items():
                if cancelled:
                    break
                try:
                    enum_type = MetadataType(obj_type)
                except ValueError:
                    logger.warning("Unknown type %r — skipping %d objects", obj_type, len(guids))
                    failed_delete.extend(guids)
                    continue

                for chunk in _chunks(guids, 50):
                    if is_cancelled(job_id):
                        cancelled = True
                        logger.info("archive.delete job=%s cancelled at chunk boundary", job_id)
                        break

                    try:
                        await client.delete_metadata(object_ids=chunk, object_type=enum_type)

                        # Remove from CachedMetadata cache
                        with Session(_db.get_engine()) as session:
                            session.exec(sql_delete(CachedMetadata).where(col(CachedMetadata.ts_guid).in_(chunk)))
                            session.commit()

                        succeeded += len(chunk)
                    except Exception as exc:
                        logger.warning("delete_metadata chunk failed: %s", exc)
                        failed_delete.extend(chunk)

                    update_progress(job_id, succeeded)

        # ── Phase C: Audit ───────────────────────────────────────────────────
        status = "PARTIAL" if (failed_tml or failed_delete or cancelled) else "COMPLETE"
        result = {
            "succeeded": succeeded,
            "failed_tml_export": len(failed_tml),
            "failed_delete": len(failed_delete),
            "cancelled": cancelled,
            "job_id": job_id,
            "tml_export_path": str(job_tml_dir),
        }

        with Session(_db.get_engine()) as session:
            entry = AuditLog(
                cluster_id=cluster_id,
                action_type="delete",
                entity_type="metadata",
                items_affected=succeeded,
                status=status,
            )
            entry.set_parameters(
                {
                    "object_ids": object_ids,
                    **result,
                }
            )
            session.add(entry)
            session.commit()

        if succeeded == 0 and not cancelled:
            mark_failed(job_id, f"0 objects deleted — {len(failed_tml)} TML exports failed")
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)

        logger.info(
            "archive.delete job=%s cluster=%s succeeded=%d failed_tml=%d failed_delete=%d cancelled=%s",
            job_id,
            cluster_id,
            succeeded,
            len(failed_tml),
            len(failed_delete),
            cancelled,
        )

    except Exception as exc:
        logger.exception("_execute_delete job %s failed: %s", job_id, exc)
        mark_failed(job_id, str(exc))


# ── Phase 4: Dry-run impact check ─────────────────────────────────────────────


async def dryrun(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_ids: list[str],
) -> None:
    """
    Background task: check permissions + dependencies for a proposed deletion.

    Stores a summary in Job.result:
      total, by_type, shared_count, affected_principals, dependency_warnings, errors
    No objects are modified.
    """
    import asyncio

    from ts_admin.services.job_service import mark_complete, mark_failed, mark_running
    from ts_admin.ts_client import ThoughtSpotClient

    total = len(object_ids)
    mark_running(job_id, total)

    try:
        cluster = _get_cluster(cluster_id)

        # ── Fetch object metadata from SQLite cache (early — before any sync can wipe it)
        with Session(_db.get_engine()) as session:
            cached_objs = _fetch_objects_by_guids(session, object_ids, cluster_id, org_id)
        obj_map = {o.ts_guid: o for o in cached_objs}

        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id if org_id != 0 else None),
        ) as client:
            # ── Permission check (concurrent, Semaphore(10)) ──────────────────
            sem = asyncio.Semaphore(10)

            async def _check_perms(guid: str, obj_type: str):
                async with sem:
                    return guid, await client.fetch_permissions(ts_guid=guid, object_type=obj_type)

            pairs = [(guid, obj_map[guid].object_type) for guid in object_ids if guid in obj_map]
            perm_results = await asyncio.gather(
                *[_check_perms(g, t) for g, t in pairs],
                return_exceptions=True,
            )

            # ── Dependency check (single batch call) ──────────────────────────
            dep_objects = [
                {"identifier": guid, "type": obj_map[guid].object_type} for guid in object_ids if guid in obj_map
            ]
            try:
                dep_map = await client.fetch_dependents(objects=dep_objects)
            except Exception as exc:
                logger.warning("fetch_dependents failed (non-fatal): %s", exc)
                dep_map = {}

        # ── Aggregate ─────────────────────────────────────────────────────────
        by_type: dict[str, int] = {}
        for obj in cached_objs:
            by_type[obj.object_type] = by_type.get(obj.object_type, 0) + 1

        # Objects not in cache (deleted between selection and dryrun)
        missing_guids = [g for g in object_ids if g not in obj_map]

        principals: dict[str, dict] = {}  # principal_id → {name, type, object_count}
        shared_count = 0
        errors: list[dict] = [
            {"ts_guid": g, "error": "Not found in local cache (may have been deleted)"} for g in missing_guids
        ]

        for item in perm_results:
            if isinstance(item, Exception):
                errors.append({"ts_guid": "unknown", "error": str(item)})
                continue
            guid, perms = item
            if perms:
                shared_count += 1
                for p in perms:
                    if p.principal_id not in principals:
                        principals[p.principal_id] = {
                            "name": p.principal_name,
                            "type": p.principal_type,
                            "object_count": 0,
                        }
                    principals[p.principal_id]["object_count"] += 1

        dependency_warnings: list[dict] = []
        for guid, deps in dep_map.items():
            if deps and guid in obj_map:
                obj = obj_map[guid]
                dependency_warnings.append(
                    {
                        "ts_guid": guid,
                        "name": obj.name,
                        "object_type": obj.object_type,
                        "dependents": [
                            {"name": d.get("name", ""), "type": d.get("type", d.get("object_type", ""))}
                            for d in deps[:10]  # cap at 10 per object
                        ],
                    }
                )

        result = {
            "total": len(object_ids),
            "by_type": by_type,
            "shared_count": shared_count,
            "affected_principals": list(principals.values()),
            "dependency_warnings": dependency_warnings,
            "errors": errors,
        }
        mark_complete(job_id, result)
        logger.info(
            "archive.dryrun job=%s cluster=%s total=%d shared=%d deps=%d errors=%d",
            job_id,
            cluster_id,
            len(object_ids),
            shared_count,
            len(dependency_warnings),
            len(errors),
        )

    except Exception as exc:
        logger.exception("dryrun job %s failed: %s", job_id, exc)
        mark_failed(job_id, str(exc))


def dryrun_objects(
    *,
    job_id: str,
    cluster_id: str,
    record_offset: int = 0,
    page_size: int = 100,
) -> tuple[list[dict], int]:
    """
    Return paginated objects queued in a dryrun job, sorted by staleness.

    Reads object_ids from Job.parameters and looks them up in CachedMetadata.
    Objects deleted from the cache since the dryrun started will be absent.
    """
    from ts_admin.database import get_session as _get_session
    from ts_admin.models.job import Job

    with _get_session() as session:
        job = session.get(Job, job_id)
        if job is None:
            return [], 0
        params = job.get_parameters()

    object_ids: list[str] = params.get("object_ids", [])
    if not object_ids:
        return [], 0

    # Re-read org_id from params (stored by the execute endpoint)
    org_id: int = params.get("org_id", 0)
    total = len(object_ids)

    # Paginate the ID list, then fetch from SQLite ordered by staleness
    page_ids = object_ids[record_offset : record_offset + page_size]
    if not page_ids:
        return [], total

    with Session(_db.get_engine()) as session:
        rows = _fetch_objects_by_guids(session, page_ids, cluster_id, org_id)

    # Sort by last_accessed_at ASC (None first = most stale)
    rows.sort(key=lambda r: r.last_accessed_at or datetime.min)

    items = [
        {
            "ts_guid": r.ts_guid,
            "name": r.name,
            "object_type": r.object_type,
            "owner_guid": r.owner_guid,
            "owner_name": r.owner_name,
            "org_id": r.org_id,
            "last_accessed_at": r.last_accessed_at.isoformat() if r.last_accessed_at else None,
            "modified_at": r.modified_at.isoformat() if r.modified_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "view_count": r.view_count,
            "days_unused": _compute_days_unused(r),
            "tags": r.get_tag_names(),
        }
        for r in rows
    ]
    return items, total


# ── Phase 6: Restore & History ─────────────────────────────────────────────────


async def restore(
    job_id: str,
    cluster_id: str,
    org_id: int,
    archive_record_ids: list[str],
) -> None:
    """
    Background task: re-import TML files back into ThoughtSpot.

    For each archive record:
      1. Read TML file from disk (tml_path)
      2. POST to /metadata/tml/import — ThoughtSpot assigns a NEW GUID
      3. Update ArchiveRecord: restored_at, restored_as_guid, restored_by_job_id
      4. Re-insert CachedMetadata row with the new GUID

    Records with missing TML or tml_export_status != "SUCCESS" are skipped.
    """
    from ts_admin.models.archive_record import ArchiveRecord
    from ts_admin.models.audit_log import AuditLog
    from ts_admin.services.job_service import (
        mark_complete,
        mark_failed,
        mark_partial,
        mark_running,
        update_progress,
    )
    from ts_admin.ts_client import ThoughtSpotClient

    total = len(archive_record_ids)
    mark_running(job_id, total)

    try:
        cluster = _get_cluster(cluster_id)

        # Load ArchiveRecord rows
        with Session(_db.get_engine()) as session:
            records = session.exec(select(ArchiveRecord).where(col(ArchiveRecord.id).in_(archive_record_ids))).all()
        rec_map = {r.id: r for r in records}

        succeeded = 0
        failed: list[str] = []
        skipped: list[str] = []

        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id if org_id != 0 else None),
        ) as client:
            for chunk_ids in _chunks(archive_record_ids, 10):
                chunk_recs = [rec_map[rid] for rid in chunk_ids if rid in rec_map]

                # Filter to restorable
                restorable = [
                    r for r in chunk_recs if r.tml_export_status == "SUCCESS" and r.restored_at is None and r.tml_path
                ]
                skip_ids = [r.id for r in chunk_recs if r not in restorable]
                skipped.extend(skip_ids)

                if not restorable:
                    update_progress(job_id, succeeded)
                    continue

                # Read TML files from disk
                tml_strings: list[str] = []
                valid_recs: list[ArchiveRecord] = []
                for rec in restorable:
                    path = Path(rec.tml_path)
                    if not path.exists():
                        logger.warning("TML file missing for record %s: %s", rec.id, rec.tml_path)
                        skipped.append(rec.id)
                        continue
                    tml_strings.append(path.read_text(encoding="utf-8"))
                    valid_recs.append(rec)

                if not tml_strings:
                    continue

                try:
                    import_results = await client.import_tml(tml_strings=tml_strings)
                except Exception as exc:
                    logger.warning("import_tml chunk failed: %s", exc)
                    failed.extend([r.id for r in valid_recs])
                    update_progress(job_id, succeeded)
                    continue

                # Map results back to records by index
                now = datetime.now(timezone.utc)
                for idx, rec in enumerate(valid_recs):
                    result_item = import_results[idx] if idx < len(import_results) else {}
                    # TS REST v2 import response shape:
                    # {"response": {"header": {"id_guid": "..."}, "status": {"status_code": "OK"}}}
                    resp = result_item.get("response") or {}
                    new_guid = (
                        resp.get("header", {}).get("id_guid")  # primary path (v2)
                        or result_item.get("object_id")  # newer TS versions
                        or result_item.get("id")
                        or (result_item.get("header") or {}).get("id_guid")
                        or ""
                    )
                    status_code = (
                        resp.get("status", {}).get("status_code")  # primary path (v2)
                        or result_item.get("status", {}).get("status_code")
                        or "UNKNOWN"
                    )

                    if new_guid and status_code == "OK":
                        # Update ArchiveRecord
                        with Session(_db.get_engine()) as session:
                            db_rec = session.get(ArchiveRecord, rec.id)
                            if db_rec:
                                db_rec.restored_at = now
                                db_rec.restored_as_guid = new_guid
                                db_rec.restored_by_job_id = job_id
                                session.add(db_rec)
                                session.commit()

                        # Re-insert CachedMetadata with the new GUID
                        tags_json = rec.tags if rec.tags else "[]"
                        new_row = CachedMetadata(
                            cluster_id=cluster_id,
                            org_id=org_id,
                            ts_guid=new_guid,
                            name=rec.name,
                            object_type=rec.object_type,
                            owner_guid=rec.owner_guid,
                            owner_name=rec.owner_name,
                            tag_names=tags_json,
                            last_accessed_at=rec.last_accessed_at,
                            synced_at=now,
                        )
                        with Session(_db.get_engine()) as session:
                            # Avoid duplicate if already exists
                            existing = session.exec(
                                select(CachedMetadata).where(
                                    CachedMetadata.cluster_id == cluster_id,
                                    CachedMetadata.ts_guid == new_guid,
                                )
                            ).first()
                            if not existing:
                                session.add(new_row)
                                session.commit()

                        succeeded += 1
                    else:
                        error_msg = (
                            result_item.get("status", {}).get("error_message")
                            or f"Import returned status {status_code}"
                        )
                        logger.warning("restore record %s failed: %s", rec.id, error_msg)
                        failed.append(rec.id)

                update_progress(job_id, succeeded)

        # Audit log
        with Session(_db.get_engine()) as session:
            entry = AuditLog(
                cluster_id=cluster_id,
                action_type="restore",
                entity_type="metadata",
                items_affected=succeeded,
                status="COMPLETE" if not failed else "PARTIAL",
            )
            entry.set_parameters(
                {
                    "archive_record_ids": archive_record_ids,
                    "succeeded": succeeded,
                    "failed": len(failed),
                    "skipped": len(skipped),
                    "job_id": job_id,
                }
            )
            session.add(entry)
            session.commit()

        result = {"succeeded": succeeded, "failed": len(failed), "skipped": len(skipped)}
        if succeeded == 0 and failed:
            mark_failed(job_id, f"0 objects restored — {len(failed)} imports failed")
        elif failed:
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)

        logger.info(
            "archive.restore job=%s cluster=%s succeeded=%d failed=%d skipped=%d",
            job_id,
            cluster_id,
            succeeded,
            len(failed),
            len(skipped),
        )

    except Exception as exc:
        logger.exception("restore job %s failed: %s", job_id, exc)
        mark_failed(job_id, str(exc))


# ── Phase 6: History & Restore ─────────────────────────────────────────────────


def history(
    *,
    cluster_id: str,
    org_id: int,
    record_offset: int = 0,
    page_size: int = 20,
) -> tuple[list[dict], int]:
    """
    Return paginated archive sessions (one row per delete job) sorted newest first.

    A session = one delete job. Aggregates ArchiveRecord rows by job_id to compute
    succeeded / failed counts.
    """
    from ts_admin.models.archive_record import ArchiveRecord

    with Session(_db.get_engine()) as session:
        # Distinct job_ids for this cluster, ordered by newest first
        job_ids_q = (
            select(ArchiveRecord.job_id, func.min(ArchiveRecord.archived_at).label("archived_at"))
            .where(
                ArchiveRecord.cluster_id == cluster_id,
                ArchiveRecord.org_id == org_id,
            )
            .group_by(ArchiveRecord.job_id)
            .order_by(desc("archived_at"))
        )
        total_rows = session.exec(select(func.count()).select_from(job_ids_q.subquery())).one()

        page_rows = session.exec(job_ids_q.offset(record_offset).limit(page_size)).all()
        job_ids = [r[0] for r in page_rows]

        if not job_ids:
            return [], total_rows

        # Aggregate per job
        sessions = []
        for job_id, archived_at in page_rows:
            records = session.exec(select(ArchiveRecord).where(ArchiveRecord.job_id == job_id)).all()
            total = len(records)
            succeeded = sum(1 for r in records if r.tml_export_status == "SUCCESS" and r.restored_at is None)
            failed_tml = sum(1 for r in records if r.tml_export_status == "FAILED")
            failed_delete = 0  # hard to determine post-hoc; use 0 for now
            sessions.append(
                {
                    "job_id": job_id,
                    "archived_at": archived_at.isoformat() if archived_at else None,
                    "total": total,
                    "succeeded": succeeded,
                    "failed_tml_export": failed_tml,
                    "failed_delete": failed_delete,
                }
            )

    return sessions, total_rows


def all_archive_records(
    *,
    cluster_id: str,
    org_id: int,
    sort_field: str = "archived_at",
    sort_order: str = "desc",
    search: str | None = None,
    types: list[str] | None = None,
    owner_name_search: str | None = None,
    archived_before: str | None = None,
    archived_after: str | None = None,
    record_offset: int = 0,
    page_size: int = 200,
) -> tuple[list[dict], int]:
    """
    All ArchiveRecord rows for a cluster/org, paginated, sorted, and filtered.
    Used by the flat History table — no grouping by job.
    """
    from ts_admin.models.archive_record import ArchiveRecord

    _sortable = {
        "name": ArchiveRecord.name,
        "object_type": ArchiveRecord.object_type,
        "owner_name": ArchiveRecord.owner_name,
        "archived_at": ArchiveRecord.archived_at,
    }
    sort_col = _sortable.get(sort_field, ArchiveRecord.archived_at)
    direction = asc if sort_order.lower() == "asc" else desc
    order_expr = nulls_last(direction(sort_col))

    conditions: list[Any] = [
        ArchiveRecord.cluster_id == cluster_id,
        ArchiveRecord.org_id == org_id,
    ]
    if search:
        conditions.append(col(ArchiveRecord.name).ilike(f"%{search}%"))
    if types:
        conditions.append(col(ArchiveRecord.object_type).in_(types))
    if owner_name_search:
        conditions.append(col(ArchiveRecord.owner_name).ilike(f"%{owner_name_search}%"))

    archived_before_dt = _parse_iso_date(archived_before)
    archived_after_dt = _parse_iso_date(archived_after)
    if archived_before_dt is not None:
        conditions.append(col(ArchiveRecord.archived_at) <= archived_before_dt + timedelta(days=1))
    if archived_after_dt is not None:
        conditions.append(col(ArchiveRecord.archived_at) >= archived_after_dt)

    with Session(_db.get_engine()) as session:
        total = session.exec(select(func.count()).select_from(ArchiveRecord).where(*conditions)).one()

        rows = session.exec(
            select(ArchiveRecord).where(*conditions).order_by(order_expr).offset(record_offset).limit(page_size)
        ).all()

    items = [
        {
            "id": r.id,
            "ts_guid": r.ts_guid,
            "name": r.name,
            "object_type": r.object_type,
            "owner_name": r.owner_name,
            "archived_at": r.archived_at.isoformat(),
            "tml_export_status": r.tml_export_status,
            "job_id": r.job_id,
        }
        for r in rows
    ]
    return items, total


def history_session(
    *,
    job_id: str,
    cluster_id: str,
    record_offset: int = 0,
    page_size: int = 100,
) -> tuple[list[dict], int]:
    """
    Return paginated ArchiveRecord rows for one archive session.

    is_restorable = TML export succeeded AND not yet restored.
    """
    from ts_admin.models.archive_record import ArchiveRecord

    with Session(_db.get_engine()) as session:
        total = session.exec(
            select(func.count())
            .select_from(ArchiveRecord)
            .where(
                ArchiveRecord.job_id == job_id,
                ArchiveRecord.cluster_id == cluster_id,
            )
        ).one()

        rows = session.exec(
            select(ArchiveRecord)
            .where(ArchiveRecord.job_id == job_id, ArchiveRecord.cluster_id == cluster_id)
            .order_by(ArchiveRecord.archived_at)
            .offset(record_offset)
            .limit(page_size)
        ).all()

    items = [
        {
            "id": r.id,
            "ts_guid": r.ts_guid,
            "name": r.name,
            "object_type": r.object_type,
            "owner_name": r.owner_name,
            "last_accessed_at": r.last_accessed_at.isoformat() if r.last_accessed_at else None,
            "days_unused": r.days_unused,
            "tags": json.loads(r.tags) if r.tags else [],
            "tml_export_status": r.tml_export_status,
            "archived_at": r.archived_at.isoformat(),
            "restored_at": r.restored_at.isoformat() if r.restored_at else None,
            "restored_as_guid": r.restored_as_guid,
            "is_restorable": r.tml_export_status == "SUCCESS" and r.restored_at is None,
        }
        for r in rows
    ]
    return items, total
