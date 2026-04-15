"""
Health check endpoints.

GET /api/v1/health        — is the app running?
GET /api/v1/health/ts     — can we reach ThoughtSpot?
"""

import logging

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str


class TSHealthResponse(BaseModel):
    status: str
    cluster_url: str | None = None
    ts_version: str | None = None
    error: str | None = None


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Basic liveness check — confirms the app is running."""
    from ts_admin import __version__

    return HealthResponse(status="ok", version=__version__)


@router.get("/health/ts", response_model=TSHealthResponse)
async def ts_health() -> TSHealthResponse:
    """
    Test the connection to the active ThoughtSpot cluster.
    Used by the setup screen's 'Test connection' button and the
    connection status indicator in the top nav.
    """
    from ts_admin.config import load_config
    from ts_admin.ts_client import ThoughtSpotClient
    from ts_admin.ts_client.exceptions import ConfigNotFoundError, TSAdminError

    try:
        config = load_config()
        cluster = config.active_cluster
        auth = cluster.build_auth_strategy()

        async with ThoughtSpotClient(url=cluster.url, auth=auth) as client:
            info = await client.test_connection()

        return TSHealthResponse(
            status="connected",
            cluster_url=cluster.url,
            ts_version=info.get("release_version"),
        )

    except ConfigNotFoundError:
        return TSHealthResponse(status="not_configured")

    except TSAdminError as exc:
        logger.warning("TS health check failed: %s", exc)
        return TSHealthResponse(status="error", error=str(exc))
