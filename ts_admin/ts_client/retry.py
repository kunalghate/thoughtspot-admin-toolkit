"""
Retry logic with exponential backoff for ThoughtSpot API calls.

Handles:
  - HTTP 429 (rate limited) — backs off and retries up to MAX_RETRIES times
  - HTTP 5xx (server error) — retries once after a short delay
  - httpx.TimeoutException  — retries up to MAX_RETRIES times
"""

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

import httpx

from ts_admin.ts_client.exceptions import TSRateLimitError, TSServerError, TSTimeoutError

logger = logging.getLogger(__name__)

T = TypeVar("T")

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 1.0


async def with_retry(
    operation: Callable[[], Coroutine[Any, Any, T]],
    *,
    context: str = "API call",
) -> T:
    """
    Execute an async operation with exponential backoff retry.

    Args:
        operation: Async callable to retry (takes no arguments).
        context:   Human-readable description logged on each retry.

    Returns:
        The result of the operation on success.

    Raises:
        TSRateLimitError: After MAX_RETRIES exhausted on 429.
        TSTimeoutError:   After MAX_RETRIES exhausted on timeout.
        TSServerError:    After 1 retry on 5xx.
    """
    last_exception: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return await operation()

        except httpx.TimeoutException as exc:
            last_exception = TSTimeoutError(
                f"ThoughtSpot did not respond ({context})"
            )
            wait = BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
            logger.warning(
                "Timeout on attempt %d/%d for %s — retrying in %.1fs",
                attempt, MAX_RETRIES, context, wait,
            )
            if attempt < MAX_RETRIES:
                await asyncio.sleep(wait)

        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                retry_after = int(exc.response.headers.get("Retry-After", 0)) or None
                last_exception = TSRateLimitError(retry_after=retry_after)
                wait = retry_after or BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))
                logger.warning(
                    "Rate limited on attempt %d/%d for %s — retrying in %.1fs",
                    attempt, MAX_RETRIES, context, wait,
                )
                if attempt < MAX_RETRIES:
                    await asyncio.sleep(wait)

            elif exc.response.status_code >= 500:
                last_exception = TSServerError(
                    status_code=exc.response.status_code,
                    body=exc.response.text,
                )
                logger.warning(
                    "Server error %d on attempt %d/%d for %s — retrying once",
                    exc.response.status_code, attempt, MAX_RETRIES, context,
                )
                if attempt == 1:
                    await asyncio.sleep(BASE_BACKOFF_SECONDS)
                else:
                    break
            else:
                # 4xx errors are not retryable — raise immediately
                raise

    raise last_exception  # type: ignore[misc]
