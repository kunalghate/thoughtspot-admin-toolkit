"""
ThoughtSpotClient — thin HTTP wrapper around the ThoughtSpot REST API v2.

Rules:
  - No business logic here. Orchestration belongs in services/.
  - Every method maps to one or more TS API endpoints.
  - All responses are parsed into Pydantic models before returning.
  - All errors are mapped to named exceptions from exceptions.py.

Usage:
    client = ThoughtSpotClient(url="https://co.thoughtspot.cloud", auth=BasicAuth(...))
    users = await client.search_users()
"""

import logging
from collections.abc import AsyncIterator
from typing import Any

import httpx
from pydantic import ValidationError

from ts_admin.ts_client.auth import AuthStrategy
from ts_admin.ts_client.exceptions import (
    TSAuthenticationError,
    TSConnectionError,
    TSInsufficientPrivilegesError,
    TSInvalidParametersError,
    TSObjectNotFoundError,
    TSResponseParseError,
    TSSSLError,
    TSServerError,
)
from ts_admin.ts_client.models import (
    MetadataType,
    SharePermission,
    TSGroup,
    TSMetadataObject,
    TSOrg,
    TSPermission,
    TSTag,
    TSTokenResponse,
    TSUser,
)
from ts_admin.ts_client.retry import with_retry

logger = logging.getLogger(__name__)

# Maximum records per paginated API request
PAGE_SIZE = 500


class ThoughtSpotClient:
    """
    Async HTTP client for the ThoughtSpot REST API v2.

    All methods are async and must be called inside an async context.
    The client manages its own httpx.AsyncClient lifecycle.

    Diagram — request flow:
        caller
          │
          ▼
        method (e.g. search_users)
          │  builds request params
          ▼
        _request()
          │  adds auth headers, calls with_retry()
          ▼
        httpx.AsyncClient.request()
          │
          ▼
        _handle_error()   ← maps HTTP errors to named exceptions
          │
          ▼
        Pydantic model    ← parsed response returned to caller
    """

    def __init__(self, url: str, auth: AuthStrategy, timeout: float = 30.0) -> None:
        self._base_url = url.rstrip("/")
        self._auth = auth
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=timeout,
            follow_redirects=True,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "ThoughtSpotClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── Internal request helper ────────────────────────────────────────────────

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        context: str = "",
    ) -> Any:
        """Make an authenticated request with retry logic."""

        async def _do_request() -> Any:
            try:
                auth_headers = await self._auth.get_headers(self._http)
            except TSAuthenticationError:
                raise

            try:
                response = await self._http.request(
                    method,
                    path,
                    json=json,
                    params=params,
                    headers=auth_headers,
                )
            except httpx.ConnectError as exc:
                # ConnectError covers both TCP connection failures and TLS/SSL errors
                # in httpx >= 0.28 (SSLError was removed as a separate class)
                msg = str(exc)
                if "ssl" in msg.lower() or "tls" in msg.lower() or "certificate" in msg.lower():
                    raise TSSSLError(f"TLS error connecting to ThoughtSpot: {exc}") from exc
                raise TSConnectionError(
                    f"Cannot reach ThoughtSpot at {self._base_url}: {exc}"
                ) from exc

            if response.status_code == 401:
                self._auth.invalidate()
                raise TSAuthenticationError(
                    "ThoughtSpot rejected credentials — session may have expired"
                )
            if response.status_code == 403:
                raise TSInsufficientPrivilegesError(
                    f"Insufficient privileges for {method} {path}"
                )
            if response.status_code == 400:
                raise TSInvalidParametersError(
                    f"Invalid parameters for {method} {path}: {response.text[:200]}"
                )
            if response.status_code == 404:
                raise TSObjectNotFoundError(object_type="resource", identifier=path)
            if response.status_code >= 500:
                raise TSServerError(
                    status_code=response.status_code, body=response.text
                )

            response.raise_for_status()

            try:
                return response.json()
            except Exception as exc:
                raise TSResponseParseError(url=path, detail=str(exc)) from exc

        log_context = context or f"{method} {path}"
        logger.debug("→ %s %s", method, path)
        result = await with_retry(_do_request, context=log_context)
        logger.debug("← %s %s OK", method, path)
        return result

    # ── Paginated helper ───────────────────────────────────────────────────────

    async def _paginate(
        self,
        path: str,
        body: dict,
        result_key: str,
        *,
        context: str = "",
    ) -> AsyncIterator[list]:
        """
        Yield pages of results from a paginated ThoughtSpot API endpoint.

        ThoughtSpot uses record_offset + record_size for pagination.
        Stops when a page returns fewer records than PAGE_SIZE.
        """
        offset = 0
        while True:
            data = await self._request(
                "POST",
                path,
                json={**body, "record_offset": offset, "record_size": PAGE_SIZE},
                context=context,
            )
            page: list = data.get(result_key, [])
            if not page:
                break
            yield page
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE

    # ── Connection test ────────────────────────────────────────────────────────

    async def test_connection(self) -> dict:
        """
        Verify credentials and return basic instance info.
        Used by the setup screen's "Test connection" button.
        """
        data = await self._request(
            "GET",
            "/api/rest/2.0/system",
            context="test connection",
        )
        return {
            "release_version": data.get("release_version", "unknown"),
            "url": self._base_url,
        }

    # ── Users ──────────────────────────────────────────────────────────────────

    async def search_users(
        self,
        *,
        org_id: int | None = None,
    ) -> AsyncIterator[list[TSUser]]:
        """
        Yield pages of all users on the cluster (or within a specific org).
        """
        body: dict = {"user_identifier": "", "include_favorite_metadata": False}
        if org_id is not None:
            body["org_identifiers"] = [org_id]

        async for page in self._paginate(
            "/api/rest/2.0/users/search",
            body,
            result_key="users",
            context="search_users",
        ):
            try:
                yield [TSUser.model_validate(u) for u in page]
            except ValidationError as exc:
                raise TSResponseParseError(
                    url="/api/rest/2.0/users/search",
                    detail=str(exc),
                ) from exc

    # ── Groups ─────────────────────────────────────────────────────────────────

    async def search_groups(
        self,
        *,
        org_id: int | None = None,
    ) -> AsyncIterator[list[TSGroup]]:
        """Yield pages of all user groups."""
        body: dict = {"group_identifier": ""}
        if org_id is not None:
            body["org_identifiers"] = [org_id]

        async for page in self._paginate(
            "/api/rest/2.0/groups/search",
            body,
            result_key="user_groups",
            context="search_groups",
        ):
            try:
                yield [TSGroup.model_validate(g) for g in page]
            except ValidationError as exc:
                raise TSResponseParseError(
                    url="/api/rest/2.0/groups/search",
                    detail=str(exc),
                ) from exc

    # ── Metadata ───────────────────────────────────────────────────────────────

    async def search_metadata(
        self,
        *,
        org_id: int | None = None,
    ) -> AsyncIterator[list[TSMetadataObject]]:
        """Yield pages of all content objects (Liveboards, Answers, Worksheets, Tables).

        The TS API type=LOGICAL_TABLE covers both worksheets and physical tables.
        We make separate requests per subtype so we can stamp each result with the
        correct effective type (WORKSHEET vs ONE_TO_ONE_LOGICAL).

        /api/rest/2.0/metadata/search returns a list directly, so we paginate manually.
        """
        # Each spec: (api_type, subtypes_filter, effective_type_to_store)
        specs = [
            ("LIVEBOARD",     None,                    MetadataType.LIVEBOARD),
            ("ANSWER",        None,                    MetadataType.ANSWER),
            ("LOGICAL_TABLE", ["WORKSHEET"],           MetadataType.WORKSHEET),
            ("LOGICAL_TABLE", ["ONE_TO_ONE_LOGICAL"],  MetadataType.ONE_TO_ONE_LOGICAL),
            ("LOGICAL_TABLE", ["AGGR_WORKSHEET"],      MetadataType.AGGR_WORKSHEET),
            ("LOGICAL_TABLE", ["SQL_VIEW"],            MetadataType.SQL_VIEW),
            ("LOGICAL_TABLE", ["USER_DEFINED"],        MetadataType.USER_DEFINED),
        ]

        for api_type, subtypes, effective_type in specs:
            metadata_filter: dict = {"type": api_type}
            if subtypes:
                metadata_filter["subtypes"] = subtypes

            body: dict = {"metadata": [metadata_filter]}
            if org_id is not None:
                body["org_identifiers"] = [org_id]

            offset = 0
            while True:
                data = await self._request(
                    "POST",
                    "/api/rest/2.0/metadata/search",
                    json={**body, "record_offset": offset, "record_size": PAGE_SIZE},
                    context="search_metadata",
                )
                page: list = data if isinstance(data, list) else data.get("metadata_details", [])
                if not page:
                    break
                # Stamp each item with the effective type so the model stores it correctly
                for item in page:
                    item["metadata_type"] = effective_type
                try:
                    yield [TSMetadataObject.model_validate(m) for m in page]
                except ValidationError as exc:
                    raise TSResponseParseError(
                        url="/api/rest/2.0/metadata/search",
                        detail=str(exc),
                    ) from exc
                if len(page) < PAGE_SIZE:
                    break
                offset += PAGE_SIZE

    # ── Tags ───────────────────────────────────────────────────────────────────

    async def search_tags(self) -> list[TSTag]:
        """Return all tags defined on the instance."""
        data = await self._request(
            "POST",
            "/api/rest/2.0/tags/search",
            json={},
            context="search_tags",
        )
        try:
            return [TSTag.model_validate(t) for t in data.get("tags", [])]
        except ValidationError as exc:
            raise TSResponseParseError(
                url="/api/rest/2.0/tags/search", detail=str(exc)
            ) from exc

    async def assign_tag(self, *, object_ids: list[str], tag_id: str) -> None:
        """Assign a tag to one or more objects."""
        await self._request(
            "POST",
            "/api/rest/2.0/tags/assign",
            json={"metadata": [{"identifier": oid} for oid in object_ids], "tag_identifiers": [tag_id]},
            context="assign_tag",
        )

    async def unassign_tag(self, *, object_ids: list[str], tag_id: str) -> None:
        """Remove a tag from one or more objects."""
        await self._request(
            "POST",
            "/api/rest/2.0/tags/unassign",
            json={"metadata": [{"identifier": oid} for oid in object_ids], "tag_identifiers": [tag_id]},
            context="unassign_tag",
        )

    # ── Orgs ───────────────────────────────────────────────────────────────────

    async def search_orgs(self) -> list[TSOrg]:
        """Return all orgs on the cluster. Returns [primary org] if no orgs feature."""
        data = await self._request(
            "POST",
            "/api/rest/2.0/orgs/search",
            json={},
            context="search_orgs",
        )
        try:
            # TS API returns a plain list, not {"orgs": [...]}
            items = data if isinstance(data, list) else data.get("orgs", [])
            return [TSOrg.model_validate(o) for o in items]
        except ValidationError as exc:
            raise TSResponseParseError(
                url="/api/rest/2.0/orgs/search", detail=str(exc)
            ) from exc

    # ── Sharing ────────────────────────────────────────────────────────────────

    async def share_objects(
        self,
        *,
        object_ids: list[str],
        principal_ids: list[str],
        permission: SharePermission = SharePermission.READ_ONLY,
    ) -> None:
        """
        Share a list of objects with a list of users or groups.
        Raises TSPartialSuccessError if some objects fail.
        """
        from ts_admin.ts_client.exceptions import TSPartialSuccessError

        await self._request(
            "POST",
            "/api/rest/2.0/security/share",
            json={
                "metadata_list": [{"identifier": oid} for oid in object_ids],
                "permissions": [
                    {"principal": {"identifier": pid}, "share_mode": permission}
                    for pid in principal_ids
                ],
            },
            context="share_objects",
        )

    # ── Permissions ────────────────────────────────────────────────────────────

    # Subtypes that the permissions API doesn't know about — map them to LOGICAL_TABLE
    _PERMISSIONS_TYPE_MAP: dict[str, str] = {
        "WORKSHEET":          "LOGICAL_TABLE",
        "ONE_TO_ONE_LOGICAL": "LOGICAL_TABLE",
        "AGGR_WORKSHEET":     "LOGICAL_TABLE",
        "SQL_VIEW":           "LOGICAL_TABLE",
        "USER_DEFINED":       "LOGICAL_TABLE",
    }

    async def fetch_permissions(
        self,
        *,
        ts_guid: str,
        object_type: str,
    ) -> list[TSPermission]:
        """
        Fetch all users and groups that have access to a metadata object.

        Calls POST /api/rest/2.0/security/metadata/fetch-permissions live —
        results are never cached in SQLite.

        object_type may be a subtype (e.g. WORKSHEET); it's mapped to the
        TS API type (LOGICAL_TABLE) automatically.
        """
        api_type = self._PERMISSIONS_TYPE_MAP.get(object_type, object_type)

        data = await self._request(
            "POST",
            "/api/rest/2.0/security/metadata/fetch-permissions",
            json={
                "metadata": [{"identifier": ts_guid, "type": api_type}],
                "record_size": -1,
                "permission_type": "DEFINED",   # explicitly shared only, matches TS UI
            },
            context="fetch_permissions",
        )

        permissions: list[TSPermission] = []
        # Response: {"metadata_permission_details": [{
        #   "principal_permission_info": [{
        #     "principal_type": "USER"|"USER_GROUP",
        #     "principal_permissions": [{
        #       "principal_id", "principal_name", "permission": "READ_ONLY"|"MODIFY"|"NO_ACCESS"
        #     }]
        #   }]
        # }]}
        details = data.get("metadata_permission_details") or [] if isinstance(data, dict) else []
        for item in details:
            for group in (item.get("principal_permission_info") or []):
                principal_type = group.get("principal_type", "USER")
                for entry in (group.get("principal_permissions") or []):
                    share_mode = entry.get("permission", "")
                    if not share_mode or share_mode == "NO_ACCESS":
                        continue
                    permissions.append(TSPermission(
                        principal_id=entry.get("principal_id", ""),
                        principal_name=entry.get("principal_name", ""),
                        principal_type=principal_type,
                        share_mode=share_mode,
                    ))
        return permissions

    # ── Metadata deletion ──────────────────────────────────────────────────────

    async def delete_metadata(self, *, object_ids: list[str], object_type: MetadataType) -> None:
        """Permanently delete metadata objects. Irreversible."""
        await self._request(
            "DELETE",
            "/api/rest/2.0/metadata/delete",
            json={
                "metadata": [
                    {"identifier": oid, "type": object_type}
                    for oid in object_ids
                ]
            },
            context="delete_metadata",
        )
