"""
bulk_sharing_service — share many objects with many principals in one job.

Two intake modes wrap the same write pipeline:
  - object_guids list  (selection intake — caller provides explicit GUIDs)
  - tag_name           (by-tag intake — resolve to GUIDs from CachedMetadata)

Pipeline:
  preview_share() — for each (object × principal), look up current ACL via
                    ts_client.fetch_permissions and diff against the proposed
                    mode. Returns a row per (object × principal) pair.
  execute_share() — bucket calls by object_type, issue one share_objects
                    request per (type, principal-set, mode) tuple, write a
                    ShareRecord per pair + a single AuditLog at the end.

Both stages resolve the requested GUIDs through `_resolve_object_map`, so they
operate on the *identical* set: execute never touches an object the preview did
not show, and a GUID with no cache row is reported as skipped by both rather
than shared under a guessed object_type.
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
from sqlmodel import Session, col, func, select

import ts_admin.database as _db
from ts_admin.models.audit_log import AuditLog
from ts_admin.models.cache.ts_group import CachedGroup
from ts_admin.models.cache.ts_metadata import CachedMetadata
from ts_admin.models.cache.ts_user import CachedUser, UserOrgMembership
from ts_admin.models.share_record import ShareRecord
from ts_admin.services.deletion_service import SYSTEM_OWNER_NAME
from ts_admin.ts_client.exceptions import StaleCacheError, TSAdminError

logger = logging.getLogger(__name__)

# `security/metadata/share` marks `message` REQUIRED — omitting the key is a 400,
# not a silent default. It is the body of the notification email, so it only
# reaches a human when `notify_on_share` is true.
SHARE_MESSAGE = "Shared with you by a ThoughtSpot administrator."

# The lens both the preview diff and the post-execute verification read through.
# `security/metadata/fetch-permissions` accepts `permission_type` ("DEFINED" |
# "EFFECTIVE"); `share_objects` creates an explicit, i.e. DEFINED, share, so
# DEFINED is what "did the share land?" has to be asked in — and asking the
# before-picture and the after-picture through the SAME lens is what makes them
# comparable. Passed explicitly at both call sites in this module so a change to
# the client's default cannot silently switch the lens under them. (Other
# callers — `deletion_service.dryrun`'s "who loses access if I delete this" —
# ask a different question and are deliberately left on the client default.)
PERMISSION_LENS = "DEFINED"

# Post-execute verification cap. `security/metadata/share` answers a bare 204 No
# Content — no per-object, no per-principal status, and no bulk-share endpoint
# offers one — so verification is a read-back, one live call per object. A bulk
# share can cover thousands of objects, so it is bounded: at most this many
# objects, spread evenly across the shared set. What was actually verified is
# always stated in `Job.result` and the audit row, because a sampled
# verification reported as a full one is the same class of lie the read-back
# exists to catch.
VERIFY_SAMPLE_LIMIT = 50


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


# ── Principal picker ──────────────────────────────────────────────────────────


def list_principals(
    *,
    cluster_id: str,
    org_id: int,
    search: str | None,
    include_users: bool,
    include_groups: bool,
    limit: int,
) -> list[dict]:
    """Combined users + groups list for the principal picker."""
    items: list[dict] = []
    with Session(_db.get_engine()) as session:
        if include_users:
            uq = (
                select(CachedUser)
                .join(
                    UserOrgMembership,
                    (UserOrgMembership.ts_guid == CachedUser.ts_guid)
                    & (UserOrgMembership.cluster_id == CachedUser.cluster_id),
                )
                .where(
                    CachedUser.cluster_id == cluster_id,
                    UserOrgMembership.org_id == org_id,
                )
            )
            if search:
                pattern = f"%{search}%"
                uq = uq.where(col(CachedUser.username).ilike(pattern) | col(CachedUser.display_name).ilike(pattern))
            uq = uq.limit(limit)
            for u in session.exec(uq).all():
                items.append(
                    {
                        "ts_guid": u.ts_guid,
                        "name": u.username,
                        "display_name": u.display_name or u.username,
                        "principal_type": "USER",
                    }
                )

        if include_groups:
            gq = select(CachedGroup).where(
                CachedGroup.cluster_id == cluster_id,
                CachedGroup.org_id == org_id,
            )
            if search:
                pattern = f"%{search}%"
                gq = gq.where(col(CachedGroup.name).ilike(pattern) | col(CachedGroup.display_name).ilike(pattern))
            gq = gq.limit(limit)
            for g in session.exec(gq).all():
                items.append(
                    {
                        "ts_guid": g.ts_guid,
                        "name": g.name,
                        "display_name": g.display_name or g.name,
                        "principal_type": "USER_GROUP",
                    }
                )

    items.sort(key=lambda x: (x["principal_type"], x["display_name"].lower()))
    return items[:limit]


# ── Tag intake ─────────────────────────────────────────────────────────────────


def resolve_tag_to_guids(*, cluster_id: str, org_id: int, tag_name: str) -> list[str]:
    """
    Return CachedMetadata GUIDs for `(cluster_id, org_id)` that carry `tag_name`.

    System-User-owned content is excluded, matching `deleter_service.resolve_tag`.
    The two features used to disagree — the same tag name selected ThoughtSpot's
    built-in content in Bulk Sharing but not in the Bulk Deleter — and the
    direction that fails safe is exclusion: tag intake is a convenience path, and
    `mode=NO_ACCESS` on system-owned content revokes access to objects the admin
    does not own and never picked out by hand. An admin who really wants to
    reshare built-in content can still name its GUIDs explicitly via
    `object_guids`, exactly as the Deleter requires.

    Narrowed by a LIKE on the JSON string first (SQLite has no portable
    JSON-array contains), then verified in Python — the same two-step as
    `deleter_service.resolve_tag`, so the two never load different row sets.
    """
    with Session(_db.get_engine()) as session:
        like_pattern = f"%{json.dumps(tag_name)[1:-1]}%"  # escape JSON quoting
        rows = session.exec(
            select(CachedMetadata).where(
                CachedMetadata.cluster_id == cluster_id,
                CachedMetadata.org_id == org_id,
                CachedMetadata.owner_name != SYSTEM_OWNER_NAME,
                col(CachedMetadata.tag_names).like(like_pattern),
            )
        ).all()
    return [r.ts_guid for r in rows if tag_name in r.get_tag_names()]


# ── Object resolution ─────────────────────────────────────────────────────────


def _resolve_object_map(
    session: Session,
    *,
    cluster_id: str,
    org_id: int,
    object_guids: list[str],
) -> tuple[dict[str, CachedMetadata], list[dict]]:
    """
    Resolve requested GUIDs against CachedMetadata for `(cluster_id, org_id)`.

    Returns `(obj_map, skipped)` where `skipped` holds one row per GUID with no
    cache entry for this cluster + org. Such an object has no known
    `object_type`, and `share_objects` takes exactly one type per call — sharing
    it under a guessed type would send it in the wrong bucket. It is therefore
    excluded, and reported, rather than guessed at.

    Preview and execute both go through here so the admin approves exactly the
    set that gets shared.
    """
    objs = session.exec(
        select(CachedMetadata).where(
            CachedMetadata.cluster_id == cluster_id,
            CachedMetadata.org_id == org_id,
            col(CachedMetadata.ts_guid).in_(object_guids),
        )
    ).all()
    obj_map = {o.ts_guid: o for o in objs}
    skipped = [
        {
            "object_guid": guid,
            "reason": "Not found in the local metadata cache for this cluster and org",
        }
        for guid in dict.fromkeys(object_guids)  # dedupe, keep request order
        if guid not in obj_map
    ]
    return obj_map, skipped


# ── Preview ────────────────────────────────────────────────────────────────────


async def preview_share(
    *,
    cluster_id: str,
    org_id: int,
    object_guids: list[str],
    principal_guids: list[str],
    mode: str,
) -> dict:
    """
    For each (object × principal) pair, return the current ACL vs proposed mode.

    Live call to fetch_permissions per object (cached objects aren't enough —
    permissions are not in the local cache by default for fresh-enough data).
    Concurrency capped to 10 to avoid hammering the cluster.

    Refuses on a non-authoritative metadata cache: `object_guids` are resolved
    against the cache, so a truncated cache would quietly drop objects from the
    preview — and from the share the user then approves.

    GUIDs with no cache row are excluded from the rows AND listed under
    `skipped`, and `execute_share` excludes exactly the same set — the preview
    is a faithful description of what execute will do.
    """
    from ts_admin.services.sync_status import require_authoritative_metadata

    # Fail closed before any live ThoughtSpot call.
    require_authoritative_metadata(cluster_id=cluster_id, org_id=org_id)

    from ts_admin.ts_client import ThoughtSpotClient

    with Session(_db.get_engine()) as session:
        obj_map, skipped = _resolve_object_map(
            session,
            cluster_id=cluster_id,
            org_id=org_id,
            object_guids=object_guids,
        )

        # Principal display names for the preview
        user_map = {
            u.ts_guid: u
            for u in session.exec(
                select(CachedUser).where(
                    CachedUser.cluster_id == cluster_id,
                    col(CachedUser.ts_guid).in_(principal_guids),
                )
            ).all()
        }
        group_map = {
            g.ts_guid: g
            for g in session.exec(
                select(CachedGroup).where(
                    CachedGroup.cluster_id == cluster_id,
                    CachedGroup.org_id == org_id,
                    col(CachedGroup.ts_guid).in_(principal_guids),
                )
            ).all()
        }

    cluster = _get_cluster(cluster_id)
    sem = asyncio.Semaphore(10)

    async with ThoughtSpotClient(
        url=cluster.url,
        auth=cluster.build_auth_strategy(org_id=org_id),
    ) as client:

        async def _fetch(guid: str, obj_type: str):
            async with sem:
                try:
                    perms = await client.fetch_permissions(
                        ts_guid=guid,
                        object_type=obj_type,
                        permission_type=PERMISSION_LENS,
                    )
                    return guid, perms, None
                # Only the two families a live call can raise: `_request` maps
                # every HTTP outcome onto TSAdminError, httpx.HTTPError covers
                # the transport. A TypeError from our own code is not "this
                # object's ACL could not be read" and must escape.
                except (TSAdminError, httpx.HTTPError) as exc:
                    logger.warning("fetch_permissions failed for %s: %s", guid, exc)
                    return guid, [], exc

        tasks = [_fetch(g, obj_map[g].object_type) for g in object_guids if g in obj_map]
        results = await asyncio.gather(*tasks)

    # Build a (guid, principal) → current mode map
    current_acl: dict[tuple[str, str], str] = {}
    for guid, perms, _err in results:
        for p in perms:
            current_acl[(guid, p.principal_id)] = str(p.share_mode)

    rows: list[dict] = []
    will_change = 0
    for guid in object_guids:
        obj = obj_map.get(guid)
        if obj is None:
            continue  # already reported in `skipped`; execute drops it too
        for pid in principal_guids:
            previous = current_acl.get((guid, pid), "")
            changing = previous != mode
            if changing:
                will_change += 1
            principal_name = ""
            principal_type = "USER"
            if pid in user_map:
                principal_name = user_map[pid].display_name or user_map[pid].username
                principal_type = "USER"
            elif pid in group_map:
                principal_name = group_map[pid].display_name or group_map[pid].name
                principal_type = "USER_GROUP"
            else:
                principal_name = pid
            rows.append(
                {
                    "object_guid": guid,
                    "object_name": obj.name,
                    "object_type": obj.object_type,
                    "principal_guid": pid,
                    "principal_name": principal_name,
                    "principal_type": principal_type,
                    "previous_mode": previous,
                    "new_mode": mode,
                    "will_change": changing,
                }
            )

    if skipped:
        logger.warning(
            "preview_share cluster=%s org=%s: %d of %d GUIDs are not in the metadata cache — excluded",
            cluster_id,
            org_id,
            len(skipped),
            len(object_guids),
        )

    return {
        "items": rows,
        "total": len(rows),
        "will_change_count": will_change,
        "skipped": skipped,
        "skipped_count": len(skipped),
    }


# ── Dry-run → execute binding ─────────────────────────────────────────────────

# The job_type a `dryrun_job_id` must name. A share must never be authorised by
# some other feature's job row.
DRYRUN_JOB_TYPE = "bulk_share_dryrun"

# Reason codes from `load_dryrun_selection`. The service stays HTTP-agnostic;
# `api/sharing.py` owns the mapping onto status codes.
DRYRUN_NOT_FOUND = "dryrun_not_found"
DRYRUN_WRONG_TYPE = "dryrun_wrong_type"
DRYRUN_NOT_COMPLETE = "dryrun_not_complete"
DRYRUN_MISMATCH = "dryrun_mismatch"
DRYRUN_NOT_PREVIEWED = "dryrun_not_previewed"


def load_dryrun_selection(
    *,
    dryrun_job_id: str,
    cluster_id: str,
    org_id: int,
    principal_guids: list[str],
    mode: str,
    requested_guids: list[str] | None = None,
) -> tuple[list[str], str | None, str]:
    """
    Return the GUID set a completed dry-run resolved, for execute to act on.

    Preview, dry-run and execute each used to resolve `tag_name` independently,
    so nothing linked an execute to the dry-run the admin approved: any
    tag/untag job, metadata sync or second admin in between silently changed the
    set. For `mode=NO_ACCESS` that meant execute revoking access on objects that
    were never previewed.

    The dry-run's `Job.parameters["object_guids"]` — resolved once, at dry-run
    time — is the approved set, and it is the CEILING for the execute:

      - `tag_name` on the execute request is ignored outright; re-resolving it
        is the bug.
      - explicit `requested_guids` may only NARROW the set (the admin deselected
        rows after reviewing the dry-run). A GUID outside the approved set is
        refused, never shared.

    Everything that identifies *what was approved* must match: cluster, org,
    mode and the principal set. A READ_ONLY dry-run can never authorise a
    NO_ACCESS execute.

    Returns `(object_guids, reason, detail)`. `reason` is None on success;
    otherwise it is one of the `DRYRUN_*` codes and `object_guids` is empty.
    """
    from ts_admin.models.job import Job

    with Session(_db.get_engine()) as session:
        job = session.get(Job, dryrun_job_id)
        if job is None:
            return [], DRYRUN_NOT_FOUND, f"Dry-run job {dryrun_job_id!r} not found"
        job_type = job.job_type
        job_status = job.status
        params = job.get_parameters()

    if job_type != DRYRUN_JOB_TYPE:
        return (
            [],
            DRYRUN_WRONG_TYPE,
            f"Job {dryrun_job_id!r} is a {job_type!r} job, not a share dry-run",
        )
    if job_status != "COMPLETE":
        return (
            [],
            DRYRUN_NOT_COMPLETE,
            f"Dry-run {dryrun_job_id!r} is {job_status} — wait for it to complete before executing",
        )

    if params.get("cluster_id") != cluster_id or params.get("org_id") != org_id:
        return (
            [],
            DRYRUN_MISMATCH,
            "The dry-run was run against a different cluster or org",
        )
    if params.get("mode") != mode:
        return (
            [],
            DRYRUN_MISMATCH,
            f"The dry-run approved mode {params.get('mode')!r}, not {mode!r}",
        )
    if set(params.get("principal_guids") or []) != set(principal_guids):
        return (
            [],
            DRYRUN_MISMATCH,
            "The dry-run approved a different set of principals",
        )

    approved = list(dict.fromkeys(params.get("object_guids") or []))
    if not approved:
        return (
            [],
            DRYRUN_MISMATCH,
            f"Dry-run {dryrun_job_id!r} resolved 0 objects — nothing was approved",
        )

    if requested_guids:
        approved_set = set(approved)
        unapproved = [g for g in dict.fromkeys(requested_guids) if g not in approved_set]
        if unapproved:
            return (
                [],
                DRYRUN_NOT_PREVIEWED,
                (
                    f"{len(unapproved)} object(s) are not in dry-run {dryrun_job_id!r} "
                    f"(first: {unapproved[0]}) — re-run the dry-run"
                ),
            )
        requested_set = set(requested_guids)
        # Order comes from the dry-run, so the audit trail reads the same way.
        return [g for g in approved if g in requested_set], None, ""

    return approved, None, ""


# ── Dry run ──────────────────────────────────────────────────────────────────


async def dryrun_share(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_guids: list[str],
    principal_guids: list[str],
    mode: str,
) -> None:
    """
    Live, no-write impact check for a proposed share.

    Runs the same live ACL diff as :func:`preview_share` but as a background job,
    storing the summary in ``Job.result`` and writing nothing. This is the
    dry-run that gates the destructive ``execute_share`` (especially ``NO_ACCESS``
    revokes), satisfying the dry-run safety contract.
    """
    from ts_admin.services.job_service import mark_complete, mark_failed, mark_running

    mark_running(job_id, len(object_guids) * len(principal_guids))
    try:
        preview = await preview_share(
            cluster_id=cluster_id,
            org_id=org_id,
            object_guids=object_guids,
            principal_guids=principal_guids,
            mode=mode,
        )
        result = {
            "total": preview["total"],
            "will_change_count": preview["will_change_count"],
            "items": preview["items"][:500],  # cap the stored sample
            # Surfaced verbatim so the dry-run tells the admin what execute will
            # NOT touch, instead of leaving the omission to be discovered later.
            "skipped": preview.get("skipped", []),
            "skipped_count": preview.get("skipped_count", 0),
        }
        mark_complete(job_id, result)
        logger.info(
            "dryrun_share job=%s cluster=%s total=%d will_change=%d",
            job_id,
            cluster_id,
            result["total"],
            result["will_change_count"],
        )
    # Last-resort handler for a background task — see the note on the matching
    # handler in `execute_share`. Permitted to swallow ANY exception because an
    # escape here strands the Job row at RUNNING; nothing is swallowed silently.
    except Exception as exc:
        logger.exception("dryrun_share job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


# ── Post-execute verification ─────────────────────────────────────────────────


def _verification_sample(guids: list[str], limit: int) -> list[str]:
    """Pick at most `limit` GUIDs, spread evenly across `guids`.

    A stride rather than the first N: objects are shared in `object_type`
    buckets, so the first N would all come from one bucket and a per-type
    failure (the shape of every 404 this row exists for) could hide behind a
    fully verified sample of the other type.
    """
    if len(guids) <= limit:
        return list(guids)
    step = len(guids) / limit
    return [guids[int(i * step)] for i in range(limit)]


async def _verify_share(
    client,
    *,
    shared_guids: list[str],
    obj_map: dict[str, CachedMetadata],
    principal_guids: list[str],
    mode: str,
) -> dict:
    """
    Re-read the shared objects' ACLs and compare them against what was requested.

    `share_objects` returns 204 No Content, so without this the audit row saying
    "shared" is a guess. Each sampled object is re-read through
    `fetch_permissions` (PERMISSION_LENS) and counted as:

      - verified_ok      — every requested principal is at the requested mode
      - verified_failed  — at least one is not (the share did not land)
      - verified_errors  — the read-back itself failed, so nothing is proven
        either way. An unreachable read is NOT evidence of a failed share and is
        deliberately kept out of `verified_failed`.

    `NO_ACCESS` is a revoke and `fetch_permissions` never returns a NO_ACCESS
    row, so "applied" there means the principal is ABSENT from the defined ACL.
    """
    candidates = list(dict.fromkeys(shared_guids))
    sample = _verification_sample(candidates, VERIFY_SAMPLE_LIMIT)
    if not sample:
        return {
            "verified_ok": 0,
            "verified_failed": 0,
            "verified_errors": 0,
            "verified_sampled": 0,
            "verified_candidates": 0,
            "verification_scope": "none",
            "verification_note": "Nothing was shared, so nothing was verified.",
            "verified_failed_guids": [],
        }

    sem = asyncio.Semaphore(10)

    async def _read(guid: str):
        async with sem:
            try:
                perms = await client.fetch_permissions(
                    ts_guid=guid,
                    object_type=obj_map[guid].object_type,
                    permission_type=PERMISSION_LENS,
                )
                return guid, perms, None
            # Same narrow pair as every other live call in this module.
            except (TSAdminError, httpx.HTTPError) as exc:
                logger.warning("share verification could not re-read %s: %s", guid, exc)
                return guid, [], exc

    results = await asyncio.gather(*[_read(guid) for guid in sample])

    verified_ok = 0
    verified_errors = 0
    failed_guids: list[dict] = []
    for guid, perms, err in results:
        if err is not None:
            verified_errors += 1
            continue
        actual = {p.principal_id: str(p.share_mode) for p in perms}
        if mode == "NO_ACCESS":
            wrong = [pid for pid in principal_guids if pid in actual]
        else:
            wrong = [pid for pid in principal_guids if actual.get(pid) != mode]
        if wrong:
            failed_guids.append({"object_guid": guid, "principals": wrong[:20]})
        else:
            verified_ok += 1

    scope = "full" if len(sample) == len(candidates) else "sample"
    note = (
        f"Re-read {len(sample)} of {len(candidates)} shared object(s) "
        f"({PERMISSION_LENS} permissions; cap {VERIFY_SAMPLE_LIMIT})."
    )
    if scope == "sample":
        note += " Objects outside the sample were NOT verified."
    if verified_errors:
        note += f" {verified_errors} object(s) could not be re-read, so their outcome is unknown."

    return {
        "verified_ok": verified_ok,
        "verified_failed": len(failed_guids),
        "verified_errors": verified_errors,
        "verified_sampled": len(sample),
        "verified_candidates": len(candidates),
        "verification_scope": scope,
        "verification_note": note,
        "verified_failed_guids": failed_guids[:50],
    }


def _share_failure_reason(
    *,
    succeeded_pairs: int,
    failed_records: list[dict],
    skipped: list[dict],
    cancelled: bool,
    verification: dict,
) -> str:
    """Name what actually went wrong, not just how many things did.

    "0 share operations succeeded" told an admin nothing they could act on. What
    they need is the error ThoughtSpot returned — for the non-existent endpoint
    this row exists for, a 404 whose canned message ("run a sync and retry") was
    itself the thing sending people in circles.
    """
    parts: list[str] = []
    if failed_records:
        first = failed_records[0]
        parts.append(
            f"{len(failed_records)} share call(s) failed — first error on "
            f"{first.get('object_type') or '?'}: {first.get('error') or 'unknown error'}"
        )
    if verification["verified_failed"]:
        parts.append(
            f"{verification['verified_failed']} of {verification['verified_sampled']} re-read object(s) "
            "do not carry the requested access — the share call reported no error but did not take effect"
        )
    if cancelled:
        parts.append("the job was cancelled")
    if skipped:
        parts.append(f"{len(skipped)} object(s) were not in the metadata cache and were never attempted")
    if not parts:
        parts.append("no share call was attempted")

    lead = "0 share operations succeeded" if succeeded_pairs == 0 else f"{succeeded_pairs} share pair(s) succeeded"
    return f"{lead}: " + "; ".join(parts) + "."


# ── Execute ────────────────────────────────────────────────────────────────────


async def execute_share(
    job_id: str,
    cluster_id: str,
    org_id: int,
    object_guids: list[str],
    principal_guids: list[str],
    mode: str,
    notify: bool = False,
) -> None:
    """
    Share `object_guids` with `principal_guids` at `mode`. One share_objects
    call per object_type group (the API requires a single type per call).

    `notify` is forwarded to the API as `notify_on_share`. It used to be
    recorded in the audit log and then dropped, and the wire default is TRUE —
    so had the endpoint path been correct, every share would have emailed its
    recipients while the audit row said `"notify": false`.

    Refuses on a non-authoritative metadata cache: objects are grouped by the
    `object_type` read from the cache, so a truncated cache drops objects from
    the share entirely — silently, and for NO_ACCESS revokes that means access
    the admin believes was removed is still live.

    GUIDs with no cache row are excluded — the same set `preview_share` excludes
    — and reported in `Job.result["skipped"]` plus the audit log. A run with any
    skipped GUID is PARTIAL, never SUCCESS: the admin asked for something that
    did not happen. A run in which NOTHING succeeded is FAILED, never PARTIAL.

    `share_objects` answers 204 No Content, so every shared object is re-read
    afterwards (bounded — see VERIFY_SAMPLE_LIMIT) and compared against what was
    requested. The `verified_*` counts and `verification_note` land in both
    `Job.result` and the audit row, and a run whose re-read shows nothing landed
    is not reported as a success.
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
    from ts_admin.ts_client.models import SharePermission

    # Defense in depth. The REAL refusal is in `api/sharing.py::execute`, which
    # returns 409 before any Job row exists — this function only ever runs as a
    # background task, so it is unreachable in practice. It is kept because it
    # is the layer that owns the invariant, but it must NOT raise: an exception
    # escaping a background task is invisible to the caller and would leave the
    # job QUEUED forever. Mark the job FAILED instead, BEFORE mark_running.
    try:
        require_authoritative_metadata(cluster_id=cluster_id, org_id=org_id)
    except StaleCacheError as exc:
        mark_failed(job_id, exc)
        return

    # Resolve to display data for ShareRecord — and to the exact set the preview
    # showed. Anything not in the cache is dropped here, before any live call.
    with Session(_db.get_engine()) as session:
        obj_map, skipped = _resolve_object_map(
            session,
            cluster_id=cluster_id,
            org_id=org_id,
            object_guids=object_guids,
        )
        users = session.exec(
            select(CachedUser).where(
                CachedUser.cluster_id == cluster_id,
                col(CachedUser.ts_guid).in_(principal_guids),
            )
        ).all()
        groups = session.exec(
            select(CachedGroup).where(
                CachedGroup.cluster_id == cluster_id,
                CachedGroup.org_id == org_id,
                col(CachedGroup.ts_guid).in_(principal_guids),
            )
        ).all()

    principal_meta: dict[str, dict] = {}
    for u in users:
        principal_meta[u.ts_guid] = {
            "name": u.display_name or u.username,
            "type": "USER",
        }
    for g in groups:
        principal_meta[g.ts_guid] = {
            "name": g.display_name or g.name,
            "type": "USER_GROUP",
        }
    for pid in principal_guids:
        principal_meta.setdefault(pid, {"name": pid, "type": "USER"})

    # The resolved set — identical to the preview rows' object_guids.
    resolved_guids = [guid for guid in object_guids if guid in obj_map]
    requested_pairs = len(object_guids) * len(principal_guids)
    total_pairs = len(resolved_guids) * len(principal_guids)
    mark_running(job_id, total_pairs)

    if skipped:
        logger.warning(
            "execute_share job=%s: %d of %d GUIDs are not in the metadata cache — excluded from the share",
            job_id,
            len(skipped),
            len(object_guids),
        )
    if not resolved_guids:
        mark_failed(
            job_id,
            f"0 of {len(object_guids)} objects resolved in the metadata cache — nothing was shared",
        )
        return

    succeeded_pairs = 0
    failed_records: list[dict] = []
    shared_guids: list[str] = []  # GUIDs whose share call returned without error
    cancelled = False

    try:
        enum_mode = SharePermission(mode)
    except ValueError:
        mark_failed(job_id, f"Invalid share mode {mode!r}")
        return

    try:
        cluster = _get_cluster(cluster_id)
        # Bucket objects by type — one share_objects call per (type, principals,
        # mode). Types come from the cache only; there is no fallback type,
        # because guessing would send e.g. an ANSWER inside a LIVEBOARD call.
        type_groups: dict[str, list[str]] = {}
        for guid in resolved_guids:
            type_groups.setdefault(obj_map[guid].object_type, []).append(guid)

        async with ThoughtSpotClient(
            url=cluster.url,
            auth=cluster.build_auth_strategy(org_id=org_id),
        ) as client:
            for obj_type, guids in type_groups.items():
                if cancelled:
                    break
                for chunk in _chunks(guids, 50):
                    if is_cancelled(job_id):
                        cancelled = True
                        break

                    chunk_error: str | None = None
                    try:
                        await client.share_objects(
                            object_ids=chunk,
                            principal_ids=principal_guids,
                            permission=enum_mode,
                            message=SHARE_MESSAGE,
                            notify=notify,
                        )
                    # ONLY the live call is guarded, and only against the two
                    # families it can raise (`_request` maps every HTTP outcome
                    # onto TSAdminError; httpx.HTTPError covers the transport).
                    # The blanket `except Exception` that used to wrap this
                    # whole block — the share call AND the ShareRecord writes —
                    # turned any failure at all into "this chunk failed", which
                    # is how a TypeError in our own code shipped as a PARTIAL
                    # job. Anything else must reach the outer handler and FAIL.
                    except (TSAdminError, httpx.HTTPError) as exc:
                        logger.warning("share_objects chunk failed (%s): %s", obj_type, exc)
                        chunk_error = str(exc)[:300]
                        failed_records.append({"object_type": obj_type, "guids": chunk, "error": chunk_error})

                    if chunk_error is None:
                        succeeded_pairs += len(chunk) * len(principal_guids)
                        shared_guids.extend(chunk)

                    # ShareRecord per (object × principal), either way.
                    with Session(_db.get_engine()) as session:
                        for guid in chunk:
                            for pid in principal_guids:
                                meta = principal_meta.get(pid, {"name": pid, "type": "USER"})
                                session.add(
                                    ShareRecord(
                                        cluster_id=cluster_id,
                                        job_id=job_id,
                                        org_id=org_id,
                                        object_guid=guid,
                                        object_name=obj_map[guid].name,
                                        object_type=obj_type,
                                        principal_guid=pid,
                                        principal_name=meta["name"],
                                        principal_type=meta["type"],
                                        new_mode=mode,
                                        status="FAILED" if chunk_error else "SUCCESS",
                                        error=chunk_error,
                                    )
                                )
                        session.commit()
                    update_progress(job_id, succeeded_pairs)

            # `security/metadata/share` answers 204 No Content, so the only way
            # to know the share landed is to read it back. Runs inside the same
            # client session, and is bounded — see VERIFY_SAMPLE_LIMIT.
            verification = await _verify_share(
                client,
                shared_guids=shared_guids,
                obj_map=obj_map,
                principal_guids=principal_guids,
                mode=mode,
            )

        # Zero successes is FAILED, never PARTIAL. PARTIAL reads to an admin as
        # "some of it worked, retry the rest" — attached to a 404 from an
        # endpoint that never existed, it sent people re-syncing forever. The
        # succeeded == 0 branch is therefore evaluated FIRST, and the failure
        # message names what happened rather than only counting it.
        #
        # A skipped GUID means the admin asked for something that did not
        # happen, so a run with any skips can never report a clean SUCCESS.
        if succeeded_pairs == 0:
            status = "FAILED"
        elif failed_records or cancelled or skipped:
            status = "PARTIAL"
        else:
            status = "SUCCESS"

        # The read-back overrides the optimistic result: shares that were issued
        # without error but did not land are not successes.
        if status != "FAILED" and verification["verified_failed"]:
            status = "FAILED" if verification["verified_ok"] == 0 else "PARTIAL"

        failure_reason = _share_failure_reason(
            succeeded_pairs=succeeded_pairs,
            failed_records=failed_records,
            skipped=skipped,
            cancelled=cancelled,
            verification=verification,
        )

        with Session(_db.get_engine()) as session:
            audit = AuditLog(
                cluster_id=cluster_id,
                action_type="bulk_share",
                entity_type="metadata",
                items_affected=succeeded_pairs,
                # The audit row carries the SAME terminal status as the job. It
                # used to be computed from an earlier expression that could not
                # say FAILED at all, so a job that achieved nothing left a
                # PARTIAL row behind it in the audit trail.
                status=status,
            )
            audit.set_parameters(
                {
                    "object_guids": object_guids,
                    "principal_guids": principal_guids,
                    "mode": mode,
                    "notify": notify,
                    "succeeded_pairs": succeeded_pairs,
                    "total_pairs": total_pairs,
                    "requested_pairs": requested_pairs,
                    "failed": failed_records,
                    "skipped": skipped,
                    "cancelled": cancelled,
                    "error": failure_reason,
                    **verification,
                }
            )
            session.add(audit)
            session.commit()

        result = {
            "succeeded_pairs": succeeded_pairs,
            "failed_pairs": total_pairs - succeeded_pairs,
            "total_pairs": total_pairs,
            "requested_pairs": requested_pairs,
            "skipped": skipped,
            "skipped_count": len(skipped),
            "cancelled": cancelled,
            **verification,
        }
        if status == "FAILED":
            mark_failed(job_id, failure_reason)
        elif status == "PARTIAL":
            mark_partial(job_id, result)
        else:
            mark_complete(job_id, result)
        logger.info(
            "bulk_share job=%s cluster=%s status=%s succeeded=%d failed=%d skipped=%d cancelled=%s "
            "verified_ok=%d verified_failed=%d",
            job_id,
            cluster_id,
            status,
            succeeded_pairs,
            total_pairs - succeeded_pairs,
            len(skipped),
            cancelled,
            verification["verified_ok"],
            verification["verified_failed"],
        )
    # Last-resort handler for a background task: this coroutine is awaited by
    # Starlette AFTER the 202 is on the wire, so an exception that escapes here
    # is invisible to the caller and strands the Job row at RUNNING forever.
    # Narrowing it would re-introduce that. It is permitted to swallow ANY
    # exception, and it swallows none of them silently — every one is logged
    # with a traceback and re-reported as a FAILED job.
    except Exception as exc:
        logger.exception("execute_share job %s failed: %s", job_id, exc)
        mark_failed(job_id, exc)


# ── History ────────────────────────────────────────────────────────────────────


def list_history(
    *,
    cluster_id: str,
    org_id: int | None,
    record_offset: int,
    page_size: int,
) -> tuple[list[dict], int]:
    """Aggregate ShareRecord by job_id, newest first."""
    with Session(_db.get_engine()) as session:
        base = select(
            ShareRecord.job_id,
            func.min(ShareRecord.executed_at).label("executed_at"),
            func.count(func.distinct(ShareRecord.object_guid)).label("object_count"),
            func.count(func.distinct(ShareRecord.principal_guid)).label("principal_count"),
            func.sum(
                func.iif(ShareRecord.status == "SUCCESS", 1, 0)  # SQLite-friendly
            ).label("succeeded"),
            func.sum(func.iif(ShareRecord.status == "FAILED", 1, 0)).label("failed"),
        ).where(ShareRecord.cluster_id == cluster_id)
        if org_id is not None:
            base = base.where(ShareRecord.org_id == org_id)
        base = base.group_by(ShareRecord.job_id).order_by(func.min(ShareRecord.executed_at).desc())

        all_rows = session.exec(base).all()
        total = len(all_rows)
        page = all_rows[record_offset : record_offset + page_size]

        items = []
        for r in page:
            failed = r.failed or 0
            items.append(
                {
                    "job_id": r.job_id,
                    "executed_at": r.executed_at.isoformat() if r.executed_at else "",
                    "object_count": r.object_count or 0,
                    "principal_count": r.principal_count or 0,
                    "succeeded": r.succeeded or 0,
                    "failed": failed,
                    "status": "PARTIAL" if failed and r.succeeded else ("FAILED" if failed else "SUCCESS"),
                }
            )
        return items, total
