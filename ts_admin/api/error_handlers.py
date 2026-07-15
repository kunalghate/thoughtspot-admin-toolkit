"""
Global FastAPI exception handlers.

Before this existed, a `TSAdminError` raised inside a router (and not caught
locally) bubbled up to Starlette's default handler and became an opaque
``500 Internal Server Error`` with no useful body. Endpoints each had to
re-catch and re-map the same exceptions, and most didn't.

These handlers give every uncaught domain error one consistent JSON shape::

    {
      "detail": "<short title>",      # human-readable, shown in the UI
      "hint":   "<what to do next>",  # actionable next step
      "error_type": "TSAuthenticationError"
    }

Title + hint come from :mod:`ts_admin.services.error_formatter`, the same
mapping the /jobs UI already uses — so an error reads identically whether it
surfaces from a live request or a background job.

As a side effect, an auth failure (401) flips the active cluster's live health
to EXPIRED in :mod:`ts_admin.services.connection_status`, so the "Connected"
badge stops lying the moment a request is rejected.

Routers that already catch and re-raise as ``HTTPException`` are unaffected —
``HTTPException`` has its own handler and never reaches these.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ts_admin.services import connection_status
from ts_admin.services.error_formatter import format_error
from ts_admin.ts_client.exceptions import (
    ConfigInvalidError,
    ConfigNotFoundError,
    KeyringError,
    TSAdminError,
    TSAuthenticationError,
    TSConnectionError,
    TSInsufficientPrivilegesError,
    TSInvalidParametersError,
    TSObjectNotFoundError,
    TSPartialSuccessError,
    TSRateLimitError,
    TSResponseParseError,
    TSServerError,
    TSSSLError,
    TSTimeoutError,
)

logger = logging.getLogger(__name__)

# Map each domain error to the HTTP status it should surface as. Anything not
# listed (including the base TSAdminError) falls back to 500.
_STATUS_BY_TYPE: tuple[tuple[type[TSAdminError], int], ...] = (
    (TSAuthenticationError, 401),
    (TSInsufficientPrivilegesError, 403),
    (TSObjectNotFoundError, 404),
    (TSInvalidParametersError, 400),
    (TSPartialSuccessError, 207),
    (TSRateLimitError, 429),
    # Upstream (ThoughtSpot) is unreachable or misbehaving → 502 Bad Gateway.
    (TSConnectionError, 502),
    (TSSSLError, 502),
    (TSTimeoutError, 504),
    (TSServerError, 502),
    (TSResponseParseError, 502),
    # Local misconfiguration the admin must fix.
    (ConfigNotFoundError, 400),
    (ConfigInvalidError, 400),
    (KeyringError, 500),
)


def _status_for(exc: TSAdminError) -> int:
    for cls, status in _STATUS_BY_TYPE:
        if isinstance(exc, cls):
            return status
    return 500


def _mark_active_cluster_expired(detail: str) -> None:
    """Flip the active cluster to EXPIRED so the UI badge reflects the dead session.

    Best-effort: most live operations target the active cluster, so this is the
    right cluster the overwhelming majority of the time. Background jobs that
    know their exact cluster_id mark it directly (see sync_service).
    """
    try:
        from ts_admin.config import load_config

        config = load_config()
    except ConfigNotFoundError:
        return
    if config.active_cluster_id:
        connection_status.mark_expired(config.active_cluster_id, detail=detail)


def register_exception_handlers(app: FastAPI) -> None:
    """Attach domain + catch-all exception handlers to the app."""

    @app.exception_handler(TSAdminError)
    async def _handle_ts_admin_error(_request: Request, exc: TSAdminError) -> JSONResponse:
        formatted = format_error(exc)
        status_code = _status_for(exc)

        if isinstance(exc, TSAuthenticationError):
            _mark_active_cluster_expired(str(exc))

        # 5xx is our fault / upstream's fault — log with stack. 4xx is the
        # caller's input and doesn't warrant noise.
        if status_code >= 500:
            logger.exception("Unhandled %s → HTTP %d", formatted.error_type, status_code)
        else:
            logger.info("%s → HTTP %d: %s", formatted.error_type, status_code, exc)

        return JSONResponse(
            status_code=status_code,
            content={
                "detail": formatted.title,
                "hint": formatted.hint,
                "error_type": formatted.error_type,
            },
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(_request: Request, exc: Exception) -> JSONResponse:
        # Truly unexpected — a bug, not a known failure mode. Never leak the raw
        # message to the client; log it server-side with a stack trace.
        logger.exception("Unhandled %s", type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content={
                "detail": "Something went wrong",
                "hint": "Check the server logs, or download the support bundle from Settings → Diagnostics.",
                "error_type": type(exc).__name__,
            },
        )
