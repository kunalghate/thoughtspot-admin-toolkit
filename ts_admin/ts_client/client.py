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

import json
import logging
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import quote

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
# Smaller page for requests that ask for `include_details`: a 500-row page of table
# details is ~10MB and was observed timing out against a live cluster on the default
# 30s client timeout, costing a full retry of the page.
DETAIL_PAGE_SIZE = 200


# `CachedMetadata.object_type` values that are NOT members of any ThoughtSpot v2
# `type` enum. Every one of them is a logical table underneath — the five API
# subtypes plus DATASET, which is our own derived value (an Analyst Studio table)
# and has never existed on the wire at all. Any endpoint with an enum-typed
# `type` field must collapse them, or ThoughtSpot rejects the request.
#
# `tests/unit/test_client_write_endpoints.py` iterates every `MetadataType`
# member against each endpoint's real enum, so adding a member without adding it
# here fails the build.
LOGICAL_TABLE_SUBTYPES: dict[str, str] = {
    MetadataType.WORKSHEET: "LOGICAL_TABLE",
    MetadataType.ONE_TO_ONE_LOGICAL: "LOGICAL_TABLE",
    MetadataType.AGGR_WORKSHEET: "LOGICAL_TABLE",
    MetadataType.SQL_VIEW: "LOGICAL_TABLE",
    MetadataType.USER_DEFINED: "LOGICAL_TABLE",
    MetadataType.DATASET: "LOGICAL_TABLE",
}


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
    "DATASET": "LOGICAL_TABLE",
    "TABLE": "LOGICAL_TABLE",
    "MODEL": "LOGICAL_TABLE",
    "VIEW": "LOGICAL_TABLE",
    "LOGICAL_TABLE": "LOGICAL_TABLE",
    "CONNECTION": "CONNECTION",
}


# Analyst Studio is built on Mode, so its datasets land in ThoughtSpot as ordinary
# ONE_TO_ONE_LOGICAL tables sitting on a connection the API reports as RDBMS_MODE.
# That connection is not returned by /connection/search, and the tables carry no
# distinguishing subType — the warehouse type in metadata_detail is the only signal.
_ANALYST_STUDIO_SOURCE_TYPE = "RDBMS_MODE"


def _belongs_to_org(user: dict, org_id: int) -> bool:
    """True when a users/search record lists membership of ``org_id``.

    ``users/search`` returns each user's full org list inline, so scoping does
    not need the server-side ``org_identifiers`` filter. A record with no ``orgs``
    key at all is kept: on a cluster with orgs disabled there is only one org, and
    dropping everything would be worse than being permissive.
    """
    orgs = user.get("orgs")
    if orgs is None:
        return True
    return any(o.get("id") == org_id for o in orgs if isinstance(o, dict))


def _unresolvable_guids(body: str) -> list[str]:
    """GUIDs ThoughtSpot names as the reason for an error, if it named any.

    A ThoughtSpot error body can carry a nested ``debug`` string holding a JSON
    array of the object ids the server could not resolve, e.g.::

        {"error": {"message": {"debug": {"code": 13003,
                                         "debug": "[\\"53d3d10e-…\\", \\"\\"]"}}}}

    Those ids are the only thing that makes such a failure actionable — they name
    the object to go fix — and nothing else in the response identifies it.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        return []
    if not isinstance(payload, dict):
        return []
    message = ((payload.get("error") or {}).get("message")) or {}
    if not isinstance(message, dict):
        return []
    inner = message.get("debug")
    if not isinstance(inner, dict):
        return []
    try:
        ids = json.loads(inner.get("debug") or "[]")
    except (ValueError, TypeError):
        return []
    if not isinstance(ids, list):
        return []
    return [i for i in ids if isinstance(i, str) and i]


def _is_analyst_studio_dataset(item: dict) -> bool:
    """True when a LOGICAL_TABLE search result is an Analyst Studio dataset."""
    detail = item.get("metadata_detail") or {}
    return detail.get("dataSourceTypeEnum") == _ANALYST_STUDIO_SOURCE_TYPE


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
                # A 404 on a *search* endpoint is usually not "you asked for a
                # missing object" — it is ThoughtSpot failing to resolve some
                # object referenced by a row in the result set (a dangling
                # group/org membership, say). The body's nested debug array names
                # that object, and it is the only pointer to what to go fix, so
                # lift it into the message instead of burying it in raw JSON.
                guids = _unresolvable_guids(response.text)
                detail = response.text[:200]
                if guids:
                    detail = f"ThoughtSpot could not resolve object(s) {', '.join(guids)} — {detail}"
                raise TSObjectNotFoundError(
                    object_type="resource",
                    identifier=path,
                    detail=detail,
                )
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

        Org scoping is applied CLIENT-SIDE from each record's own ``orgs`` list
        rather than by sending ``org_identifiers``. The two are equivalent —
        verified live against PS-internal Prod (26.8.0.cl), where both paths
        return the identical 362-user set for org 0 — but the server-side filter
        makes ThoughtSpot resolve every result's org/group membership, and a
        single unresolvable membership 404s the whole page.

        That is not hypothetical: on SE Demo one user's membership points at a
        group id the server cannot resolve, so `org_identifiers: [0]` fails for
        any page spanning that record (record_size 249 succeeds, 250 fails) while
        the unfiltered call returns all 325 users at any page size. Filtering here
        keeps one broken membership from making the whole org unsyncable.
        """
        body: dict = {"user_identifier": "", "include_favorite_metadata": False}

        async for page in self._paginate(
            "/api/rest/2.0/users/search",
            body,
            result_key="users",
            context="search_users",
        ):
            if org_id is not None:
                page = [u for u in page if _belongs_to_org(u, org_id)]
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

        The tables pass additionally asks for `include_details` so Analyst Studio
        datasets can be told apart from ordinary tables — see _is_analyst_studio_dataset.

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
            # Only the tables pass pays for details — the response is ~10x larger
            # (measured 18MB vs 1.8MB for 900 tables) and no other subtype needs it.
            wants_details = effective_type is MetadataType.ONE_TO_ONE_LOGICAL
            if wants_details:
                body["include_details"] = True
            page_size = DETAIL_PAGE_SIZE if wants_details else PAGE_SIZE

            offset = 0
            while True:
                data = await self._request(
                    "POST",
                    "/api/rest/2.0/metadata/search",
                    json={**body, "record_offset": offset, "record_size": page_size},
                    context="search_metadata",
                )
                page: list = data if isinstance(data, list) else data.get("metadata_details", [])
                if not page:
                    break
                # Stamp each item with the effective type so the model stores it correctly
                for item in page:
                    if wants_details and _is_analyst_studio_dataset(item):
                        item["metadata_type"] = MetadataType.DATASET
                    else:
                        item["metadata_type"] = effective_type
                try:
                    yield [TSMetadataObject.model_validate(m) for m in page]
                except ValidationError as exc:
                    raise TSResponseParseError(
                        url="/api/rest/2.0/metadata/search",
                        detail=str(exc),
                    ) from exc
                if len(page) < page_size:
                    break
                offset += page_size

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

        POST /api/rest/2.0/tags/{tag_identifier}/delete — the identifier is a
        PATH segment and the request carries NO body. 204 No Content on success.

        `/api/rest/2.0/tags/delete` (what this used to POST, with a body of
        `{"tag": [...]}`) is not a route: it 404s on every cluster, and our 404
        handler renders that as "the object may have been deleted … run a sync
        and retry", which sent admins re-syncing after a failure a sync can
        never fix.

        Deleting a tag automatically removes it from every object it was
        assigned to (one API call instead of unassigning per-object first).
        Irreversible.
        """
        await self._request(
            "POST",
            f"/api/rest/2.0/tags/{quote(tag_id, safe='')}/delete",
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
        message: str = "",
        notify: bool = False,
    ) -> None:
        """
        Share a list of objects with a list of users or groups.

        POST /api/rest/2.0/security/metadata/share — 204 No Content on success,
        so the response carries no evidence of what was shared. The REQUEST is
        the only thing worth asserting in a test.

        `/api/rest/2.0/security/share` (what this used to POST, with a body
        keyed `metadata_list`) is not a route — it 404s on every cluster, and
        `metadata_list` appears nowhere in the schema.

        Body shape (`shareMetadata`, 9.0.0.cl+):
          - `metadata`: [{identifier, type?}]. No `type` is sent: we always pass
            GUIDs, for which the spec makes `type` optional, and our cache's
            object_type values (WORKSHEET, DATASET, …) are not members of this
            endpoint's type enum.
          - `permissions`: [{principal: {identifier}, share_mode}].
          - `message`: **required by the spec** — omitting the key is a 400.
          - `notify_on_share`: defaults to **true** server-side, so it is always
            sent explicitly. Left implicit, every share emails its recipients
            no matter what the caller asked for.
        """
        await self._request(
            "POST",
            "/api/rest/2.0/security/metadata/share",
            json={
                "metadata": [{"identifier": oid} for oid in object_ids],
                "permissions": [{"principal": {"identifier": pid}, "share_mode": permission} for pid in principal_ids],
                "message": message,
                "notify_on_share": notify,
            },
            context="share_objects",
        )

    # ── Permissions ────────────────────────────────────────────────────────────

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
        api_type = LOGICAL_TABLE_SUBTYPES.get(object_type, object_type)

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
        """
        Permanently delete metadata objects. Irreversible.

        `deleteMetadata`'s `type` enum is exactly
        LIVEBOARD | ANSWER | LOGICAL_TABLE | LOGICAL_COLUMN | LOGICAL_RELATIONSHIP.
        The caller (`deletion_service._execute_delete`) groups by
        `CachedMetadata.object_type`, whose values also include WORKSHEET,
        ONE_TO_ONE_LOGICAL, AGGR_WORKSHEET, SQL_VIEW, USER_DEFINED and our own
        derived DATASET — none of them enum members. They collapse to
        LOGICAL_TABLE here, exactly as they already do for fetch_permissions.
        """
        api_type = LOGICAL_TABLE_SUBTYPES.get(object_type, object_type)
        await self._request(
            "POST",
            "/api/rest/2.0/metadata/delete",
            json={"metadata": [{"identifier": oid, "type": api_type} for oid in object_ids]},
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
        default_metadata_type: str | None = None,
    ) -> list[dict]:
        """
        Return everything a principal (user or group) has access to.

        POST /api/rest/2.0/security/principals/fetch-permissions

        Used by the User Management transfer-sharing flow: discover everything
        the leaving user could see, so we can re-share each item with the
        replacement at the same access level.

        **The request schema has exactly five keys** — `principals` (required),
        `metadata`, `record_offset`, `record_size`, `default_metadata_type` —
        and v2 silently ignores anything else. Two keys this used to send were
        therefore pure no-ops, both measured live on 26.8.0.cl:

          - `permission_type` — not a key on THIS endpoint (it exists only on
            `security/metadata/fetch-permissions`, 10.3.0.cl+). "DEFINED",
            "EFFECTIVE" and omitting it returned identical results, i.e. always
            the effective set, group-inherited access included. The old comment
            claiming DEFINED meant "direct shares only" was false.
          - `metadata_type: ["LIVEBOARD"]` — the real key is
            `default_metadata_type` and it is a SINGLE STRING, not an array.
            The array form returned all types (23,197 rows); the string form
            returned 372. Callers needing several types loop.

        Returns a flat list of:
          {metadata_id, metadata_name, metadata_type, share_mode,
           shared_permission, group_permissions, is_direct_share}

        `share_mode` is the EFFECTIVE permission (`permission` on the wire).
        `shared_permission` / `group_permissions` are carried through so a
        caller can tell a share made to this principal by name from one
        inherited through a group; `is_direct_share` is the derived flag.
        """
        body: dict = {
            "principals": [{"identifier": principal_identifier}],
            "record_size": -1,
        }
        if default_metadata_type:
            body["default_metadata_type"] = default_metadata_type

        data = await self._request(
            "POST",
            "/api/rest/2.0/security/principals/fetch-permissions",
            json=body,
            context="principal_permissions",
        )

        out: list[dict] = []
        # Response shape: {"principal_permission_details": [{
        #   "principal_id", "principal_name", "principal_type",
        #   "metadata_permission_info": [{
        #     "metadata_type",
        #     "metadata_permissions": [{
        #       "metadata_id", "metadata_name",
        #       "permission": "READ_ONLY" | "MODIFY" | "NO_ACCESS",
        #       "shared_permission": "READ_ONLY" | "MODIFY" | "NO_ACCESS",
        #       "group_permission": [{"id", "name", "permission"}]
        #     }]
        #   }]
        # }]}
        principals = data.get("principal_permission_details") or [] if isinstance(data, dict) else []
        for p in principals:
            for info in p.get("metadata_permission_info") or []:
                metadata_type = info.get("metadata_type", "")
                for item in info.get("metadata_permissions") or []:
                    permission = item.get("permission", "NO_ACCESS")
                    if permission == "NO_ACCESS":
                        continue
                    shared = item.get("shared_permission") or ""
                    group_permissions = item.get("group_permission") or []
                    out.append(
                        {
                            "metadata_id": item.get("metadata_id", ""),
                            "metadata_name": item.get("metadata_name", ""),
                            "metadata_type": metadata_type,
                            "share_mode": permission,
                            "shared_permission": shared,
                            "group_permissions": group_permissions,
                            "is_direct_share": bool(shared) and shared != "NO_ACCESS",
                        }
                    )
        return out

    async def delete_user(self, *, user_identifier: str) -> None:
        """
        Permanently delete ONE user from the cluster.

        POST /api/rest/2.0/users/{user_identifier}/delete — the identifier is a
        PATH segment and the request carries NO body. 204 No Content on success.

        There is deliberately no bulk variant: `/api/rest/2.0/users/delete`
        (what this used to POST, with a body of `{"users": [...]}`) is not a
        route and 404s on every cluster. Callers loop, which they already did —
        one identifier per call keeps per-user retries isolated.

        The identifier may be a username or a user GUID, so it is percent-encoded
        before being interpolated: an unescaped `/` or `..` in a username would
        otherwise rewrite the request path.

        Irreversible — the user is removed from every org and every group. Owned
        content stays but becomes orphan-owned; transfer ownership first if you
        need to preserve attribution.
        """
        await self._request(
            "POST",
            f"/api/rest/2.0/users/{quote(user_identifier, safe='')}/delete",
            context="delete_user",
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
