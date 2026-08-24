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
from collections.abc import Iterator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

import httpx
from sqlalchemy import asc, desc, func, nulls_last
from sqlmodel import Session, col, or_, select

import ts_admin.database as _db
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.services.deletion_service import (
    TML_EXPORT_DIR,  # re-exported for ts_admin.main startup-cleanup hook
    _chunks,
    _fetch_objects_by_guids,
)
from ts_admin.services.metadata_service import ARCHIVABLE_TYPES
from ts_admin.ts_client.exceptions import (
    TSAdminError,
    TSAuthenticationError,
    TSInsufficientPrivilegesError,
)

if TYPE_CHECKING:  # import only for annotations — the runtime import stays deferred
    from ts_admin.models.archive_record import ArchiveRecord

logger = logging.getLogger(__name__)

__all__ = ["TML_EXPORT_DIR", "ArchiverService"]


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

# Archiver only targets these two object types. Defined in MetadataService so
# the dashboard's staleness stats and this module cannot drift apart.
_ARCHIVABLE_TYPES = ARCHIVABLE_TYPES


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
        from ts_admin.services.deletion_service import _execute_delete

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
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            # Resolve or create the tag (case-insensitive match — TS tag names are unique by name)
            def _find_tag(tag_list: list, name: str):
                lowered = name.lower()
                return next((t for t in tag_list if t.name.lower() == lowered), None)

            tags = await client.search_tags()
            tag = _find_tag(tags, tag_name)

            if tag is None:
                if not create_tag_if_missing:
                    mark_failed(job_id, f"Tag {tag_name!r} not found on cluster")
                    return
                try:
                    tag = await client.create_tag(name=tag_name)
                    logger.info("Created tag %r on cluster %s", tag_name, cluster_id)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 409:
                        raise
                    # Tag name already exists (possibly in another org or just created
                    # concurrently). Re-search and reuse.
                    logger.info("create_tag 409 for %r — re-searching to reuse existing tag", tag_name)
                    tags = await client.search_tags()
                    tag = _find_tag(tags, tag_name)
                    if tag is None:
                        mark_failed(
                            job_id,
                            f"Tag {tag_name!r} exists on the cluster but is not visible "
                            f"in the current org. Switch to the org that owns the tag, or "
                            f"use a different tag name.",
                        )
                        return

            succeeded = 0
            failed_ids: list[str] = []
            first_error = ""

            for chunk in _chunks(object_ids, 50):
                try:
                    if action == "tag":
                        await client.assign_tag(object_ids=chunk, tag_id=tag.id)
                    else:
                        await client.unassign_tag(object_ids=chunk, tag_id=tag.id)
                # ONLY the live call is guarded, and only against the two
                # families it can raise (`_request` maps every HTTP outcome onto
                # TSAdminError; httpx.HTTPError covers the transport). The
                # blanket `except Exception` that used to wrap the cache mirror
                # too turned a bug in our own code into "this chunk failed
                # upstream" and reported it as a PARTIAL job.
                except (TSAdminError, httpx.HTTPError) as exc:
                    logger.warning("execute %s chunk failed: %s", action, exc)
                    failed_ids.extend(chunk)
                    if not first_error:
                        first_error = str(exc)
                    update_progress(job_id, succeeded)
                    continue

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
                update_progress(job_id, succeeded)

        # Zero successes is FAILED, never PARTIAL — see the note in
        # `bulk_sharing_service.execute_share`. A tag run in which every chunk
        # failed used to report PARTIAL with 0 objects tagged, which reads as
        # "some of them got the tag, retry the rest".
        if succeeded == 0:
            status = "FAILED"
        elif failed_ids:
            status = "PARTIAL"
        else:
            status = "COMPLETE"
        if not object_ids:
            failure_reason = f"0 objects {action}ged — the request named no objects."
        else:
            failure_reason = (
                f"0 of {len(object_ids)} objects {action}ged with {tag_name!r}: "
                f"{len(failed_ids)} call(s) failed — first error: {first_error or 'unknown error'}."
            )

        # Audit log — same terminal status as the job. It used to be computed
        # from an expression that could not say FAILED at all.
        with Session(_db.get_engine()) as session:
            entry = AuditLog(
                cluster_id=cluster_id,
                action_type=action,
                entity_type="metadata",
                items_affected=succeeded,
                status=status,
            )
            entry.set_parameters(
                {
                    "tag_name": tag_name,
                    "object_ids": object_ids,
                    "errors": failed_ids,
                    "error": failure_reason if status == "FAILED" else "",
                }
            )
            session.add(entry)
            session.commit()

        result = {"succeeded": succeeded, "failed": len(failed_ids), "tag_name": tag_name}
        if status == "FAILED":
            mark_failed(job_id, failure_reason)
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)

        logger.info(
            "archive.%s job=%s cluster=%s status=%s tag=%r succeeded=%d failed=%d",
            action,
            job_id,
            cluster_id,
            status,
            tag_name,
            succeeded,
            len(failed_ids),
        )

    # Last-resort handler for a background task: this runs after the 202 is on
    # the wire, so an exception that escapes is invisible to the caller and
    # strands the Job row at RUNNING forever. Permitted to swallow ANY
    # exception — none silently: each is logged with a traceback and re-reported
    # as a FAILED job.
    except Exception as exc:
        logger.exception("execute job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


# Phases 4 (dry-run) and 5 (delete) live in ts_admin.services.deletion_service —
# both Archiver and Bulk Deleter share that pipeline.


# ── Phase 6: Restore & History ─────────────────────────────────────────────────

# What restore does and does not do. Carried in the job result so the API — and
# therefore the UI — states it plainly instead of implying an in-place undelete.
RESTORE_NOTES: tuple[str, ...] = (
    "Restore re-imports the TML backup, so ThoughtSpot creates a NEW object with a NEW GUID.",
    "Sharing rules (ACLs) are not restored — the object comes back visible only to its owner.",
    "Tags are not re-applied — re-tag the object if it needs its old tags.",
    "Objects that pointed at the deleted GUID are not relinked to the new one.",
)

# Import batch size. Batches are additionally split so no two records in one
# request share a name — see _name_unique_batches.
_IMPORT_BATCH_SIZE = 10


class _ImportOutcome(NamedTuple):
    """One entry of a metadata/tml/import response, normalized."""

    name: str
    guid: str
    status_code: str
    error: str


def _import_outcome(item: dict) -> _ImportOutcome:
    """
    Normalize one import-response entry across the shapes TS has shipped.

    v2:    {"response": {"header": {"id_guid", "name"}, "status": {"status_code"}}}
    newer: {"object_id", "name", "status": {"status_code", "error_message"}}
    """
    resp = item.get("response") or {}
    header = resp.get("header") or item.get("header") or {}
    status = resp.get("status") or item.get("status") or {}
    return _ImportOutcome(
        name=header.get("name") or item.get("name") or "",
        guid=header.get("id_guid") or item.get("object_id") or item.get("id") or "",
        status_code=status.get("status_code") or "UNKNOWN",
        error=status.get("error_message") or "",
    )


def _restore_failure_reason(*, total: int, failed: int, skipped: int) -> str:
    """Name why a restore restored nothing.

    "0 objects restored — N imports failed" said nothing about the skipped
    bucket, which is the far more common cause: a record is skipped when its TML
    backup never succeeded, its `.tml` file is gone from disk, or it has already
    been restored. An all-skipped run used to report COMPLETE.
    """
    if total == 0:
        return "0 objects restored — the request named no archive records."
    parts: list[str] = []
    if failed:
        parts.append(f"{failed} TML import(s) failed")
    if skipped:
        parts.append(
            f"{skipped} record(s) were not restorable — no successful TML backup, the backup file is gone "
            "from disk, or they were already restored"
        )
    if not parts:
        parts.append("no import was attempted")
    return f"0 of {total} objects restored: " + "; ".join(parts) + "."


def _name_unique_batches(records: list[ArchiveRecord], size: int) -> Iterator[list[ArchiveRecord]]:
    """
    Yield batches of at most `size` records in which every `name` is unique.

    The import response carries no reference to the pre-delete GUID, so the
    object name is the only key that can tie a result back to its record. Making
    the names unique within each request is what makes that key sound: a record
    whose name is already in the current batch is deferred to a later one rather
    than guessed at.
    """
    remaining = list(records)
    while remaining:
        batch: list[ArchiveRecord] = []
        deferred: list[ArchiveRecord] = []
        seen: set[str] = set()
        for rec in remaining:
            # The first record always enters the batch, so this terminates.
            if len(batch) < size and rec.name not in seen:
                seen.add(rec.name)
                batch.append(rec)
            else:
                deferred.append(rec)
        yield batch
        remaining = deferred


async def restore(
    job_id: str,
    cluster_id: str,
    org_id: int,
    archive_record_ids: list[str],
) -> None:
    """
    Background task: re-import TML files back into ThoughtSpot.

    **Restore is not an undelete.** The TML is imported as a brand-new object, so
    ThoughtSpot assigns it a NEW GUID. Consequences the caller must know about
    (also returned verbatim in the job result as ``notes`` — see RESTORE_NOTES):

      - sharing rules (ACLs) are NOT restored,
      - tags are NOT re-applied (the cache row is written with no tags so it
        cannot claim tags the cluster does not have),
      - objects that referenced the deleted GUID are NOT relinked.

    For each archive record:
      1. Read TML file from disk (tml_path)
      2. POST to /metadata/tml/import — ThoughtSpot assigns a NEW GUID
      3. Update ArchiveRecord: restored_at, restored_as_guid, restored_by_job_id
      4. Insert a placeholder CachedMetadata row for the new GUID, carrying
         restore-time timestamps (the object really was created just now). The
         next metadata sync replaces it with the cluster's own values.

    Records with missing TML, tml_export_status != "SUCCESS", no
    deleted_confirmed_at (never actually deleted — restoring would duplicate a
    live object), or an existing restored_at are skipped.
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

        # Partition up front: a record is restorable only when its TML export
        # succeeded, the file is still on disk, and it has not been restored
        # already (restoring twice would leave two copies in ThoughtSpot).
        restorable: list[ArchiveRecord] = []
        tml_by_record: dict[str, str] = {}
        for rid in archive_record_ids:
            rec = rec_map.get(rid)
            # `deleted_confirmed_at` is required, not `tml_export_status`: an
            # object whose TML exported but whose delete never happened (a crash
            # between Phase A and Phase B) is still live in ThoughtSpot, and
            # "restoring" it would import a second copy.
            if (
                rec is None
                or rec.tml_export_status != "SUCCESS"
                or rec.deleted_confirmed_at is None
                or rec.restored_at is not None
                or not rec.tml_path
            ):
                skipped.append(rid)
                continue
            path = Path(rec.tml_path)
            if not path.exists():
                logger.warning("TML file missing for record %s: %s", rec.id, rec.tml_path)
                skipped.append(rid)
                continue
            try:
                tml_by_record[rec.id] = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("TML file unreadable for record %s: %s", rec.id, exc)
                skipped.append(rid)
                continue
            restorable.append(rec)

        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            for batch in _name_unique_batches(restorable, _IMPORT_BATCH_SIZE):
                try:
                    import_results = await client.import_tml(tml_strings=[tml_by_record[r.id] for r in batch])
                except (TSAuthenticationError, TSInsufficientPrivilegesError):
                    # Whole-cluster conditions — the next batch would fail the
                    # same way. Let the outer handler fail the whole job.
                    raise
                except (TSAdminError, httpx.HTTPError) as exc:
                    logger.warning("import_tml batch failed: %s", exc)
                    failed.extend([r.id for r in batch])
                    update_progress(job_id, succeeded)
                    continue

                # Match results back to records by NAME, never by list position.
                # TS does not promise to echo the request order, and the response
                # carries no reference to the pre-delete GUID — so a positional
                # match writes another object's GUID onto the record. The batch
                # was built with unique names precisely so this key is sound.
                outcomes = [_import_outcome(item) for item in import_results]
                by_name: dict[str, _ImportOutcome] = {}
                ambiguous: set[str] = set()
                for outcome in outcomes:
                    if not outcome.name:
                        continue
                    if outcome.name in by_name:
                        # Two results for one name: neither can be attributed.
                        ambiguous.add(outcome.name)
                        continue
                    by_name[outcome.name] = outcome

                # Degenerate fallback: a TS build that returns no names at all
                # leaves position as the only key. Only usable when the response
                # is 1:1 with the request, and loud about it.
                positional: list[_ImportOutcome] | None = None
                if not by_name and len(outcomes) == len(batch):
                    logger.warning("import_tml response carried no object names — falling back to positional matching")
                    positional = outcomes

                now = datetime.now(timezone.utc)
                for idx, rec in enumerate(batch):
                    outcome = by_name.get(rec.name)
                    if outcome is None and positional is not None:
                        outcome = positional[idx]

                    if outcome is None or rec.name in ambiguous:
                        logger.warning(
                            "restore record %s: no unambiguous import result for %r — not attributing a GUID",
                            rec.id,
                            rec.name,
                        )
                        failed.append(rec.id)
                        continue

                    if not outcome.guid or outcome.status_code != "OK":
                        error_msg = outcome.error or f"Import returned status {outcome.status_code}"
                        logger.warning("restore record %s failed: %s", rec.id, error_msg)
                        failed.append(rec.id)
                        continue

                    new_guid = outcome.guid

                    # Update ArchiveRecord
                    with Session(_db.get_engine()) as session:
                        db_rec = session.get(ArchiveRecord, rec.id)
                        if db_rec:
                            db_rec.restored_at = now
                            db_rec.restored_as_guid = new_guid
                            db_rec.restored_by_job_id = job_id
                            session.add(db_rec)
                            session.commit()

                    # Placeholder CachedMetadata row for the new GUID.
                    #
                    # Every timestamp is restore-time on purpose. The object is
                    # genuinely new in ThoughtSpot, and _stale_conditions treats
                    # NULL modified_at/last_accessed_at as stale — so copying the
                    # pre-deletion last_accessed_at and leaving the other two NULL
                    # made the object satisfy BOTH halves of the staleness test and
                    # come straight back as an Archiver delete candidate.
                    #
                    # tag_names is empty because no assign_tag call is made; the
                    # cache must not claim tags the cluster does not have.
                    with Session(_db.get_engine()) as session:
                        existing = session.exec(
                            select(CachedMetadata).where(
                                CachedMetadata.cluster_id == cluster_id,
                                CachedMetadata.org_id == org_id,
                                CachedMetadata.ts_guid == new_guid,
                            )
                        ).first()
                        row = existing or CachedMetadata(
                            cluster_id=cluster_id,
                            org_id=org_id,
                            ts_guid=new_guid,
                        )
                        row.name = rec.name
                        row.object_type = rec.object_type
                        row.owner_guid = rec.owner_guid
                        row.owner_name = rec.owner_name
                        row.tag_names = "[]"
                        row.created_at = now
                        row.modified_at = now
                        row.last_accessed_at = now
                        row.view_count = 0
                        row.synced_at = now
                        session.add(row)
                        session.commit()

                    succeeded += 1

                update_progress(job_id, succeeded)

        # Zero successes is FAILED, never PARTIAL and never COMPLETE — see the
        # note in `bulk_sharing_service.execute_share`. The all-skipped case
        # (every record already restored, or its TML gone from disk) used to
        # fall through to mark_complete: a job that restored nothing reported
        # COMPLETE with succeeded=0.
        if succeeded == 0:
            status = "FAILED"
        elif failed:
            status = "PARTIAL"
        else:
            status = "COMPLETE"
        failure_reason = _restore_failure_reason(
            total=total,
            failed=len(failed),
            skipped=len(skipped),
        )

        # Audit log — same terminal status as the job.
        with Session(_db.get_engine()) as session:
            entry = AuditLog(
                cluster_id=cluster_id,
                action_type="restore",
                entity_type="metadata",
                items_affected=succeeded,
                status=status,
            )
            entry.set_parameters(
                {
                    "archive_record_ids": archive_record_ids,
                    "succeeded": succeeded,
                    "failed": len(failed),
                    "skipped": len(skipped),
                    "job_id": job_id,
                    "error": failure_reason if status == "FAILED" else "",
                }
            )
            session.add(entry)
            session.commit()

        result = {
            "succeeded": succeeded,
            "failed": len(failed),
            "skipped": len(skipped),
            # Discoverability: the caller sees the new-GUID / no-ACL / no-tag
            # semantics in the job result, not only in this docstring.
            "notes": list(RESTORE_NOTES),
        }
        if status == "FAILED":
            mark_failed(job_id, failure_reason)
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)

        logger.info(
            "archive.restore job=%s cluster=%s status=%s succeeded=%d failed=%d skipped=%d",
            job_id,
            cluster_id,
            status,
            succeeded,
            len(failed),
            len(skipped),
        )

    # Last-resort handler for a background task — see the note on the matching
    # handler in `execute`. Permitted to swallow ANY exception because an escape
    # here strands the Job row at RUNNING; nothing is swallowed silently.
    except Exception as exc:
        logger.exception("restore job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


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
            succeeded = sum(1 for r in records if r.deleted_confirmed_at is not None and r.restored_at is None)
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

    is_restorable = TML export succeeded AND the delete was confirmed AND not
    yet restored. An object that was exported but never actually deleted (a
    crash between export and delete) is still live in ThoughtSpot, so offering
    Restore for it would create a duplicate.
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
            "is_restorable": (
                r.tml_export_status == "SUCCESS" and r.deleted_confirmed_at is not None and r.restored_at is None
            ),
        }
        for r in rows
    ]
    return items, total
