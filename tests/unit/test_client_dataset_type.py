"""
Analyst Studio datasets must be distinguishable from ordinary tables.

Analyst Studio is built on Mode, so its datasets arrive as ONE_TO_ONE_LOGICAL
logical tables with no distinguishing subType — the only signal is the connection
they sit on, which the API reports as `dataSourceTypeEnum: RDBMS_MODE` inside
`metadata_detail`. Confirmed against PS-internal Prod (26.8.0.cl): 24 of 897
tables, and the Mode connection is not returned by /connection/search at all, so
there is no cheaper route to it.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from ts_admin.ts_client.auth import BearerTokenAuth
from ts_admin.ts_client.client import ThoughtSpotClient
from ts_admin.ts_client.models import MetadataType

BASE = "https://ts.example.com"
SEARCH = f"{BASE}/api/rest/2.0/metadata/search"


def _table(guid: str, name: str, source_type: str | None) -> dict:
    detail = {"dataSourceTypeEnum": source_type} if source_type else {}
    return {
        "metadata_id": guid,
        "metadata_name": name,
        "metadata_type": "LOGICAL_TABLE",
        "metadata_header": {"id": guid, "name": name, "author": "u1", "authorDisplayName": "U"},
        "metadata_detail": detail,
    }


def _subtype_of(request: httpx.Request) -> str | None:
    body = json.loads(request.content)
    subtypes = body["metadata"][0].get("subtypes")
    return subtypes[0] if subtypes else None


@respx.mock
@pytest.mark.anyio
async def test_mode_backed_tables_are_typed_as_datasets():
    def responder(request: httpx.Request) -> httpx.Response:
        if _subtype_of(request) != "ONE_TO_ONE_LOGICAL":
            return httpx.Response(200, json=[])
        if json.loads(request.content)["record_offset"] > 0:
            return httpx.Response(200, json=[])
        return httpx.Response(
            200,
            json=[
                _table("t1", "Ordinary Snowflake table", "RDBMS_SNOWFLAKE"),
                _table("t2", "Analyst Studio dataset", "RDBMS_MODE"),
                _table("t3", "Table with no detail", None),
            ],
        )

    respx.post(SEARCH).mock(side_effect=responder)

    collected = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_metadata():
            collected.extend(page)

    by_id = {o.id: o.type for o in collected}
    assert by_id["t1"] is MetadataType.ONE_TO_ONE_LOGICAL
    assert by_id["t2"] is MetadataType.DATASET
    # No detail at all must fall back to Table, never to Dataset.
    assert by_id["t3"] is MetadataType.ONE_TO_ONE_LOGICAL


@respx.mock
@pytest.mark.anyio
async def test_only_the_tables_pass_asks_for_details():
    """include_details roughly 10x's the response, so no other subtype pays for it."""
    seen: dict[str | None, bool] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen[_subtype_of(request)] = body.get("include_details", False)
        return httpx.Response(200, json=[])

    respx.post(SEARCH).mock(side_effect=responder)

    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for _ in client.search_metadata():
            pass

    assert seen.pop("ONE_TO_ONE_LOGICAL") is True
    assert not any(seen.values()), f"details requested for {[k for k, v in seen.items() if v]}"
