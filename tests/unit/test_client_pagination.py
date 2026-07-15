"""
Regression tests for ThoughtSpotClient._paginate response-shape handling.

The TS REST v2 search endpoints (users/search, groups/search) return a *bare
JSON array*, but `_paginate` originally assumed a `{result_key: [...]}` dict and
called `data.get(...)`. Against a live cluster that raised:

    AttributeError: 'list' object has no attribute 'get'

…which surfaced to the user as a failed `sync:users` job. These tests pin the
helper to accept both the bare-array shape and the legacy dict-wrapped shape.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from ts_admin.ts_client.auth import BearerTokenAuth
from ts_admin.ts_client.client import ThoughtSpotClient

BASE = "https://ts.example.com"


def _user(uid: str, name: str) -> dict:
    return {"id": uid, "name": name, "display_name": name.title(), "email": f"{name}@x.com", "status": "ACTIVE"}


def _group(gid: str, name: str) -> dict:
    return {"id": gid, "name": name, "display_name": name.title()}


@respx.mock
@pytest.mark.anyio
async def test_search_users_accepts_bare_array_response():
    """users/search returns a top-level list — must not raise AttributeError."""
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(
        return_value=httpx.Response(200, json=[_user("u1", "alice"), _user("u2", "bob")])
    )

    collected = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_users(org_id=0):
            collected.extend(page)

    assert [u.id for u in collected] == ["u1", "u2"]
    assert collected[0].name == "alice"


@respx.mock
@pytest.mark.anyio
async def test_search_groups_accepts_bare_array_response():
    """groups/search returns a top-level list too — same shared _paginate path."""
    respx.post(f"{BASE}/api/rest/2.0/groups/search").mock(
        return_value=httpx.Response(200, json=[_group("g1", "sales")])
    )

    collected = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_groups(org_id=0):
            collected.extend(page)

    assert [g.id for g in collected] == ["g1"]


@respx.mock
@pytest.mark.anyio
async def test_search_users_still_accepts_dict_wrapped_response():
    """Legacy/other endpoints may wrap results under result_key — keep that working."""
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(
        return_value=httpx.Response(200, json={"users": [_user("u9", "carol")]})
    )

    collected = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_users(org_id=0):
            collected.extend(page)

    assert [u.id for u in collected] == ["u9"]


@respx.mock
@pytest.mark.anyio
async def test_search_users_empty_array_terminates_cleanly():
    """An empty list response yields nothing and stops paginating."""
    respx.post(f"{BASE}/api/rest/2.0/users/search").mock(return_value=httpx.Response(200, json=[]))

    collected = []
    async with ThoughtSpotClient(url=BASE, auth=BearerTokenAuth(token="t")) as client:
        async for page in client.search_users(org_id=0):
            collected.extend(page)

    assert collected == []
