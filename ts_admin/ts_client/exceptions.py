"""
Named exception hierarchy for ThoughtSpot API errors.

Every exception has a specific name. Never catch the base Exception class.
"""


class TSAdminError(Exception):
    """Base class for all ts-admin-toolkit errors."""


# ── Configuration errors ───────────────────────────────────────────────────────


class ConfigNotFoundError(TSAdminError):
    """No cluster configuration exists. Admin must complete setup first."""


class ConfigInvalidError(TSAdminError):
    """Configuration file exists but is malformed or missing required fields."""


class KeyringError(TSAdminError):
    """OS keychain is unavailable or credential retrieval failed."""


# ── Connection errors ──────────────────────────────────────────────────────────


class TSConnectionError(TSAdminError):
    """Cannot reach the ThoughtSpot instance (DNS failure, network issue)."""


class TSSSLError(TSAdminError):
    """TLS/SSL certificate error when connecting to ThoughtSpot."""


class TSTimeoutError(TSAdminError):
    """ThoughtSpot did not respond within the timeout window."""


# ── Authentication errors ──────────────────────────────────────────────────────


class TSAuthenticationError(TSAdminError):
    """Credentials are wrong or the session has expired (HTTP 401)."""


class TSInsufficientPrivilegesError(TSAdminError):
    """The authenticated user lacks the required privilege (HTTP 403)."""


# ── API errors ─────────────────────────────────────────────────────────────────


class TSObjectNotFoundError(TSAdminError):
    """The requested ThoughtSpot object does not exist (HTTP 404)."""

    def __init__(self, object_type: str, identifier: str, detail: str = "") -> None:
        self.object_type = object_type
        self.identifier = identifier
        # ThoughtSpot puts the *reason* for a 404 in the response body (an unknown
        # org identifier, a missing GUID). Without it a support bundle only shows
        # the path, which is never enough to tell those apart.
        self.detail = detail
        msg = f"{object_type} not found: {identifier!r}"
        if detail:
            msg = f"{msg} — {detail}"
        super().__init__(msg)


class TSRateLimitError(TSAdminError):
    """ThoughtSpot is rate limiting requests (HTTP 429). Retry after backoff."""

    def __init__(self, retry_after: int | None = None) -> None:
        self.retry_after = retry_after
        msg = "Rate limited by ThoughtSpot"
        if retry_after:
            msg += f" — retry after {retry_after}s"
        super().__init__(msg)


class TSServerError(TSAdminError):
    """ThoughtSpot returned an internal server error (HTTP 5xx)."""

    def __init__(self, status_code: int, body: str) -> None:
        self.status_code = status_code
        self.body = body
        super().__init__(f"ThoughtSpot server error {status_code}: {body[:200]}")


class TSInvalidParametersError(TSAdminError):
    """Request parameters were rejected by ThoughtSpot (HTTP 400)."""


class TSResponseParseError(TSAdminError):
    """ThoughtSpot returned a response that could not be parsed."""

    def __init__(self, url: str, detail: str) -> None:
        self.url = url
        super().__init__(f"Failed to parse response from {url}: {detail}")


# ── Partial success ────────────────────────────────────────────────────────────


class TSPartialSuccessError(TSAdminError):
    """
    A bulk operation completed but some items failed.
    Returns HTTP 207. The caller receives both succeeded and failed items.
    """

    def __init__(self, succeeded: list, failed: list) -> None:
        self.succeeded = succeeded
        self.failed = failed
        super().__init__(f"Partial success: {len(succeeded)} succeeded, {len(failed)} failed")


# ── TML errors ─────────────────────────────────────────────────────────────────


class TMLValidationError(TSAdminError):
    """TML content failed validation before import."""

    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__(f"TML validation failed: {'; '.join(errors)}")


class TMLConflictError(TSAdminError):
    """TML import failed due to an FQN collision with an existing object."""


class TMLDependencyError(TSAdminError):
    """TML import failed because a referenced dependency does not exist."""


# ── Local cache errors ─────────────────────────────────────────────────────────


class StaleCacheError(TSAdminError):
    """
    The local cache for `entity_type` is not certified complete.

    `_sync_metadata` deletes every row for the org and then re-pages in spec
    order, committing per page — so an interrupted metadata sync leaves a
    non-empty but TRUNCATED cache (liveboards + answers present, models and
    tables missing). Row counts cannot distinguish that from a healthy cache;
    only the `sync_log` row can. Operations whose *input set* is read from the
    cache must refuse rather than silently act on a subset.
    """

    def __init__(self, entity_type: str, status: str) -> None:
        self.entity_type = entity_type
        self.status = status
        super().__init__(
            f"The local {entity_type} cache is not complete (last sync status: {status}). "
            f"Run a {entity_type} sync before continuing."
        )


# ── Job errors ─────────────────────────────────────────────────────────────────


class JobInterruptedError(TSAdminError):
    """A background job was interrupted before completing."""

    def __init__(self, job_id: str, completed: int, total: int) -> None:
        self.job_id = job_id
        self.completed = completed
        self.total = total
        super().__init__(f"Job {job_id} interrupted at {completed}/{total} items")
