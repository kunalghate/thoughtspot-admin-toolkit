"""
Cluster connection management endpoints.

GET    /api/v1/clusters           — list all configured clusters
POST   /api/v1/clusters           — add a new cluster
PUT    /api/v1/clusters/{id}      — update a cluster
DELETE /api/v1/clusters/{id}      — remove a cluster
POST   /api/v1/clusters/{id}/test — test connection to a cluster
POST   /api/v1/clusters/{id}/activate — switch active cluster
"""

import logging

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, field_validator

from ts_admin.ts_client.models import AuthType

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/clusters", tags=["clusters"])


# ── Request / Response models ──────────────────────────────────────────────────


class ClusterIn(BaseModel):
    id: str
    name: str
    url: str
    username: str
    auth_type: AuthType = AuthType.BASIC
    credential: str  # password | secret_key | bearer token

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str) -> str:
        from ts_admin.services.cluster_service import validate_cluster_url

        return validate_cluster_url(v)


class ClusterOut(BaseModel):
    id: str
    name: str
    url: str
    username: str
    auth_type: AuthType
    is_active: bool = False
    # Live session health, from the process-scoped connection_status registry.
    # "unknown" until something actually talks to this cluster this process.
    connection_status: str = "unknown"
    connection_detail: str | None = None
    connection_checked_at: str | None = None


class TestResult(BaseModel):
    success: bool
    ts_version: str | None = None
    error: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.get("", response_model=list[ClusterOut])
async def list_clusters() -> list[ClusterOut]:
    """Return all configured clusters with active cluster flagged."""
    from ts_admin.config import load_config
    from ts_admin.services import connection_status
    from ts_admin.ts_client.exceptions import ConfigNotFoundError

    try:
        config = load_config()
    except ConfigNotFoundError:
        return []

    result = []
    for c in config.clusters.values():
        health = connection_status.get(c.id)
        result.append(
            ClusterOut(
                id=c.id,
                name=c.name,
                url=c.url,
                username=c.username,
                auth_type=c.auth_type,
                is_active=(c.id == config.active_cluster_id),
                connection_status=health.state.value,
                connection_detail=health.detail,
                connection_checked_at=health.checked_at,
            )
        )
    return result


@router.post("", response_model=ClusterOut, status_code=status.HTTP_201_CREATED)
async def add_cluster(body: ClusterIn) -> ClusterOut:
    """Add a new cluster connection and save credentials to keychain."""
    from ts_admin.config import ClusterConfig, load_config, save_cluster, set_active_cluster
    from ts_admin.ts_client.exceptions import TSAdminError

    cluster = ClusterConfig(
        id=body.id,
        name=body.name,
        url=body.url,
        username=body.username,
        auth_type=body.auth_type,
    )

    try:
        save_cluster(cluster, secret=body.credential)
    except TSAdminError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Auto-activate if this is the first cluster
    config = load_config()
    is_active = False
    if len(config.clusters) == 1:
        set_active_cluster(cluster.id)
        is_active = True

    return ClusterOut(
        id=cluster.id,
        name=cluster.name,
        url=cluster.url,
        username=cluster.username,
        auth_type=cluster.auth_type,
        is_active=is_active,
    )


class ClusterUpdate(BaseModel):
    name: str
    url: str
    username: str
    auth_type: AuthType = AuthType.BASIC
    credential: str | None = None  # if None, keep existing keychain entry


@router.put("/{cluster_id}", response_model=ClusterOut)
async def update_cluster(cluster_id: str, body: ClusterUpdate) -> ClusterOut:
    """Update a cluster's config and optionally rotate its credential."""
    from ts_admin.config import load_config
    from ts_admin.config import update_cluster as cfg_update
    from ts_admin.services import connection_status
    from ts_admin.ts_client.exceptions import ConfigInvalidError, TSAdminError

    try:
        cluster = cfg_update(
            cluster_id,
            name=body.name,
            url=body.url,
            username=body.username,
            auth_type=body.auth_type,
            new_secret=body.credential,  # None = keep existing
        )
        # Config (possibly the credential) changed — drop stale "expired" health
        # so the badge resets to "unknown" until the next call re-establishes it.
        connection_status.clear(cluster_id)
        config = load_config()
        return ClusterOut(
            id=cluster.id,
            name=cluster.name,
            url=cluster.url,
            username=cluster.username,
            auth_type=cluster.auth_type,
            is_active=(config.active_cluster_id == cluster.id),
        )
    except ConfigInvalidError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TSAdminError as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_cluster(cluster_id: str) -> None:
    """Remove a cluster and delete its keychain entry."""
    from ts_admin.config import delete_cluster
    from ts_admin.services import connection_status

    delete_cluster(cluster_id)
    connection_status.clear(cluster_id)


@router.post("/{cluster_id}/test", response_model=TestResult)
async def test_cluster(cluster_id: str) -> TestResult:
    """Test the connection to a specific cluster."""
    import httpx

    from ts_admin.config import load_config
    from ts_admin.services import connection_status
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.ts_client.exceptions import ConfigNotFoundError, TSAdminError, TSAuthenticationError

    try:
        config = load_config()
        cluster = config.clusters.get(cluster_id)
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Instance {cluster_id!r} not found")

        auth = cluster.build_auth_strategy()
        async with ThoughtSpotClient(url=cluster.url, auth=auth) as client:
            info = await client.test_connection()

        version = info.get("release_version")
        connection_status.mark_connected(cluster_id, ts_version=version)
        return TestResult(success=True, ts_version=version)

    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TSAuthenticationError as exc:
        connection_status.mark_expired(cluster_id, detail=str(exc))
        return TestResult(success=False, error=str(exc))
    except TSAdminError as exc:
        connection_status.mark_unreachable(cluster_id, detail=str(exc))
        return TestResult(success=False, error=str(exc))
    except httpx.HTTPStatusError as exc:
        connection_status.mark_unreachable(cluster_id, detail=f"HTTP {exc.response.status_code}")
        return TestResult(success=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except httpx.ConnectError:
        connection_status.mark_unreachable(cluster_id, detail="Cannot reach ThoughtSpot")
        return TestResult(success=False, error="Cannot reach ThoughtSpot — check the URL and your network connection")
    except httpx.TimeoutException:
        connection_status.mark_unreachable(cluster_id, detail="Connection timed out")
        return TestResult(success=False, error="Connection timed out — ThoughtSpot is not responding")


class OrgOut(BaseModel):
    org_id: int
    name: str
    description: str = ""
    status: str = "ACTIVE"


@router.get("/{cluster_id}/orgs", response_model=list[OrgOut])
async def list_cluster_orgs(cluster_id: str) -> list[OrgOut]:
    """Fetch all orgs from a cluster live. Used to populate the org switcher."""
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.ts_client.exceptions import ConfigNotFoundError, TSAdminError

    try:
        config = load_config()
        cluster = config.clusters.get(cluster_id)
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Instance {cluster_id!r} not found")

        auth = cluster.build_auth_strategy()
        async with ThoughtSpotClient(url=cluster.url, auth=auth) as client:
            orgs = await client.search_orgs()

        # Replace cached orgs for this cluster so deletions in TS are reflected
        from sqlmodel import delete as sql_delete

        from ts_admin.database import get_session
        from ts_admin.models.cache.ts_org import CachedOrg

        with get_session() as session:
            session.exec(sql_delete(CachedOrg).where(CachedOrg.cluster_id == cluster_id))
            for o in orgs:
                session.add(
                    CachedOrg(
                        cluster_id=cluster_id,
                        ts_org_id=o.id,
                        name=o.name,
                        description=o.description or "",
                        status=o.status.value if hasattr(o.status, "value") else str(o.status),
                        is_primary=o.is_primary,
                    )
                )
            session.commit()

        return [
            OrgOut(
                org_id=o.id,
                name=o.name,
                description=o.description or "",
                status=o.status,
            )
            for o in orgs
        ]

    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TSAdminError as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/{cluster_id}/orgs/cached", response_model=list[OrgOut])
async def list_cluster_orgs_cached(cluster_id: str) -> list[OrgOut]:
    """Return orgs from the local SQLite cache. Used as fallback when cluster is offline."""
    from sqlmodel import select

    from ts_admin.database import get_session
    from ts_admin.models.cache.ts_org import CachedOrg

    with get_session() as session:
        rows = session.exec(select(CachedOrg).where(CachedOrg.cluster_id == cluster_id)).all()

    return [
        OrgOut(
            org_id=row.ts_org_id,
            name=row.name,
            description=row.description or "",
            status=row.status or "ACTIVE",
        )
        for row in rows
    ]


@router.post("/{cluster_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate_cluster(cluster_id: str) -> None:
    """Switch the active cluster."""
    from ts_admin.config import set_active_cluster
    from ts_admin.ts_client.exceptions import ConfigInvalidError

    try:
        set_active_cluster(cluster_id)
    except ConfigInvalidError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
