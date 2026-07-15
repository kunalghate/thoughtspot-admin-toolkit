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
    TSServerError,
    TSSSLError,
)
from ts_admin.ts_client.models import (
    MetadataType,
    SharePermission,
    TSGroup,
    TSMetadataObject,
    TSOrg,
    TSPermission,
    TSTag,
    TSUser,
)
from ts_admin.ts_client.retry import with_retry

logger = logging.getLogger(__name__)

# Maximum records per paginated API request
PAGE_SIZE = 500


# Cached `object_type` values → the `type` enum metadata/search accepts.
# All tabular subtypes collapse to LOGICAL_TABLE; leaves keep their own type.
_SEARCH_TYPE_FOR_DEPENDENTS: dict[str, str] = {
    "LIVEBOARD": "LIVEBOARD",
    "ANSWER": "ANSWER",
    "WORKSHEET": "LOGICAL_TABLE",
    "ONE_TO_ONE_LOGICAL": "LOGICAL_TABLE",
    "AGGR_WORKSHEET": "LOGICAL_TABLE",
    "SQL_VIEW": "LOGICAL_TABLE",
    "USER_DEFINED": "LOGICAL_TABLE",
    "TABLE": "LOGICAL_TABLE",
    "MODEL": "LOGICAL_TABLE",
    "VIEW": "LOGICAL_TABLE",
    "LOGICAL_TABLE": "LOGICAL_TABLE",
    "CONNECTION": "CONNECTION",
}


def _flatten_dependent_objects(items: list[dict], *, root_guid: str) -> list[dict]:
    """
    Walk a metadata/search response and pull dependents for `root_guid`.

    `dependent_objects` is documented as `object` only — observed shapes are
    `{<root_guid>: {<TYPE>: [{id,name,...}, ...]}}` and the un-keyed
    `{<TYPE>: [...]}` variant. Both are accepted; anything that quacks like
    a dependent dict (has id/identifier/guid) is collected.

    The grouping key (`<TYPE>`, e.g. `PINBOARD_ANSWER_BOOK`, `QUESTION_ANSWER_BOOK`)
    is the authoritative type: against a live cluster the individual items carry
    `type: null`, so the group key is the *only* type signal. We stamp it onto
    each item (when the item has no usable `type` of its own) so callers can
    classify the dependent instead of guessing.
    """
    out: list[dict] = []
    for item in items:
        if item.get("metadata_id") and item["metadata_id"] != root_guid:
            continue
        blob = item.get("dependent_objects") or {}
        if not isinstance(blob, dict):
            continue
        # Strip the optional outer GUID layer.
        if root_guid in blob and isinstance(blob[root_guid], dict):
            blob = blob[root_guid]
        for type_key, value in blob.items():
            if not isinstance(value, list):
                continue
            for dep in value:
                if isinstance(dep, dict) and (dep.get("id") or dep.get("identifier") or dep.get("guid")):
                    if not dep.get("type") and isinstance(type_key, str):
                        dep = {**dep, "type": type_key}
                    out.append(dep)
    return out


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
                raise TSConnectionError(f"Cannot reach ThoughtSpot at {self._base_url}: {exc}") from exc

            if response.status_code == 401:
                self._auth.invalidate()
                raise TSAuthenticationError("ThoughtSpot rejected credentials — session may have expired")
            if response.status_code == 403:
                raise TSInsufficientPrivilegesError(f"Insufficient privileges for {method} {path}")
            if response.status_code == 400:
                raise TSInvalidParametersError(f"Invalid parameters for {method} {path}: {response.text[:200]}")
            if response.status_code == 404:
                raise TSObjectNotFoundError(object_type="resource", identifier=path)
            if response.status_code >= 500:
                raise TSServerError(status_code=response.status_code, body=response.text)

            response.raise_for_status()

            # 204 No Content — endpoints like assign_tag/unassign_tag return empty body
            if response.status_code == 204 or not response.content:
                return {}

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
            # TS REST v2 search endpoints (users/search, groups/search) return a
            # bare JSON array; older/other endpoints wrap results under `result_key`.
            # Accept both so a list response doesn't blow up on `.get()`.
            page: list = data if isinstance(data, list) else data.get(result_key, [])
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
    ) -> AsyncIterator[list[TSMetadataObject]]:
        """Yield pages of all content objects (Liveboards, Answers, Worksheets, Tables).

        Org context is determined by the token used to authenticate — pass org_id
        to build_auth_strategy() when constructing the client to scope to a specific org.

        The TS API type=LOGICAL_TABLE covers both worksheets and physical tables.
        We make separate requests per subtype so we can stamp each result with the
        correct effective type (WORKSHEET vs ONE_TO_ONE_LOGICAL).

        /api/rest/2.0/metadata/search returns a list directly, so we paginate manually.
        """
        # Each spec: (api_type, subtypes_filter, effective_type_to_store)
        specs = [
            ("LIVEBOARD", None, MetadataType.LIVEBOARD),
            ("ANSWER", None, MetadataType.ANSWER),
            ("LOGICAL_TABLE", ["WORKSHEET"], MetadataType.WORKSHEET),
            ("LOGICAL_TABLE", ["ONE_TO_ONE_LOGICAL"], MetadataType.ONE_TO_ONE_LOGICAL),
            ("LOGICAL_TABLE", ["AGGR_WORKSHEET"], MetadataType.AGGR_WORKSHEET),
            ("LOGICAL_TABLE", ["SQL_VIEW"], MetadataType.SQL_VIEW),
            ("LOGICAL_TABLE", ["USER_DEFINED"], MetadataType.USER_DEFINED),
        ]

        for api_type, subtypes, effective_type in specs:
            metadata_filter: dict = {"type": api_type}
            if subtypes:
                metadata_filter["subtypes"] = subtypes

            body: dict = {"metadata": [metadata_filter], "include_stats": True}

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
        """Return all tags visible in the current auth/org scope, paginated."""
        all_tags: list[TSTag] = []
        offset = 0
        while True:
            data = await self._request(
                "POST",
                "/api/rest/2.0/tags/search",
                json={"record_offset": offset, "record_size": PAGE_SIZE},
                context="search_tags",
            )
            try:
                items = data if isinstance(data, list) else data.get("tags", [])
                page = [TSTag.model_validate(t) for t in items]
            except ValidationError as exc:
                raise TSResponseParseError(url="/api/rest/2.0/tags/search", detail=str(exc)) from exc
            if not page:
                break
            all_tags.extend(page)
            if len(page) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return all_tags

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

    async def delete_tag(self, *, tag_id: str) -> None:
        """
        Permanently delete a tag from the cluster.

        Deleting a tag automatically removes it from every object it was
        assigned to (one API call instead of unassigning per-object first).
        Irreversible.
        """
        await self._request(
            "POST",
            "/api/rest/2.0/tags/delete",
            json={"tag": [{"identifier": tag_id}]},
            context="delete_tag",
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
            raise TSResponseParseError(url="/api/rest/2.0/orgs/search", detail=str(exc)) from exc

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

        await self._request(
            "POST",
            "/api/rest/2.0/security/share",
            json={
                "metadata_list": [{"identifier": oid} for oid in object_ids],
                "permissions": [{"principal": {"identifier": pid}, "share_mode": permission} for pid in principal_ids],
            },
            context="share_objects",
        )

    # ── Permissions ────────────────────────────────────────────────────────────

    # Subtypes that the permissions API doesn't know about — map them to LOGICAL_TABLE
    _PERMISSIONS_TYPE_MAP: dict[str, str] = {
        "WORKSHEET": "LOGICAL_TABLE",
        "ONE_TO_ONE_LOGICAL": "LOGICAL_TABLE",
        "AGGR_WORKSHEET": "LOGICAL_TABLE",
        "SQL_VIEW": "LOGICAL_TABLE",
        "USER_DEFINED": "LOGICAL_TABLE",
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
                "permission_type": "DEFINED",  # explicitly shared only, matches TS UI
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
            for group in item.get("principal_permission_info") or []:
                principal_type = group.get("principal_type", "USER")
                for entry in group.get("principal_permissions") or []:
                    share_mode = entry.get("permission", "")
                    if not share_mode or share_mode == "NO_ACCESS":
                        continue
                    permissions.append(
                        TSPermission(
                            principal_id=entry.get("principal_id", ""),
                            principal_name=entry.get("principal_name", ""),
                            principal_type=principal_type,
                            share_mode=share_mode,
                        )
                    )
        return permissions

    # ── Metadata deletion ──────────────────────────────────────────────────────

    async def delete_metadata(self, *, object_ids: list[str], object_type: MetadataType) -> None:
        """Permanently delete metadata objects. Irreversible."""
        await self._request(
            "POST",
            "/api/rest/2.0/metadata/delete",
            json={"metadata": [{"identifier": oid, "type": object_type} for oid in object_ids]},
            context="delete_metadata",
        )

    # ── TML export / import ────────────────────────────────────────────────────

    async def tml_export(
        self,
        *,
        object_ids: list[str],
        edoc_format: str | None = None,
    ) -> list[dict]:
        """
        Export TML for a batch of objects.

        POST /api/rest/2.0/metadata/tml/export

        Returns a list of dicts. A successful item has a non-empty "edoc" key
        containing the TML string. A failed item has no "edoc" key (or an
        empty string). Check `"edoc" in result and result["edoc"]` — do NOT
        check status_code; the v2 export API omits it on success.

        edoc_format (`"JSON"` | `"YAML"`, default server-side `JSON`) is injected
        into the request body ONLY when provided. The delete-backup path
        (deletion_service._execute_delete) calls this with no `edoc_format`, so
        its request body — and the `.tml` string it writes verbatim — stays
        byte-identical. The lineage path passes `edoc_format="JSON"` so the
        service can `json.loads` the edoc without a YAML parser.
        """
        body: dict = {
            "metadata": [{"identifier": oid} for oid in object_ids],
            "export_associated_objects": "NONE",
            "export_fqn": True,
        }
        if edoc_format is not None:
            body["edoc_format"] = edoc_format
        data = await self._request(
            "POST",
            "/api/rest/2.0/metadata/tml/export",
            json=body,
            context="tml_export",
        )
        return data if isinstance(data, list) else data.get("object", [])

    async def import_tml(
        self,
        *,
        tml_strings: list[str],
        import_policy: str = "PARTIAL",
    ) -> list[dict]:
        """
        Import TML strings as new objects.

        POST /api/rest/2.0/metadata/tml/import

        import_policy="PARTIAL": import what we can, surface per-item errors
        rather than failing the entire batch.

        Each returned item has:
          object_id (new GUID), name, type,
          status { status_code: "OK"|"ERROR", error_message: str|None }
        """
        data = await self._request(
            "POST",
            "/api/rest/2.0/metadata/tml/import",
            json={"metadata_tmls": tml_strings, "import_policy": import_policy},
            context="import_tml",
        )
        return data if isinstance(data, list) else data.get("object", [])

    # ── Tag creation ───────────────────────────────────────────────────────────

    async def create_tag(self, *, name: str, color: str = "") -> TSTag:
        """
        Create a new tag on the instance.
        POST /api/rest/2.0/tags
        """
        data = await self._request(
            "POST",
            "/api/rest/2.0/tags/create",
            json={"name": name, "color": color or None},
            context="create_tag",
        )
        try:
            return TSTag.model_validate(data)
        except ValidationError as exc:
            raise TSResponseParseError(url="/api/rest/2.0/tags", detail=str(exc)) from exc

    # ── Dependencies ───────────────────────────────────────────────────────────

    # ── User management ────────────────────────────────────────────────────────

    async def assign_metadata_owner(
        self,
        *,
        object_ids: list[str],
        new_owner_identifier: str,
    ) -> None:
        """
        Reassign ownership of one or more metadata objects to a new user.

        POST /api/rest/2.0/security/metadata/assign

        The objects themselves don't move — only the `author` field is rewritten.
        `new_owner_identifier` may be a username or a user GUID.
        """
        await self._request(
            "POST",
            "/api/rest/2.0/security/metadata/assign",
            json={
                "metadata": [{"identifier": oid} for oid in object_ids],
                "user_identifier": new_owner_identifier,
            },
            context="assign_metadata_owner",
        )

    async def principal_permissions(
        self,
        *,
        principal_identifier: str,
        metadata_types: list[str] | None = None,
    ) -> list[dict]:
        """
        Return everything a principal (user or group) has access to.

        POST /api/rest/2.0/security/principal/fetch-permissions

        Used by the User Management transfer-sharing flow: discover everything
        the leaving user could see, so we can re-share each item with the
        replacement at the same access level.

        Returns a flat list of:
          {metadata_id, metadata_name, metadata_type, share_mode}
        """
        body: dict = {
            "principal": [{"identifier": principal_identifier}],
            "record_size": -1,
            "permission_type": "DEFINED",
        }
        if metadata_types:
            body["metadata_type"] = metadata_types

        data = await self._request(
            "POST",
            "/api/rest/2.0/security/principal/fetch-permissions",
            json=body,
            context="principal_permissions",
        )

        out: list[dict] = []
        # Response shape: {"principal_permissions": [{
        #   "principal_id", "principal_name",
        #   "metadata_permission_details": [{
        #     "metadata_id", "metadata_name", "metadata_type",
        #     "share_mode": "READ_ONLY" | "MODIFY"
        #   }]
        # }]}
        principals = data.get("principal_permissions") or [] if isinstance(data, dict) else []
        for p in principals:
            for item in p.get("metadata_permission_details") or []:
                out.append(
                    {
                        "metadata_id": item.get("metadata_id", ""),
                        "metadata_name": item.get("metadata_name", ""),
                        "metadata_type": item.get("metadata_type", ""),
                        "share_mode": item.get("share_mode", "READ_ONLY"),
                    }
                )
        return out

    async def delete_users(self, *, user_identifiers: list[str]) -> None:
        """
        Permanently delete one or more users from the cluster.

        POST /api/rest/2.0/users/delete

        Each identifier may be a username or a user GUID. Irreversible — the
        user is removed from every org and every group. Owned content stays
        but becomes orphan-owned; transfer ownership first if you need to
        preserve attribution.
        """
        await self._request(
            "POST",
            "/api/rest/2.0/users/delete",
            json={"users": [{"identifier": uid} for uid in user_identifiers]},
            context="delete_users",
        )

    async def fetch_dependents(
        self,
        *,
        objects: list[dict],  # [{"identifier": guid, "type": "<cache type>"}, ...]
    ) -> dict[str, list[dict]]:
        """
        Return a map of { guid → [dependent objects] } for the given objects.

        Implemented via POST /api/rest/2.0/metadata/search with
        include_dependent_objects=True. (TS REST v2 has no standalone
        listdependents endpoint — the cluster returns 404 for that path.)

        Cache subtypes (WORKSHEET, AGGR_WORKSHEET, ONE_TO_ONE_LOGICAL,
        SQL_VIEW, USER_DEFINED, TABLE, MODEL, VIEW) all collapse to
        LOGICAL_TABLE for the search payload — that's the only valid type
        enum for tabular objects in metadata/search.
        """
        result: dict[str, list[dict]] = {}
        if not objects:
            return result

        for obj in objects:
            guid = obj.get("identifier", "")
            if not guid:
                continue
            api_type = _SEARCH_TYPE_FOR_DEPENDENTS.get(
                obj.get("type", "").upper(),
                "LOGICAL_TABLE",
            )
            data = await self._request(
                "POST",
                "/api/rest/2.0/metadata/search",
                json={
                    "metadata": [{"identifier": guid, "type": api_type}],
                    "include_dependent_objects": True,
                    "dependent_objects_record_size": 1000,
                    "include_headers": False,
                    "record_size": -1,
                    "record_offset": 0,
                },
                context="fetch_dependents",
            )
            items = data if isinstance(data, list) else data.get("metadata_details", [])
            try:
                result[guid] = _flatten_dependent_objects(items, root_guid=guid)
            except (KeyError, TypeError, AttributeError) as exc:
                raise TSResponseParseError(
                    url="/api/rest/2.0/metadata/search",
                    detail=f"Unexpected dependent_objects shape: {exc}",
                ) from exc
        return result

    async def search_dependents(
        self,
        *,
        object_ids: list[str],
        object_type: str,
        batch_size: int = 100,
    ) -> dict[str, list[dict]]:
        """
        Batched variant of fetch_dependents: one metadata/search call per
        `batch_size` GUIDs instead of one call per object.

        `object_type` is the metadata/search `type` enum shared by every GUID in
        the batch — callers pass a single homogeneous type (e.g. `LOGICAL_TABLE`
        for the lineage crawl's table→model→answer sweep). Returns the same
        `{ guid → [dependent objects] }` shape as fetch_dependents.

        The one-GUID-per-call fetch_dependents (used by the Deleter's live
        single-hop lookup) is left untouched — a different contract.
        """
        result: dict[str, list[dict]] = {}
        if not object_ids:
            return result

        api_type = _SEARCH_TYPE_FOR_DEPENDENTS.get(object_type.upper(), "LOGICAL_TABLE")

        for start in range(0, len(object_ids), batch_size):
            batch = object_ids[start : start + batch_size]
            data = await self._request(
                "POST",
                "/api/rest/2.0/metadata/search",
                json={
                    "metadata": [{"identifier": guid, "type": api_type} for guid in batch],
                    "include_dependent_objects": True,
                    "dependent_objects_record_size": 1000,
                    "include_headers": False,
                    "record_size": -1,
                    "record_offset": 0,
                },
                context="search_dependents",
            )
            items = data if isinstance(data, list) else data.get("metadata_details", [])
            for guid in batch:
                try:
                    result[guid] = _flatten_dependent_objects(items, root_guid=guid)
                except (KeyError, TypeError, AttributeError) as exc:
                    raise TSResponseParseError(
                        url="/api/rest/2.0/metadata/search",
                        detail=f"Unexpected dependent_objects shape: {exc}",
                    ) from exc
        return result

    # ── Connections ──────────────────────────────────────────────────────────────

    async def list_connections(self) -> list[dict]:
        """
        Return every data connection as `[{"id": ..., "name": ...}, ...]`.

        POST /api/rest/2.0/connection/search — an empty body lists all
        connections. Used to resolve a TML `connection.name` back to a GUID for
        the lineage graph's Connection nodes. Requires DATAMANAGEMENT or
        ADMINISTRATION privilege (admins have this).
        """
        data = await self._request(
            "POST",
            "/api/rest/2.0/connection/search",
            json={"record_offset": 0, "record_size": -1},
            context="list_connections",
        )
        items = data if isinstance(data, list) else data.get("connections", [])
        return [
            {"id": item.get("id", ""), "name": item.get("name", "")}
            for item in items
            if isinstance(item, dict) and item.get("id")
        ]
