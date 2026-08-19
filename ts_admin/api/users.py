"""
User Management API.

Read endpoints:
  GET  /api/v1/users                       — paginated user grid (cluster + org scoped)
  GET  /api/v1/users/{ts_guid}             — single user detail
  GET  /api/v1/users/history               — past user-management actions

Action endpoints (preview is sync, execute is a background job):
  POST /api/v1/users/transfer/preview      — list objects that will move
  POST /api/v1/users/transfer/execute      — kick reassign-ownership job
  POST /api/v1/users/transfer-sharing/preview — live: what the source user can see
  POST /api/v1/users/transfer-sharing/execute — kick re-share job
  POST /api/v1/users/delete/preview        — snapshot users + owned-object counts
  POST /api/v1/users/delete/execute        — admin-only; kick retry-to-10 delete job

Every execute endpoint returns 202 with a job_id; poll /api/v1/jobs/{job_id}.
"""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel

from ts_admin.services import user_management_service as svc
from ts_admin.ts_client.exceptions import (
    TSAuthenticationError,
    TSConnectionError,
    TSInsufficientPrivilegesError,
    TSInvalidParametersError,
    TSObjectNotFoundError,
    TSResponseParseError,
    TSServerError,
    TSTimeoutError,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["User Management"])


# ── Schemas ────────────────────────────────────────────────────────────────────


class UserListItem(BaseModel):
    ts_guid: str
    username: str
    display_name: str
    email: str
    status: str
    created_at: str | None
    modified_at: str | None
    synced_at: str | None


class UserListResponse(BaseModel):
    items: list[UserListItem]
    total: int
    record_offset: int
    page_size: int


class UserGroupInfo(BaseModel):
    ts_guid: str
    name: str
    display_name: str
    privileges: list[str]


class UserDetail(UserListItem):
    owned_object_count: int
    org_ids: list[int]
    groups: list[str]
    group_details: list[UserGroupInfo]
    privileges: list[str]  # effective: union of all group privileges
    is_admin: bool


class TransferObjectItem(BaseModel):
    ts_guid: str
    name: str
    object_type: str
    owner_guid: str
    owner_name: str
    modified_at: str | None
    tags: list[str]


class TransferPreviewRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    from_user_guid: str
    object_types: list[str] | None = None
    tag_names: list[str] | None = None
    explicit_guids: list[str] | None = None


class TransferPreviewResponse(BaseModel):
    items: list[TransferObjectItem]
    total: int
    by_type: dict[str, int]


class TransferExecuteRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    from_user_guid: str
    to_user_identifier: str  # username or GUID
    object_ids: list[str]


class JobAcceptedResponse(BaseModel):
    job_id: str
    total: int


class SharingPermissionItem(BaseModel):
    metadata_id: str
    metadata_name: str
    metadata_type: str
    share_mode: str


class TransferSharingPreviewRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    from_user_guid: str
    to_user_identifier: str


class TransferSharingPreviewResponse(BaseModel):
    items: list[SharingPermissionItem]
    total: int
    by_type: dict[str, int]


class TransferSharingExecuteRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    from_user_guid: str
    to_user_identifier: str
    notify: bool = False


class DeletePreviewRequest(BaseModel):
    cluster_id: str | None = None
    user_guids: list[str]


class DeletePreviewItem(UserListItem):
    owned_object_count: int
    is_admin: bool


class DeletePreviewResponse(BaseModel):
    items: list[DeletePreviewItem]
    total: int
    unrecognized: list[str]


class DeleteDryRunRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    user_guids: list[str]
    user_identifiers: list[str] | None = None


class DeleteExecuteRequest(BaseModel):
    cluster_id: str | None = None
    org_id: int = 0
    user_guids: list[str]
    user_identifiers: list[str] | None = None  # usernames preferred for TS API
    # Deleting a ThoughtSpot admin is refused unless the caller opts in, because
    # it is the one delete that can lock the admin out of their own cluster. The
    # dry-run reports `admin_count`, so the UI knows to ask. There is no such
    # escape hatch for deleting the account this toolkit signs in as — that is
    # refused unconditionally in the service.
    confirm_admin_delete: bool = False


class UserHistoryItem(BaseModel):
    id: str
    job_id: str
    action_type: str
    from_username: str
    from_display_name: str
    to_username: str
    to_display_name: str
    items_total: int
    items_succeeded: int
    items_failed: int
    status: str
    error: str | None
    executed_at: str


class UserHistoryResponse(BaseModel):
    items: list[UserHistoryItem]
    total: int
    record_offset: int
    page_size: int


# ── Helpers ────────────────────────────────────────────────────────────────────


def _resolve_cluster_id(cluster_id: str | None) -> str:
    if cluster_id:
        return cluster_id
    from ts_admin.config import load_config

    return load_config().active_cluster.id


# ── List / detail ──────────────────────────────────────────────────────────────


@router.get("", response_model=UserListResponse)
def list_users(
    cluster_id: str | None = Query(default=None),
    org_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    search: str | None = Query(default=None),
    sort_field: str = Query(default="username"),
    sort_order: Literal["asc", "desc"] = Query(default="asc"),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=200, ge=1, le=1000),
) -> UserListResponse:
    """Paginated user grid for the User Management page."""
    items, total = svc.list_users(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        status=status,
        search=search,
        sort_field=sort_field,
        sort_order=sort_order,
        record_offset=record_offset,
        page_size=page_size,
    )
    return UserListResponse(
        items=[UserListItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


@router.get("/history", response_model=UserHistoryResponse)
def list_history(
    cluster_id: str | None = Query(default=None),
    org_id: int | None = Query(default=None),
    action_type: str | None = Query(default=None),
    record_offset: int = Query(default=0, ge=0),
    page_size: int = Query(default=50, ge=1, le=200),
) -> UserHistoryResponse:
    """Past user-management actions for the History tab."""
    items, total = svc.list_history(
        cluster_id=_resolve_cluster_id(cluster_id),
        org_id=org_id,
        action_type=action_type,
        record_offset=record_offset,
        page_size=page_size,
    )
    return UserHistoryResponse(
        items=[UserHistoryItem(**i) for i in items],
        total=total,
        record_offset=record_offset,
        page_size=page_size,
    )


@router.get("/{ts_guid}", response_model=UserDetail)
def get_user(
    ts_guid: str,
    cluster_id: str | None = Query(default=None),
) -> UserDetail:
    """Single user detail — used by the offboarding wizards as a sanity check."""
    detail = svc.get_user_detail(cluster_id=_resolve_cluster_id(cluster_id), ts_guid=ts_guid)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"User {ts_guid!r} not found in cache")
    return UserDetail(**detail)


class UserAccessResponse(BaseModel):
    items: list[SharingPermissionItem]
    total: int
    by_type: dict[str, int]


@router.get("/{ts_guid}/access", response_model=UserAccessResponse)
async def get_user_access(
    ts_guid: str,
    cluster_id: str | None = Query(default=None),
    org_id: int = Query(default=0),
) -> UserAccessResponse:
    """Live API call — everything the user can currently see (audit view)."""
    try:
        result = await svc.get_user_access(
            cluster_id=_resolve_cluster_id(cluster_id),
            org_id=org_id,
            ts_guid=ts_guid,
        )
    except TSAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except TSInsufficientPrivilegesError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (
        TSConnectionError,
        TSTimeoutError,
        TSServerError,
        TSResponseParseError,
        TSInvalidParametersError,
        TSObjectNotFoundError,
    ) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return UserAccessResponse(
        items=[SharingPermissionItem(**i) for i in result["items"]],
        total=result["total"],
        by_type=result["by_type"],
    )


# ── Transfer ownership ─────────────────────────────────────────────────────────


@router.post("/transfer/preview", response_model=TransferPreviewResponse)
def transfer_preview(body: TransferPreviewRequest) -> TransferPreviewResponse:
    """Return objects currently owned by the source user (filtered)."""
    if not body.from_user_guid:
        raise HTTPException(status_code=422, detail="from_user_guid is required")
    result = svc.preview_transfer(
        cluster_id=_resolve_cluster_id(body.cluster_id),
        org_id=body.org_id,
        from_user_guid=body.from_user_guid,
        object_types=body.object_types,
        tag_names=body.tag_names,
        explicit_guids=body.explicit_guids,
    )
    return TransferPreviewResponse(
        items=[TransferObjectItem(**i) for i in result["items"]],
        total=result["total"],
        by_type=result["by_type"],
    )


@router.post("/transfer/execute", response_model=JobAcceptedResponse, status_code=202)
async def transfer_execute(
    body: TransferExecuteRequest,
    background_tasks: BackgroundTasks,
) -> JobAcceptedResponse:
    """Kick a background job that reassigns ownership of `object_ids`."""
    if not body.object_ids:
        raise HTTPException(status_code=422, detail="object_ids must not be empty")
    if not body.to_user_identifier:
        raise HTTPException(status_code=422, detail="to_user_identifier is required")

    cluster_id = _resolve_cluster_id(body.cluster_id)

    # Fail closed on a truncated metadata cache, HERE and not in the service.
    # `execute_transfer` only ever runs as a Starlette background task, i.e.
    # AFTER the 202 + job_id is on the wire — a raise there cannot become a
    # response and would strand the Job row in QUEUED forever. Refusing before
    # create_job means the caller gets a real 409 and no job is created at all.
    from ts_admin.services.sync_status import require_authoritative_metadata

    require_authoritative_metadata(cluster_id=cluster_id, org_id=body.org_id)

    from ts_admin.services.job_service import create_job

    job_id = create_job(
        job_type="user_transfer_ownership",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "from_user_guid": body.from_user_guid,
            "to_user_identifier": body.to_user_identifier,
            "object_ids": body.object_ids,
        },
    )
    background_tasks.add_task(
        svc.execute_transfer,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        from_user_guid=body.from_user_guid,
        to_user_identifier=body.to_user_identifier,
        object_ids=body.object_ids,
    )
    return JobAcceptedResponse(job_id=job_id, total=len(body.object_ids))


# ── Transfer sharing ───────────────────────────────────────────────────────────


@router.post("/transfer-sharing/preview", response_model=TransferSharingPreviewResponse)
async def transfer_sharing_preview(body: TransferSharingPreviewRequest) -> TransferSharingPreviewResponse:
    """Live API call — fetches what the source user can see."""
    cluster_id = _resolve_cluster_id(body.cluster_id)
    try:
        result = await svc.preview_transfer_sharing(
            cluster_id=cluster_id,
            org_id=body.org_id,
            from_user_guid=body.from_user_guid,
            to_user_identifier=body.to_user_identifier,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TSAuthenticationError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except TSInsufficientPrivilegesError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except (
        TSConnectionError,
        TSTimeoutError,
        TSServerError,
        TSResponseParseError,
        TSInvalidParametersError,
        TSObjectNotFoundError,
    ) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return TransferSharingPreviewResponse(
        items=[SharingPermissionItem(**i) for i in result["items"]],
        total=result["total"],
        by_type=result["by_type"],
    )


@router.post("/transfer-sharing/execute", response_model=JobAcceptedResponse, status_code=202)
async def transfer_sharing_execute(
    body: TransferSharingExecuteRequest,
    background_tasks: BackgroundTasks,
) -> JobAcceptedResponse:
    """Kick re-share job (preview must have been run first to confirm count)."""
    if not body.from_user_guid or not body.to_user_identifier:
        raise HTTPException(status_code=422, detail="from_user_guid and to_user_identifier are required")

    cluster_id = _resolve_cluster_id(body.cluster_id)

    from ts_admin.services.job_service import create_job

    job_id = create_job(
        job_type="user_transfer_sharing",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "from_user_guid": body.from_user_guid,
            "to_user_identifier": body.to_user_identifier,
            "notify": body.notify,
        },
    )
    background_tasks.add_task(
        svc.execute_transfer_sharing,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        from_user_guid=body.from_user_guid,
        to_user_identifier=body.to_user_identifier,
        notify=body.notify,
    )
    return JobAcceptedResponse(job_id=job_id, total=0)  # total known only after fetch


# ── Delete users ───────────────────────────────────────────────────────────────


@router.post("/delete/preview", response_model=DeletePreviewResponse)
def delete_preview(body: DeletePreviewRequest) -> DeletePreviewResponse:
    """Snapshot users + owned-object counts before delete."""
    if not body.user_guids:
        raise HTTPException(status_code=422, detail="user_guids must not be empty")
    result = svc.preview_delete(
        cluster_id=_resolve_cluster_id(body.cluster_id),
        user_guids=body.user_guids,
    )
    return DeletePreviewResponse(
        items=[DeletePreviewItem(**i) for i in result["items"]],
        total=result["total"],
        unrecognized=result["unrecognized"],
    )


@router.post("/delete/dryrun", response_model=JobAcceptedResponse, status_code=202)
async def delete_dryrun(
    body: DeleteDryRunRequest,
    background_tasks: BackgroundTasks,
) -> JobAcceptedResponse:
    """
    Start a live, no-write impact check for a proposed user deletion. Confirms
    which selected users still exist upstream and reports owned-object/admin
    impact. Poll /api/v1/jobs/{job_id}; the summary lands in the job result.
    """
    if not body.user_guids:
        raise HTTPException(status_code=422, detail="user_guids must not be empty")

    cluster_id = _resolve_cluster_id(body.cluster_id)

    from ts_admin.services.job_service import create_job

    job_id = create_job(
        job_type="user_delete_dryrun",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "user_guids": body.user_guids,
            "user_identifiers": body.user_identifiers,
        },
    )
    background_tasks.add_task(
        svc.dryrun_delete,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        user_guids=body.user_guids,
        user_identifiers=body.user_identifiers,
    )
    return JobAcceptedResponse(job_id=job_id, total=len(body.user_guids))


@router.post("/delete/execute", response_model=JobAcceptedResponse, status_code=202)
async def delete_execute(
    body: DeleteExecuteRequest,
    background_tasks: BackgroundTasks,
) -> JobAcceptedResponse:
    """
    Kick retry-to-10 delete loop. Caller MUST send a typed confirmation (the UI
    layer enforces a typed-`DELETE` step; the API trusts that body.user_guids
    arrived after that gate).
    """
    if not body.user_guids:
        raise HTTPException(status_code=422, detail="user_guids must not be empty")

    cluster_id = _resolve_cluster_id(body.cluster_id)

    from ts_admin.services.job_service import create_job

    job_id = create_job(
        job_type="user_delete",
        parameters={
            "cluster_id": cluster_id,
            "org_id": body.org_id,
            "user_guids": body.user_guids,
            "user_identifiers": body.user_identifiers,
            "confirm_admin_delete": body.confirm_admin_delete,
        },
    )
    background_tasks.add_task(
        svc.execute_delete,
        job_id=job_id,
        cluster_id=cluster_id,
        org_id=body.org_id,
        user_guids=body.user_guids,
        user_identifiers=body.user_identifiers,
        confirm_admin_delete=body.confirm_admin_delete,
    )
    return JobAcceptedResponse(job_id=job_id, total=len(body.user_guids))
