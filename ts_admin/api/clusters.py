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
    credential: str                     # password | secret_key | bearer token

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


class TestResult(BaseModel):
    success: bool
    ts_version: str | None = None
    error: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("", response_model=list[ClusterOut])
async def list_clusters() -> list[ClusterOut]:
    """Return all configured clusters with active cluster flagged."""
    from ts_admin.config import load_config
    from ts_admin.ts_client.exceptions import ConfigNotFoundError

    try:
        config = load_config()
    except ConfigNotFoundError:
        return []

    return [
        ClusterOut(
            id=c.id,
            name=c.name,
            url=c.url,
            username=c.username,
            auth_type=c.auth_type,
            is_active=(c.id == config.active_cluster_id),
        )
        for c in config.clusters.values()
    ]


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


@router.delete("/{cluster_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_cluster(cluster_id: str) -> None:
    """Remove a cluster and delete its keychain entry."""
    from ts_admin.config import delete_cluster
    delete_cluster(cluster_id)


@router.post("/{cluster_id}/test", response_model=TestResult)
async def test_cluster(cluster_id: str) -> TestResult:
    """Test the connection to a specific cluster."""
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    import httpx
    from ts_admin.ts_client.exceptions import ConfigNotFoundError, TSAdminError

    try:
        config = load_config()
        cluster = config.clusters.get(cluster_id)
        if not cluster:
            raise HTTPException(status_code=404, detail=f"Cluster {cluster_id!r} not found")

        auth = cluster.build_auth_strategy()
        async with ThoughtSpotClient(url=cluster.url, auth=auth) as client:
            info = await client.test_connection()

        return TestResult(success=True, ts_version=info.get("release_version"))

    except ConfigNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except TSAdminError as exc:
        return TestResult(success=False, error=str(exc))
    except httpx.HTTPStatusError as exc:
        return TestResult(success=False, error=f"HTTP {exc.response.status_code}: {exc.response.text[:200]}")
    except httpx.ConnectError:
        return TestResult(success=False, error="Cannot reach ThoughtSpot — check the URL and your network connection")
    except httpx.TimeoutException:
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
            raise HTTPException(status_code=404, detail=f"Cluster {cluster_id!r} not found")

        auth = cluster.build_auth_strategy()
        async with ThoughtSpotClient(url=cluster.url, auth=auth) as client:
            orgs = await client.search_orgs()

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


@router.post("/{cluster_id}/activate", status_code=status.HTTP_204_NO_CONTENT)
async def activate_cluster(cluster_id: str) -> None:
    """Switch the active cluster."""
    from ts_admin.config import set_active_cluster
    from ts_admin.ts_client.exceptions import ConfigInvalidError

    try:
        set_active_cluster(cluster_id)
    except ConfigInvalidError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
