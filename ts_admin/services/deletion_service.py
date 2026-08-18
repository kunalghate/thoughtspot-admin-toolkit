"""
deletion_service — shared destructive-write pipeline for Archiver and Bulk Deleter.

Holds the TML-export-then-delete machinery and the dry-run impact check that
both features need. Archiver invokes these from its tag/untag/delete wrapper;
the Bulk Deleter calls them directly from its three intake modes.

Rules:
  - SQLite-only methods are sync.
  - Methods that call the ThoughtSpot API are async.
  - Every delete: TML export first, delete second, audit log third.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlmodel import Session

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


# ── Phase 5: Delete with mandatory TML safety net ─────────────────────────────


async def _execute_delete(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_ids: list[str],
    *,
    action_type: str = "delete",
) -> None:
    """
    Permanently delete objects with a mandatory TML backup before each deletion.

    Phase A — TML Export:
      Export TML for every object. Objects whose export fails are excluded from
      deletion and marked FAILED in ArchiveRecord — they are never deleted.
      The export RESPONSE is reconciled against the REQUEST: ThoughtSpot omits
      objects it cannot export from the response entirely (no error row), so a
      requested GUID with no matching response entry is an export failure, not
      a success.

    Phase B — Delete (cancel-aware):
      Delete only objects whose TML export succeeded. Checks job.is_cancelled
      before each chunk and stops cleanly if set. GUIDs left unattempted by a
      cancellation are counted, not dropped.

    Phase C — Audit:
      Write AuditLog + structured log line. `action_type` differentiates the
      caller in the audit trail (e.g. "delete" for Archiver, "bulk_delete"
      for Bulk Deleter).

    Accounting invariant: every requested GUID ends in exactly one bucket —
    deleted, failed_tml, failed_delete or not_attempted — and
    `succeeded + failed_tml + failed_delete + not_attempted == len(object_ids)`.
    A run that does not reconcile, or that leaves any non-deleted GUID, is
    PARTIAL. Only a fully reconciled, fully deleted run is COMPLETE.
    """
    from sqlmodel import col
    from sqlmodel import delete as sql_delete

    from ts_admin.models.archive_record import ArchiveRecord
    from ts_admin.models.audit_log import AuditLog
    from ts_admin.services.archiver_service import _compute_days_unused, _get_cluster
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

    # Dedupe, keeping request order: the buckets below are per-GUID, so a
    # repeated GUID would otherwise be counted twice and break reconciliation.
    requested = list(dict.fromkeys(object_ids))
    requested_set = set(requested)
    total = len(requested)
    mark_running(job_id, total)

    try:
        # ── Fetch object metadata early (before any concurrent sync can wipe rows)
        with Session(_db.get_engine()) as session:
            cached_objs = _fetch_objects_by_guids(session, requested, cluster_id, org_id)
        obj_map = {o.ts_guid: o for o in cached_objs}

        # ── Create TML export directory for this job
        job_tml_dir = TML_EXPORT_DIR / job_id
        job_tml_dir.mkdir(parents=True, exist_ok=True)

        # ── Batch-insert ArchiveRecord rows as PENDING ────────────────────────
        now = datetime.now(timezone.utc)
        archive_rows: list[ArchiveRecord] = []
        for guid in requested:
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
        accounted: set[str] = set()  # requested GUIDs the export response covered

        cluster = _get_cluster(cluster_id)

        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            for chunk in _chunks(requested, 50):
                try:
                    tml_results = await client.tml_export(object_ids=chunk)
                except Exception as exc:
                    logger.warning("TML export chunk failed: %s", exc)
                    for guid in chunk:
                        accounted.add(guid)
                        failed_tml.append(guid)
                        _update_record(guid, tml_export_status="FAILED", tml_export_error=str(exc)[:500])
                    continue

                for item in tml_results:
                    info = item.get("info") or {}
                    guid = info.get("id") or info.get("identifier", "")
                    edoc = item.get("edoc") or ""

                    if not guid:
                        # Unattributable row — it cannot be matched to a requested
                        # GUID. The reconciliation pass below still accounts for
                        # whichever GUID it was meant to answer for.
                        logger.warning("TML export returned an entry with no info.id — ignored")
                        continue

                    if guid not in requested_set:
                        logger.warning("TML export returned unrequested GUID %s — ignored", guid)
                        continue

                    if guid in accounted:
                        logger.warning("TML export returned a duplicate entry for %s — ignored", guid)
                        continue

                    accounted.add(guid)

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

                # Reconcile response against request: a GUID TS silently omitted
                # is an export failure. Without this it would land in no bucket
                # at all — invisible in every count, and stranded at PENDING.
                for guid in chunk:
                    if guid in accounted:
                        continue
                    accounted.add(guid)
                    failed_tml.append(guid)
                    logger.warning("TML export response omitted requested GUID %s — treated as failed", guid)
                    _update_record(
                        guid,
                        tml_export_status="FAILED",
                        tml_export_error="TML export response omitted this object",
                    )

            logger.info(
                "%s job=%s TML export done: %d ok, %d failed",
                action_type,
                job_id,
                len(delete_batch),
                len(failed_tml),
            )

            # ── Phase B: Delete (cancel-aware) ───────────────────────────────
            deleted: list[str] = []
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
                        logger.info("%s job=%s cancelled at chunk boundary", action_type, job_id)
                        break

                    try:
                        await client.delete_metadata(object_ids=chunk, object_type=enum_type)

                        # Remove from CachedMetadata cache. Scoped to
                        # (cluster_id, org_id) like the sibling read in
                        # `_fetch_objects_by_guids` — a GUID can exist in more
                        # than one org and on more than one cluster, and an
                        # unscoped purge wipes cache rows for objects that were
                        # never deleted.
                        with Session(_db.get_engine()) as session:
                            session.exec(
                                sql_delete(CachedMetadata).where(
                                    CachedMetadata.cluster_id == cluster_id,
                                    CachedMetadata.org_id == org_id,
                                    col(CachedMetadata.ts_guid).in_(chunk),
                                )
                            )
                            session.commit()

                        deleted.extend(chunk)
                    except Exception as exc:
                        logger.warning("delete_metadata chunk failed: %s", exc)
                        failed_delete.extend(chunk)

                    update_progress(job_id, len(deleted))

            succeeded = len(deleted)
            # Exported objects a cancellation (or a skipped type group) never
            # reached: neither deleted nor failed, but still requested.
            attempted = set(deleted) | set(failed_delete)
            not_attempted = [guid for guid in delete_batch if guid not in attempted]

        # ── Phase C: Reconcile, then audit ───────────────────────────────────
        # Every requested GUID must sit in exactly one bucket. `unaccounted`
        # should always be empty — it is computed anyway so a future regression
        # shows up as a PARTIAL job instead of a silent under-report.
        bucketed = set(deleted) | set(failed_tml) | set(failed_delete) | set(not_attempted)
        unaccounted = [guid for guid in requested if guid not in bucketed]
        reconciled = succeeded + len(failed_tml) + len(failed_delete) + len(not_attempted) == total
        if unaccounted or not reconciled:
            logger.error(
                "%s job=%s accounting mismatch: %d requested vs %d deleted + %d failed_tml + %d failed_delete "
                "+ %d not_attempted (%d unaccounted)",
                action_type,
                job_id,
                total,
                succeeded,
                len(failed_tml),
                len(failed_delete),
                len(not_attempted),
                len(unaccounted),
            )

        # COMPLETE means "every requested object was deleted, and the numbers
        # add up". Anything else is PARTIAL.
        status = (
            "COMPLETE"
            if (succeeded == total and not failed_tml and not failed_delete and not cancelled and reconciled)
            else "PARTIAL"
        )
        result = {
            "succeeded": succeeded,
            "failed_tml_export": len(failed_tml),
            "failed_delete": len(failed_delete),
            "not_attempted": len(not_attempted),
            "requested": total,
            "reconciled": reconciled and not unaccounted,
            # GUID-level detail so an admin can see exactly which objects were
            # left behind. Capped — the exact counts are the fields above.
            "failed_tml_guids": failed_tml[:200],
            "failed_delete_guids": failed_delete[:200],
            "not_attempted_guids": not_attempted[:200],
            "unaccounted_guids": unaccounted[:200],
            "cancelled": cancelled,
            "job_id": job_id,
            "tml_export_path": str(job_tml_dir),
        }

        with Session(_db.get_engine()) as session:
            entry = AuditLog(
                cluster_id=cluster_id,
                action_type=action_type,
                entity_type="metadata",
                items_affected=succeeded,
                status=status,
            )
            entry.set_parameters(
                {
                    "object_ids": requested,
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
            "%s job=%s cluster=%s succeeded=%d failed_tml=%d failed_delete=%d not_attempted=%d cancelled=%s",
            action_type,
            job_id,
            cluster_id,
            succeeded,
            len(failed_tml),
            len(failed_delete),
            len(not_attempted),
            cancelled,
        )

    except Exception as exc:
        logger.exception("_execute_delete job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


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

    from ts_admin.services.archiver_service import _get_cluster
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
            auth=cluster.build_auth_strategy(org_id=org_id),
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
            "dryrun job=%s cluster=%s total=%d shared=%d deps=%d errors=%d",
            job_id,
            cluster_id,
            len(object_ids),
            shared_count,
            len(dependency_warnings),
            len(errors),
        )

    except Exception as exc:
        logger.exception("dryrun job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


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
    from ts_admin.services.archiver_service import _compute_days_unused

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
