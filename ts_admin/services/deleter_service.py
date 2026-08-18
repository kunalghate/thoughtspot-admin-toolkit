"""
deleter_service — selection-mode logic for the Bulk Deleter.

The destruction pipeline (TML backup → delete → audit) lives in
deletion_service and is shared with the Archiver. This module only handles
the *intake*: turning one of three user-supplied selections into a list of
CachedMetadata rows.

Modes:
  - resolve_downstream — given a root GUID, return its dependents (root excluded)
  - resolve_tag        — given a tag name, return all CachedMetadata with that tag
  - resolve_list       — given a list of GUIDs, return resolved rows + unrecognized
"""

from __future__ import annotations

import json
import logging

from sqlalchemy import func
from sqlmodel import Session, col, select

import ts_admin.database as _db
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.services.deletion_service import _fetch_objects_by_guids

logger = logging.getLogger(__name__)


def _row_to_dict(r: CachedMetadata) -> dict:
    """Serialize a CachedMetadata row into the shared grid item shape."""
    from ts_admin.services.archiver_service import _compute_days_unused

    return {
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


def _by_type(rows: list[dict]) -> dict[str, int]:
    """Count rows grouped by object_type."""
    out: dict[str, int] = {}
    for r in rows:
        out[r["object_type"]] = out.get(r["object_type"], 0) + 1
    return out


# ── Mode 1: Downstream ─────────────────────────────────────────────────────────


async def resolve_downstream(
    *,
    root_guid: str,
    root_type: str,
    cluster_id: str,
    org_id: int,
) -> dict:
    """
    Return all objects that depend on `root_guid`.

    The root itself is *excluded* from the result (matching the CLI's
    `bulk-deleter downstream` semantics — the root data source is preserved).

    Calls TS REST `/dependency/listdependents`, then enriches each dependent
    GUID against CachedMetadata. Dependents not in the local cache are
    omitted (the user can run a sync first if they want to see them).

    Refuses outright on a non-authoritative metadata cache: dependents are
    enriched *out of* the cache and silently dropped when absent, so a truncated
    cache would under-report exactly the models and tables this mode exists to
    find — and the user would delete a root believing nothing depends on it.
    """
    from ts_admin.services.sync_status import require_authoritative_metadata

    # Fail closed BEFORE any live ThoughtSpot call.
    require_authoritative_metadata(cluster_id=cluster_id, org_id=org_id)

    from ts_admin.services.archiver_service import _get_cluster
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _get_cluster(cluster_id)
    async with ThoughtSpotClient(
        url=cluster.url,
        auth=cluster.build_auth_strategy(org_id=org_id),
    ) as client:
        dep_map = await client.fetch_dependents(
            objects=[{"identifier": root_guid, "type": root_type}],
        )

    raw_dependents = dep_map.get(root_guid, [])
    dep_guids: list[str] = []
    for d in raw_dependents:
        gid = d.get("id") or d.get("identifier") or d.get("guid")
        if gid and gid != root_guid:
            dep_guids.append(gid)

    if not dep_guids:
        return {"items": [], "total": 0, "by_type": {}, "root_guid": root_guid}

    with Session(_db.get_engine()) as session:
        rows = _fetch_objects_by_guids(session, dep_guids, cluster_id, org_id)

    items = [_row_to_dict(r) for r in rows if r.owner_name != "System User"]
    return {
        "items": items,
        "total": len(items),
        "by_type": _by_type(items),
        "root_guid": root_guid,
    }


# ── Mode 2: From Tag ───────────────────────────────────────────────────────────


def resolve_tag(*, tag_name: str, cluster_id: str, org_id: int) -> dict:
    """
    Return all CachedMetadata rows whose `tag_names` JSON array contains `tag_name`.

    Pure SQLite query — no TS API call needed (we query the cache).
    System-User-owned content is excluded. Caller is responsible for syncing
    the metadata cache first if it might be stale.
    """
    with Session(_db.get_engine()) as session:
        # Fetch candidates by simple LIKE on the JSON string, then verify in Python.
        # SQLite has no JSON-array contains operator portable across versions.
        like_pattern = f"%{json.dumps(tag_name)[1:-1]}%"  # escape JSON quoting
        rows = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.owner_name != "System User",
                col(CachedMetadata.tag_names).like(like_pattern),
            )
        ).all()

    matched = [r for r in rows if tag_name in r.get_tag_names()]
    items = [_row_to_dict(r) for r in matched]
    return {
        "items": items,
        "total": len(items),
        "by_type": _by_type(items),
        "tag_name": tag_name,
    }


async def delete_tag_only(*, tag_name: str, cluster_id: str, org_id: int) -> dict:
    """
    Delete the *tag itself* — objects are untouched, but the label is removed
    from every object that carried it (a single TS API call).

    Mirrors the CLI's `bulk-deleter from-tag --tag-only` flag. Used as a
    safety undo when a tag was applied by mistake. Writes an audit log
    entry with action_type='bulk_delete_tag' and items_affected=1.

    Returns {tag_id, tag_name, removed_from} where removed_from is the
    count of locally-cached objects that had the tag (best-effort, since
    we can't trust the cache to reflect every object on the cluster).
    """
    from ts_admin.models.audit_log import AuditLog
    from ts_admin.services.archiver_service import _get_cluster, _remove_tag
    from ts_admin.ts_client import ThoughtSpotClient

    cluster = _get_cluster(cluster_id)

    # Fetch the cluster's current tag list to resolve name -> id (case-insensitive).
    async with ThoughtSpotClient(
        url=cluster.url,
        auth=cluster.build_auth_strategy(org_id=org_id),
    ) as client:
        tags = await client.search_tags()
        lowered = tag_name.lower()
        tag = next((t for t in tags if t.name.lower() == lowered), None)
        if tag is None:
            raise ValueError(f"Tag {tag_name!r} not found on cluster")

        await client.delete_tag(tag_id=tag.id)

    # Strip the tag from every CachedMetadata row that carried it locally,
    # so the UI doesn't keep showing a now-deleted tag.
    removed_from = 0
    with Session(_db.get_engine()) as session:
        like_pattern = f"%{json.dumps(tag_name)[1:-1]}%"
        rows = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                col(CachedMetadata.tag_names).like(like_pattern),
            )
        ).all()
        for r in rows:
            if tag_name in r.get_tag_names():
                r.tag_names = _remove_tag(r.tag_names, tag_name)
                session.add(r)
                removed_from += 1
        session.commit()

        entry = AuditLog(
            cluster_id=cluster_id,
            action_type="bulk_delete_tag",
            entity_type="tag",
            items_affected=1,
            status="COMPLETE",
        )
        entry.set_parameters({"tag_id": tag.id, "tag_name": tag.name, "removed_from": removed_from})
        session.add(entry)
        session.commit()

    logger.info(
        "bulk_delete_tag job=tag-only cluster=%s tag=%r removed_from=%d",
        cluster_id,
        tag_name,
        removed_from,
    )
    return {"tag_id": tag.id, "tag_name": tag.name, "removed_from": removed_from}


def list_available_tags(*, cluster_id: str, org_id: int) -> list[str]:
    """
    Return sorted list of distinct tag names present on at least one
    user-owned CachedMetadata row in this cluster+org.
    """
    with Session(_db.get_engine()) as session:
        rows = session.exec(
            select(CachedMetadata.tag_names).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.owner_name != "System User",
                func.length(CachedMetadata.tag_names) > 2,  # not "[]"
            )
        ).all()

    tags: set[str] = set()
    for raw in rows:
        try:
            for t in json.loads(raw):
                if t:
                    tags.add(t)
        except (json.JSONDecodeError, TypeError):
            continue
    return sorted(tags)


# ── Mode 3: From List ──────────────────────────────────────────────────────────


def resolve_list(*, guids: list[str], cluster_id: str, org_id: int) -> dict:
    """
    Look up each GUID in CachedMetadata. Return resolved rows + the list of
    GUIDs that were not found (so the UI can show "X unrecognized").
    """
    # Dedupe while preserving order
    seen: set[str] = set()
    deduped: list[str] = []
    for g in guids:
        g = g.strip()
        if g and g not in seen:
            seen.add(g)
            deduped.append(g)

    if not deduped:
        return {"items": [], "total": 0, "by_type": {}, "unrecognized": []}

    with Session(_db.get_engine()) as session:
        rows = _fetch_objects_by_guids(session, deduped, cluster_id, org_id)

    found_guids = {r.ts_guid for r in rows}
    unrecognized = [g for g in deduped if g not in found_guids]

    # Drop System-User-owned even if explicitly requested (consistency guard).
    items = [_row_to_dict(r) for r in rows if r.owner_name != "System User"]
    return {
        "items": items,
        "total": len(items),
        "by_type": _by_type(items),
        "unrecognized": unrecognized,
    }


# ── Root picker autocomplete (used by Downstream mode) ────────────────────────


def search_roots(
    *,
    cluster_id: str,
    org_id: int,
    query: str,
    types: list[str] | None = None,
    limit: int = 25,
) -> list[dict]:
    """
    Type-ahead search over CachedMetadata for picking a Downstream root.

    Defaults to Worksheet/Table/Model/View types (what dependents are usually
    fanned out from). Caller can override via `types`.
    """
    if types is None:
        types = ["WORKSHEET", "TABLE", "MODEL", "VIEW", "ONE_TO_ONE_LOGICAL", "DATASET"]

    with Session(_db.get_engine()) as session:
        rows = session.exec(
            select(CachedMetadata)
            .where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.owner_name != "System User",
                col(CachedMetadata.object_type).in_(types),
                col(CachedMetadata.name).ilike(f"%{query}%"),
            )
            .limit(limit)
        ).all()

    return [
        {
            "ts_guid": r.ts_guid,
            "name": r.name,
            "object_type": r.object_type,
            "owner_name": r.owner_name,
        }
        for r in rows
    ]
