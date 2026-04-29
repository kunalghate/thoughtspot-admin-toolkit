"""
Translate raw exceptions into user-readable copy for the /jobs UI.

Each known exception class maps to a (title, hint) pair. Unknown exceptions
fall through to a generic message that nudges the user toward downloading a
support bundle. The technical detail (str(exc)) is always preserved on the
Job row, so power users can still drill into the original message.
"""

from __future__ import annotations

from dataclasses import dataclass

from ts_admin.ts_client.exceptions import (
    ConfigInvalidError,
    ConfigNotFoundError,
    JobInterruptedError,
    KeyringError,
    TMLConflictError,
    TMLDependencyError,
    TMLValidationError,
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


@dataclass(frozen=True)
class FormattedError:
    title: str
    hint: str
    error_type: str

    @property
    def display(self) -> str:
        """Single-line string for the /jobs grid."""
        return f"{self.title} — {self.hint}"


_MAPPINGS: tuple[tuple[type[BaseException], str, str], ...] = (
    (TSAuthenticationError, "ThoughtSpot login expired",
     "Reconnect this cluster from Settings → Clusters."),
    (TSInsufficientPrivilegesError, "Not enough ThoughtSpot privileges",
     "The signed-in user is missing a required privilege for this action."),
    (TSTimeoutError, "ThoughtSpot didn't respond in time",
     "The cluster may be slow or under load. Try again, or run with smaller batches."),
    (TSRateLimitError, "ThoughtSpot rate-limited the request",
     "Wait a minute and retry, or run with smaller batches."),
    (TSSSLError, "TLS/SSL certificate problem reaching ThoughtSpot",
     "Verify the cluster URL uses https and the certificate chain is trusted."),
    (TSConnectionError, "Couldn't reach ThoughtSpot",
     "Check the cluster URL and your network."),
    (TSServerError, "ThoughtSpot returned a server error",
     "ThoughtSpot is failing internally. Wait a moment and retry, or check the cluster status."),
    (TSResponseParseError, "Unexpected response from ThoughtSpot",
     "Possibly a version mismatch. Download the support bundle and send it to support."),
    (TSObjectNotFoundError, "Object not found on ThoughtSpot",
     "The object may have been deleted or moved since the local cache was synced. Run a sync and retry."),
    (TSInvalidParametersError, "Request rejected by ThoughtSpot",
     "ThoughtSpot did not accept the request parameters. See details below."),
    (TSPartialSuccessError, "Operation partially succeeded",
     "Some items completed, others failed. Open the job for the per-item breakdown."),
    (TMLValidationError, "TML validation failed",
     "Fix the listed TML errors before retrying the import."),
    (TMLConflictError, "TML import conflict",
     "An object with the same identifier already exists. Rename, replace, or skip it."),
    (TMLDependencyError, "TML import is missing a dependency",
     "Import the referenced object first, or include it in the same TML bundle."),
    (KeyringError, "Couldn't read credentials from the OS keychain",
     "Reopen the keychain, or reconnect this cluster from Settings → Clusters."),
    (ConfigNotFoundError, "No ThoughtSpot cluster configured",
     "Add a cluster from Settings → Clusters before running this action."),
    (ConfigInvalidError, "Cluster configuration is invalid",
     "Re-enter the cluster details from Settings → Clusters."),
    (JobInterruptedError, "Job was interrupted",
     "The job did not finish. Restart it; already-completed items are tracked and won't be redone."),
)


def format_error(exc: BaseException) -> FormattedError:
    """Map an exception to a human-readable title + hint.

    Falls back to a generic message if the exception class isn't known.
    The exception's class name is always returned in `error_type`.
    """
    error_type = type(exc).__name__

    # httpx connection errors live outside our typed hierarchy — string-match
    # to avoid an httpx import cycle here.
    module = type(exc).__module__
    if module.startswith("httpx") and "ConnectError" in error_type:
        return FormattedError(
            title="Couldn't reach ThoughtSpot",
            hint="Check the cluster URL and your network.",
            error_type=error_type,
        )

    for cls, title, hint in _MAPPINGS:
        if isinstance(exc, cls):
            return FormattedError(title=title, hint=hint, error_type=error_type)

    return FormattedError(
        title="Something went wrong",
        hint="Open the job for details, or download the support bundle from Settings → Diagnostics.",
        error_type=error_type,
    )
